"""The safety order over verdicts (``ADR-0014``, specification section 5.3).

The resolution procedure is required to be *monotone*: adding a failure to an
evaluation must never move the verdict toward ``ALLOW``. That sentence is
meaningless without a defined order, and ``ADR-0007`` shipped without one, which
is how the v0.1 procedure ended up letting "abstention plus one more repairable
failure" become a repair - a verdict that can end in an allow.

The order is::

    ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY

Read it as "distance from execution". ``ALLOW`` executes now. ``REQUIRES_APPROVAL``
can still execute, but only after a human decides and a fresh evaluation resolves
to ``ALLOW`` (``FR-47``). ``REPAIR`` can still execute, but only after the
proposer changes the action and it is evaluated again; it is further from
execution than approval because it requires a new proposal rather than a
signature on this one.

**Why abstention outranks repair.** ``ABSTAIN`` means the harness could not
evaluate: a solver returned ``UNKNOWN``, a fact is stale, an engine is down. Two
reasons put it above ``REPAIR``:

1. *Monotonicity*. If repair outranked abstention, then a request that abstains
   would become a repair the moment one more repairable failure was added, and a
   repair loop can terminate in ``ALLOW``. Adding a failure would have moved the
   verdict toward execution - exactly the property this order exists to forbid.
2. *Substance*. Repairing means asking the proposer to try again while the
   harness cannot judge the result. That spends agent turns against an oracle
   that is not answering, and it is a probing channel: an adversarial proposer
   learns which mutations change an incomplete evaluation.

Note what the order does *not* say: ``ABSTAIN`` and ``DENY`` both block, and
neither issues a token. ``DENY`` ranks higher because it is a decision the
harness stands behind, while an abstention is an admission that it could not
decide; an operator reading the record must be able to tell those apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from neuroharness.errors import ConfigurationError
from neuroharness.models.common import Verdict

__all__ = [
    "SAFETY_ORDER",
    "SAFETY_RANK",
    "safer_of",
    "safest_of",
    "is_at_least_as_safe",
]

#: The safety order, least safe first. This tuple is the single definition of
#: the order; ranks are derived from it so the two can never disagree.
SAFETY_ORDER: Final[tuple[Verdict, ...]] = (
    Verdict.ALLOW,
    Verdict.REQUIRES_APPROVAL,
    Verdict.REPAIR,
    Verdict.ABSTAIN,
    Verdict.DENY,
)

#: Position of each verdict in :data:`SAFETY_ORDER`. Higher is safer.
SAFETY_RANK: Final[Mapping[Verdict, int]] = MappingProxyType(
    {verdict: rank for rank, verdict in enumerate(SAFETY_ORDER)}
)

if set(SAFETY_RANK) != set(Verdict) or len(SAFETY_ORDER) != len(Verdict):
    # Import-time rather than call-time: a new verdict that nobody placed in the
    # order would otherwise surface as a KeyError inside the decision path, and
    # the decision path is not where an unranked verdict should be discovered.
    raise ConfigurationError(
        "the safety order must rank every Verdict member exactly once; "
        f"ranked={sorted(v.value for v in SAFETY_RANK)} "
        f"members={sorted(v.value for v in Verdict)}"
    )


def is_at_least_as_safe(candidate: Verdict, baseline: Verdict) -> bool:
    """True when ``candidate`` is no closer to execution than ``baseline``.

    This is the relation the monotonicity property is stated against: for any
    evaluation, adding an input that can only constrain it must satisfy
    ``is_at_least_as_safe(after, before)``.
    """
    return SAFETY_RANK[candidate] >= SAFETY_RANK[baseline]


def safer_of(a: Verdict, b: Verdict) -> Verdict:
    """Return whichever verdict is further from execution.

    The safety order is total, so this is a join: commutative, associative and
    idempotent. Those three properties are what make it safe to fold over an
    arbitrary set of partial outcomes without caring in which order the critics
    happened to answer - a resolver whose result depended on critic scheduling
    could not satisfy ``INV-09`` (same inputs, same verdict).
    """
    return a if SAFETY_RANK[a] >= SAFETY_RANK[b] else b


def safest_of(verdicts: tuple[Verdict, ...], *, default: Verdict = Verdict.ALLOW) -> Verdict:
    """Fold :func:`safer_of` over ``verdicts``.

    ``default`` is returned for an empty input. It is ``ALLOW`` because the
    identity of a join is its least element; callers that must fail closed on an
    empty verdict set are asserting something about *their* inputs, not about
    this order, and should say so at their own boundary.
    """
    result = default
    for verdict in verdicts:
        result = safer_of(result, verdict)
    return result
