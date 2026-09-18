"""The safety order is a total order and ``safer_of`` is its join (``ADR-0014``).

Every other test in the resolver suite is stated against this order, so if the
order itself is wrong, the monotonicity property is vacuous. These tests pin the
order by value rather than by construction: rewriting ``SAFETY_ORDER`` to put
``REPAIR`` above ``ABSTAIN`` must fail here, loudly, with the ADR in the message.
"""

from __future__ import annotations

import itertools

import pytest

from neuroharness.models.common import Verdict
from neuroharness.resolve.safety import (
    SAFETY_ORDER,
    SAFETY_RANK,
    is_at_least_as_safe,
    safer_of,
    safest_of,
)

ALL_VERDICTS = tuple(Verdict)
PAIRS = tuple(itertools.product(ALL_VERDICTS, repeat=2))
TRIPLES = tuple(itertools.product(ALL_VERDICTS, repeat=3))


def test_order_is_exactly_the_one_adr_0014_defines() -> None:
    assert SAFETY_ORDER == (
        Verdict.ALLOW,
        Verdict.REQUIRES_APPROVAL,
        Verdict.REPAIR,
        Verdict.ABSTAIN,
        Verdict.DENY,
    )


def test_every_verdict_is_ranked_exactly_once() -> None:
    assert set(SAFETY_RANK) == set(Verdict)
    assert len(set(SAFETY_RANK.values())) == len(ALL_VERDICTS)


def test_ranks_increase_along_the_order() -> None:
    ranks = [SAFETY_RANK[verdict] for verdict in SAFETY_ORDER]
    assert ranks == sorted(ranks)
    assert all(lower < higher for lower, higher in itertools.pairwise(ranks))


def test_abstention_outranks_repair() -> None:
    """The fix at the heart of ``ADR-0014``.

    With repair above abstention, an abstaining evaluation became a repair when
    one more repairable failure was added, and a repair loop can end in ALLOW.
    """
    assert SAFETY_RANK[Verdict.ABSTAIN] > SAFETY_RANK[Verdict.REPAIR]


def test_repair_outranks_approval_and_approval_outranks_allow() -> None:
    assert SAFETY_RANK[Verdict.REPAIR] > SAFETY_RANK[Verdict.REQUIRES_APPROVAL]
    assert SAFETY_RANK[Verdict.REQUIRES_APPROVAL] > SAFETY_RANK[Verdict.ALLOW]


def test_deny_is_the_safest_and_allow_the_least_safe() -> None:
    assert safest_of(ALL_VERDICTS) is Verdict.DENY
    assert min(ALL_VERDICTS, key=lambda v: SAFETY_RANK[v]) is Verdict.ALLOW


def test_only_allow_permits_execution() -> None:
    permitting = {v for v in ALL_VERDICTS if v.permits_execution}
    assert permitting == {Verdict.ALLOW}


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_safer_of_is_commutative(a: Verdict, b: Verdict) -> None:
    assert safer_of(a, b) is safer_of(b, a)


@pytest.mark.parametrize(("a", "b", "c"), TRIPLES)
def test_safer_of_is_associative(a: Verdict, b: Verdict, c: Verdict) -> None:
    assert safer_of(safer_of(a, b), c) is safer_of(a, safer_of(b, c))


@pytest.mark.parametrize("verdict", ALL_VERDICTS)
def test_safer_of_is_idempotent(verdict: Verdict) -> None:
    assert safer_of(verdict, verdict) is verdict


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_safer_of_returns_an_argument_that_dominates_both(a: Verdict, b: Verdict) -> None:
    result = safer_of(a, b)
    assert result in (a, b)
    assert is_at_least_as_safe(result, a)
    assert is_at_least_as_safe(result, b)


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_is_at_least_as_safe_is_total_and_antisymmetric(a: Verdict, b: Verdict) -> None:
    assert is_at_least_as_safe(a, b) or is_at_least_as_safe(b, a)
    if is_at_least_as_safe(a, b) and is_at_least_as_safe(b, a):
        assert a is b


@pytest.mark.parametrize("verdict", ALL_VERDICTS)
def test_is_at_least_as_safe_is_reflexive(verdict: Verdict) -> None:
    assert is_at_least_as_safe(verdict, verdict)


@pytest.mark.parametrize(("a", "b", "c"), TRIPLES)
def test_is_at_least_as_safe_is_transitive(a: Verdict, b: Verdict, c: Verdict) -> None:
    if is_at_least_as_safe(a, b) and is_at_least_as_safe(b, c):
        assert is_at_least_as_safe(a, c)


def test_safest_of_folds_and_has_allow_as_its_identity() -> None:
    assert safest_of(()) is Verdict.ALLOW
    assert safest_of((Verdict.ALLOW, Verdict.REPAIR, Verdict.REQUIRES_APPROVAL)) is Verdict.REPAIR
    assert safest_of((Verdict.ABSTAIN, Verdict.DENY)) is Verdict.DENY
