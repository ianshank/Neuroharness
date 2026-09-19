"""One lexical grammar for every identifier the harness records.

Four modules used to spell the resource-key grammar independently -
:mod:`neuroharness.registry.resource_keys`, :mod:`neuroharness.models.record`,
:mod:`neuroharness.reason` and the two published JSON Schemas - and they had
drifted. The registry admitted a hyphen in a *kind* segment and refused an
underscore; the record model and both schemas did the opposite:

=====================  ==========  ========
key                    registry    record
=====================  ==========  ========
``repo:my-repo``       accepted    accepted
``cluster-prod:svc``   accepted    **refused**
``s3-bucket:data``     accepted    **refused**
``s3:bucket_name``     **refused** accepted
=====================  ==========  ========

A hyphenated resource *kind* - ``cluster-prod``, ``s3-bucket``,
``k8s-namespace``, which is what a real deployment writes - was therefore
accepted by the signed registry, became the broker's lease identity under
``FR-25``, and then could not be recorded. Per the fail-closed rule that is
``SCHEMA_INVALID`` and then ``ABSTAIN``, so **lease contention on a hyphenated
resource reached an operator as a harness malfunction rather than as
contention**, and the whole action class abstained until somebody renamed the
resource. The mirror direction was quieter and worse: a record could assert a
lease on ``s3:bucket_name``, which no registry lookup will ever match.

``ADR-0023`` records the decision. The registry grammar wins, and this module is
where it lives. The record model and the schemas are derived from here, so there
is nothing left to drift.

**The invariant that was claimed and false.** ``resource_keys.py`` documented
its grammar as "a subset of the reason-code subject charset [...] excludes
``_`` for the same reason". Both halves were untrue: it admitted ``-``, which
the record model's kind segment rejected, and the record model admitted ``_``,
which voided the stated reason for excluding it. The claim is now asserted at
import - see :func:`_assert_resource_keys_are_renderable` - because a docstring
that states an invariant nothing checks is how this drift happened.
"""

from __future__ import annotations

import re
import string
from typing import Final

__all__ = [
    # Character-level building blocks
    "RESOURCE_KIND_SOURCE",
    "RESOURCE_ID_SOURCE",
    "RESOURCE_KEY_SOURCE",
    "SUBJECT_SOURCE",
    # Reason-code subject shapes, one per parameterised reason name
    "RULE_ID_SOURCE",
    "SNAKE_SOURCE",
    "CRITIC_ID_SOURCE",
    "PROPERTY_ID_SOURCE",
    "UUID_SOURCE",
    "BATCH_POSITION_SOURCE",
    # Compiled, anchored
    "RESOURCE_KEY_PATTERN",
    "SUBJECT_PATTERN",
    # Bounds
    "MAX_RESOURCE_KEY_LENGTH",
    "MAX_SUBJECT_LENGTH",
    "MAX_REASON_CODE_LENGTH",
    "anchored",
    "GrammarInconsistencyError",
    "longest_resource_key",
    "MAX_RESOURCE_KIND_LENGTH",
    "MAX_RESOURCE_ID_LENGTH",
]


def anchored(source: str) -> re.Pattern[str]:
    """Compile ``source`` as a whole-string pattern.

    Every pattern in this module is used with :meth:`re.Pattern.fullmatch`
    rather than :meth:`~re.Pattern.match`, and is anchored besides. Both,
    deliberately: ``$`` also matches *before* a final newline, so an identifier
    carrying one would satisfy an anchored ``.match`` and then forge a line in a
    JSONL evidence export (``SEC-07``). ``tests/unit/test_anchored_patterns.py``
    holds that case open.
    """
    return re.compile(f"^(?:{source})$")


# --- Resource keys (FR-25, FR-34) --------------------------------------------

#: Longest ``kind`` and ``id`` segment. Named rather than written into the
#: regexes, because the maximal-key probe below has to construct a key of
#: exactly these dimensions, and a probe that restated them would be a sixth
#: place for the grammar to live.
MAX_RESOURCE_KIND_LENGTH: Final[int] = 32
MAX_RESOURCE_ID_LENGTH: Final[int] = 64

#: The part before the colon. Lowercase and hyphenated: ``cluster-prod``,
#: ``k8s-namespace``, ``s3-bucket``. No underscore, because the reason-code
#: subject charset has none and a key that cannot be rendered as
#: ``RESOURCE_BUSY:<key>`` is a key whose contention cannot be recorded.
RESOURCE_KIND_SOURCE: Final[str] = rf"[a-z][a-z0-9-]{{0,{MAX_RESOURCE_KIND_LENGTH - 1}}}"

#: The part after the colon. Mixed case, because identifiers in real systems
#: are: ``svc-a``, ``example-api``, ``v1.2.3``. Must start alphanumeric, so a
#: key can never begin with the ``.`` or ``-`` that a path or a flag starts
#: with.
RESOURCE_ID_SOURCE: Final[str] = rf"[A-Za-z0-9][A-Za-z0-9.-]{{0,{MAX_RESOURCE_ID_LENGTH - 1}}}"

