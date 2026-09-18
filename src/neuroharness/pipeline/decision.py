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

The pipeline owns the *order*, and it owns one thing more: every payload it
writes is constructed through the typed models in
:mod:`neuroharness.models.record` and therefore validated against
``docs/sdd/schemas/decision-record.schema.json`` **before** it is hash-chained.
An earlier version assembled a free-form dictionary. That is worse than it
sounds: a hash chain makes a record permanent, so a malformed record is
malformed for as long as the chain is retained, and the failure surfaces at the
auditor rather than at the writer. Constitution Article IV says evidence is a
byproduct of enforcement; evidence that does not parse is not evidence, so the
validation failure is a fail-closed condition (Article II) rather than a warning.

The *payload* is the word that matters, and an earlier version of this paragraph
overstated it. A record has two halves and they have different owners: the
payload block is the pipeline's, and the identity and link fields --
``record_id``, ``decision_id``, ``action_id``, ``trace_id`` -- are the store's,
because it is the store that assigns and chains them
(:func:`~neuroharness.evidence.store.prepare_record`, ``FR-72``). While only
this half was checked, a deployment wired to a non-UUID identifier seam produced
a perfectly valid ``evaluation`` block inside a record the published schema
rejects. Between them the two checks now cover every field except the chain
positions the store computes, which cannot be validated before they exist.

What the record *says* is still the caller's business - the gateway knows the
envelope reference, the critic timings and the fact freshness, and this module
knows none of that. The split is: the caller supplies the evaluation-scoped
facts through :attr:`DecisionContext.evaluation_payload`, and the pipeline
supplies, and refuses to let the caller supply, the fields that state what was
decided (:data:`PIPELINE_OWNED_EVALUATION_FIELDS`).

One field deliberately does not reach the record: ``Resolution.explain``. It is
an ordered prose trace of which rule fired at each step, and the record schema
has no place for it. That is the schema being right rather than incomplete: the
record explains a verdict through ``pdp_outcomes``, ``critic_results``,
``facts_used`` and ``reason_codes`` - typed, replayable, machine-comparable -
and ``NFR-20`` asks for an explanation from the record, not for prose in it. The
trace travels on the returned :class:`EvaluationOutcome` and in the structured
log, where it is a debugging aid and cannot be mistaken for evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError

from neuroharness.errors import FailClosedError
from neuroharness.evidence.chain import FIELD_KIND, FIELD_TENANT_ID, RecordKind
from neuroharness.evidence.store import AppendResult, EvidenceWriter
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.models.record import DecisionTokenRecord, Evaluation
from neuroharness.observability.logging import bind_context, get_logger
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve.inputs import Resolution, ResolutionRequest
from neuroharness.resolve.resolver import resolve as default_resolve
from neuroharness.tokens.model import SignedToken
from neuroharness.tokens.service import TokenService

__all__ = [
    "DecisionContext",
    "EvaluationOutcome",
    "DecisionPipeline",
    "RecordNotConstructibleError",
    "PIPELINE_OWNED_EVALUATION_FIELDS",
]

_LOG = get_logger("neuroharness.pipeline")

_EVENT_EVALUATED = "pipeline.evaluated"
_EVENT_EVIDENCE_FAILED = "pipeline.evidence_unavailable"
_EVENT_TOKEN_WITHHELD = "pipeline.token_withheld"
_EVENT_RECORD_INVALID = "pipeline.record_not_constructible"
_EVENT_TOKEN_UNRECORDED = "pipeline.token_record_failed"

#: Evaluation fields the pipeline derives, and which a caller may therefore not
#: supply. Every one of them states *what was decided* rather than what was
#: asked: the digests the decision was bound to, the mode it was taken under,
#: the verdict, its reasons, and which repair attempt this was.
#:
#: Refused rather than overwritten. Silently discarding a caller's ``verdict``
#: would hide the bug that produced it; accepting it would let the record say
#: something other than what the resolver decided, which is the one thing an
#: evidence chain must never permit (Constitution Art. IV).
PIPELINE_OWNED_EVALUATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "proposal_digest",
        "envelope_digest",
        "mode",
        "verdict",
        "reason_codes",
        "repair_iteration",
    }
)

#: Where the bundle digest the token binds to appears inside the evaluation.
_POLICY_BUNDLE_FIELD: Final[str] = "policy_bundle"
_DIGEST_FIELD: Final[str] = "digest"
_SESSION_ID_FIELD: Final[str] = "session_id"


