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

``FR-23`` the issuance record is the issuance claim
    The ``token_issued`` record is not written after the mint; it is handed to
    the mint as :class:`~neuroharness.tokens.nonce.IssuanceEvidence` and the
    decision is claimed *by* making it durable. Writing it afterwards is two
    steps, and the gap between them is a defect with no safe compensation: if
    the record fails, the token is rightly withheld while the claim stands, and
    the ledger that must never forget an issuance can never let that decision be
    minted again. One step has no gap. Because the record's identity is derived
    from the decision (:func:`issuance_record_id`), the store's own refusal of a
    duplicate id is the conditional insert, the lookup that proves a decision
    was never authorised is one call rather than a scan, and an issuance that
    landed while reporting failure still refuses the next attempt - from the
    chain, which cannot forget, rather than from a ledger that can.

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
from datetime import datetime
from enum import Enum
from typing import Any, Final
from uuid import UUID, uuid5

from pydantic import ValidationError

from neuroharness.errors import FailClosedError
from neuroharness.evidence.chain import FIELD_KIND, FIELD_RECORD_ID, FIELD_TENANT_ID, RecordKind
from neuroharness.evidence.store import AppendResult, DuplicateRecordError, EvidenceWriter
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.models.record import DecisionTokenRecord, Evaluation
from neuroharness.observability.logging import bind_context, get_logger
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve.inputs import Resolution, ResolutionRequest
from neuroharness.resolve.resolver import resolve as default_resolve
from neuroharness.tokens.model import DecisionToken, SignedToken
from neuroharness.tokens.nonce import DuplicateIssuanceError, IssuanceEntry
from neuroharness.tokens.service import TokenService

__all__ = [
    "DecisionContext",
    "EvaluationOutcome",
    "TokenWithheld",
    "DecisionPipeline",
    "RecordNotConstructibleError",
    "PIPELINE_OWNED_EVALUATION_FIELDS",
    "issuance_record_id",
]

_LOG = get_logger("neuroharness.pipeline")

_EVENT_EVALUATED = "pipeline.evaluated"
_EVENT_EVIDENCE_FAILED = "pipeline.evidence_unavailable"
_EVENT_TOKEN_WITHHELD = "pipeline.token_withheld"
_EVENT_RECORD_INVALID = "pipeline.record_not_constructible"
_EVENT_TOKEN_UNRECORDED = "pipeline.token_record_failed"
_EVENT_ISSUANCE_UNSTAGED = "pipeline.issuance_record_unstaged"

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
_TOKEN_ISSUED_FIELD: Final[str] = "token_issued"
_DECISION_ID_FIELD: Final[str] = "decision_id"
_ACTION_ID_FIELD: Final[str] = "action_id"
_TRACE_ID_FIELD: Final[str] = "trace_id"

#: Namespace for deriving a ``token_issued`` record's identity from the decision
#: it authorises. Fixed for the same reason
#: :data:`~neuroharness.tokens.model.TOKEN_SIGNING_DOMAIN` is fixed: the value
#: has to mean the same thing in every process and every release, because it is
#: what makes two attempts to authorise one decision collide instead of
#: coexisting. Derived once as
#: ``uuid5(NAMESPACE_DNS, "token-issued.issuance.neuroharness")`` and written
#: down, so nothing has to recompute it to read this module.
_ISSUANCE_RECORD_NAMESPACE: Final[UUID] = UUID("8a43e94a-aff7-5b3b-a43f-761a5e9292eb")


def _unreadable_issuance(decision_id: str) -> DuplicateIssuanceError:
    """The refusal for a decision whose issuance record exists but will not read.

    One message and one reason for all three ways that can happen - a payload
    that is not an object, a payload missing the fields that identify the
    issuance, and a store that reports the record and then does not return it -
    because they call for the same operator action and admit the same answer: an
    unreadable chain is not an empty chain, so nothing is minted.
    """
    return DuplicateIssuanceError(
        "this decision's issuance record is in the chain but cannot be read; "
        "refusing to mint a second token for it",
        reason_code=ReasonCode(ReasonName.DUPLICATE_ISSUANCE, decision_id),
    )


