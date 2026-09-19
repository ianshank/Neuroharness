"""RFC 8785 (JCS) canonical JSON.

Two digests decide who may act: approvals bind to the proposal digest and
decision tokens bind to the envelope digest (``FR-04``, ADR-0015). Both are
SHA-256 over *bytes*, so the only thing standing between a decision and a
time-of-check/time-of-use forgery is that one logical document always produces
one byte string -- here in the gateway, in the broker that recomputes the digest
before it executes anything (``FR-21``), and in whatever language a later
component is written in. ``json.dumps`` cannot supply that: key order,
whitespace, ``ensure_ascii`` and float formatting are all free parameters, and
any of them differing between two components turns every honest decision into
``TOKEN_INVALID:digest_mismatch`` and every dishonest one into a coin flip.

So this module implements the scheme rather than configuring the standard
encoder, and it is deliberately stricter than the RFC in one direction: any
input it cannot canonicalize with certainty raises
:class:`~neuroharness.errors.CanonicalizationError` rather than guessing.
Constitution Article II -- a value the harness cannot pin down has no identity,
and no decision can be made about it.

Decisions recorded here because they are visible on the wire:

* **Exact integers.** RFC 8785 defines numbers through IEEE-754 doubles. Python
  distinguishes ``int`` from ``float``, and the harness routinely carries
  identifiers, offsets and byte counts beyond 2**53. Rounding a resource
  identifier while computing the digest that authorises acting on it is a worse
  failure than deviating from the number grammar, so a Python ``int`` is emitted
  with its exact digits and a ``float`` always takes the ECMAScript path. Hence
  ``1e30`` canonicalises to ``1e+30`` while ``10**30`` canonicalises to its full
  decimal expansion. ``json.loads`` preserves the same distinction, so the
  canonical form survives a parse-and-recanonicalise round trip either way.
* **Escaping stops at U+001F.** The RFC escapes only what JSON requires, so C1
  controls, U+007F and every non-ASCII character are emitted literally as UTF-8.
  "Defensive" extra escaping would change the bytes and therefore the digest.
* **Lone surrogates are rejected** rather than smuggled through with
  ``surrogatepass``: they have no UTF-8 encoding, so they have no canonical
  form, and a digest over a guess is worse than no digest.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from operator import itemgetter
from typing import Any, Final, TypeAlias, cast

from neuroharness.errors import CanonicalizationError

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "JSONValue",
    "canonical_string",
    "canonicalize",
    "parse_json",
    "reject_duplicate_keys",
]

#: What this module accepts. Kept loose on the container types because values
#: reach it from parsed JSON, from pydantic models and from test fixtures alike;
#: the runtime check in :func:`_write` is the authority, not this alias.
JSONScalar: TypeAlias = "bool | int | float | str | None"
JSONValue: TypeAlias = "JSONScalar | Mapping[str, Any] | Sequence[Any]"

#: Maximum nesting of objects and arrays. ``{"a": {"b": 1}}`` is depth two.
#:
#: This is a denial-of-service bound, not a schema bound: an agent-authored
#: proposal is untrusted input (``FR-03``), and unbounded recursion here would
#: let a proposal crash the gateway before policy ever sees it -- an availability
#: attack on the component whose job is to say no. Sixty-four is far above any
#: envelope the schema permits and far below CPython's own recursion ceiling, so
#: the failure is a recorded ``SCHEMA_INVALID``, never a ``RecursionError``.
DEFAULT_MAX_DEPTH: Final[int] = 64

# --- Number formatting (RFC 8785 section 3.2.2.3, ECMAScript Number::toString)

#: Above this decimal exponent ECMAScript switches to exponential notation.
_ES6_MAX_PLAIN_EXPONENT: Final[int] = 21
#: At or below this decimal exponent it does the same for small magnitudes.
_ES6_MIN_PLAIN_EXPONENT: Final[int] = -6

# --- String escaping (RFC 8785 section 3.2.2.2) ------------------------------

#: The complete escape table. Anything not listed and not a C0 control is
#: emitted literally; C0 controls without a short form use ``\u00xx`` in
#: lowercase hexadecimal.
_SHORT_ESCAPES: Final[Mapping[str, str]] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

#: First code point that never needs escaping. Everything below it is a C0
#: control, which JSON forbids unescaped. Nothing above it is escaped at all --
#: not DEL, not the C1 controls, not any non-ASCII character.
_FIRST_LITERAL_CODE_POINT: Final[int] = 0x20

#: Width of the ``\u`` escape's hexadecimal field.
_ESCAPE_HEX_WIDTH: Final[int] = 4

#: The surrogate block, which UTF-8 cannot encode.
_FIRST_SURROGATE: Final[int] = 0xD800
_LAST_SURROGATE: Final[int] = 0xDFFF


def _build_escape_table() -> dict[int, str]:
    """Derive the full escape table from the two rules above.

    Built rather than written out so that the table and the pattern that finds
    its members cannot drift apart, and so that the boundary at
    :data:`_FIRST_LITERAL_CODE_POINT` appears once.
    """
    table = {ord(character): escape for character, escape in _SHORT_ESCAPES.items()}
    for code_point in range(_FIRST_LITERAL_CODE_POINT):
        table.setdefault(code_point, "\\u" + format(code_point, f"0{_ESCAPE_HEX_WIDTH}x"))
    return table


_ESCAPE_TABLE: Final[Mapping[int, str]] = _build_escape_table()

#: Matches exactly the characters :data:`_ESCAPE_TABLE` can replace. A pattern
#: rather than ``str.translate`` because the overwhelming majority of strings in
#: an envelope need no escaping at all, and this way they are not copied.
_ESCAPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    "[" + "".join(re.escape(chr(code_point)) for code_point in sorted(_ESCAPE_TABLE)) + "]"
)

_SURROGATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    "[" + chr(_FIRST_SURROGATE) + "-" + chr(_LAST_SURROGATE) + "]"
)

_JSON_POINTER_ESCAPES: Final[tuple[tuple[str, str], ...]] = (("~", "~0"), ("/", "~1"))

_ROOT_PATH_LABEL: Final[str] = "<document root>"


def _render_path(path: tuple[str, ...]) -> str:
    """Render a location as an RFC 6901 JSON Pointer for an error message.

    Errors from a canonicaliser are read by whoever has to fix the document, and
    "unsupported type" without a location is unactionable in a nested envelope.

    The rendering is ASCII-only, and that is a fail-closed requirement rather
    than a formatting preference. The values that reach this function are the
    ones that had no canonical form, and the commonest reason is an unpaired
    surrogate - which has no UTF-8 encoding at all. Interpolating such a key
    verbatim produces a message that raises ``UnicodeEncodeError`` the moment
    anything tries to write it, so the clean refusal this module is raising
    would be replaced, in the caller's logging path, by a crash carrying no
    reason code (Constitution Art. II, ``NFR-18``). Escaping here keeps a
    refusal a refusal.
    """
    if not path:
        return _ROOT_PATH_LABEL
    parts = []
    for segment in path:
        for raw, escaped in _JSON_POINTER_ESCAPES:
            segment = segment.replace(raw, escaped)
        # ``ascii()`` quotes as well as escapes; the quotes are stripped so the
        # pointer still reads as a pointer.
        parts.append(segment if segment.isascii() else ascii(segment)[1:-1])
    return "/" + "/".join(parts)


def _plain_str(value: str) -> str:
    """Return the character content of a ``str`` or any ``str`` subclass.

    ``Digest``, ``Principal`` and the ``str``-valued enums in
    :mod:`neuroharness.models.common` are all ``str`` subclasses that travel
    inside envelopes. Calling ``str()`` on a ``str``-valued enum member yields
    ``"Verdict.ALLOW"`` rather than ``"ALLOW"``, which would silently change the
    digest depending on whether a value had been through a parse. Going straight
    to ``str.__str__`` takes the content and nothing else.
    """
    return value if type(value) is str else str.__str__(value)


def _escape_string(text: str, path: tuple[str, ...]) -> str:
    """Quote and escape one JSON string exactly as RFC 8785 section 3.2.2.2 does."""
    if _SURROGATE_PATTERN.search(text):
        raise CanonicalizationError(
            f"string at {_render_path(path)} contains an unpaired surrogate and "
            "has no UTF-8 encoding, so it has no canonical form"
        )
    return '"' + _ESCAPE_PATTERN.sub(_escape_match, text) + '"'


def _escape_match(match: re.Match[str]) -> str:
    return _ESCAPE_TABLE[ord(match.group())]


def _shortest_decimal(value: float) -> tuple[str, int]:
    """Split a positive finite float into ECMAScript's ``(digits, n)``.

    ECMAScript defines ``Number::toString`` over the *shortest* digit string
    that round-trips, which is exactly what CPython's ``repr`` produces; only the
    placement of the decimal point differs between the two languages. Reusing
    ``repr`` therefore reuses CPython's correctly-rounded shortest-representation
    algorithm instead of reimplementing Ryu, and leaves this function with the
    part that actually differs.

    Returns ``digits`` with no leading or trailing zeros and ``n`` such that the
    value equals ``0.<digits> * 10**n``.
    """
    text = repr(value)
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    integer_part, _, fraction_part = mantissa.partition(".")
    significant = (integer_part + fraction_part).lstrip("0")
    digits = significant.rstrip("0")
    trailing_zeros = len(significant) - len(digits)
    n = len(digits) + trailing_zeros + exponent - len(fraction_part)
    return digits, n


def _format_float(value: float, path: tuple[str, ...]) -> str:
    """Serialise a float the way ECMAScript's ``Number::toString`` would."""
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError(
            f"number at {_render_path(path)} is {value!r}; JSON has no "
            "representation for NaN or Infinity"
        )
    if value == 0.0:
        # Covers negative zero, which RFC 8785 serialises as "0": JSON draws no
        # distinction, so neither may the digest.
        return "0"
    sign = "-" if value < 0 else ""
    digits, n = _shortest_decimal(abs(value))
    k = len(digits)
    if k <= n <= _ES6_MAX_PLAIN_EXPONENT:
        return sign + digits + "0" * (n - k)
    if 0 < n <= _ES6_MAX_PLAIN_EXPONENT:
        return sign + digits[:n] + "." + digits[n:]
    if _ES6_MIN_PLAIN_EXPONENT < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    exponent = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    exponent_sign = "+" if exponent > 0 else "-"
    return f"{sign}{mantissa}e{exponent_sign}{abs(exponent)}"