#: Upper bound on a rendered key, chosen to fit inside the reason-code subject
#: limit so ``RESOURCE_BUSY:<key>`` is always constructible.
MAX_RESOURCE_KEY_LENGTH: Final[int] = 158

#: The shortest possible segment, ``a:B``: one kind character, the colon, one
#: identifier character.
_MIN_SEGMENT_LENGTH: Final[int] = 3

#: How many ``/``-joined segments can fit inside :data:`MAX_RESOURCE_KEY_LENGTH`
#: beyond the first. Derived rather than written down, so raising the length
#: bound raises this with it: an unbounded ``*`` would let the pattern accept a
#: key the length check then rejects, and two checks that disagree about the
#: same value is the defect this module exists to remove.
_MAX_EXTRA_SEGMENTS: Final[int] = (MAX_RESOURCE_KEY_LENGTH - _MIN_SEGMENT_LENGTH) // (
    _MIN_SEGMENT_LENGTH + 1
)

_SEGMENT_SOURCE: Final[str] = f"{RESOURCE_KIND_SOURCE}:{RESOURCE_ID_SOURCE}"

#: The length bound, as a lookahead, so it travels with the pattern.
#:
#: Found by ``tests/property/test_grammar_agreement.py``: bounding the segments
#: individually is not the same as bounding the key. Two maximal segments joined
#: by ``/`` are 195 characters, every one of them legal, so the regex admitted
#: keys that ``is_resource_key``'s separate ``len()`` check then refused - the
#: same "one constraint, two checks, different answers" shape as the charset
#: drift, one level down. A consumer that reached for the *pattern* rather than
#: the function - which is exactly what ``models/record.py`` and both published
#: schemas do - got the unbounded version.
#:
#: ``[^\n]`` rather than ``.``: explicit about the newline that ``SEC-07``
#: turns on, and ECMA-262 lookahead syntax, so the rendered JSON Schema pattern
#: stays valid for every validator that reads it.
_LENGTH_GUARD: Final[str] = rf"(?=[^\n]{{1,{MAX_RESOURCE_KEY_LENGTH}}}$)"

#: ``kind:id(/kind:id)*`` -- the whole grammar, unanchored so it can be embedded
#: in the reason-code alternation. The lookahead works in both positions: at the
#: start of an anchored whole-string match it bounds the string, and after
#: ``RESOURCE_BUSY:`` it bounds the remainder, which is the key.
RESOURCE_KEY_SOURCE: Final[str] = (
    f"{_LENGTH_GUARD}{_SEGMENT_SOURCE}(?:/{_SEGMENT_SOURCE}){{0,{_MAX_EXTRA_SEGMENTS}}}"
)

RESOURCE_KEY_PATTERN: Final[re.Pattern[str]] = anchored(RESOURCE_KEY_SOURCE)


# --- Reason-code subjects (SEC-07, section 5.6) ------------------------------

#: A requirement identifier: ``FR-02``, ``WF-06a``, ``SEC-11``.
RULE_ID_SOURCE: Final[str] = r"[A-Z]{2,6}-[0-9]{2,4}[a-z]?"

#: A fact name. Facts are named by the registry, which is snake_case.
SNAKE_SOURCE: Final[str] = r"[a-z][a-z0-9_]*"

#: A critic identifier: ``pdp.deploy.allowlist``, ``smt.version-contract``.
CRITIC_ID_SOURCE: Final[str] = r"[a-z][a-z0-9_]*(?:\.[a-z0-9_-]+)*"

#: A temporal property: ``WF-06a``, optionally with a sub-property.
PROPERTY_ID_SOURCE: Final[str] = r"[A-Z]{2,6}-[0-9]{2,4}[a-z]?(?:\.[a-z0-9_]+)?"

UUID_SOURCE: Final[str] = r"[0-9a-f-]{36}"

#: A position in a batch (``FR-07``). Two digits: a batch larger than the
#: evaluator's bound is refused before any position is reported.
BATCH_POSITION_SOURCE: Final[str] = r"[0-9]{1,2}"

#: Longest subject a reason code may carry.
MAX_SUBJECT_LENGTH: Final[int] = 159

#: A subject is an identifier, never prose: the structural half of ``SEC-07``.
#: The gateway additionally resolves each subject against the registry, but a
#: subject that is not even identifier-shaped must never reach that stage,
#: because prose smuggled into a reason code is prose delivered to the governed
#: model as an instruction.
SUBJECT_SOURCE: Final[str] = rf"[A-Za-z0-9][A-Za-z0-9._:/-]{{0,{MAX_SUBJECT_LENGTH - 1}}}"

SUBJECT_PATTERN: Final[re.Pattern[str]] = anchored(SUBJECT_SOURCE)

#: Rendered reason codes are bounded; the record is evidence, not a log sink.
MAX_REASON_CODE_LENGTH: Final[int] = 320


# --- The invariant, asserted rather than documented ---------------------------