def issuance_record_id(*, tenant_id: str, decision_id: str) -> str:
    """The ``record_id`` of the one ``token_issued`` record a decision may have.

    Derived rather than generated, and that is the whole of ``D-5``'s third
    option in one function. Because the identity of the issuance record is a
    function of the decision, appending it *is* the at-most-once claim: the
    evidence store already refuses a second record with an id its chain holds
    (:class:`~neuroharness.evidence.store.DuplicateRecordError`), so the claim
    and the record are one conditional insert rather than two steps with a gap.
    It also gives an operator and an auditor a lookup instead of a scan: this
    decision's authorisation is that record or there is none.

    The tenant is length-prefixed so no pair of identifiers can be re-cut into
    another pair with the same rendering. A collision would fail closed - one
    decision refused as a duplicate of another - rather than mint twice, but a
    denial nobody can explain is not much better.
    """
    name = f"{len(tenant_id)}:{tenant_id}:{decision_id}"
    return str(uuid5(_ISSUANCE_RECORD_NAMESPACE, name))


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


class TokenWithheld(str, Enum):
    """Why an outcome carries no token.

    Every absent token has a named reason, and that is the whole point.
    ``EvaluationOutcome`` used to be constructible as ``verdict=ALLOW,
    token=None, evidence_failed=False`` - an allow, nothing to execute with, and
    no signal saying why. The docstring's defence was that every consumer reads
    ``permits_execution``; the consumer is the gateway, which is written in a
    later increment, and "the next person will read the docstring" is not an
    invariant.

    A gateway branching on ``outcome.verdict`` - the obvious thing to do -
    would have read ``ALLOW`` on a decision whose issuance record never landed.
    It is fail-closed either way, because nothing can execute without the token,
    but the *log line* and the *metric* would say the decision was allowed, and
    an operator reading rollout data would be reading a fiction.
    """

    #: The verdict itself does not permit execution. ``verdict`` says which.
    VERDICT = "verdict"
    #: The class is halted (``FR-49``): nothing is minted whatever the verdict.
    CLASS_HALTED = "class_halted"
    #: ``ADR-0016``: an infrastructure reason blocks in every mode, so a shadow
    #: class is not an unguarded one.
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    #: The *evaluation* record could not be made durable, so the verdict was
    #: overridden to ``ABSTAIN`` (``INV-05``, specification 5.3).
    EVALUATION_UNRECORDED = "evaluation_unrecorded"
    #: The evaluation was recorded and the ``token_issued`` record was not, so
    #: the token was minted and withheld and the decision stays retryable.
    ISSUANCE_UNRECORDED = "issuance_unrecorded"
    #: The mint refused on its own preconditions - most often that the chain
    #: already authorises this decision (``DUPLICATE_ISSUANCE``).
    ISSUANCE_REFUSED = "issuance_refused"


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
    #: Why no token, or ``None`` when one was issued. Exactly one of these two
    #: is set - see :meth:`__post_init__`.
    withheld: TokenWithheld | None = None

    def __post_init__(self) -> None:
        """A token and a reason for its absence are mutually exclusive and total.

        ``token is None`` if and only if ``withheld`` says why. Stated as a
        biconditional rather than as two separate checks because the defect this
        closes was precisely the gap between them: an outcome could carry
        neither, and read as an allow that authorised nothing.

        Not ``verdict.permits_execution`` on the left: a shadow class holds a
        token on a ``DENY`` (``INV-11``), so the verdict is the wrong predicate.
        The token is the only signal that anything may execute, so the token is
        what the invariant is stated over.
        """
        if self.token is not None and self.withheld is not None:
            raise ValueError(
                f"outcome carries a token and also says it was withheld "
                f"({self.withheld.value}); one decision has one answer"
            )
        if self.token is None and self.withheld is None:
            raise ValueError(
                "outcome carries no token and no reason for its absence; an "
                "allow that authorises nothing must say why (INV-05)"
            )

    @property
    def permits_execution(self) -> bool:
        return self.token is not None

    @property
    def evidence_failed(self) -> bool:
        """The *evaluation* record did not land, so the verdict was overridden.

        A property rather than a field, derived from :attr:`withheld`, so the two
        cannot disagree. Deliberately narrow, and it means exactly what it meant
        before: ``ISSUANCE_UNRECORDED`` is also an evidence failure, but it
        leaves the verdict standing and the decision retryable, which is a
        different thing for a caller to do something about. Read
        :attr:`withheld` to tell them apart.
        """
        return self.withheld is TokenWithheld.EVALUATION_UNRECORDED

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
                    withheld=TokenWithheld.EVALUATION_UNRECORDED,
                )

            token, withheld = self._maybe_issue(resolution, context, record)

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
                withheld=withheld,
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
    ) -> tuple[SignedToken | None, TokenWithheld | None]:
        """The token, or ``None`` and the reason there is none.

        Returns the pair rather than a bare ``None`` so the caller cannot build
        an outcome that withholds a token without saying why - which is the
        state :class:`EvaluationOutcome` now refuses.
        """
        withheld = self._withholding_reason(resolution, context.mode)
        if withheld is not None:
            _LOG.debug(
                _EVENT_TOKEN_WITHHELD,
                mode=context.mode.value,
                verdict=resolution.verdict.value,
                because=withheld.value,
            )
            return None, withheld

        # ``FR-23``: the issuance is itself evidence, and it is handed to the
        # mint rather than written after it. The token service claims the
        # decision *by* making this record durable, so a store failure leaves no
        # claim behind and the decision stays mintable on the next attempt --
        # while a record that landed, even one whose write reported failure,
        # refuses the next attempt from the chain rather than from memory.
        issuance = _IssuanceRecord(self._writer, context)
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
                issuance_evidence=issuance,
            )
        except FailClosedError as exc:
            # Two shapes of refusal, kept apart because an operator acts on them
            # differently. An unrecorded issuance is an evidence outage and the
            # decision can be retried; anything else is the token service being
            # the second opinion on its own preconditions, and withholding is
            # simply the safe direction. Both are caught here rather than
            # allowed to propagate: letting one out would abandon the evaluation
            # record that *was* written and surface a store failure from a
            # method whose contract is to return an outcome.
            if issuance.unrecorded_token_id is not None:
                _LOG.error(
                    _EVENT_TOKEN_UNRECORDED,
                    mode=context.mode.value,
                    verdict=resolution.verdict.value,
                    token_id=issuance.unrecorded_token_id,
                    because=exc.reason_code.render(),
                )
                return None, TokenWithheld.ISSUANCE_UNRECORDED
            _LOG.warning(
                _EVENT_TOKEN_WITHHELD,
                mode=context.mode.value,
                verdict=resolution.verdict.value,
                because=exc.reason_code.render(),
            )
            return None, TokenWithheld.ISSUANCE_REFUSED
        return signed, None

    @staticmethod
    def _withholding_reason(resolution: Resolution, mode: Mode) -> TokenWithheld | None:
        """Why no token may be minted, or ``None`` when one may.

        Written as a single function so the whole rule is readable at once, and
        so every reason it returns is logged rather than inferred.
        """
        if mode is Mode.HALTED:
            return TokenWithheld.CLASS_HALTED
        if any(code.is_infrastructure for code in resolution.reason_codes):
            # ADR-0016: this is what stops a shadow class being an unguarded one.
            return TokenWithheld.INFRASTRUCTURE_FAILURE
        if mode.blocks_on_verdict and not resolution.permits_execution:
            # The verdict, not `verdict_deny`: the outcome already carries the
            # verdict, and a reason that restated it would be two places for one
            # fact.
            return TokenWithheld.VERDICT
        return None


