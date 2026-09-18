"""Property tests for canonicalisation and digests (``FR-04``, ``INV-04``).

The unit tests pin what the canonical form *is*. These pin what it must *be
like*, over inputs nobody thought to write down.

Three properties, and each of them is a security property rather than a
tidiness one:

* **Idempotence.** Canonicalising, parsing and canonicalising again must return
  the same bytes. This is exactly the path a decision takes in production -- the
  gateway canonicalises and digests, the envelope is stored and later read back
  as JSON, and the broker canonicalises and digests again before executing
  (``FR-21``). A value that shifts on that round trip produces
  ``TOKEN_INVALID:digest_mismatch`` for an envelope nobody touched.
* **Order independence.** Two components that build the same document in
  different key order must agree. If they do not, the digest identifies the
  construction rather than the document, and an approval bound to it means
  nothing.
* **Determinism.** The same value must digest to the same thing every time. Any
  hidden state -- a salt, a cache, iteration order, an address -- would make a
  digest unreproducible from an archived record, which is the one thing an
  audit trail must never be (Constitution Article III).
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from neuroharness.canonical.digest import (
    SAFE_INTEGER_BOUND,
    digest_bytes,
    digest_value,
    renders_as_unsafe_integer,
)
from neuroharness.canonical.jcs import canonical_string, canonicalize
from neuroharness.errors import CanonicalizationError

pytestmark = pytest.mark.property

#: Nesting depth of generated documents. Bounded explicitly rather than left to
#: ``st.recursive`` so that no generated example can trip the canonicaliser's
#: own depth guard and turn a property failure into a strategy artefact.
GENERATED_MAX_DEPTH = 4

#: Breadth of generated containers. Small: these properties are about shape, and
#: a thousand-element array exercises nothing a four-element one does not.
GENERATED_MAX_SIZE = 4

#: Repetitions for the determinism property. A hundred is enough to catch
#: anything driven by hash randomisation, iteration order or an address, all of
#: which vary within a single process.
DETERMINISM_RUNS = 100

#: Examples per property. Enough coverage to be worth running on every commit,
#: few enough that the 100-run determinism property stays inside a test budget.
EXAMPLES = 50
DETERMINISM_EXAMPLES = 20

#: ``derandomize`` because ``.hypothesis/`` is not checked in: without it a
#: failing example found in CI exists only in that run's database, and the
#: report says "a property failed" with no way for anyone to reproduce it.
_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)


def json_scalars() -> st.SearchStrategy[Any]:
    """Every JSON scalar the canonicaliser accepts.

    Non-finite floats are excluded because they are not JSON values at all;
    :mod:`tests.unit.test_jcs` proves they are refused.

    Numbers are bounded to the IEEE-754 safe range. That is not a convenience:
    ``ADR-0021`` narrows the *digest* domain to values a conforming RFC 8785
    encoder would read identically, so a document carrying a larger integer has
    no digest to reason about. :mod:`tests.unit.test_digest_integer_bounds`
    covers the refusal; these properties cover the domain where digests exist.
    ``canonicalize`` itself is unrestricted and :mod:`tests.unit.test_jcs`
    exercises it on larger integers.

    The bound is expressed over the *canonical form* for floats, not over the
    Python type, because that is how the rule itself is written. ``1e20`` is a
    float here and an integer once RFC 8785 has serialised it, so a filter on
    ``isinstance`` would generate documents the digest domain excludes; a filter
    on magnitude alone would exclude ``1e300``, which serialises with an
    exponent and round-trips perfectly well.
    """
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-SAFE_INTEGER_BOUND, max_value=SAFE_INTEGER_BOUND),
        st.floats(allow_nan=False, allow_infinity=False).filter(
            lambda value: not renders_as_unsafe_integer(value)
        ),
        st.text(),
    )


def json_values(max_depth: int = GENERATED_MAX_DEPTH) -> st.SearchStrategy[Any]:
    """Documents of bounded depth and breadth."""
    if max_depth <= 0:
        return json_scalars()
    child = json_values(max_depth - 1)
    return st.one_of(
        json_scalars(),
        st.lists(child, max_size=GENERATED_MAX_SIZE),
        st.dictionaries(st.text(), child, max_size=GENERATED_MAX_SIZE),
    )


def json_objects(max_depth: int = GENERATED_MAX_DEPTH) -> st.SearchStrategy[dict[str, Any]]:
    """Documents whose root is an object, as every envelope's is."""
    return st.dictionaries(
        st.text(), json_values(max_depth - 1), min_size=1, max_size=GENERATED_MAX_SIZE
    )


