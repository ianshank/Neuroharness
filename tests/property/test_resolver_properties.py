"""Properties of the resolution procedure (``FR-05``, ``ADR-0014``, ``INV-09``).

**Monotonicity.** Adding an input that can only constrain an evaluation - a
failing critic, an unusable required fact, an infrastructure failure, a rate
limit, a rule that demands approval, another spent repair iteration - must never
move the verdict toward ``ALLOW`` on the safety order. No exceptions: section
5.3 decides every denial before every abstention precisely so that this holds
unconditionally.

An earlier draft of the procedure put the abstention steps above the repair
budget and approvability checks, and this property is what found it. A ``DENY``
for an exhausted repair budget softened to an ``ABSTAIN`` when an abstention was
added, and an abstention on an escalatable fact in an approvable class escalates
to ``REQUIRES_APPROVAL`` - so an agent that had spent its budget could let a
required fact go stale and convert a settled denial into a request for human
approval. Adding a problem bought a path to execution. The order was fixed; this
test is what holds it fixed.

**Determinism.** ``INV-09`` requires that the same canonical inputs always yield
the same verdict. The resolver is pure, so this is cheap to check and catches
the two ways purity is usually lost: iteration over a set, and a value read from
outside the request.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from neuroharness.defaults import MAX_REPAIR_BUDGET
from neuroharness.models.common import FactStatus, Mode, Verdict, VerifierResult
from neuroharness.reason import (
    ESCALATABLE_REASONS,
    INFRASTRUCTURE_REASONS,
    PARAMETERISED_REASONS,
    ReasonCode,
    ReasonName,
)
from neuroharness.resolve import (
    CriticOutcome,
    FactState,
    Resolution,
    ResolutionRequest,
    SimpleClassPolicy,
    is_at_least_as_safe,
    resolve,
)

pytestmark = pytest.mark.property

#: How many times one request is resolved when checking determinism.
_DETERMINISM_RUNS: Final[int] = 50

#: Identifiers are drawn from a small pool so that repeated ids - and therefore
#: repeated reason codes - actually occur.
_CRITIC_IDS: Final[tuple[str, ...]] = ("critic.a", "critic.b", "critic.c")
_FACT_NAMES: Final[tuple[str, ...]] = ("fact.a", "fact.b")
_RULE_ID: Final[str] = "rule.a"


def _reason(name: ReasonName, subject: str = "subject.a") -> ReasonCode:
    return ReasonCode(name, subject if name in PARAMETERISED_REASONS else None)


critic_outcomes = st.builds(
    CriticOutcome,
    critic_id=st.sampled_from(_CRITIC_IDS),
    result=st.sampled_from(tuple(VerifierResult)),
    hard=st.booleans(),
    effective_mode=st.sampled_from((Mode.SHADOW, Mode.ADVISORY, Mode.ENFORCE)),
    repairable=st.sampled_from((True, False, None)),
    reason=st.one_of(
        st.none(), st.just(ReasonCode(ReasonName.MONITOR_VIOLATION, "prop.window"))
    ),
)

#: Critics that can only constrain: a failure or an unusable result, blocking.
constraining_critics = st.builds(
    CriticOutcome,
    critic_id=st.sampled_from(_CRITIC_IDS),
    result=st.sampled_from(
        (
            VerifierResult.FAIL,
            VerifierResult.UNKNOWN,
            VerifierResult.TIMEOUT,
            VerifierResult.ERROR,
        )
    ),
    hard=st.just(True),
    effective_mode=st.sampled_from((Mode.SHADOW, Mode.ADVISORY, Mode.ENFORCE)),
    repairable=st.sampled_from((True, False, None)),
)

fact_states = st.builds(
    FactState,
    name=st.sampled_from(_FACT_NAMES),
    status=st.sampled_from(tuple(FactStatus)),
    required=st.booleans(),
    escalatable=st.booleans(),
)

#: Facts that can only constrain: required and unusable.
constraining_facts = st.builds(
    FactState,
    name=st.sampled_from(_FACT_NAMES),
    status=st.sampled_from((FactStatus.MISSING, FactStatus.STALE, FactStatus.PROVIDER_ERROR)),
    required=st.just(True),
    escalatable=st.booleans(),
)

infrastructure_reasons = st.sampled_from(sorted(INFRASTRUCTURE_REASONS, key=lambda r: r.value)).map(
    _reason
)

#: Policies include entries the registry loader would reject (escalation on a
#: non-approvable class, an infrastructure reason declared escalatable), because
#: the resolver's independent refusal to honour them is a hard gate.
class_policies = st.builds(
    SimpleClassPolicy,
    mode=st.sampled_from(tuple(Mode)),
    approvable=st.booleans(),
    escalate_on=st.frozensets(
        st.sampled_from(
            sorted(ESCALATABLE_REASONS | {ReasonName.CRITIC_ERROR}, key=lambda r: r.value)
        ),
        max_size=len(ESCALATABLE_REASONS) + 1,
    ),
    repair_budget=st.integers(min_value=0, max_value=MAX_REPAIR_BUDGET + 2),
)

requests = st.builds(
    ResolutionRequest,
    policy=class_policies,
    critic_outcomes=st.lists(critic_outcomes, max_size=4).map(tuple),
    fact_states=st.lists(fact_states, max_size=3).map(tuple),
    infrastructure_reasons=st.lists(infrastructure_reasons, max_size=2).map(tuple),
    repair_iteration=st.integers(min_value=0, max_value=MAX_REPAIR_BUDGET + 2),
    approval_required=st.booleans(),
    approval_rule_id=st.just(_RULE_ID),
    approval_satisfied=st.booleans(),
    rate_limited=st.booleans(),
)


@st.composite
def constrained_pairs(draw: st.DrawFn) -> tuple[ResolutionRequest, ResolutionRequest, str]:
    """A request and the same request with one more thing wrong with it."""
    base = draw(requests)
    mutation = draw(
        st.sampled_from(
            ("critic", "infrastructure", "fact", "rate_limit", "approval", "iteration")
        )
    )
    if mutation == "critic":
        after = replace(
            base, critic_outcomes=base.critic_outcomes + (draw(constraining_critics),)
        )
    elif mutation == "infrastructure":
        after = replace(
            base,
            infrastructure_reasons=base.infrastructure_reasons + (draw(infrastructure_reasons),),
        )
    elif mutation == "fact":
        after = replace(base, fact_states=base.fact_states + (draw(constraining_facts),))
    elif mutation == "rate_limit":
        after = replace(base, rate_limited=True)
    elif mutation == "approval":
        after = replace(base, approval_required=True, approval_rule_id=_RULE_ID)
    else:
        after = replace(
            base, repair_iteration=base.repair_iteration + draw(st.integers(1, MAX_REPAIR_BUDGET))
        )
    return base, after, mutation


def _enforced(resolution: Resolution) -> Verdict:
    """The verdict enforcement would have applied, whatever the class mode."""
    return resolution.verdict if resolution.shadow_verdict is None else resolution.shadow_verdict


@settings(deadline=None, max_examples=400)
@given(constrained_pairs())
def test_adding_a_constraint_never_moves_the_verdict_toward_allow(
    pair: tuple[ResolutionRequest, ResolutionRequest, str],
) -> None:
    base_request, harder_request, mutation = pair
    before = resolve(base_request)
    after = resolve(harder_request)

    assert is_at_least_as_safe(after.verdict, before.verdict), (
        f"adding a {mutation} moved the verdict from {before.verdict.value} "
        f"to {after.verdict.value}, which is closer to execution"
    )


@settings(deadline=None, max_examples=400)
@given(constrained_pairs())
def test_adding_a_constraint_never_creates_an_allow(
    pair: tuple[ResolutionRequest, ResolutionRequest, str],
) -> None:
    """The property that matters even where the safety order softens: a
    constraint can never buy an execution that the looser request was refused."""
    base_request, harder_request, _ = pair
    before = resolve(base_request)
    after = resolve(harder_request)

    if after.verdict.permits_execution:
        assert before.verdict.permits_execution


@settings(deadline=None, max_examples=400)
@given(constrained_pairs())
def test_the_enforced_verdict_is_monotone_too(
    pair: tuple[ResolutionRequest, ResolutionRequest, str],
) -> None:
    """Shadow data has to be monotone as well, or a rollout's false-block
    measurements would not predict what enforcement will do (``ADR-0016``)."""
    base_request, harder_request, mutation = pair
    before = _enforced(resolve(base_request))
    after = _enforced(resolve(harder_request))

    assert is_at_least_as_safe(after, before), (
        f"adding a {mutation} moved the enforced verdict from {before.value} to {after.value}"
    )


@settings(deadline=None, max_examples=200)
@given(requests)
def test_resolution_is_deterministic(request: ResolutionRequest) -> None:
    first = resolve(request)
    for _ in range(_DETERMINISM_RUNS - 1):
        assert resolve(request) == first


@settings(deadline=None, max_examples=400)
@given(requests)
def test_every_resolution_is_recordable(request: ResolutionRequest) -> None:
    """Section 5.6 and ``NFR-20``: a verdict that cannot be explained cannot be
    recorded, and a decision that is not recorded was not made."""
    resolution = resolve(request)

    assert bool(resolution.reason_codes) is (resolution.verdict is not Verdict.ALLOW)
    assert len(set(resolution.reason_codes)) == len(resolution.reason_codes)
    assert resolution.explain
    assert all(line for line in resolution.explain)


@settings(deadline=None, max_examples=400)
@given(requests)
def test_shadow_verdict_is_present_exactly_when_the_paths_can_differ(
    request: ResolutionRequest,
) -> None:
    """``ADR-0016``: a non-``None`` shadow verdict always means "enforcement
    would have taken a different code path here", never "this class is fine"."""
    resolution = resolve(request)
    mode = request.policy.mode
    demoted = any(o.hard and not o.counts_as_hard for o in request.critic_outcomes)

    expected = mode is not Mode.HALTED and (mode is not Mode.ENFORCE or demoted)
    assert (resolution.shadow_verdict is not None) is expected