class _IssuanceRecord:
    """One decision's ``token_issued`` record, offered to the mint as its claim.

    Implements :class:`~neuroharness.tokens.nonce.IssuanceEvidence`. Both halves
    of that protocol read and write exactly one record, the one
    :func:`issuance_record_id` names, which is what lets absence be *proved*
    with a single lookup rather than inferred from a scan that might be stale.

    The obligations the protocol states, and how each is met here:

    * *see every durable issuance*. :meth:`recorded_issuance` asks the store,
      not this process's memory, so an issuance written by another gateway, by a
      write-ahead replay, or by an attempt whose caller was told the write had
      failed is found all the same.
    * *raise unless durable*. The write goes through
      :class:`~neuroharness.evidence.store.EvidenceWriter`, which re-raises
      rather than swallowing an outage.

    The signature is never recorded and is not reachable from here: the record
    proves that a token with these bindings existed, and storing the credential
    would make the audit log worth stealing from.
    """

    __slots__ = ("_writer", "_context", "record_id", "unrecorded_token_id")

    def __init__(self, writer: EvidenceWriter, context: DecisionContext) -> None:
        self._writer = writer
        self._context = context
        self.record_id = issuance_record_id(
            tenant_id=context.tenant_id, decision_id=context.decision_id
        )
        #: Set when a write was attempted and could not be made durable, so the
        #: caller can tell an evidence outage from a refusal to mint.
        self.unrecorded_token_id: str | None = None

    def recorded_issuance(self) -> IssuanceEntry | None:
        """The issuance the chain already holds for this decision, if any."""
        if not self._writer.store.has_record(self._context.tenant_id, self.record_id):
            return None
        return self._entry_from_chain()

    def record_issuance(self, token: DecisionToken) -> None:
        """Append the ``token_issued`` record (``FR-23``).

        Every way this can fail is a way the issuance did not become durable, so
        every one of them is marked as such: a record the schema rejects is as
        absent as a record the store refused, and an operator reading the log
        should see "this issuance was not recorded" in both cases rather than
        the generic withholding that also covers a refusal to mint at all.
        """
        try:
            self._append(token)
        except DuplicateRecordError as exc:
            # The store refused the id, which for this record means the chain
            # already authorises this decision. Reported as what it is -- a
            # second mint, refused -- rather than as a malformed record, because
            # the identity is derived and therefore never an accident.
            raise DuplicateIssuanceError(
                "the chain already holds the issuance record for this decision",
                reason_code=ReasonCode(
                    ReasonName.DUPLICATE_ISSUANCE, self._context.decision_id
                ),
            ) from exc
        except FailClosedError:
            self.unrecorded_token_id = token.token_id
            self._drop_staged_issuance()
            raise

    # -- internals ---------------------------------------------------------

    def _append(self, token: DecisionToken) -> None:
        self._writer.write(
            {
                FIELD_TENANT_ID: self._context.tenant_id,
                FIELD_KIND: RecordKind.TOKEN_ISSUED.value,
                FIELD_RECORD_ID: self.record_id,
                _TRACE_ID_FIELD: self._context.trace_id,
                _DECISION_ID_FIELD: self._context.decision_id,
                _ACTION_ID_FIELD: self._context.action_id,
                _TOKEN_ISSUED_FIELD: self._validated_payload(token),
            }
        )

    def _validated_payload(self, token: DecisionToken) -> dict[str, Any]:
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
        return validated.model_dump(mode="json", exclude_none=True)

    def _entry_from_chain(self) -> IssuanceEntry:
        """Rebuild the holding issuance from the record that proves it.

        Only ever reached on the duplicate path, which is why a scan is
        affordable here and a lookup is used everywhere else. A record that is
        present but unreadable raises rather than reporting "no issuance": the
        alternative is to mint a second token because the first one's evidence
        could not be parsed, and an unreadable chain is not an empty chain.
        """
        for record in self._writer.store.read(self._context.tenant_id):
            if str(record.get(FIELD_RECORD_ID)) != self.record_id:
                continue
            payload = record.get(_TOKEN_ISSUED_FIELD)
            if not isinstance(payload, Mapping):
                raise _unreadable_issuance(self._context.decision_id)
            try:
                return IssuanceEntry(
                    tenant_id=self._context.tenant_id,
                    decision_id=self._context.decision_id,
                    token_id=str(payload["token_id"]),
                    envelope_digest=Digest(str(payload["envelope_digest"])),
                    issued_at=datetime.fromisoformat(str(payload["issued_at"])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise _unreadable_issuance(self._context.decision_id) from exc
        raise _unreadable_issuance(self._context.decision_id)

    def _drop_staged_issuance(self) -> None:
        """Un-stage the record of an issuance that was never handed out.

        The write-ahead log stages whatever the store could not take and replays
        it on recovery (specification 5.3). For this record that would be wrong
        twice over: the token was withheld, so the chain would assert an
        authorisation that never existed, and - because the record's identity is
        the decision's - the replay would then refuse every retry of a decision
        that was never authorised at all. The pipeline can drop it precisely
        because it can prove the token never left this method.

        ``ADR-0026`` records this as the narrowest exception that makes the
        derived-identity claim correct.

        Narrow on purpose: one record, named by its derived id, only after its
        own write failed, and logged. It is not a way to make an inconvenient
        record go away, and it never touches the evaluation record, which stays
        staged and replays exactly as ``FR-23`` requires.
        """
        wal = self._writer.wal
        if wal is None:
            return
        if wal.discard(self.record_id, tenant_id=self._context.tenant_id):
            _LOG.warning(
                _EVENT_ISSUANCE_UNSTAGED,
                record_id=self.record_id,
                tenant_id=self._context.tenant_id,
                token_id=self.unrecorded_token_id,
            )