class GrammarInconsistencyError(RuntimeError):
    """Two grammars in this module disagree, so some identifier is unrecordable.

    Deliberately not a :class:`neuroharness.errors.FailClosedError`, which is
    where every other refusal in the package lives. This module is a leaf: it
    imports nothing from the package, which is what lets ``reason``, ``models``
    and ``registry`` all depend on it without a cycle - and ``errors`` imports
    ``reason``, so importing ``errors`` here would close one. The trade is worth
    it. A leaf that nothing can cycle through is the only place a grammar shared
    by four modules can live, and this error can only be raised at import, where
    no verdict is being decided and so no reason code is owed.
    """

#: Every ASCII character an identifier could plausibly be probed with. Wider
#: than either grammar on purpose: the probe must be able to *find* a character
#: one grammar admits and the other does not, which is the whole point.
_PROBE_CHARACTERS: Final[str] = string.ascii_letters + string.digits + "._:/-+ @#\\\n\t"


def _resource_key_characters() -> frozenset[str]:
    """Every character a well-formed resource key can contain.

    Derived by probing the compiled pattern rather than by listing characters
    beside it. A hand-kept list is a fifth place for the grammar to live, which
    is what this module is for removing.
    """
    admitted = {":", "/"}
    for character in _PROBE_CHARACTERS:
        # Each position separately: a character legal in an identifier is not
        # necessarily legal in a kind, and vice versa.
        for probe in (f"a{character}:B", f"a:B{character}", f"{character}a:B", f"a:{character}B"):
            if RESOURCE_KEY_PATTERN.fullmatch(probe):
                admitted.add(character)
    return frozenset(admitted)


def _subject_characters() -> frozenset[str]:
    """Every character a reason-code subject can contain."""
    return frozenset(
        character
        for character in _PROBE_CHARACTERS
        if SUBJECT_PATTERN.fullmatch(f"A{character}") or SUBJECT_PATTERN.fullmatch(f"{character}A")
    )


def longest_resource_key() -> str:
    """Build the longest key the grammar admits, up to the declared bound.

    Exported because the bound is only meaningful if something can reach it: a
    length check nobody can trip is a length check that could be any number.
    The property tests use this to probe the boundary, and the import-time
    assertion below uses it to prove the regex and the bound agree.
    """
    full = "a" * (MAX_RESOURCE_KIND_LENGTH - 1) + "0"
    full_segment = f"{full}:" + "B" * MAX_RESOURCE_ID_LENGTH
    key = full_segment
    while len(key) + 1 + _MIN_SEGMENT_LENGTH <= MAX_RESOURCE_KEY_LENGTH:
        remaining = MAX_RESOURCE_KEY_LENGTH - len(key) - 1
        # Grow the identifier, not the kind: kinds are a small vocabulary and a
        # 32-character one is already unrealistic, while identifiers are
        # whatever the target system names things.
        kind_length = min(MAX_RESOURCE_KIND_LENGTH, max(1, remaining - 2))
        id_length = min(MAX_RESOURCE_ID_LENGTH, remaining - kind_length - 1)
        if id_length < 1:
            break
        segment = "a" * kind_length + ":" + "B" * id_length
        key = f"{key}/{segment}"
    return key


def _assert_resource_keys_are_renderable() -> None:
    """A resource key must always be renderable as a reason-code subject.

    Fails at import, like :mod:`neuroharness.resolve.safety`'s verdict-rank
    guard, because the alternative is discovering it when a lease is contended
    in production. It is cheap: two regex probes over a few dozen characters,
    once per process.

    Three things have to hold, and the drift this module fixes broke the first
    two of them:

    1. every character a key can contain, a subject can contain;
    2. every character a key can *start* with, a subject can start with;
    3. a maximum-length key still fits inside a subject.
    """
    stray = _resource_key_characters() - _subject_characters()
    if stray:
        raise GrammarInconsistencyError(
            "the resource-key grammar admits characters the reason-code subject "
            f"grammar does not ({sorted(stray)!r}), so a key exists that cannot "
            "be recorded as RESOURCE_BUSY:<key> (FR-25, SEC-07)"
        )
    if MAX_RESOURCE_KEY_LENGTH > MAX_SUBJECT_LENGTH:
        raise GrammarInconsistencyError(
            f"a maximum-length resource key ({MAX_RESOURCE_KEY_LENGTH}) does not "
            f"fit inside a reason-code subject ({MAX_SUBJECT_LENGTH})"
        )
    longest = longest_resource_key()
    if len(longest) != MAX_RESOURCE_KEY_LENGTH:
        raise GrammarInconsistencyError(
            f"the grammar cannot express a key of the declared maximum length: "
            f"the longest it admits is {len(longest)}, the bound is "
            f"{MAX_RESOURCE_KEY_LENGTH}"
        )
    if not (RESOURCE_KEY_PATTERN.fullmatch(longest) and SUBJECT_PATTERN.fullmatch(longest)):
        raise GrammarInconsistencyError(
            "a maximum-length resource key is not accepted by both grammars"
        )


_assert_resource_keys_are_renderable()
