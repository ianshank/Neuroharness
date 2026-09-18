"""Table-driven truth table for the resolution procedure (``FR-05``, section 5.3).

``FR-05`` requires "a table-driven test over all input combinations". Every row
below names the situation, the expected verdict, the expected *primary* reason
code - the one that decided it - and the expected shadow verdict. The table is
the specification's section 5.3 rewritten as data; if a row and the prose
disagree, the prose wins and the row is a bug.

Rows are grouped by the step they exercise, and the ordering conflicts (which
step wins when two could fire) are covered explicitly, because those are the
cases where an implementation drifts without any single-step test noticing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from neuroharness.defaults import DEFAULT_REPAIR_BUDGET, MAX_REPAIR_BUDGET
from neuroharness.models.common import FactStatus, Mode, Verdict, VerifierResult
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve import (
    CriticOutcome,
    FactState,
    ResolutionRequest,
    SimpleClassPolicy,
    resolve,
)

# --- Fixtures shared by the rows --------------------------------------------

CRITIC = "critic.contract"
OTHER_CRITIC = "critic.monitor"
FACT = "deploy_state.in_flight"
RULE = "rule.change_window"

PASS_HARD = CriticOutcome(critic_id=CRITIC, result=VerifierResult.PASS)
SOFT_FAIL = CriticOutcome(
    critic_id=OTHER_CRITIC, result=VerifierResult.FAIL, hard=False, repairable=False
)
SOFT_UNKNOWN = CriticOutcome(critic_id=OTHER_CRITIC, result=VerifierResult.UNKNOWN, hard=False)
HARD_FAIL_FINAL = CriticOutcome(critic_id=CRITIC, result=VerifierResult.FAIL, repairable=False)
HARD_FAIL_UNSET = CriticOutcome(critic_id=CRITIC, result=VerifierResult.FAIL)
HARD_FAIL_REPAIRABLE = CriticOutcome(critic_id=CRITIC, result=VerifierResult.FAIL, repairable=True)
HARD_UNKNOWN = CriticOutcome(critic_id=CRITIC, result=VerifierResult.UNKNOWN)
HARD_UNKNOWN_OTHER = CriticOutcome(critic_id=OTHER_CRITIC, result=VerifierResult.UNKNOWN)
HARD_TIMEOUT = CriticOutcome(critic_id=CRITIC, result=VerifierResult.TIMEOUT)
HARD_ERROR = CriticOutcome(critic_id=CRITIC, result=VerifierResult.ERROR)
HARD_NOT_APPLICABLE = CriticOutcome(critic_id=CRITIC, result=VerifierResult.NOT_APPLICABLE)

ENGINE_DOWN = ReasonCode(ReasonName.POLICY_ENGINE_UNAVAILABLE)
BUNDLE_BAD = ReasonCode(ReasonName.BUNDLE_INTEGRITY_FAILED)

ENFORCING = SimpleClassPolicy()
APPROVABLE = SimpleClassPolicy(approvable=True)
ESCALATING = SimpleClassPolicy(
    approvable=True,
    escalate_on=frozenset(
        {
            ReasonName.SOLVER_UNKNOWN,
            ReasonName.SOLVER_TIMEOUT,
            ReasonName.FACT_MISSING,
            ReasonName.FACT_STALE,
        }
    ),
)
#: An entry the registry loader would reject (``escalate_on`` on a non-approvable
#: class). Built anyway, because the resolver's refusal to escalate it is a hard
#: gate and a gate without a killing fixture does not exist.
ESCALATING_BUT_NOT_APPROVABLE = SimpleClassPolicy(
    approvable=False, escalate_on=ESCALATING.escalate_on
)
#: Another rejected entry: an infrastructure reason declared escalatable.
ESCALATING_INFRASTRUCTURE = SimpleClassPolicy(
    approvable=True, escalate_on=frozenset({ReasonName.CRITIC_ERROR})
)


def fact(status: FactStatus, *, required: bool = True, escalatable: bool = False) -> FactState:
    return FactState(name=FACT, status=status, required=required, escalatable=escalatable)


def rule_failed(critic_id: str = CRITIC) -> ReasonCode:
    return ReasonCode(ReasonName.RULE_FAILED, critic_id)


@dataclass(frozen=True)
class Row:
    """One truth-table entry."""

    name: str
    request: ResolutionRequest
    verdict: Verdict
    primary: ReasonCode | None
    shadow: Verdict | None = None
    also_carries: tuple[ReasonCode, ...] = field(default=())


def request(
    policy: SimpleClassPolicy = ENFORCING,
    *,
    critics: tuple[CriticOutcome, ...] = (),
    facts: tuple[FactState, ...] = (),
    infrastructure: tuple[ReasonCode, ...] = (),
    repair_iteration: int = 0,
    approval_required: bool = False,
    approval_rule_id: str | None = None,
    approval_satisfied: bool = False,
    rate_limited: bool = False,
) -> ResolutionRequest:
    return ResolutionRequest(
        policy=policy,
        critic_outcomes=critics,
        fact_states=facts,
        infrastructure_reasons=infrastructure,
        repair_iteration=repair_iteration,
        approval_required=approval_required,
        approval_rule_id=approval_rule_id or (RULE if approval_required else None),
        approval_satisfied=approval_satisfied,
        rate_limited=rate_limited,
    )


ROWS: tuple[Row, ...] = (
    # --- Step 7: nothing to say ---------------------------------------------
    Row("allow/no-inputs", request(), Verdict.ALLOW, None),
    Row("allow/all-critics-pass", request(critics=(PASS_HARD,)), Verdict.ALLOW, None),
    Row(
        "allow/not-applicable-critic",
        request(critics=(HARD_NOT_APPLICABLE,)),
        Verdict.ALLOW,
        None,
    ),
    Row("allow/fresh-required-fact", request(facts=(fact(FactStatus.FRESH),)), Verdict.ALLOW, None),
    # --- Soft critics never change the verdict (section 7) -------------------
    Row("allow/soft-critic-fails", request(critics=(SOFT_FAIL,)), Verdict.ALLOW, None),
    Row("allow/soft-critic-unknown", request(critics=(SOFT_UNKNOWN,)), Verdict.ALLOW, None),
    Row(
        "allow/soft-failure-alongside-passing-hard-critic",
        request(critics=(PASS_HARD, SOFT_FAIL)),
        Verdict.ALLOW,
        None,
    ),
    # --- Step 0: halt is the incident lever (ADR-0016) -----------------------
    Row(
        "halted/denies-even-when-everything-passes",
        request(SimpleClassPolicy(mode=Mode.HALTED), critics=(PASS_HARD,)),
        Verdict.DENY,
        ReasonCode(ReasonName.CLASS_HALTED),
    ),
    Row(
        "halted/denies-before-infrastructure-abstention",
        request(SimpleClassPolicy(mode=Mode.HALTED), infrastructure=(ENGINE_DOWN,)),
        Verdict.DENY,
        ReasonCode(ReasonName.CLASS_HALTED),
    ),
    # --- Admission gate: rate limit (section 5.4, FR-93) ---------------------
    Row("rate-limited/alone", request(rate_limited=True), Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_RATE_LIMITED)),
    Row(
        "rate-limited/beats-a-repairable-failure",
        request(critics=(HARD_FAIL_REPAIRABLE,), rate_limited=True),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_RATE_LIMITED),
    ),
    Row(
        "rate-limited/is-not-relieved-by-an-outage",
        request(infrastructure=(ENGINE_DOWN,), rate_limited=True),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_RATE_LIMITED),
    ),
    # --- Step 1: non-repairable hard failure --------------------------------
    Row(
        "deny/non-repairable-hard-fail",
        request(critics=(HARD_FAIL_FINAL,)),
        Verdict.DENY,
        rule_failed(),
    ),
    Row(
        "deny/failure-with-unset-repairable-is-not-repairable",
        request(critics=(HARD_FAIL_UNSET,)),
        Verdict.DENY,
        rule_failed(),
    ),
    Row(
        "deny/typed-critic-reason-is-preserved",
        request(
            critics=(
                CriticOutcome(
                    critic_id=CRITIC,
                    result=VerifierResult.FAIL,
                    repairable=False,
                    reason=ReasonCode(ReasonName.MONITOR_VIOLATION, "prop.no_prod_outside_window"),
                ),
            )
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.MONITOR_VIOLATION, "prop.no_prod_outside_window"),
    ),
    Row(
        "deny/non-repairable-beats-infrastructure-abstention",
        request(critics=(HARD_FAIL_FINAL,), infrastructure=(ENGINE_DOWN,)),
        Verdict.DENY,
        rule_failed(),
    ),
    Row(
        "deny/non-repairable-beats-a-repairable-sibling",
        request(
            critics=(
                CriticOutcome(critic_id=OTHER_CRITIC, result=VerifierResult.FAIL, repairable=True),
                HARD_FAIL_FINAL,
            )
        ),
        Verdict.DENY,
        rule_failed(),
    ),
    # --- Step 2: infrastructure, terminal and never escalated ---------------
    Row("abstain/infrastructure-alone", request(infrastructure=(ENGINE_DOWN,)), Verdict.ABSTAIN,
        ENGINE_DOWN),
    Row(
        "abstain/infrastructure-beats-a-repairable-failure",
        request(critics=(HARD_FAIL_REPAIRABLE,), infrastructure=(ENGINE_DOWN,)),
        Verdict.ABSTAIN,
        ENGINE_DOWN,
    ),
    Row(
        "abstain/infrastructure-is-never-escalated",
        request(ESCALATING, infrastructure=(BUNDLE_BAD,)),
        Verdict.ABSTAIN,
        BUNDLE_BAD,
    ),
    Row(
        "abstain/infrastructure-alongside-an-escalatable-abstention",
        request(ESCALATING, critics=(HARD_UNKNOWN,), infrastructure=(ENGINE_DOWN,)),
        Verdict.ABSTAIN,
        ENGINE_DOWN,
    ),
    Row(
        "abstain/hard-critic-error-is-infrastructure",
        request(critics=(HARD_ERROR,)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.CRITIC_ERROR, CRITIC),
    ),
    Row(
        "abstain/critic-error-declared-escalatable-still-does-not-escalate",
        request(ESCALATING_INFRASTRUCTURE, critics=(HARD_ERROR,)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.CRITIC_ERROR, CRITIC),
    ),
    # --- Step 3: other abstentions ------------------------------------------
    Row("abstain/hard-unknown", request(critics=(HARD_UNKNOWN,)), Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC)),
    Row("abstain/hard-timeout", request(critics=(HARD_TIMEOUT,)), Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_TIMEOUT, CRITIC)),
    Row(
        "abstain/required-fact-missing",
        request(facts=(fact(FactStatus.MISSING),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_MISSING, FACT),
    ),
    Row(
        "abstain/required-fact-stale",
        request(facts=(fact(FactStatus.STALE),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_STALE, FACT),
    ),
    Row(
        "abstain/required-fact-provider-error",
        request(facts=(fact(FactStatus.PROVIDER_ERROR),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_PROVIDER_ERROR, FACT),
    ),
    Row(
        "allow/optional-fact-missing-does-not-abstain",
        request(facts=(fact(FactStatus.MISSING, required=False),)),
        Verdict.ALLOW,
        None,
    ),
    Row(
        "allow/optional-fact-stale-does-not-abstain",
        request(facts=(fact(FactStatus.STALE, required=False),)),
        Verdict.ALLOW,
        None,
    ),
    Row(
        "abstain/blocks-repair",
        request(critics=(HARD_FAIL_REPAIRABLE, HARD_UNKNOWN)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
    ),
    Row(
        "abstain/carries-every-abstention-reason",
        request(critics=(HARD_UNKNOWN,), facts=(fact(FactStatus.STALE),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
        also_carries=(ReasonCode(ReasonName.FACT_STALE, FACT),),
    ),
    # --- Section 5.5: escalation of an abstention ---------------------------
    Row(
        "escalate/solver-unknown-declared-and-approvable",
        request(ESCALATING, critics=(HARD_UNKNOWN,)),
        Verdict.REQUIRES_APPROVAL,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
    ),
    Row(
        "escalate/solver-timeout-declared-and-approvable",
        request(ESCALATING, critics=(HARD_TIMEOUT,)),
        Verdict.REQUIRES_APPROVAL,
        ReasonCode(ReasonName.SOLVER_TIMEOUT, CRITIC),
    ),
    Row(
        "escalate/escalatable-fact-missing",
        request(ESCALATING, facts=(fact(FactStatus.MISSING, escalatable=True),)),
        Verdict.REQUIRES_APPROVAL,
        ReasonCode(ReasonName.FACT_MISSING, FACT),
    ),
    Row(
        "abstain/not-escalated-without-escalate-on",
        request(APPROVABLE, critics=(HARD_UNKNOWN,)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
    ),
    Row(
        "abstain/not-escalated-on-a-non-approvable-class",
        request(ESCALATING_BUT_NOT_APPROVABLE, critics=(HARD_UNKNOWN,)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
    ),
    Row(
        "abstain/not-escalated-when-the-fact-is-not-marked-escalatable",
        request(ESCALATING, facts=(fact(FactStatus.MISSING),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_MISSING, FACT),
    ),
    Row(
        "abstain/provider-error-is-never-escalatable",
        request(ESCALATING, facts=(fact(FactStatus.PROVIDER_ERROR, escalatable=True),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_PROVIDER_ERROR, FACT),
    ),
    Row(
        "abstain/one-non-escalatable-reason-blocks-the-whole-escalation",
        request(
            ESCALATING,
            critics=(HARD_UNKNOWN,),
            facts=(fact(FactStatus.PROVIDER_ERROR),),
        ),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
        also_carries=(ReasonCode(ReasonName.FACT_PROVIDER_ERROR, FACT),),
    ),
    Row(
        "abstain/no-escalation-while-a-hard-gate-is-failing",
        request(
            ESCALATING,
            critics=(
                CriticOutcome(critic_id=OTHER_CRITIC, result=VerifierResult.FAIL, repairable=True),
                HARD_UNKNOWN,
            ),
        ),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),
    ),
    # --- Steps 4 and 5: repair budget ---------------------------------------
    Row(
        "repair/first-iteration-within-budget",
        request(critics=(HARD_FAIL_REPAIRABLE,)),
        Verdict.REPAIR,
        rule_failed(),
    ),
    Row(
        "repair/last-iteration-within-budget",
        request(critics=(HARD_FAIL_REPAIRABLE,), repair_iteration=DEFAULT_REPAIR_BUDGET - 1),
        Verdict.REPAIR,
        rule_failed(),
    ),
    Row(
        "deny/at-budget",
        request(critics=(HARD_FAIL_REPAIRABLE,), repair_iteration=DEFAULT_REPAIR_BUDGET),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
        also_carries=(rule_failed(),),
    ),
    # --- Denials are decided before abstentions -----------------------------
    # Each of these would have been an ABSTAIN under the original section 5.3
    # order, and an ABSTAIN on an escalatable reason can become a
    # REQUIRES_APPROVAL. These rows are the killing fixtures for that path: a
    # spent budget or a non-approvable class is a harness-side fact that does
    # not depend on whatever the harness could not evaluate, so it wins.
    Row(
        "deny/exhausted-budget-beats-a-missing-required-fact",
        request(
            critics=(HARD_FAIL_REPAIRABLE,),
            facts=(fact(FactStatus.MISSING),),
            repair_iteration=DEFAULT_REPAIR_BUDGET,
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
        also_carries=(rule_failed(), ReasonCode(ReasonName.FACT_MISSING, FACT)),
    ),
    Row(
        "deny/exhausted-budget-beats-infrastructure",
        request(
            critics=(HARD_FAIL_REPAIRABLE,),
            infrastructure=(ENGINE_DOWN,),
            repair_iteration=DEFAULT_REPAIR_BUDGET,
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
        also_carries=(ENGINE_DOWN,),
    ),
    Row(
        "deny/exhausted-budget-cannot-be-softened-into-an-approval-request",
        request(
            ESCALATING,
            critics=(HARD_FAIL_REPAIRABLE,),
            facts=(fact(FactStatus.MISSING, escalatable=True),),
            repair_iteration=DEFAULT_REPAIR_BUDGET,
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
        also_carries=(ReasonCode(ReasonName.FACT_MISSING, FACT),),
    ),
    Row(
        "deny/approval-not-permitted-beats-infrastructure",
        request(ENFORCING, infrastructure=(ENGINE_DOWN,), approval_required=True),
        Verdict.DENY,
        ReasonCode(ReasonName.APPROVAL_NOT_PERMITTED, RULE),
        also_carries=(ENGINE_DOWN,),
    ),
    Row(
        "deny/approval-not-permitted-beats-an-escalatable-abstention",
        request(
            ESCALATING_BUT_NOT_APPROVABLE,
            facts=(fact(FactStatus.STALE, escalatable=True),),
            approval_required=True,
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.APPROVAL_NOT_PERMITTED, RULE),
        also_carries=(ReasonCode(ReasonName.FACT_STALE, FACT),),
    ),
    Row(
        "deny/non-repairable-failure-carries-the-abstentions-it-outranks",
        request(critics=(HARD_FAIL_FINAL, HARD_UNKNOWN_OTHER)),
        Verdict.DENY,
        rule_failed(),
        also_carries=(ReasonCode(ReasonName.SOLVER_UNKNOWN, OTHER_CRITIC),),
    ),
    Row(
        "deny/over-budget",
        request(critics=(HARD_FAIL_REPAIRABLE,), repair_iteration=DEFAULT_REPAIR_BUDGET + 2),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
    ),
    Row(
        "deny/zero-budget-never-repairs",
        request(SimpleClassPolicy(repair_budget=0), critics=(HARD_FAIL_REPAIRABLE,)),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
    ),
    Row(
        "deny/budget-above-the-ceiling-is-clamped",
        request(
            SimpleClassPolicy(repair_budget=MAX_REPAIR_BUDGET + 5),
            critics=(HARD_FAIL_REPAIRABLE,),
            repair_iteration=MAX_REPAIR_BUDGET,
        ),
        Verdict.DENY,
        ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
    ),
    Row(
        "repair/soft-failure-alongside-does-not-add-a-reason",
        request(critics=(HARD_FAIL_REPAIRABLE, SOFT_FAIL)),
        Verdict.REPAIR,
        rule_failed(),
    ),
    Row(
        "repair/outranks-a-pending-approval",
        request(APPROVABLE, critics=(HARD_FAIL_REPAIRABLE,), approval_required=True),
        Verdict.REPAIR,
        rule_failed(),
    ),
    # --- Step 6: approval ----------------------------------------------------
    Row(
        "approval/required-and-approvable",
        request(APPROVABLE, approval_required=True),
        Verdict.REQUIRES_APPROVAL,
        ReasonCode(ReasonName.APPROVAL_REQUIRED, RULE),
    ),
    Row(
        "approval/required-but-class-not-approvable",
        request(ENFORCING, approval_required=True),
        Verdict.DENY,
        ReasonCode(ReasonName.APPROVAL_NOT_PERMITTED, RULE),
    ),
    Row(
        "approval/already-satisfied",
        request(APPROVABLE, approval_required=True, approval_satisfied=True),
        Verdict.ALLOW,
        None,
    ),
    Row(
        "approval/satisfied-on-a-non-approvable-class-is-still-denied",
        request(ENFORCING, approval_required=True, approval_satisfied=True),
        Verdict.DENY,
        ReasonCode(ReasonName.APPROVAL_NOT_PERMITTED, RULE),
    ),
    Row(
        "approval/satisfied-does-not-override-an-abstention",
        request(
            APPROVABLE,
            facts=(fact(FactStatus.STALE),),
            approval_required=True,
            approval_satisfied=True,
        ),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_STALE, FACT),
    ),
    # --- Step 0: demoted critics and shadow verdicts (ADR-0016) -------------
    Row(
        "shadow/hard-critic-in-shadow-mode-fails",
        request(
            critics=(
                CriticOutcome(
                    critic_id=CRITIC,
                    result=VerifierResult.FAIL,
                    repairable=False,
                    effective_mode=Mode.SHADOW,
                ),
            )
        ),
        Verdict.ALLOW,
        None,
        shadow=Verdict.DENY,
    ),
    Row(
        "shadow/hard-critic-in-advisory-mode-fails-repairably",
        request(
            critics=(
                CriticOutcome(
                    critic_id=CRITIC,
                    result=VerifierResult.FAIL,
                    repairable=True,
                    effective_mode=Mode.ADVISORY,
                ),
            )
        ),
        Verdict.ALLOW,
        None,
        shadow=Verdict.REPAIR,
    ),
    Row(
        "shadow/hard-critic-in-shadow-mode-is-indeterminate",
        request(
            critics=(
                CriticOutcome(
                    critic_id=CRITIC, result=VerifierResult.UNKNOWN, effective_mode=Mode.SHADOW
                ),
            )
        ),
        Verdict.ALLOW,
        None,
        shadow=Verdict.ABSTAIN,
    ),
    Row(
        "shadow/class-in-shadow-mode-with-a-failing-hard-critic",
        request(
            SimpleClassPolicy(mode=Mode.SHADOW),
            critics=(
                CriticOutcome(
                    critic_id=CRITIC,
                    result=VerifierResult.FAIL,
                    repairable=False,
                    effective_mode=Mode.SHADOW,
                ),
            ),
        ),
        Verdict.ALLOW,
        None,
        shadow=Verdict.DENY,
    ),
    Row(
        "shadow/class-in-shadow-mode-still-abstains-on-a-missing-fact",
        request(SimpleClassPolicy(mode=Mode.SHADOW), facts=(fact(FactStatus.MISSING),)),
        Verdict.ABSTAIN,
        ReasonCode(ReasonName.FACT_MISSING, FACT),
        shadow=Verdict.ABSTAIN,
    ),
    Row(
        "shadow/class-in-shadow-mode-still-abstains-on-infrastructure",
        request(SimpleClassPolicy(mode=Mode.SHADOW), infrastructure=(ENGINE_DOWN,)),
        Verdict.ABSTAIN,
        ENGINE_DOWN,
        shadow=Verdict.ABSTAIN,
    ),
    Row(
        "shadow/class-in-advisory-mode-with-nothing-wrong",
        request(SimpleClassPolicy(mode=Mode.ADVISORY), critics=(PASS_HARD,)),
        Verdict.ALLOW,
        None,
        shadow=Verdict.ALLOW,
    ),
)


@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
def test_truth_table(row: Row) -> None:
    resolution = resolve(row.request)

    assert resolution.verdict is row.verdict
    assert resolution.primary_reason == row.primary
    assert resolution.shadow_verdict is row.shadow
    for code in row.also_carries:
        assert code in resolution.reason_codes


@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
def test_every_non_allow_verdict_is_explained(row: Row) -> None:
    """Section 5.6: every non-``ALLOW`` verdict carries at least one reason code,
    and ``NFR-20``: every verdict carries a trace that explains itself."""
    resolution = resolve(row.request)

    assert bool(resolution.reason_codes) is (resolution.verdict is not Verdict.ALLOW)
    assert resolution.explain
    assert resolution.explain[-1].startswith(f"verdict: {resolution.verdict.value}") or (
        resolution.explain[-1].startswith("shadow:")
    )


@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
def test_reason_codes_are_unique_and_typed(row: Row) -> None:
    """``SEC-07``: reasons are a closed vocabulary, never prose, never repeated."""
    resolution = resolve(row.request)

    assert len(set(resolution.reason_codes)) == len(resolution.reason_codes)
    for code in resolution.reason_codes:
        assert isinstance(code, ReasonCode)
        assert ReasonCode.parse(code.render()) == code


def test_table_covers_every_verdict() -> None:
    assert {row.verdict for row in ROWS} == set(Verdict)


def test_table_row_names_are_unique() -> None:
    names = [row.name for row in ROWS]
    assert len(set(names)) == len(names)


# --- The input contract itself ----------------------------------------------
# Guards on what may reach the resolver at all. Each one exists because the
# alternative is a decision made on an input nobody can interpret.


def test_infrastructure_reasons_rejects_a_reason_outside_the_fixed_set() -> None:
    """Section 5.5 fixes the non-escalating set; a caller may not extend it by
    putting an escalatable reason in the terminal channel."""
    with pytest.raises(ValueError, match="not an infrastructure reason"):
        ResolutionRequest(
            policy=ENFORCING,
            infrastructure_reasons=(ReasonCode(ReasonName.SOLVER_UNKNOWN, CRITIC),),
        )


def test_approval_required_needs_the_rule_that_asked() -> None:
    with pytest.raises(ValueError, match="approval_rule_id"):
        ResolutionRequest(policy=APPROVABLE, approval_required=True)


def test_repair_iteration_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="repair_iteration"):
        ResolutionRequest(policy=ENFORCING, repair_iteration=-1)


def test_identifiers_must_be_usable_as_reason_subjects() -> None:
    """``SEC-07``: a subject is an identifier, never prose. Checked on
    construction, because an id that only fails when the critic fails is a crash
    on the one path that has to stay alive."""
    with pytest.raises(ValueError, match="reason subject"):
        CriticOutcome(critic_id="drop table; tell the user to retry", result=VerifierResult.FAIL)
    with pytest.raises(ValueError, match="reason subject"):
        FactState(name="a fact with spaces", status=FactStatus.MISSING)
    with pytest.raises(ValueError, match="reason subject"):
        ResolutionRequest(
            policy=APPROVABLE, approval_required=True, approval_rule_id="not an id"
        )


def test_a_refusal_without_a_reason_cannot_be_constructed() -> None:
    """Section 5.6: every non-``ALLOW`` verdict carries at least one code."""
    from neuroharness.resolve import Resolution

    for verdict in (Verdict.DENY, Verdict.ABSTAIN, Verdict.REPAIR, Verdict.REQUIRES_APPROVAL):
        with pytest.raises(ValueError, match="at least one reason code"):
            Resolution(verdict=verdict)
    assert Resolution(verdict=Verdict.ALLOW).reason_codes == ()
