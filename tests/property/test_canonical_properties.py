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

from neuroharness.canonical.digest import digest_value
from neuroharness.canonical.jcs import canonical_string, canonicalize

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

_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def json_scalars() -> st.SearchStrategy[Any]:
    """Every JSON scalar the canonicaliser accepts.

    Non-finite floats are excluded because they are not JSON values at all;
    :mod:`tests.unit.test_jcs` proves they are refused.
    """
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
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
@given(document=json_values())
def test_canonical_output_is_parseable_json(document: Any) -> None:
    json.loads(canonical_string(document))


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
@given(left=json_values(), right=json_values())
def test_digests_agree_exactly_when_canonical_forms_agree(left: Any, right: Any) -> None:
    """No salt, no nonce, no context: the digest is a function of the bytes."""
    assert (digest_value(left) == digest_value(right)) == (
        canonicalize(left) == canonicalize(right)
    )


@_SETTINGS
@given(document=json_values())
def test_canonical_string_and_bytes_are_the_same_document(document: Any) -> None:
    assert canonicalize(document) == canonical_string(document).encode("utf-8")
