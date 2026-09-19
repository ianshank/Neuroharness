"""The four resource-key grammars agree, on every string, in both directions.

Four modules used to spell this grammar independently and two of them
disagreed. The consequence was not cosmetic: ``cluster-prod:svc-a`` was accepted
by the signed registry, became the broker's lease identity under ``FR-25``, and
then could not be recorded, so a lease contention reached an operator as
``SCHEMA_INVALID`` - a harness fault - rather than as contention, and the action
class abstained until somebody renamed the resource.

Nothing sat between the four. ``tests/unit/test_anchored_patterns.py`` checks
that each pattern is anchored; no test checked that they were the *same*
pattern. This module is that check, and it is a property test rather than a
table because the disagreement was in a corner of the charset - one character,
in one position, in one of two segments - which is exactly what a table of
hand-chosen examples misses and what generation finds.

Two directions, because the drift had two:

* every string the registry accepts must be recordable (``cluster-prod:svc-a``
  was not);
* every string the record model accepts must resolve in the registry
  (``s3:bucket_name`` did not, so a record could assert a lease on a key no
  lookup will ever match - the ``C-02`` shape reached through a grammar
  mismatch instead of a trailing newline).

:func:`test_the_property_catches_the_drift_it_was_written_for` pins the
historical patterns and proves this module would have failed on the tree before
the fix. Without it a property that silently stopped generating interesting
candidates would pass forever.
"""

from __future__ import annotations

import re
from typing import Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from neuroharness import grammar
from neuroharness.models.record import _REASON_CODE_RE
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.registry.resource_keys import is_resource_key

