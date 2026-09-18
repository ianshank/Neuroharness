"""Resolve, record, then and only then mint a token.

Three constitutional rules meet here, and none of them can be honoured by any
single subsystem alone:

``INV-05`` no token without a durable record
    The evaluation record is appended *before* the token service is consulted.
    If the record cannot be made durable the pipeline overrides the response to
    ``ABSTAIN (EVIDENCE_UNAVAILABLE)`` and returns without a token, which is what
    specification 5.3's post-resolution clause requires.

``INV-11`` modes change only whether the broker consults the verdict
    Shadow and advisory classes take the identical path and receive a token
    marked ``shadow``, so the code exercised while a gate is being observed is
    the code that will later enforce it. Without that, shadow data measures a
    different program than the one that ships.

``ADR-0016`` infrastructure failures block in every mode
    A shadow class is not an unguarded class. When the verdict carries a
    non-escalating infrastructure reason, no token is issued whatever the mode,
    so nothing can execute on an evaluation the harness could not complete.

The pipeline owns the *order* and nothing else. What goes into a record is the
caller's business; that separation is what keeps this module small enough to
read in one sitting and to reason about under review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from neuroharness.errors import EvidenceUnavailableError, FailClosedError
from neuroharness.evidence.chain import FIELD_KIND, FIELD_TENANT_ID, RecordKind
from neuroharness.evidence.store import AppendResult, EvidenceWriter
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.observability.logging import bind_context, get_logger
from neuroharness.reason import ReasonCode
from neuroharness.resolve.inputs import Resolution, ResolutionRequest
from neuroharness.resolve.resolver import resolve as default_resolve
from neuroharness.tokens.model import SignedToken
from neuroharness.tokens.service import TokenService

__all__ = ["DecisionContext", "EvaluationOutcome", "DecisionPipeline"]

_LOG = get_logger("neuroharness.pipeline")

_EVENT_EVALUATED = "pipeline.evaluated"
_EVENT_EVIDENCE_FAILED = "pipeline.evidence_unavailable"
_EVENT_TOKEN_WITHHELD = "pipeline.token_withheld"


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Everything the pipeline needs to bind a decision to an action.

    Identity fields only. The pipeline never invents them: ``action_id`` and
    ``decision_id`` arrive from the caller's identifier seam so that a replay
    reproduces the same record (``FR-71``).
    """

    tenant_id: str
    trace_id: str
    action_id: str
    decision_id: str
    envelope_digest: Digest
    proposal_digest: Digest
    policy_bundle_digest: Digest
    mode: Mode
    action_class: str | None = None
    session_id: str | None = None
    #: Additional fields merged into the ``evaluation`` block of the record.
    #: Content is the caller's responsibility; ordering is this module's.
    evaluation_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """What the pipeline decided, recorded, and authorised.

    ``token`` is ``None`` whenever nothing may execute. There is no other signal
    to check and no way to reach execution without one, which is the point.
    """

    verdict: Verdict
    reason_codes: tuple[ReasonCode, ...]
    resolution: Resolution
    record: AppendResult | None
    token: SignedToken | None
    evidence_failed: bool = False

    @property
    def permits_execution(self) -> bool:
        return self.token is not None

    @property
    def shadow(self) -> bool:
        """True when a token exists but the class is not enforcing on it."""
        return self.token is not None and self.token.token.shadow