@settings(deadline=None, max_examples=200)
@given(requests)
def test_soft_critics_never_change_the_verdict(request: ResolutionRequest) -> None:
    """Section 7: a soft critic's failure is recorded and nothing else."""
    without_soft = replace(
        request,
        critic_outcomes=tuple(o for o in request.critic_outcomes if o.counts_as_hard),
    )
    assert resolve(request).verdict is resolve(without_soft).verdict


@settings(deadline=None, max_examples=200)
@given(requests)
def test_a_halted_class_denies_whatever_else_is_true(request: ResolutionRequest) -> None:
    """``ADR-0016``: halt is unconditional, and no input can talk it out of it."""
    halted = replace(request, policy=replace(request.policy, mode=Mode.HALTED))
    resolution = resolve(halted)

    assert resolution.verdict is Verdict.DENY
    assert resolution.primary_reason == ReasonCode(ReasonName.CLASS_HALTED)


@settings(deadline=None, max_examples=200)
@given(requests)
def test_an_infrastructure_reason_is_never_escalated(request: ResolutionRequest) -> None:
    """Section 5.5: no configuration turns a dead engine into a rubber stamp."""
    with_outage = replace(
        request,
        policy=replace(request.policy, mode=Mode.ENFORCE),
        infrastructure_reasons=request.infrastructure_reasons
        + (ReasonCode(ReasonName.POLICY_ENGINE_UNAVAILABLE),),
    )
    resolution = resolve(with_outage)

    assert resolution.verdict is not Verdict.REQUIRES_APPROVAL
    assert not resolution.verdict.permits_execution


@settings(deadline=None, max_examples=400)
@given(requests)
def test_approval_is_never_offered_to_a_non_approvable_class(
    request: ResolutionRequest,
) -> None:
    """``FR-45``: a class nobody may authorise never waits for authorisation."""
    resolution = resolve(replace(request, policy=replace(request.policy, approvable=False)))

    assert resolution.verdict is not Verdict.REQUIRES_APPROVAL