#: Deterministic, like every other property module here: `.hypothesis/` is not
#: checked in, and a replay-determinism project with a randomised suite is a
#: contradiction.
_SETTINGS: Final = settings(
    derandomize=True,
    deadline=None,
    max_examples=400,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Wider than any of the four grammars, on purpose. A candidate alphabet drawn
#: from what the grammars accept could never expose a disagreement about what
#: they accept; the interesting characters are the ones exactly one of them
#: admits - `_` and `-` are why this module exists.
_CANDIDATE_CHARACTERS: Final[str] = "abzABZ019_-.:/"

candidate_keys = st.text(alphabet=_CANDIDATE_CHARACTERS, min_size=1, max_size=40)

#: Well-formed keys, built segment-wise so generation spends its budget on keys
#: that are *nearly* valid rather than on strings that are obviously not.
_kind = st.from_regex(grammar.anchored(grammar.RESOURCE_KIND_SOURCE), fullmatch=True)
_identifier = st.from_regex(grammar.anchored(grammar.RESOURCE_ID_SOURCE), fullmatch=True)
well_formed_keys = st.lists(
    st.tuples(_kind, _identifier).map(lambda pair: f"{pair[0]}:{pair[1]}"),
    min_size=1,
    max_size=3,
).map("/".join)


def _recordable(key: str) -> bool:
    """Can ``RESOURCE_BUSY:<key>`` be written into a decision record?"""
    return bool(_REASON_CODE_RE.fullmatch(f"RESOURCE_BUSY:{key}"))


def _carryable(key: str) -> bool:
    """Can a :class:`ReasonCode` carry it as a subject at all?"""
    try:
        ReasonCode(ReasonName.RESOURCE_BUSY, key)
    except ValueError:
        return False
    return True


@_SETTINGS
@given(candidate_keys)
def test_the_registry_and_the_record_model_agree(key: str) -> None:
    """The headline property: one grammar, four consumers, no disagreement.

    Generated over an alphabet wider than any of them, so a character exactly
    one side admits is reachable. Before the fix this failed on the first
    candidate containing ``-`` in a kind segment.
    """
    assert is_resource_key(key) == _recordable(key), (
        f"{key!r}: registry says {is_resource_key(key)}, record model says {_recordable(key)}"
    )


@_SETTINGS
@given(well_formed_keys)
def test_every_key_the_registry_accepts_can_be_recorded(key: str) -> None:
    """Direction one. ``FR-25`` leases on these; ``FR-34`` names them.

    A key the registry accepts and the record refuses is a lease that can be
    taken and not written down, and an unrecorded decision was not made
    (Constitution Art. III).
    """
    if not is_resource_key(key):  # length bound, which the regex alone cannot express
        pytest.skip("longer than MAX_RESOURCE_KEY_LENGTH")
    assert _carryable(key), f"{key!r} cannot be carried as a reason-code subject"
    assert _recordable(key), f"{key!r} is registry-valid and unrecordable"


@_SETTINGS
@given(candidate_keys)
def test_every_key_the_record_model_accepts_resolves_in_the_registry(key: str) -> None:
    """Direction two, the quieter one.

    A record that accepts a key the registry rejects can assert a lease on a
    resource no lookup will ever match. The refusal then happens after the
    decision was recorded, which is the failure the resource-key registry
    (``FR-34``) exists to prevent: two spellings of one resource are two
    policies, and only one of them was reviewed.
    """
    if not _recordable(key):
        return
    assert is_resource_key(key), f"{key!r} is recordable and resolves to nothing"


def test_a_maximum_length_key_is_accepted_by_every_consumer() -> None:
    """The bound is only meaningful if something can reach it.

    ``MAX_RESOURCE_KEY_LENGTH`` was 158 in Python and 256 in both published
    schemas - a fourth way for one constraint to disagree with itself, and the
    axis a charset property would never have found.
    """
    longest = grammar.longest_resource_key()
    assert len(longest) == grammar.MAX_RESOURCE_KEY_LENGTH
    assert is_resource_key(longest)
    assert _carryable(longest)
    assert _recordable(longest)


def test_one_character_past_the_bound_is_refused_everywhere() -> None:
    """And the bound is a bound, not a suggestion, on both sides."""
    too_long = grammar.longest_resource_key() + "0"
    assert len(too_long) == grammar.MAX_RESOURCE_KEY_LENGTH + 1
    assert not is_resource_key(too_long)
    assert not _recordable(too_long)


# --- The regression, so the property cannot pass vacuously -------------------

#: What ``models/record.py`` spelled before the fix. Kept verbatim: a property
#: that stopped finding disagreements would otherwise be indistinguishable from
#: a property that had nothing to find.
_HISTORICAL_RECORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*:[A-Za-z0-9._-]+(/[a-z][a-z0-9_]*:[A-Za-z0-9._-]+)*$"
)

#: The keys that were the defect, in both directions.
_DRIFTED: Final[tuple[tuple[str, str], ...]] = (
    ("cluster-prod:svc-a", "registry-valid, and the record model refused it"),
    ("s3-bucket:data", "registry-valid, and the record model refused it"),
    ("k8s-namespace:default", "registry-valid, and the record model refused it"),
    ("s3:bucket_name", "recordable, and the registry refused it"),
    ("my_kind:foo", "recordable, and the registry refused it"),
)


@pytest.mark.parametrize(("key", "why"), _DRIFTED, ids=[key for key, _ in _DRIFTED])
def test_the_property_catches_the_drift_it_was_written_for(key: str, why: str) -> None:
    """Proof that this module would have failed on the tree before the fix.

    Each key disagreed between the registry and the *historical* record
    pattern, in the direction the docstring names. Each now agrees, because
    both read the same source. If this test ever passes trivially - because the
    historical pattern was "tidied" to match the current one - the parametrize
    ids say what was lost.
    """
    historical = bool(_HISTORICAL_RECORD_PATTERN.fullmatch(key))
    assert is_resource_key(key) != historical, (
        f"{key!r} no longer demonstrates the drift ({why}); "
        "the historical pattern above has been altered"
    )
    assert is_resource_key(key) == _recordable(key), f"{key!r} still disagrees after the fix"