class RecordNotConstructibleError(FailClosedError):
    """A decision record could not be built in a form the schema admits.

    ``SCHEMA_INVALID`` rather than ``EVIDENCE_UNAVAILABLE``: the store is
    healthy and would have accepted the bytes. What failed is the harness's own
    ability to say what it decided, and Article II makes that an inability to
    evaluate - so nothing executes, exactly as if the store had been down.
    """

    reason_name = ReasonName.SCHEMA_INVALID


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Everything the pipeline needs to bind a decision to an action.

    Identity fields, digests, and the caller's half of the evaluation record.
    The pipeline never invents an identifier: ``action_id`` and ``decision_id``
    arrive from the caller's identifier seam so that a replay reproduces the
    same record (``FR-71``).

    ``action_class`` is a human-readable label for the log context only. The
    record's action-class reference is a structured
    :class:`~neuroharness.models.record.RecordActionClassRef` and arrives in
    ``evaluation_payload``; the two are different shapes because a log line
    wants something greppable and a record wants something comparable.

    ``token_ttl_seconds`` is the per-class lifetime from the action-class
    registry (``FR-30``). ``None`` means "use the token service's configured
    default", so a caller that does not consult the registry still gets a
    bounded token rather than an unbounded one.
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
    #: The evaluation-scoped facts the pipeline cannot know: the envelope
    #: reference, the action-class reference, session lineage, bundle and
    #: registry versions, rule outcomes, critic results, facts, claims and
    #: latencies. Validated as a
    #: :class:`~neuroharness.models.record.Evaluation` before anything is
    #: written, so an incomplete payload fails at the writer rather than at the
    #: auditor.
    evaluation_payload: Mapping[str, Any] = field(default_factory=dict)
    token_ttl_seconds: int | None = None


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
                record = self._write_evaluation(request, resolution, context)
            except FailClosedError as exc:
                # Specification 5.3, post-resolution: the response is overridden.
                # Whatever the resolver decided, an unrecorded decision was not
                # made (Article III), so nothing may execute. Every fail-closed
                # reason lands here, not only an unavailable store: a record
                # that cannot be *built* is as absent as one that cannot be
                # written, and letting that one escape ``evaluate`` would put an
                # untyped exception on the decision path.
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
                # NFR-20: the trace is a debugging aid on the log, never a field
                # of the record. See the module docstring.
                explain=list(resolution.explain),
            )
            return EvaluationOutcome(
                verdict=resolution.verdict,
                reason_codes=resolution.reason_codes,
                resolution=resolution,
                record=record,
                token=token,
            )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _assert_mode_agrees(request: ResolutionRequest, context: DecisionContext) -> None:
        """The mode that resolved must be the mode that is recorded and bound.

        The rollout mode reaches this module twice: on the policy the resolver
        consulted, and on the context that the record and the token are built
        from. They come from one registry entry, so a disagreement is either a
        caller bug or a class that was re-read between the two - and in both
        cases the token would bind a mode the decision was not taken under,
        which is exactly the binding ``FR-21`` step 6 exists to check. Refused
        rather than reconciled: picking a winner here would decide, silently,
        whether a class is enforcing.
        """
        if context.mode is not request.policy.mode:
            raise RecordNotConstructibleError(
                f"context mode {context.mode.value!r} contradicts the action class "
                f"mode {request.policy.mode.value!r} the decision was resolved "
                "against; one class has one rollout mode (FR-21, INV-11)"
            )

    def _write_evaluation(
        self,
        request: ResolutionRequest,
        resolution: Resolution,
        context: DecisionContext,
    ) -> AppendResult:
        payload = self._build_evaluation(request, resolution, context)
        record = {
            FIELD_TENANT_ID: context.tenant_id,
            FIELD_KIND: RecordKind.EVALUATION.value,
            "trace_id": context.trace_id,
            "decision_id": context.decision_id,
            "action_id": context.action_id,
            "evaluation": payload,
        }
        return self._writer.write(record)

    def _build_evaluation(
        self,
        request: ResolutionRequest,
        resolution: Resolution,
        context: DecisionContext,
    ) -> dict[str, Any]:
        """Assemble and validate the evaluation block (``FR-70``)."""
        self._assert_mode_agrees(request, context)
        supplied = dict(context.evaluation_payload)

        usurped = sorted(PIPELINE_OWNED_EVALUATION_FIELDS & supplied.keys())
        if usurped:
            raise RecordNotConstructibleError(
                f"evaluation_payload supplies {', '.join(usurped)}; the pipeline "
                "records what the resolver decided, and a caller that could "
                "overwrite it could record a decision that was never made"
            )

        self._assert_bundle_digest_agrees(supplied, context)

        if context.session_id is not None:
            existing = supplied.setdefault(_SESSION_ID_FIELD, context.session_id)
            if existing != context.session_id:
                raise RecordNotConstructibleError(
                    f"evaluation_payload session_id {existing!r} contradicts the "
                    f"decision context's {context.session_id!r}; the record and the "
                    "trace must describe one session"
                )

        payload = {
            **supplied,
            "proposal_digest": str(context.proposal_digest),
            "envelope_digest": str(context.envelope_digest),
            "mode": context.mode.value,
            "verdict": resolution.verdict.value,
            "reason_codes": [code.render() for code in resolution.reason_codes],
            "repair_iteration": request.repair_iteration,
        }

        try:
            evaluation = Evaluation.model_validate(payload)
        except ValidationError as exc:
            # The messages carry field names and constraint names only; pydantic
            # includes offending input values, so they are deliberately not
            # logged (``NFR-18``) and the count is reported instead.
            _LOG.error(
                _EVENT_RECORD_INVALID,
                kind=RecordKind.EVALUATION.value,
                error_count=exc.error_count(),
                fields=sorted(
                    {".".join(str(part) for part in err["loc"]) for err in exc.errors()}
                ),
            )
            raise RecordNotConstructibleError(
                f"the evaluation record does not satisfy the decision-record schema: "
                f"{exc.error_count()} field(s) rejected"
            ) from exc
        return evaluation.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _assert_bundle_digest_agrees(
        supplied: Mapping[str, Any], context: DecisionContext
    ) -> None:
        """The bundle the record names must be the bundle the token binds.

        ``FR-84`` lets the broker refuse a token whose bundle has been
        superseded, and it compares the token's digest against the current one.
        If the record named a different bundle than the token carries, that
        check would be testing a claim the record contradicts, and a replay
        (``FR-71``) would re-run the decision under rules it was not taken
        under.
        """
        bundle = supplied.get(_POLICY_BUNDLE_FIELD)
        if not isinstance(bundle, Mapping):
            return
        declared = bundle.get(_DIGEST_FIELD)
        if declared is not None and str(declared) != str(context.policy_bundle_digest):
            raise RecordNotConstructibleError(
                f"evaluation policy_bundle digest {str(declared)!r} contradicts the "
                f"digest the token binds ({str(context.policy_bundle_digest)!r}); a "
                "decision is taken under exactly one rule set (FR-84)"
            )

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
                ttl_seconds=context.token_ttl_seconds,
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

        try:
            self._write_token_issued(signed, context)
        except FailClosedError as exc:
            # ``FR-23``: the issuance is itself evidence. If it cannot be
            # recorded the token is not returned, so no caller ever holds the
            # signed bytes and nothing can present them - the nonce stays
            # unspent and the minting is a local event that had no effect.
            #
            # This sits inside ``_maybe_issue`` rather than around the call to
            # it, because letting it propagate would abandon the evaluation
            # record that *was* written and surface a raw store failure from a
            # method whose contract is to return an outcome.
            _LOG.error(
                _EVENT_TOKEN_UNRECORDED,
                mode=context.mode.value,
                verdict=resolution.verdict.value,
                token_id=signed.token.token_id,
                because=exc.reason_code.render(),
            )
            return None
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
        """Record the issuance as its own linked record (``FR-23``).

        The signature is not recorded and is not reachable from here: the record
        proves that a token with these bindings existed, and storing the
        credential would make the audit log worth stealing from.
        """
        token = signed.token
        payload = {
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
            "key_alg": token.key_alg.value,
            "shadow": token.shadow,
        }
        try:
            validated = DecisionTokenRecord.model_validate(payload)
        except ValidationError as exc:
            _LOG.error(
                _EVENT_RECORD_INVALID,
                kind=RecordKind.TOKEN_ISSUED.value,
                error_count=exc.error_count(),
                fields=sorted(
                    {".".join(str(part) for part in err["loc"]) for err in exc.errors()}
                ),
            )
            raise RecordNotConstructibleError(
                f"the token_issued record does not satisfy the decision-record "
                f"schema: {exc.error_count()} field(s) rejected"
            ) from exc

        self._writer.write(
            {
                FIELD_TENANT_ID: context.tenant_id,
                FIELD_KIND: RecordKind.TOKEN_ISSUED.value,
                "trace_id": context.trace_id,
                "decision_id": context.decision_id,
                "action_id": context.action_id,
                "token_issued": validated.model_dump(mode="json", exclude_none=True),
            }
        )
