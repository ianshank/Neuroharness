"""Verdict resolution (``FR-05``, specification section 5.3, ``ADR-0014``).

This is the decision procedure the harness exists to run. Everything else -
interception, canonicalisation, facts, critics, tokens, evidence - either feeds
it or carries out what it decided.

Three properties are enforced by construction rather than by review:

*Purity.* :func:`resolve` reads nothing but its argument and writes nothing but
a log line. No clock, no identifiers, no network, no configuration lookup. The
same request replayed from a decision record a year later resolves identically
(``INV-09``, ``FR-71``), which is what makes a recorded decision auditable
rather than merely archived.

*Order, not scoring.* The procedure is a fixed sequence of guards, not a fold
over per-check verdicts. Repair must outrank approval while budget remains, but
must not outrank abstention or denial; that is not expressible as a lattice join
without hiding the infrastructure short-circuit (``ADR-0014``, alternatives).

Every denial is decided before every abstention. That ordering is not cosmetic:
with the abstention steps above the budget and approvability checks, an agent
that had exhausted its repair budget - a definitive ``DENY`` - could let a
required fact go stale and, on an approvable class that declared that fact
escalatable, convert its denial into a ``REQUIRES_APPROVAL``. Adding a problem
bought a path to execution that did not exist before. Denials first removes it,
and makes the procedure monotone under the safety order without exception: a
budget that is spent and a class nobody may approve are harness-side facts that
do not depend on whatever the harness could not evaluate. The abstention reasons
travel on the denial as contributing codes, so the record still shows everything
that was wrong.

*Fail closed in every mode.* Rollout mode changes only which critics count as
blocking (step 0). It never suppresses an abstention: a missing fact or a dead
policy engine blocks a shadow class exactly as it blocks an enforcing one
(``ADR-0016``, ``INV-03``, ``INV-11``). The broker, not the resolver, decides
whether a shadow verdict stops execution.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Final

from neuroharness.defaults import MAX_REPAIR_BUDGET
from neuroharness.models.common import Mode, Verdict
from neuroharness.observability.logging import get_logger
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve.inputs import (
    CriticOutcome,
    Resolution,
    ResolutionRequest,
)

__all__ = ["resolve"]

_LOGGER: Final = get_logger(__name__)

#: Emitted on the trace line for a verdict that carries no reason codes.
_NO_REASONS: Final[str] = "-"


def resolve(request: ResolutionRequest) -> Resolution:
    """Resolve one evaluation into a verdict, its reasons and its trace.

    Pure. The only side effect is a log record, which is an observation of the
    decision rather than part of making it.
    """
    policy = request.policy

    if policy.mode is Mode.HALTED:
        # Step 0, first half. Halt is the incident lever (``ADR-0016``): deny
        # everything, issue no token, cancel pending approvals. It is checked
        # before the critics because an operator who halted a class did so
        # without reference to what any critic would say.
        halted = Resolution(
            verdict=Verdict.DENY,
            reason_codes=(ReasonCode(ReasonName.CLASS_HALTED),),
            shadow_verdict=None,
            explain=(
                f"step 0: class mode is {Mode.HALTED.value}; halt denies every action "
                "and issues no token (ADR-0016)",
                f"verdict: {Verdict.DENY.value} ({ReasonName.CLASS_HALTED.value})",
            ),
        )
        return _logged(halted, request)

    resolution = _resolve_pass(request, honour_effective_modes=True)

    demoted = tuple(o for o in request.critic_outcomes if o.hard and not o.counts_as_hard)
    if policy.mode is not Mode.ENFORCE or demoted:
        # ``ADR-0016``: the code path in shadow is identical to the code path in
        # enforce, so shadow data is representative. The only way that stays
        # true is if the enforcing verdict is actually computed and recorded,
        # which is what this second pass is for.
        would_be = _resolve_pass(request, honour_effective_modes=False)
        note = (
            f"shadow: class mode is {policy.mode.value} and {len(demoted)} hard critic(s) "
            f"were treated as soft; the verdict that would apply in enforce is "
            f"{would_be.verdict.value}"
        )
        resolution = replace(
            resolution,
            shadow_verdict=would_be.verdict,
            explain=(*resolution.explain, note),
        )

    return _logged(resolution, request)


def _resolve_pass(request: ResolutionRequest, *, honour_effective_modes: bool) -> Resolution:
    """Run section 5.3 once.

    ``honour_effective_modes=True`` is the real pass: a hard critic whose own
    declared mode is ``shadow`` or ``advisory`` is treated as soft.  ``False``
    is the shadow pass: every hard critic counts, giving the verdict enforcement
    would have produced. Both passes are the same code, which is the point.
    """
    policy = request.policy
    explain: list[str] = []

    def decided(verdict: Verdict, reasons: Sequence[ReasonCode], line: str) -> Resolution:
        codes = _dedupe(reasons)
        explain.append(line)
        explain.append(f"verdict: {verdict.value} ({_render(codes)})")
        return Resolution(
            verdict=verdict,
            reason_codes=codes,
            shadow_verdict=None,
            explain=tuple(explain),
        )

    # --- Step 0: mode normalisation -----------------------------------------
    blocking: tuple[CriticOutcome, ...] = tuple(
        outcome
        for outcome in request.critic_outcomes
        if (outcome.counts_as_hard if honour_effective_modes else outcome.hard)
    )
    demoted = sum(
        1
        for outcome in request.critic_outcomes
        if outcome.hard and honour_effective_modes and not outcome.counts_as_hard
    )
    soft = len(request.critic_outcomes) - len(blocking)
    explain.append(
        f"step 0: class mode={policy.mode.value}; critics={len(request.critic_outcomes)} "
        f"(blocking={len(blocking)}, soft={soft}, hard-but-demoted={demoted}); "
        "soft critics never change the verdict"
    )

    # Collected before the denial steps because every denial carries whatever
    # else was wrong as contributing reason codes, even though it did not need
    # them to decide.
    failures = tuple(outcome for outcome in blocking if outcome.is_failure)
    repairable = tuple(outcome for outcome in blocking if outcome.is_repairable_failure)
    budget = min(policy.repair_budget, MAX_REPAIR_BUDGET)
    budget_spent = request.repair_iteration >= budget

    # A hard critic that ERRORed contributes ``CRITIC_ERROR``, which section 5.5
    # lists as infrastructure. Deriving it here rather than at the abstention
    # step matches the section 7 failure table and keeps "no escalation, ever"
    # in one place. The ``reason`` field is honoured whatever the nominal
    # result: a critic that reports an infrastructure reason is an
    # infrastructure failure even if its result field says otherwise.
    infrastructure: tuple[ReasonCode, ...] = tuple(request.infrastructure_reasons) + tuple(
        code for code in _reasons_of(blocking) if code.is_infrastructure
    )

    # Each entry pairs the reason with whether its *source* permits escalation.
    # For a fact that is the class's per-fact ``escalatable`` flag; for a critic
    # there is no per-critic flag, so the reason name decides alone.
    abstentions: list[tuple[ReasonCode, bool]] = []
    for outcome in blocking:
        if outcome.is_indeterminate:
            code = outcome.reason_code
            if code is not None:
                abstentions.append((code, True))
    for fact in request.fact_states:
        if fact.blocks:
            code = fact.reason_code
            if code is not None:
                abstentions.append((code, fact.escalatable))

    #: Everything the harness could not evaluate, in a stable order. Carried by
    #: the denial steps so a record shows every problem, not only the decisive one.
    unevaluated: tuple[ReasonCode, ...] = infrastructure + tuple(code for code, _ in abstentions)

    # --- Step 1: non-repairable hard failure --------------------------------
    non_repairable = tuple(o for o in blocking if o.is_non_repairable_failure)
    if non_repairable:
        return decided(
            Verdict.DENY,
            _reasons_of(non_repairable) + unevaluated,
            "step 1: hard FAIL that cannot be repaired from "
            f"{_ids(non_repairable)}; no proposal change can satisfy it",
        )
    explain.append("step 1: no non-repairable hard failure")

    # --- Step 2: repair rate limit (section 5.4, FR-93) ---------------------
    # Not a judgement about this action: it says the session has proposed too
    # many new actions in this class to keep being evaluated.
    if request.rate_limited:
        return decided(
            Verdict.DENY,
            (ReasonCode(ReasonName.REPAIR_RATE_LIMITED), *unevaluated),
            "step 2: action rate limit exhausted for this (session root, action class)",
        )
    explain.append("step 2: rate limit not exhausted")

    # --- Step 3: repair budget exhausted ------------------------------------
    if repairable and budget_spent:
        return decided(
            Verdict.DENY,
            (
                ReasonCode(ReasonName.REPAIR_BUDGET_EXHAUSTED),
                *_reasons_of(repairable),
                *unevaluated,
            ),
            f"step 3: repairable hard FAIL from {_ids(repairable)} but iteration "
            f"{request.repair_iteration} has reached the budget of {budget}",
        )
    explain.append(
        f"step 3: repair budget not exhausted (iteration {request.repair_iteration} "
        f"of {budget})"
    )

    # --- Step 4: approval required by a class nobody may approve ------------
    # ``FR-45``: denied outright rather than left waiting for an approval that
    # can never be valid.
    if request.approval_required and not policy.approvable:
        rule_id = _approval_rule_id(request)
        return decided(
            Verdict.DENY,
            (ReasonCode(ReasonName.APPROVAL_NOT_PERMITTED, rule_id), *unevaluated),
            f"step 4: rule {rule_id} requires approval but the class is not approvable",
        )
    explain.append("step 4: no approval is required of a non-approvable class")

    # --- Step 5: non-escalating infrastructure reasons ----------------------
    if infrastructure:
        return decided(
            Verdict.ABSTAIN,
            infrastructure + tuple(code for code, _ in abstentions),
            "step 5: infrastructure reason present; terminal abstention with no repair "
            "and no escalation - a human cannot vouch for an engine that is down",
        )
    explain.append("step 5: no infrastructure reason")

    # --- Step 6: other abstentions (they block repair) -----------------------
    if abstentions:
        codes = [code for code, _ in abstentions]
        # Section 5.5. Every condition is necessary, and each one is a separate
        # way this has gone wrong before: a reason outside the escalatable set
        # ("CI never ran" becomes approvable), a reason the class did not
        # declare, a class that may not be approved at all, or a source the
        # class did not mark escalatable.
        blockers: list[str] = []
        if not policy.approvable:
            blockers.append("class is not approvable")
        for code, source_allows in abstentions:
            if not code.is_escalatable:
                blockers.append(f"{code.render()} is not an escalatable reason")
            elif code.name not in policy.escalate_on:
                blockers.append(f"{code.render()} is not declared in escalate_on")
            elif not source_allows:
                blockers.append(f"{code.render()} comes from a source not marked escalatable")
        # A hard gate that FAILED is not an abstention, and escalation while one
        # is outstanding would put a human in front of an action a hard critic
        # has already rejected. Section 5.5 speaks only of abstention reasons;
        # this also keeps the procedure monotone (``ADR-0014``): without it,
        # adding an abstention to a REPAIR would produce REQUIRES_APPROVAL,
        # which is closer to execution.
        if failures:
            blockers.append("a hard critic FAILED; escalation may not overrule a hard gate")

        if blockers:
            return decided(
                Verdict.ABSTAIN,
                codes,
                f"step 6: cannot evaluate ({_render(codes)}); abstention blocks repair; "
                f"not escalated because {'; '.join(blockers)}",
            )
        return decided(
            Verdict.REQUIRES_APPROVAL,
            codes,
            f"step 6: cannot evaluate ({_render(codes)}); every reason is escalatable and "
            "declared by an approvable class, so a human is asked and shown all of them",
        )
    explain.append("step 6: nothing indeterminate; every required fact is fresh")

    # --- Step 7: repairable failure with budget remaining -------------------
    if repairable:
        return decided(
            Verdict.REPAIR,
            _reasons_of(repairable),
            f"step 7: repairable hard FAIL from {_ids(repairable)}; iteration "
            f"{request.repair_iteration} of budget {budget}; all counterexamples "
            "are returned together",
        )
    explain.append("step 7: no hard failure to repair")

    # --- Step 8: approval ----------------------------------------------------
    if request.approval_required:
        rule_id = _approval_rule_id(request)
        if not request.approval_satisfied:
            return decided(
                Verdict.REQUIRES_APPROVAL,
                (ReasonCode(ReasonName.APPROVAL_REQUIRED, rule_id),),
                f"step 8: rule {rule_id} requires approval and none is resolved for this "
                "proposal digest and bundle digest",
            )
        explain.append(
            f"step 8: rule {rule_id} requires approval and a resolved approval is bound to "
            "this proposal digest; continuing on fresh facts (FR-47)"
        )
    else:
        explain.append("step 8: no rule requires approval")

    # --- Step 9 --------------------------------------------------------------
    return decided(Verdict.ALLOW, (), "step 9: every enforced check passed")


def _approval_rule_id(request: ResolutionRequest) -> str:
    rule_id = request.approval_rule_id
    if rule_id is None:  # pragma: no cover - ResolutionRequest rejects this at the door
        raise ValueError("approval_required without approval_rule_id")
    return rule_id


def _reasons_of(outcomes: Iterable[CriticOutcome]) -> tuple[ReasonCode, ...]:
    """Typed reasons for ``outcomes``, in input order, passes dropped."""
    return tuple(code for code in (o.reason_code for o in outcomes) if code is not None)


def _dedupe(codes: Iterable[ReasonCode]) -> tuple[ReasonCode, ...]:
    """Drop repeats while keeping first-seen order.

    Order is part of the contract: the head of the tuple is the reason that
    decided the verdict, the rest are contributing, and a record that reordered
    its reasons between two replays of the same inputs would falsify ``INV-09``.
    """
    seen: set[ReasonCode] = set()
    unique: list[ReasonCode] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique.append(code)
    return tuple(unique)


def _render(codes: Iterable[ReasonCode]) -> str:
    return ", ".join(code.render() for code in codes) or _NO_REASONS


def _ids(outcomes: Iterable[CriticOutcome]) -> str:
    return ", ".join(o.critic_id for o in outcomes)


def _logged(resolution: Resolution, request: ResolutionRequest) -> Resolution:
    """Record the resolution and return it unchanged."""
    _LOGGER.info(
        "verdict_resolved",
        verdict=resolution.verdict.value,
        reason_codes=[code.render() for code in resolution.reason_codes],
        shadow_verdict=(
            None if resolution.shadow_verdict is None else resolution.shadow_verdict.value
        ),
        class_mode=request.policy.mode.value,
        repair_iteration=request.repair_iteration,
    )
    _LOGGER.debug("verdict_trace", explain=list(resolution.explain))
    return resolution