def _sorted_members(
    mapping: Mapping[Any, Any], path: tuple[str, ...]
) -> list[tuple[bytes, str, Any]]:
    """Validate and order one object's members.

    RFC 8785 sorts keys by their UTF-16 code units, which is *not* Python's
    ordering by code point: U+1F600 is one code point above U+FB33 but its
    leading surrogate 0xD83D sorts below 0xFB33. Encoding each key as UTF-16
    big-endian and comparing bytes reproduces the required order exactly,
    because a lexicographic comparison of big-endian pairs is a lexicographic
    comparison of code units.
    """
    members: list[tuple[bytes, str, Any]] = []
    seen: set[str] = set()
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise CanonicalizationError(
                f"object at {_render_path(path)} has a non-string key of type "
                f"{type(key).__name__!r}; JSON object keys are strings"
            )
        text = _plain_str(key)
        if text in seen:
            raise CanonicalizationError(
                f"object at {_render_path(path)} repeats the key {text!r}; a "
                "document with two values for one key has no single meaning"
            )
        seen.add(text)
        # ``surrogatepass`` only defers the failure: _escape_string rejects the
        # key below, with its path, rather than letting sorting raise first.
        members.append((text.encode("utf-16-be", "surrogatepass"), text, value))
    members.sort(key=itemgetter(0))
    return members


