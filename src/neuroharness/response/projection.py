"""The one named way an :class:`EvaluationOutcome` becomes something an agent sees.

The hazard this module exists for is small, ordinary and fatal: ``evaluate``
returns an :class:`~neuroharness.pipeline.decision.EvaluationOutcome`, so the
obvious thing for a gateway author to do is return it. That object carries the
:class:`~neuroharness.tokens.model.SignedToken` - the credential that authorises
execution, whose signature the broker checks - and the
:class:`~neuroharness.evidence.store.AppendResult`, the harness's own chain
position. Serialising it to the agent hands over both.

:class:`~neuroharness.response.agent.AgentResponse` has no field either can
occupy, so the mistake cannot be made by construction: there is no assignment to
forget and no field to omit. What is left is the *route*, and this is it.
:func:`project_evaluation_outcome` reads the four things an agent may learn -
the verdict, its typed reasons, the action being decided, and whatever
disclosure the rollout mode permits - and returns a new object. Everything else
is dropped, and the drop is logged (``response.projected`` carries
``token_dropped`` and ``record_dropped``) so that a leak introduced by some later
path is visible in a trace rather than only in a review.

**Three things the projection needs and the outcome does not carry**, each
supplied by the caller rather than invented here:

``action_id``
    ``FR-90`` puts it in the response. ``EvaluationOutcome`` has no identity
    fields at all; they live on
    :class:`~neuroharness.pipeline.decision.DecisionContext`, which the outcome
    does not reference.
``mode``
    ``FR-80``: *"in ``shadow`` no approval requests are created and no
    counterexamples are returned to the agent"*. The outcome carries the mode
    only inside the token, which is precisely the field that is absent when the
    verdict blocked.
``counterexamples``
    :class:`~neuroharness.models.record.Counterexample` has no producer anywhere
    in the package. The resolver's
    :class:`~neuroharness.resolve.inputs.CriticOutcome` has no counterexample
    field, so no pipeline run can yield one; they arrive from critic results,
    which the gateway assembles and which increment 1 did not build.

**The repair loop itself is out of scope, and this module does not half-build
it.** ``FR-90``-``FR-92`` describe a round trip: counterexamples go back with the
``action_id``, the proposer resubmits, the resubmission is linked to the same
action and counts against the budget (``FR-91``, §5.4), and the success rate is
measured per class (``FR-92``). The resolver already decides ``REPAIR`` and
already spends the budget; what is missing is the orchestrator that holds an
action open across iterations, links a resubmission to it, and enforces the
per-``(session_root, action_class)`` rate limit (``FR-93``) - and that needs the
gateway, which does not exist. This module makes the round trip *expressible*:
the response can carry the counterexamples and the action they belong to. It
does not keep state, does not count iterations, and does not decide whether a
resubmission is the same action. When the orchestrator lands, those belong to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from pydantic import ValidationError

from neuroharness.errors import FailClosedError
from neuroharness.models.common import Mode
from neuroharness.models.record import Counterexample
from neuroharness.observability.logging import bind_context, get_logger
from neuroharness.pipeline.decision import EvaluationOutcome
from neuroharness.reason import ReasonName
from neuroharness.response.agent import AgentResponse, ExecutionResultRef

__all__ = [
    "ResponseNotProjectableError",
    "project_evaluation_outcome",
    "discloses_counterexamples",
]

_LOG = get_logger("neuroharness.response")

_EVENT_PROJECTED: Final[str] = "response.projected"
_EVENT_DISCLOSURE_WITHHELD: Final[str] = "response.disclosure_withheld"
_EVENT_REPAIR_WITHOUT_COUNTEREXAMPLE: Final[str] = "response.repair_without_counterexample"
_EVENT_NOT_PROJECTABLE: Final[str] = "response.not_projectable"


class ResponseNotProjectableError(FailClosedError):
    """An outcome could not be expressed in the agent-facing contract.

    ``SCHEMA_INVALID`` rather than ``HARNESS_UNHEALTHY``, and for the same reason
    :class:`~neuroharness.pipeline.decision.RecordNotConstructibleError` is: what
    failed is the harness's own ability to say what it decided, which Article II
    makes an inability to evaluate. A caller that cannot produce a response has
    nothing to return to the agent, and returning the outcome instead is exactly
    the leak this package exists to prevent - so this is raised rather than
    softened into a partial response.
    """

    reason_name = ReasonName.SCHEMA_INVALID


def discloses_counterexamples(mode: Mode) -> bool:
    """Whether a class in this mode may show the agent why it failed (``FR-80``).

    ``shadow`` is settled: ``FR-80`` says no counterexamples are returned and no
    approval requests are created, because a shadow rollout must not change what
    the agent does - if it did, the shadow data would measure a different
    program than the one that ships.

    ``advisory`` is not settled: ``FR-80`` says it *may* surface typed warnings
    "per ``OQ-06``", and ``OQ-06`` is open. This function withholds there too,
    because the two readings are not symmetric: withholding can be reversed by
    answering the question, while a disclosure that has already reached a model's
    context cannot be taken back. Derived from
    :attr:`~neuroharness.models.common.Mode.blocks_on_verdict` rather than from a
    list of modes, so a mode added to the enum is classified by the same property
    the broker already uses to decide whether the verdict counts.
    """
    return mode.blocks_on_verdict


def project_evaluation_outcome(
    outcome: EvaluationOutcome,
    *,
    action_id: UUID,
    mode: Mode,
    counterexamples: Sequence[Counterexample] = (),
    approval_ref: UUID | None = None,
    result: ExecutionResultRef | None = None,
    trace_id: str | None = None,
) -> AgentResponse:
    """Project ``outcome`` into the agent-facing response, dropping everything else.

    Kept, because the specification says the agent may have it: the verdict, its
    typed reason codes, the ``action_id``, and - where the mode permits -
    counterexamples and an approval reference.

    Dropped, always: ``outcome.token`` (the execution credential),
    ``outcome.record`` (the evidence chain position), ``outcome.resolution``
    (which carries ``explain``, an operator's trace, and ``shadow_verdict``,
    rollout measurement) and ``outcome.evidence_failed``. The verdict already
    states the consequence of an evidence failure - ``ABSTAIN`` with
    ``EVIDENCE_UNAVAILABLE`` - and *which internal subsystem* failed is an
    operator's fact, not the proposer's.

    ``trace_id`` is optional because a caller inside the pipeline's own
    ``bind_context`` block already has one bound; passing it is how a caller
    outside that block keeps the projection on the same trace as the decision.
    """
    with bind_context(trace_id=trace_id, action_id=str(action_id)):
        disclose = discloses_counterexamples(mode)
        offered = tuple(counterexamples)
        shown = offered if disclose else ()
        approval = approval_ref if disclose else None

        if not disclose and (offered or approval_ref is not None):
            _LOG.info(
                _EVENT_DISCLOSURE_WITHHELD,
                mode=mode.value,
                verdict=outcome.verdict.value,
                counterexamples_withheld=len(offered),
                approval_ref_withheld=approval_ref is not None,
            )

        if result is not None and not outcome.permits_execution:
            # No token, nothing executed. A result on a response the harness did
            # not authorise is either a broker that ran anyway or a caller
            # attaching content to a refusal - and the second is a channel for
            # returning whatever it likes to the model under a DENY.
            raise ResponseNotProjectableError(
                f"a result accompanies a {outcome.verdict.value} response for which no "
                "decision token was issued; only an authorised execution has a result "
                "(FR-20, FR-63, INV-05)"
            )

        response = _build(
            outcome=outcome,
            action_id=action_id,
            counterexamples=shown,
            approval_ref=approval,
            result=result,
        )

        if response.is_repairable and not response.counterexamples:
            # FR-90 requires the iteration's counterexamples to come back with
            # the action_id, and FR-56 requires a hard FAIL to produce one. Today
            # nothing produces them (see the module docstring), so this is an
            # observable gap rather than a refusal: refusing would make every
            # REPAIR unreturnable and convert a missing producer into an outage.
            # When a producer exists, this becomes a refusal.
            _LOG.warning(
                _EVENT_REPAIR_WITHOUT_COUNTEREXAMPLE,
                mode=mode.value,
                verdict=response.verdict.value,
                disclosure_permitted=disclose,
            )

        _LOG.info(
            _EVENT_PROJECTED,
            verdict=response.verdict.value,
            mode=mode.value,
            reason_codes=[code.render() for code in response.reason_codes],
            counterexample_count=len(response.counterexamples),
            approval_ref_present=response.approval_ref is not None,
            result_present=response.result is not None,
            # The point of the event: if either of these is ever false while the
            # outcome held one, the drop stopped happening.
            token_dropped=outcome.token is not None,
            record_dropped=outcome.record is not None,
            evidence_failed=outcome.evidence_failed,
        )
        return response


def _build(
    *,
    outcome: EvaluationOutcome,
    action_id: UUID,
    counterexamples: tuple[Counterexample, ...],
    approval_ref: UUID | None,
    result: ExecutionResultRef | None,
) -> AgentResponse:
    """Construct the response, turning a validation failure into a typed refusal.

    The model's refusals - a counterexample on a verdict that cannot carry one,
    an approval reference on a verdict that did not ask for one, text where an
    identifier belongs - are contract violations by the caller. Pydantic reports
    them as :class:`~pydantic.ValidationError`, which would reach a gateway as an
    untyped exception on the decision path; every other refusal in the package is
    a :class:`~neuroharness.errors.FailClosedError` carrying a reason code.

    Field *names* are logged and offending values are not: pydantic includes the
    input in its messages, and the input here is exactly the text that must not
    be copied anywhere (``NFR-18``, ``SEC-07``).
    """
    try:
        return AgentResponse(
            verdict=outcome.verdict,
            action_id=action_id,
            reason_codes=outcome.reason_codes,
            counterexamples=counterexamples,
            approval_ref=approval_ref,
            result=result,
        )
    except ValidationError as exc:
        _LOG.error(
            _EVENT_NOT_PROJECTABLE,
            verdict=outcome.verdict.value,
            error_count=exc.error_count(),
            fields=sorted(
                {".".join(str(part) for part in error["loc"]) for error in exc.errors()}
            ),
        )
        raise ResponseNotProjectableError(
            f"the decision does not fit the agent-facing response contract: "
            f"{exc.error_count()} field(s) rejected"
        ) from exc