def _reordered(value: Any, rng: random.Random) -> Any:
    """Rebuild ``value`` with every object's keys inserted in a fresh order.

    Equal as documents, different as Python objects: this is the difference
    between two components that assembled the same envelope by different routes.
    """
    if isinstance(value, dict):
        items = [(key, _reordered(member, rng)) for key, member in value.items()]
        rng.shuffle(items)
        return dict(items)
    if isinstance(value, list):
        return [_reordered(element, rng) for element in value]
    return value


@_SETTINGS
@given(document=json_values())
def test_canonicalisation_survives_a_parse(document: Any) -> None:
    """Canonicalise, parse, canonicalise again: the bytes must not move.

    The interesting cases are numeric. A float that canonicalises to plain digits
    parses back as an ``int``, and an ``int`` beyond 2**53 must not round-trip
    through a double. Both must still land on the same bytes.
    """
    once = canonicalize(document)
    twice = canonicalize(json.loads(canonical_string(document)))
    assert twice == once


@_SETTINGS
@given(document=json_objects(), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_key_insertion_order_does_not_affect_output(document: dict[str, Any], seed: int) -> None:
    reordered = _reordered(document, random.Random(seed))
    assert reordered == document
    assert canonicalize(reordered) == canonicalize(document)
    assert digest_value(reordered) == digest_value(document)


@settings(
    max_examples=DETERMINISM_EXAMPLES,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(document=json_values(), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_digest_value_is_deterministic(document: Any, seed: int) -> None:
    """The same document digests to the same value, every time, however built."""
    rng = random.Random(seed)
    expected = digest_value(document)
    for _ in range(DETERMINISM_RUNS):
        assert digest_value(_reordered(document, rng)) == expected


@_SETTINGS
@given(document=json_values())
def test_a_digest_is_the_digest_of_the_canonical_bytes_and_nothing_else(
    document: Any,
) -> None:
    """No salt, no nonce, no domain tag: ``digest_value`` adds nothing.

    This replaces a property asserting that two documents digest equally exactly
    when their canonical forms are equal. That direction restated that
    ``sha256 . canonicalize`` is a function, and its interesting half could only
    fail on a SHA-256 collision, so it could not fail.

    What can fail is this: the specification defines the digest as SHA-256 over
    exactly the JCS bytes, and publishes test vectors that non-Python components
    must reproduce. Prefixing a domain separator - a reasonable-looking hardening
    change - would silently make every one of those vectors wrong, and the
    symptom would appear as a cross-implementation token mismatch long after the
    change.
    """
    assert digest_value(document) == digest_bytes(canonicalize(document))


#: Every character RFC 8785 section 3.2.2.2 requires to be escaped rather than
#: emitted. A raw one in the output is not JSON at all, and a parser that
#: tolerated it would disagree with one that did not - which is a digest
#: mismatch between two conforming implementations.
_MUST_NOT_APPEAR_RAW = frozenset(range(0x20)) | {ord('"'), ord("\\")}


@_SETTINGS
@given(document=json_values())
def test_no_control_character_is_ever_emitted_raw(document: Any) -> None:
    """Replaces an assertion-free "it parses" property with one that can fail.

    Generated strings carry newlines, tabs and ``NUL``. Emitting one raw would
    also forge a line in the JSONL evidence export, which is the shape
    ``SEC-07`` exists to close, so this is a security property and not a
    formatting one.
    """
    text = canonical_string(document)
    inside_string = False
    escaped = False
    for character in text:
        if escaped:
            escaped = False
            continue
        if character == "\\" and inside_string:
            escaped = True
            continue
        if character == '"':
            inside_string = not inside_string
            continue
        assert ord(character) not in _MUST_NOT_APPEAR_RAW, (
            f"{character!r} appears unescaped in {text!r}"
        )


#: Characters chosen so that code-point order and UTF-16 code-unit order
#: *disagree*. A supplementary character encodes as a surrogate pair whose lead
#: unit is in U+D800-U+DBFF, so it sorts below every character in
#: U+E000-U+FFFF - the reverse of its code-point order. Left to a generic text
#: strategy, the discriminating pair would essentially never be drawn, and the
#: property would pass against a code-point implementation forever.
_UTF16_DISCRIMINATING = st.sampled_from(
    ["\uE000", "\uF8FF", "\uFFFD", "\U00010000", "\U0001F600", "\U0010FFFF", "a", "~"]
)


@_SETTINGS
@given(
    keys=st.lists(_UTF16_DISCRIMINATING, min_size=2, max_size=6, unique=True),
)
def test_supplementary_keys_sort_below_the_private_use_area(keys: list[str]) -> None:
    """The case that separates UTF-16 order from code-point order (``FR-04``).

    ``sorted()`` on ``str`` is code-point order and is the obvious thing to
    write. For a key above U+FFFF it gives the wrong answer, so two
    implementations - one following RFC 8785, one following Python's default -
    would produce different bytes for the same document, and therefore different
    digests, and therefore a token that does not verify.
    """
    document = {key: index for index, key in enumerate(keys)}
    emitted = json.loads(canonical_string(document), object_pairs_hook=lambda p: [k for k, _ in p])

    assert emitted == sorted(keys, key=lambda key: key.encode("utf-16-be"))


@_SETTINGS
@given(document=json_objects())
def test_object_keys_are_sorted_by_utf16_code_unit(document: dict[str, Any]) -> None:
    """RFC 8785 sorts keys as UTF-16 code units, not as code points.

    The two orders differ for any key above U+FFFF, because a supplementary
    character encodes as a surrogate pair whose lead unit sorts *below* U+E000.
    Python's own ``sorted`` on ``str`` is code-point order, so the obvious
    implementation is wrong for exactly the inputs nobody writes by hand - and
    the unit tests pin it with two vectors. This pins it over whatever
    Hypothesis produces, in the nesting the envelope actually has.
    """
    orders: list[list[str]] = []

    def record_order(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        orders.append([key for key, _ in pairs])
        return dict(pairs)

    json.loads(canonical_string(document), object_pairs_hook=record_order)

    assert orders, "the generated document had no object to check"
    for keys in orders:
        assert keys == sorted(keys, key=lambda key: key.encode("utf-16-be"))


#: Lone surrogates: code points with no UTF-8 encoding at all. They reach a
#: gateway through any decoder that accepts them, and they are the one string
#: input with no canonical form.
_LONE_SURROGATES = st.characters(min_codepoint=0xD800, max_codepoint=0xDFFF)


@_SETTINGS
@given(
    surrogate=_LONE_SURROGATES,
    prefix=st.text(max_size=GENERATED_MAX_SIZE),
    suffix=st.text(max_size=GENERATED_MAX_SIZE),
    in_key=st.booleans(),
)
def test_a_lone_surrogate_has_no_canonical_form(
    surrogate: str, prefix: str, suffix: str, in_key: bool
) -> None:
    """Refused, in a key or a value, wherever in the string it sits.

    The documented refusal was covered by two hand-written vectors. A string
    that has no UTF-8 encoding must not be digested rather than being replaced,
    stripped or passed through, because every one of those would give two
    implementations different bytes for the same document.

    The error must also be *renderable*: interpolating an unpaired surrogate
    into the message verbatim produced text that raised ``UnicodeEncodeError``
    the moment anything wrote it, turning a clean refusal into a crash with no
    reason code (Constitution Art. II).
    """
    text = prefix + surrogate + suffix
    document = {text: "value"} if in_key else {"key": text}

    with pytest.raises(CanonicalizationError) as raised:
        canonical_string(document)

    str(raised.value).encode("utf-8")