def _write(
    value: Any, path: tuple[str, ...], depth: int, max_depth: int, out: list[str]
) -> None:
    """Append the canonical form of ``value`` to ``out``.

    Written as an explicit append-to-buffer walk rather than a string-returning
    recursion because envelopes carry fact payloads, and quadratic concatenation
    on the hot path of every decision is a latency budget (``NFR-01``) spent for
    nothing.
    """
    if value is None:
        out.append("null")
        return
    # bool before int: ``True`` is an ``int`` in Python and would otherwise
    # canonicalise to "1", which policy would then compare against a number.
    if isinstance(value, bool):
        out.append("true" if value else "false")
        return
    if isinstance(value, int):
        out.append(int.__repr__(value))
        return
    if isinstance(value, float):
        out.append(_format_float(value, path))
        return
    if isinstance(value, str):
        out.append(_escape_string(_plain_str(value), path))
        return
    if isinstance(value, Mapping):
        if depth >= max_depth:
            raise CanonicalizationError(
                f"object at {_render_path(path)} exceeds the maximum nesting "
                f"depth of {max_depth}"
            )
        out.append("{")
        for index, (_, key, member) in enumerate(_sorted_members(value, path)):
            if index:
                out.append(",")
            out.append(_escape_string(key, (*path, key)))
            out.append(":")
            _write(member, (*path, key), depth + 1, max_depth, out)
        out.append("}")
        return
    if isinstance(value, (list, tuple)):
        if depth >= max_depth:
            raise CanonicalizationError(
                f"array at {_render_path(path)} exceeds the maximum nesting "
                f"depth of {max_depth}"
            )
        out.append("[")
        for index, element in enumerate(value):
            if index:
                out.append(",")
            _write(element, (*path, str(index)), depth + 1, max_depth, out)
        out.append("]")
        return
    raise CanonicalizationError(
        f"value at {_render_path(path)} has type {type(value).__name__!r}, "
        "which has no JSON form; convert it before canonicalising"
    )