class DecisionPipeline:
    """Sequences resolution, evidence and token issuance for one envelope."""

    __slots__ = ("_writer", "_tokens", "_resolve")

    def __init__(
        self,
        *,
        writer: EvidenceWriter,
        tokens: TokenService,
        resolver: Any = None,
    ) -> None:
        self._writer = writer
        self._tokens = tokens
        # Injected so a conformance suite can drive an alternative resolver
        # against the same ordering guarantees.
        self._resolve = resolver or default_resolve

    def evaluate(
        self, request: ResolutionRequest, context: DecisionContext
    ) -> EvaluationOutcome:
        """Run one decision end to end and return what may now happen."""
        with bind_context(
            trace_id=context.trace_id,
            tenant_id=context.tenant_id,
            action_id=context.action_id,
            decision_id=context.decision_id,
            action_class=context.action_class,
            session_id=context.session_id,
        ):
            resolution = self._resolve(request)

            try:
                record = self._write_evaluation(resolution, context)
            except EvidenceUnavailableError as exc:
                # Specification 5.3, post-resolution: the response is overridden.
                # Whatever the resolver decided, an unrecorded decision was not
                # made (Article III), so nothing may execute.
                _LOG.error(
                    _EVENT_EVIDENCE_FAILED,
                    resolved_verdict=resolution.verdict.value,
                    reason_code=exc.reason_code.render(),
                    mode=context.mode.value,
                )
                return EvaluationOutcome(
                    verdict=Verdict.ABSTAIN,
                    reason_codes=(exc.reason_code,),
                    resolution=resolution,
                    record=None,
                    token=None,
                    evidence_failed=True,
                )

            token = self._maybe_issue(resolution, context, record)

            _LOG.info(
                _EVENT_EVALUATED,
                verdict=resolution.verdict.value,
                shadow_verdict=(
                    resolution.shadow_verdict.value if resolution.shadow_verdict else None
                ),
                mode=context.mode.value,
                reason_codes=[code.render() for code in resolution.reason_codes],
                record_seq=record.seq,
                token_issued=token is not None,
            )
            return EvaluationOutcome(
                verdict=resolution.verdict,
                reason_codes=resolution.reason_codes,
                resolution=resolution,
                record=record,
                token=token,
            )

    # -- internals ---------------------------------------------------------

    def _write_evaluation(
        self, resolution: Resolution, context: DecisionContext
    ) -> AppendResult:
        payload: dict[str, Any] = {
            "proposal_digest": str(context.proposal_digest),
            "envelope_digest": str(context.envelope_digest),
            "mode": context.mode.value,
            "verdict": resolution.verdict.value,
            "reason_codes": [code.render() for code in resolution.reason_codes],
            "explain": list(resolution.explain),
        }
        if resolution.shadow_verdict is not None:
            payload["shadow_verdict"] = resolution.shadow_verdict.value
        payload.update(context.evaluation_payload)

        record = {
            FIELD_TENANT_ID: context.tenant_id,
            FIELD_KIND: RecordKind.EVALUATION.value,
            "trace_id": context.trace_id,
            "decision_id": context.decision_id,
            "action_id": context.action_id,
            "evaluation": payload,
        }
        return self._writer.write(record)

    def _maybe_issue(
        self, resolution: Resolution, context: DecisionContext, record: AppendResult
    ) -> SignedToken | None:
        withheld = self._withholding_reason(resolution, context.mode)
        if withheld is not None:
            _LOG.debug(
                _EVENT_TOKEN_WITHHELD,
                mode=context.mode.value,
                verdict=resolution.verdict.value,
                because=withheld,
            )
            return None

        try:
            signed = self._tokens.issue(
                decision_id=context.decision_id,
                envelope_digest=context.envelope_digest,
                proposal_digest=context.proposal_digest,
                policy_bundle_digest=context.policy_bundle_digest,
                record_hash=record.record_hash,
                tenant_id=context.tenant_id,
                mode=context.mode,
                verdict=resolution.verdict,
            )
        except FailClosedError as exc:
            # The token service is the second opinion on its own preconditions.
            # If it refuses, that refusal wins: withholding a token is always
            # the safe direction.
            _LOG.warning(
                _EVENT_TOKEN_WITHHELD,
                mode=context.mode.value,
                verdict=resolution.verdict.value,
                because=exc.reason_code.render(),
            )
            return None

        self._write_token_issued(signed, context)
        return signed

    @staticmethod
    def _withholding_reason(resolution: Resolution, mode: Mode) -> str | None:
        """Why no token may be minted, or ``None`` when one may.

        Written as a single function so the whole rule is readable at once, and
        so every reason it returns is logged rather than inferred.
        """
        if mode is Mode.HALTED:
            return "class_halted"
        if any(code.is_infrastructure for code in resolution.reason_codes):
            # ADR-0016: this is what stops a shadow class being an unguarded one.
            return "infrastructure_failure_blocks_every_mode"
        if mode.blocks_on_verdict and not resolution.permits_execution:
            return f"verdict_{resolution.verdict.value.lower()}"
        return None

    def _write_token_issued(self, signed: SignedToken, context: DecisionContext) -> None:
        """Record the issuance as its own linked record (``FR-23``)."""
        token = signed.token
        self._writer.write(
            {
                FIELD_TENANT_ID: context.tenant_id,
                FIELD_KIND: RecordKind.TOKEN_ISSUED.value,
                "trace_id": context.trace_id,
                "decision_id": context.decision_id,
                "action_id": context.action_id,
                "token_issued": {
                    "token_id": token.token_id,
                    "decision_id": token.decision_id,
                    "envelope_digest": str(token.envelope_digest),
                    "proposal_digest": str(token.proposal_digest),
                    "policy_bundle_digest": str(token.policy_bundle_digest),
                    "record_hash": str(token.record_hash),
                    "tenant_id": token.tenant_id,
                    "mode": token.mode.value,
                    "verdict": token.verdict.value,
                    "issued_at": token.issued_at.isoformat(),
                    "expires_at": token.expires_at.isoformat(),
                    "key_id": token.key_id,
                    "key_alg": token.key_alg,
                    "shadow": token.shadow,
                },
            }
        )