def canonical_string(value: JSONValue, *, max_depth: int = DEFAULT_MAX_DEPTH) -> str:
    """Return the RFC 8785 canonical form of ``value`` as text.

    Prefer :func:`canonicalize` anywhere the result is hashed, signed or
    compared: the canonical form is defined over UTF-8 bytes, and a ``str`` is
    one encoding step away from that definition. This variant exists for logs,
    fixtures and diffs, where a human is the consumer.

    ``max_depth`` bounds nesting; see :data:`DEFAULT_MAX_DEPTH` for why it is a
    bound and not a preference.
    """
    out: list[str] = []
    _write(value, (), 0, max_depth, out)
    return "".join(out)


def canonicalize(value: JSONValue, *, max_depth: int = DEFAULT_MAX_DEPTH) -> bytes:
    """Return the RFC 8785 canonical form of ``value`` as UTF-8 bytes.

    These bytes are what every digest in the harness is taken over, so this is
    the function the gateway, the token service and the broker must all agree on.
    """
    return canonical_string(value, max_depth=max_depth).encode("utf-8")


def reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that refuses an object with a repeated key.

    ``json.loads`` keeps the last of a repeated key and says nothing. That is a
    parser-differential waiting to happen: the harness would digest and evaluate
    one value while a downstream tool -- written against a parser that keeps the
    first -- executes another, and the digest would match throughout. Refusing
    the document is the only outcome that keeps the digest meaningful.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalizationError(
                f"JSON input repeats the object key {key!r}; parsers disagree "
                "on which value wins, so the document has no single meaning"
            )
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise CanonicalizationError(
        f"JSON input contains the non-standard literal {name!r}, which has no "
        "canonical form"
    )


def parse_json(text: str | bytes) -> JSONValue:
    """Parse JSON under the same strictness this module canonicalises with.

    Intake and canonicalisation have to agree or the strictness is decorative:
    a document that ``json.loads`` accepts by silently discarding a duplicate
    key, or by inventing ``NaN``, would sail past validation and be digested in
    its rewritten form. Anything the harness will digest should enter through
    here.
    """
    import json

    # ``json.loads`` is typed ``Any``. The two hooks above are what narrow it to
    # the declared union: ``parse_constant`` refuses ``NaN``/``Infinity`` and
    # ``object_pairs_hook`` refuses duplicate keys, so nothing outside the union
    # survives this call. The cast records that the narrowing is done by the
    # hooks rather than by the type system.
    return cast(JSONValue, json.loads(
        text, object_pairs_hook=reject_duplicate_keys, parse_constant=_reject_constant
    ))
