"""The decision record: append-only, hash-chained evidence.

Constitution Art. IV says evidence is a byproduct of enforcement - a decision
that is not recorded was not made. These models are that record's wire form.

One ``evaluation`` record is written per evaluation. Token issuance, approvals,
execution receipts, completions, effect verifications, overrides and
checkpoints are *separate* linked records rather than fields of the evaluation,
and that separation is deliberate: ``INV-05``/``FR-23`` require the evaluation
record to be durable **before** a token can exist, which is only expressible if
the token is not part of it. An evaluation record that could carry a token
would be a record that had to be rewritten after the fact, and a rewritable
hash chain is not a hash chain.

Two constraints run through every model here:

* Reason codes are :class:`~neuroharness.reason.ReasonCode` objects, validated
  from and serialised to their rendered string form. Free text can therefore
  not enter the record and cannot be relayed to the governed model through the
  repair channel (``SEC-07``, threat T-11).
* Nothing a soft verifier produces may look like authority: a hard critic may
  not carry a ``score`` (``FR-55``) and no score is ever an allow signal
  (``INV-01``).

Authoritative source: ``docs/sdd/schemas/decision-record.schema.json``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Final, TypeAlias, cast
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    InstanceOf,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from neuroharness import grammar
from neuroharness.defaults import MAX_DEMOTE_MODE_WINDOW_SECONDS, MAX_REPAIR_BUDGET
from neuroharness.models.common import (
    ApprovalState,
    CriticKind,
    Digest,
    EffectClass,
    FactStatus,
    Mode,
    OverrideKind,
    Principal,
    ReceiptStatus,
    RuleOutcomeKind,
    Verdict,
    VerifierResult,
)
from neuroharness.models.envelope import (
    BatchPolicy,
    ConnectorKind,
    FactName,
    ModelIdentity,
    ResourceKey,
    SessionId,
    SourceName,
    TenantId,
    ToolName,
    TraceId,
    VersionedArtifact,
    VersionString,
    WireModel,
)
from neuroharness.reason import (
    PARAMETERISED_REASONS,
    SUBJECT_GRAMMAR,
    ReasonCode,
    ReasonName,
)
from neuroharness.version import SchemaCompatibility, SchemaKind

__all__ = [
    "RecordKind",
    "ApprovalMethod",
    "CompletionStatus",
    "EffectVerificationResult",
    "ExpectedRelation",
    "KeyAlgorithm",
    "SolverStatus",
    "ReasonCodeField",
    "Scalar",
    "RuleId",
    "PropertyId",
    "CriticId",
    "RecordActionClassRef",
    "ClaimRecord",
    "EvaluationBatchPosition",
    "ExpectedDomain",
    "CounterexampleField",
    "Counterexample",
    "RuleOutcome",
    "CriticResult",
    "FactRef",
    "Evaluation",
    "DecisionTokenRecord",
    "Approver",
    "Approval",
    "ExecutionLease",
    "ExecutionReceipt",
    "Completion",
    "EffectVerification",
    "Override",
    "Checkpoint",
    "DecisionRecord",
    "RECORD_PAYLOAD_FIELDS",
    "RECORD_KINDS_REQUIRING_DECISION_LINK",
    "CRITIC_RESULT_KINDS",
    "MAX_DEMOTE_MODE_WINDOW_SECONDS",
]


# --- Reason codes ------------------------------------------------------------

# The record schema's ReasonCode is a closed catalogue with a *shape* per name.
# Mirroring it here is the structural half of SEC-07: the gateway additionally
# resolves each subject against the registry, but a subject that is not even
# identifier-shaped must never reach that stage, because prose smuggled into a
# reason code is prose delivered to the governed model as an instruction.
# Every one of these comes from `neuroharness.grammar`. They used to be spelled
# here as well as there, and the resource-key pair had drifted: this module's
# kind segment admitted `_` and refused `-`, so `cluster-prod:svc-a` - a key the
# signed registry accepts and the broker would lease on - could not be recorded,
# and `RESOURCE_BUSY:cluster-prod:svc-a` failed the catalogue check. A lease
# contention reached the operator as SCHEMA_INVALID, i.e. as a harness fault.
# The per-name subject shapes used to live here as well as in `reason.py`, and
# the two had come apart. They are now one map, `reason.SUBJECT_GRAMMAR`,
# checked at construction and rendered into the published schema from the same
# source - see `_reason_code_alternatives` below for what the divergence cost.

#: Names that stand alone. Derived: a name is bare exactly when it takes no
#: subject, which ``reason.PARAMETERISED_REASONS`` already decides. Restating
#: the list here is how it would come to disagree - and the catalogue is closed,
#: so a name in neither set would be silently unrecordable.
_BARE_REASON_NAMES: Final[tuple[str, ...]] = tuple(
    sorted(name.value for name in ReasonName if name not in PARAMETERISED_REASONS)
)


def _reason_code_alternatives() -> tuple[str, ...]:
    """The closed catalogue as anchored alternatives, built from one source.

    Every subject shape comes from ``reason.SUBJECT_GRAMMAR``, which is also
    what :class:`~neuroharness.reason.ReasonCode` checks at construction. They
    used to be two lists, and they had come apart in a way that mattered: the
    resolver's fallback for a hard critic that FAILs without its own reason is
    ``RULE_FAILED:<critic_id>``, ``ReasonCode`` built it happily, and this
    catalogue required a *rule id*. So the record writer refused a correct hard
    ``DENY``, the pipeline turned that into ``ABSTAIN(SCHEMA_INVALID)``, and
    nothing was recorded at all - which is the one outcome Article III says
    cannot happen.

    Names sharing a shape are grouped so the published schema stays readable,
    and both the grouping and the order are deterministic, because this feeds
    the generated JSON Schema.
    """
    by_shape: dict[str, list[str]] = {}
    for name, source in SUBJECT_GRAMMAR.items():
        by_shape.setdefault(source, []).append(name.value)

    alternatives = ["|".join(_BARE_REASON_NAMES)]
    for source in sorted(by_shape):
        names = sorted(by_shape[source])
        head = names[0] if len(names) == 1 else "(" + "|".join(names) + ")"
        # ``(?:...)`` is load-bearing: `RULE_FAILED` and `TOKEN_INVALID` have a
        # top-level ``|`` in their source, and unwrapped it would split the
        # whole alternative - `RULE_FAILED:<rule>` OR a bare `<critic_id>` with
        # no name at all, which would admit a reason code that is just an
        # identifier.
        alternatives.append(f"{head}:(?:{source})")
    return tuple(alternatives)


#: The closed catalogue as a list of anchored alternatives. Public because
#: ``tools/render_schema_patterns.py`` writes it into the published schema and
#: ``tests/unit/test_schema_patterns_are_generated.py`` proves it has not drifted.
REASON_CODE_ALTERNATIVES: Final[tuple[str, ...]] = _reason_code_alternatives()

_REASON_CODE_RE: Final[re.Pattern[str]] = re.compile(
    "^(?:" + "|".join(f"(?:{alternative})" for alternative in REASON_CODE_ALTERNATIVES) + ")$"
)

#: Rendered reason codes are bounded; the record is evidence, not a log sink.
MAX_REASON_CODE_LENGTH: Final[int] = grammar.MAX_REASON_CODE_LENGTH


def _coerce_reason_code(value: object) -> ReasonCode:
    """Accept a :class:`ReasonCode` or its rendered form; reject anything else.

    Both directions are checked against the record schema's catalogue so that a
    model built in Python and a document read off the wire are held to the same
    standard. Free text fails at :meth:`ReasonCode.parse` (unknown name) or at
    the shape check below (``SEC-07``).
    """
    if isinstance(value, ReasonCode):
        code = value
    elif isinstance(value, str):
        code = ReasonCode.parse(value)
    else:
        raise ValueError(f"reason code must be a ReasonCode or its rendered form: {value!r}")
    rendered = code.render()
    if len(rendered) > MAX_REASON_CODE_LENGTH:
        raise ValueError("rendered reason code exceeds the maximum length")
    # ``fullmatch``, not ``match``: ``$`` also matches before a final newline,
    # so ``.match`` admitted a reason code with one appended even though the
    # catalogue has no such member (``SEC-07``).
    if not _REASON_CODE_RE.fullmatch(rendered):
        raise ValueError(
            f"reason code {rendered!r} is not in the closed catalogue or its subject "
            "is not a registry-shaped identifier (SEC-07)"
        )
    return code


#: A reason code as it appears in a record: typed in Python, rendered on the
#: wire. Declared once so that no record field can quietly accept a string.
ReasonCodeField = Annotated[
    InstanceOf[ReasonCode],
    BeforeValidator(_coerce_reason_code),
    PlainSerializer(lambda code: code.render(), return_type=str, when_used="always"),
]


# --- Constrained strings the record schema defines --------------------------

Scalar = bool | int | float | Annotated[str, StringConstraints(max_length=128)] | None

RuleId = Annotated[str, StringConstraints(pattern=rf"^{grammar.RULE_ID_SOURCE}$")]
PropertyId = Annotated[
    str, StringConstraints(pattern=rf"^{grammar.PROPERTY_ID_SOURCE}$", max_length=64)
]
CriticId = Annotated[
    str, StringConstraints(pattern=rf"^{grammar.CRITIC_ID_SOURCE}$", max_length=64)
]
CriticVersion = Annotated[str, StringConstraints(max_length=96)]
JsonPointer = Annotated[
    str, StringConstraints(pattern=r"^(/[A-Za-z0-9_.~-]+)+$", max_length=128)
]
StorageRef = Annotated[str, StringConstraints(max_length=256)]
KeyId = Annotated[str, StringConstraints(max_length=64)]
LatencyStage = Annotated[str, StringConstraints(max_length=80)]


# --- Bounds declared by the schema ------------------------------------------

MAX_PDP_OUTCOMES: Final[int] = 256
MAX_CRITIC_RESULTS: Final[int] = 64
MAX_FACT_REFS: Final[int] = 64
MAX_CLAIMS_RECORDED: Final[int] = 32
MAX_REASON_CODES: Final[int] = 32
MAX_COUNTEREXAMPLES: Final[int] = 32
MAX_COUNTEREXAMPLE_FIELDS: Final[int] = 16
MAX_EXPECTED_ENUM: Final[int] = 32
MAX_AUTHORIZING_PRINCIPALS: Final[int] = 4
MAX_SIGNATURE_LENGTH: Final[int] = 512

#: ``FR-48``: a ``demote_mode`` override auto-expires within one hour unless it
#: is replaced by a signed registry change under two-person review. A loosening
#: override that outlives the incident is indistinguishable from a permanent
#: policy change nobody reviewed.
#:
#: This belongs in :mod:`neuroharness.defaults` alongside the other durations;
#: it lives here only because that module is frozen for this change.

#: Critic families the record schema admits. Rego outcomes are recorded as PDP
#: rule outcomes, not as critic results, so ``CriticKind.REGO`` has no place
#: here; see the module note in the review report.
CRITIC_RESULT_KINDS: Final[frozenset[CriticKind]] = frozenset(
    {
        CriticKind.SMT,
        CriticKind.PROLOG,
        CriticKind.MONITOR,
        CriticKind.EFFECT,
        CriticKind.LLM_SOFT,
        CriticKind.OTHER,
    }
)


# --- Enumerations local to the record ---------------------------------------


class RecordKind(str, Enum):
    """Which event this record captures (``FR-70``, ``FR-72``)."""

    EVALUATION = "evaluation"
    TOKEN_ISSUED = "token_issued"
    APPROVAL = "approval"
    EXECUTION_RECEIPT = "execution_receipt"
    COMPLETION = "completion"
    EFFECT_VERIFICATION = "effect_verification"
    OVERRIDE = "override"
    CHECKPOINT = "checkpoint"


class ApprovalMethod(str, Enum):
    """Channel a human used to decide an approval."""

    UI = "ui"
    API = "api"
    CLI = "cli"


class CompletionStatus(str, Enum):
    """Observed outcome of an asynchronous action (``FR-26``)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class EffectVerificationResult(str, Enum):
    """Whether the observed effect matched the proposal (``FR-57``)."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ExpectedRelation(str, Enum):
    """Relation a counterexample field was expected to satisfy (``FR-56``)."""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    NOT_IN = "not_in"
    PRECEDES = "precedes"
    FOLLOWS = "follows"


class KeyAlgorithm(str, Enum):
    """Signing algorithm for decision tokens (``FR-20``, ``NFR-17``)."""

    ECDSA_P256 = "ecdsa-p256"
    ED25519 = "ed25519"
    HMAC_SHA256 = "hmac-sha256"


class SolverStatus(str, Enum):
    """Raw solver answer, recorded so a replay can be compared (``FR-51``)."""

    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"


# --- Evaluation payload ------------------------------------------------------


class RecordActionClassRef(WireModel):
    """The action class an evaluation was about.

    Narrower than the envelope's :class:`~neuroharness.models.envelope.ActionClassRef`
    on purpose: the record needs the identity of the class and what it could do,
    not the whole registry entry, which is recoverable from the recorded
    registry digest.
    """

    tool: ToolName
    intent: FactName
    effect_class: EffectClass | None = None
    resource_key: ResourceKey | None = None


class ClaimRecord(WireModel):
    """An agent claim, recorded verbatim and never evaluated (``FR-12``).

    Present so that an auditor can compare what the model asserted with what
    the harness verified. Its being in the record and absent from the policy
    input is the whole distinction between a claim and a fact.
    """

    name: FactName
    value: Scalar

    @model_serializer(mode="wrap")
    def _always_emit_value(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Keep ``value`` present even when null; the schema requires the key."""
        data: dict[str, Any] = handler(self)
        data.setdefault("value", None)
        return data


class EvaluationBatchPosition(WireModel):
    """Batch position as recorded (``FR-07``).

    ``batch_size`` is absent here by design: the record captures where this
    member sat, and the batch's extent is recoverable from its siblings.
    """

    batch_id: UUID | None = None
    batch_index: Annotated[int, Field(ge=0)] | None = None
    batch_policy: BatchPolicy | None = None


class ExpectedDomain(WireModel):
    """The domain or relation a counterexample field should have satisfied.

    Typed rather than prose (``FR-56``, ``SEC-07``): repair needs to know
    *which* constraint was violated, and a sentence explaining it would be an
    instruction channel back into the governed model.
    """

    enum: Annotated[tuple[Scalar, ...], Field(max_length=MAX_EXPECTED_ENUM)] | None = None
    min: float | None = None
    max: float | None = None
    relation: ExpectedRelation | None = None
    reference_path: JsonPointer | None = None


class CounterexampleField(WireModel):
    """One offending location inside the envelope.

    ``value`` is a bounded scalar and ``value_digest`` covers anything larger,
    so that a counterexample cannot become a channel for exfiltrating or
    injecting content (``SEC-07``, ``FR-74``).
    """

    path: JsonPointer
    value: Scalar = None
    value_digest: Digest | None = None
    expected: ExpectedDomain | None = None


class Counterexample(WireModel):
    """Typed, non-instructional explanation of a failure (``FR-56``).

    A hard FAIL must include one: without a counterexample the repair loop
    degrades into the model guessing, which is the failure mode the harness
    exists to remove. ``property_id`` is registry-validated, and there is no
    free-text field anywhere in this structure.
    """

    property_id: PropertyId
    fields: Annotated[
        tuple[CounterexampleField, ...], Field(min_length=1, max_length=MAX_COUNTEREXAMPLE_FIELDS)
    ]
    message_code: ReasonCodeField | None = None


class RuleOutcome(WireModel):
    """One PDP rule result (specification section 5.1).

    ``deny`` maps to a hard FAIL and ``abstain`` to a hard UNKNOWN; the mapping
    itself lives on :class:`~neuroharness.models.common.RuleOutcomeKind` so the
    record and the resolver cannot drift apart.
    """

    rule_id: RuleId
    outcome: RuleOutcomeKind
    repairable: bool | None = None
    reason_code: ReasonCodeField | None = None
    counterexample: Counterexample | None = None

    @property
    def verifier_result(self) -> VerifierResult:
        """This outcome in the shared verifier vocabulary (section 5.1)."""
        return self.outcome.to_verifier_result()


class CriticResult(WireModel):
    """The result of one critic run (``FR-51``, ``FR-55``)."""

    critic_id: CriticId
    version: CriticVersion
    kind: CriticKind
    hard: bool
    effective_mode: Mode
    result: VerifierResult
    duration_ms: Annotated[float, Field(ge=0)]
    would_be_verdict: Verdict | None = None
    repairable: bool | None = None
    counterexample: Counterexample | None = None
    solver_status: SolverStatus | None = None
    solver_rlimit_used: Annotated[int, Field(ge=0)] | None = None
    score: Annotated[float, Field(ge=0, le=1)] | None = None

    @field_validator("kind")
    @classmethod
    def _kind_is_recordable(cls, value: CriticKind) -> CriticKind:
        """Reject critic families the record schema does not admit.

        Rego runs in the PDP and is recorded as a :class:`RuleOutcome`; a
        ``rego`` critic result would be a policy decision filed as a critic
        opinion, which breaks the section 5.1 mapping that the resolver and
        replay both depend on.
        """
        if value not in CRITIC_RESULT_KINDS:
            raise ValueError(
                f"critic kind {value.value!r} is not recordable as a critic result; "
                f"recordable kinds are {sorted(kind.value for kind in CRITIC_RESULT_KINDS)}"
            )
        return value

    @model_validator(mode="after")
    def _hard_critics_carry_no_score(self) -> CriticResult:
        """A hard critic must not carry a ``score`` (``FR-55``).

        A score is advisory confidence for calibration reporting. On a hard
        critic it would be a number attached to a gate, and a number attached to
        a gate is eventually read as permission to pass it - exactly the
        model-confidence-as-authorization path ``INV-01`` forbids.
        """
        if self.hard and self.score is not None:
            raise ValueError(
                f"hard critic {self.critic_id!r} must not report a score; "
                "confidence is never an allow signal (FR-55, INV-01)"
            )
        return self

    @property
    def blocks_verdict(self) -> bool:
        """True when this result can move the verdict away from ALLOW.

        A hard critic demoted to shadow or advisory is resolved as soft
        (section 5.3 step 0), which is why the mode is consulted here and not
        only the ``hard`` flag.
        """
        return self.hard and self.effective_mode.blocks_on_verdict


class FactRef(WireModel):
    """A fact as it was used, referenced rather than copied.

    The value is not repeated in the record: the canonical envelope is retained
    content-addressed by its digest (``FR-70``), so the digest plus the freshness
    figures are enough to replay and to audit, without duplicating provider data
    into the chain (``FR-74``).
    """

    name: FactName
    status: FactStatus
    digest: Digest | None = None
    source: SourceName | None = None
    asserted_by: Principal | None = None
    fetched_at: AwareDatetime | None = None
    age_seconds: Annotated[float, Field(ge=0)] | None = None
    max_age_seconds: Annotated[float, Field(ge=0)] | None = None


class Evaluation(WireModel):
    """Everything needed to replay one evaluation (``FR-70``, ``FR-71``).

    Deliberately absent: the decision token. ``INV-05``/``FR-23`` require this
    record to be durable *before* a token exists, so the token is a separate
    :class:`DecisionTokenRecord` linked by ``decision_id``.
    """

    proposal_digest: Digest
    envelope_digest: Digest
    envelope_ref: StorageRef
    action_class: RecordActionClassRef
    session_id: SessionId
    session_root_id: SessionId
    repair_iteration: Annotated[int, Field(ge=0, le=MAX_REPAIR_BUDGET)]
    mode: Mode
    policy_bundle: VersionedArtifact
    registry: VersionedArtifact
    pdp_outcomes: Annotated[tuple[RuleOutcome, ...], Field(max_length=MAX_PDP_OUTCOMES)]
    critic_results: Annotated[tuple[CriticResult, ...], Field(max_length=MAX_CRITIC_RESULTS)]
    facts_used: Annotated[tuple[FactRef, ...], Field(max_length=MAX_FACT_REFS)]
    claims_recorded: Annotated[tuple[ClaimRecord, ...], Field(max_length=MAX_CLAIMS_RECORDED)]
    verdict: Verdict
    reason_codes: Annotated[tuple[ReasonCodeField, ...], Field(max_length=MAX_REASON_CODES)]
    latency_ms: dict[LatencyStage, Annotated[float, Field(ge=0)]]
    end_to_end_ms: Annotated[float, Field(ge=0)]
    model_identity: ModelIdentity | None = None
    prior_decision_id: UUID | None = None
    batch: EvaluationBatchPosition | None = None
    counterexamples: (
        Annotated[tuple[Counterexample, ...], Field(max_length=MAX_COUNTEREXAMPLES)] | None
    ) = None
    approval_request_id: UUID | None = None
    monitor_state_ref: StorageRef | None = None

    @property
    def permits_token(self) -> bool:
        """True when this evaluation could lead to an executing token.

        ``FR-20``: in ``enforce`` a token is issued only on ALLOW. In
        ``shadow``/``advisory`` a token is still issued on every verdict, but it
        carries ``shadow: true`` and the broker refuses it for a class that has
        since moved to ``enforce`` (``FR-21``).
        """
        if self.mode.blocks_on_verdict:
            return self.verdict.permits_execution
        return True


# --- Linked payloads ---------------------------------------------------------


class DecisionTokenRecord(WireModel):
    """The token that was issued, recorded after the evaluation is durable.

    The signature itself is never stored: the record proves a token with these
    bindings existed, and storing the credential would turn the audit log into
    a place worth stealing from.
    """

    token_id: UUID
    decision_id: UUID
    envelope_digest: Digest
    proposal_digest: Digest
    policy_bundle_digest: Digest
    tenant_id: TenantId
    mode: Mode
    verdict: Verdict
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    key_id: KeyId
    key_alg: KeyAlgorithm
    shadow: bool
    record_hash: Digest | None = None

    @model_validator(mode="after")
    def _expiry_follows_issuance(self) -> DecisionTokenRecord:
        """A token must have a positive lifetime.

        The TTL is the time-of-check/time-of-use window (``FR-20``). A token
        that expires at or before it was issued cannot be reasoned about: it is
        either already dead or the clocks disagree, and both are fail-closed
        conditions rather than a usable credential.
        """
        if self.expires_at <= self.issued_at:
            raise ValueError("token expires_at must be after issued_at (FR-20)")
        return self


class Approver(WireModel):
    """The human who decided an approval (``FR-42``)."""

    principal: Principal
    method: ApprovalMethod
    group: Annotated[str, StringConstraints(max_length=64)] | None = None

    @field_validator("principal")
    @classmethod
    def _approver_is_human(cls, value: Principal) -> Principal:
        """Only a human principal may approve (``FR-42``).

        A service or agent principal recorded as an approver would be the
        harness approving on the model's behalf, which is the authorization
        loop ``INV-01`` closes.
        """
        if not value.is_human:
            raise ValueError(
                f"approver {value!r} is not a human principal; "
                "a service or agent may not approve (FR-42)"
            )
        return value


class Approval(WireModel):
    """A human approval, bound to the proposal and bundle digests (``FR-40``).

    Binding to the *proposal* digest rather than the envelope digest is what
    makes re-evaluation safe: facts may be refetched after a human decides, and
    the approval survives that, while any change to what is being asked, by
    whom, or under which rules invalidates it.
    """

    approval_request_id: UUID
    proposal_digest: Digest
    policy_bundle_digest: Digest
    state: ApprovalState
    requested_at: AwareDatetime
    expires_at: AwareDatetime
    requesting_envelope_digest: Digest | None = None
    decided_at: AwareDatetime | None = None
    approver: Approver | None = None
    disclosure_digest: Digest | None = None
    resolved_decision_id: UUID | None = None
    superseded_by_decision_id: UUID | None = None
    void_reason_code: ReasonCodeField | None = None
    rejection_reason_code: ReasonCodeField | None = None

    @model_validator(mode="after")
    def _approved_is_fully_attributed(self) -> Approval:
        """An ``approved`` approval names who, when and on what basis.

        ``FR-43``: the disclosure digest pins the exact text the approver saw.
        Without it the record shows that someone approved *something*, which
        cannot be audited and cannot be replayed - and a consent whose content
        is unknown is not informed consent.
        """
        if self.state is not ApprovalState.APPROVED:
            return self
        missing = [
            field
            for field, present in (
                ("approver", self.approver is not None),
                ("decided_at", self.decided_at is not None),
                ("disclosure_digest", self.disclosure_digest is not None),
            )
            if not present
        ]
        if missing:
            raise ValueError(
                f"approved approval is missing {', '.join(missing)}; an approval "
                "must record who decided, when, and the disclosure they saw (FR-43)"
            )
        return self

    @property
    def is_open(self) -> bool:
        """True while the approval may still change state (``FR-40``)."""
        return self.state.is_open


class ExecutionLease(WireModel):
    """The per-``(tenant, resource_key)`` lease the broker held (``FR-25``)."""

    resource_key: ResourceKey | None = None
    acquired: bool | None = None
    lease_id: UUID | None = None
    expires_at: AwareDatetime | None = None


class ExecutionReceipt(WireModel):
    """What the broker actually did (``FR-24``)."""

    token_id: UUID
    envelope_digest: Digest
    broker_id: Annotated[str, StringConstraints(max_length=128)]
    started_at: AwareDatetime
    status: ReceiptStatus
    result_tagged_untrusted: bool = True
    connector: Annotated[str, StringConstraints(max_length=128)] | None = None
    connector_kind: ConnectorKind | None = None
    job_handle: StorageRef | None = None
    lease: ExecutionLease | None = None
    duplicate_delivery: bool | None = None
    finished_at: AwareDatetime | None = None
    refusal_reason_code: ReasonCodeField | None = None
    result_digest: Digest | None = None
    result_size_bytes: Annotated[int, Field(ge=0)] | None = None
    result_truncated: bool | None = None

    @field_validator("result_tagged_untrusted")
    @classmethod
    def _results_are_always_untrusted(cls, value: bool) -> bool:
        """The schema pins this to ``true`` and so does the model (``FR-63``).

        A tool result is attacker-influenced content returning into the
        governed model's context (``SEC-08``, threat T-12). The field is a
        constant rather than a flag because the day it can be ``false`` is the
        day some code path treats a tool result as trusted input.
        """
        if value is not True:
            raise ValueError(
                "tool results are always tagged untrusted; result_tagged_untrusted "
                "cannot be false (FR-63, SEC-08)"
            )
        return value

    @property
    def is_unresolved(self) -> bool:
        """True when the outcome is not yet known (``FR-27``).

        A retry proposed while this holds must ABSTAIN with
        ``RETRY_UNRESOLVED`` until a fresh state fact settles it: re-running an
        action whose first attempt may still land is how one request becomes two
        effects.
        """
        return self.status.is_unresolved


class Completion(WireModel):
    """Observed completion of an asynchronous action (``FR-26``).

    Sourced from the registered ``execution_completion`` fact rather than from
    the connector's word for it, so that completion is evidence on the same
    footing as any other fact.
    """

    job_handle: StorageRef
    status: CompletionStatus
    observed_at: AwareDatetime
    source: SourceName
    lease_released: bool | None = None
    result_digest: Digest | None = None


class EffectVerification(WireModel):
    """Post-execution comparison of effect against proposal (``FR-57``).

    This is the harness checking what actually happened against what was
    authorised, using a *fresh* state fact. It is the only place the record
    learns whether the world matched the decision.
    """

    critic_id: CriticId
    version: CriticVersion
    resource_key: ResourceKey
    result: EffectVerificationResult
    observed_at: AwareDatetime
    state_fact_digest: Digest | None = None
    counterexample: Counterexample | None = None
    reason_code: ReasonCodeField | None = None


class Override(WireModel):
    """An authenticated operator action. Never grants an ALLOW (``INV-11``)."""

    override_id: UUID
    kind: OverrideKind
    target: StorageRef
    authorized_by: Annotated[
        tuple[Principal, ...], Field(min_length=1, max_length=MAX_AUTHORIZING_PRINCIPALS)
    ]
    reason_code: ReasonCodeField
    effective_at: AwareDatetime
    ticket_ref: Annotated[str, StringConstraints(max_length=128)] | None = None
    expires_at: AwareDatetime | None = None
    superseded_by_registry_version: VersionString | None = None

    @model_validator(mode="after")
    def _loosening_needs_two_people_and_an_end_time(self) -> Override:
        """``demote_mode`` is never a single-principal, open-ended action.

        ``FR-48``/``SEC-14``. Every other override tightens, so one operator
        may issue it; ``demote_mode`` is the only one that reduces enforcement,
        which is why it needs two *distinct* principals, a ticket reference and
        an expiry. Distinctness is checked rather than assumed: two signatures
        from one principal is one person's decision wearing two hats.
        """
        if self.kind is not OverrideKind.DEMOTE_MODE:
            return self
        distinct = set(self.authorized_by)
        if len(distinct) < self.kind.required_principals:
            raise ValueError(
                f"demote_mode requires {self.kind.required_principals} distinct "
                f"authorizing principals, got {len(distinct)} (FR-48, SEC-14)"
            )
        missing = [
            field
            for field, present in (
                ("expires_at", self.expires_at is not None),
                ("ticket_ref", self.ticket_ref is not None),
            )
            if not present
        ]
        if missing:
            raise ValueError(
                f"demote_mode override is missing {', '.join(missing)}; a loosening "
                "override must auto-expire and cite a ticket (FR-48)"
            )
        if self.expires_at is None:  # pragma: no cover - narrowed by the check above
            # Not an ``assert``: ``python -O`` strips those, and a stripped
            # narrowing here would raise TypeError from the subtraction below
            # instead of the typed refusal FR-48 owes an operator.
            raise ValueError("demote_mode override has no expires_at (FR-48)")
        window = (self.expires_at - self.effective_at).total_seconds()
        if window <= 0:
            raise ValueError("override expires_at must be after effective_at (FR-48)")
        if window > MAX_DEMOTE_MODE_WINDOW_SECONDS:
            raise ValueError(
                f"demote_mode may not exceed {MAX_DEMOTE_MODE_WINDOW_SECONDS} seconds; "
                "a longer change is a signed registry change under two-person "
                "review, not an override (FR-48)"
            )
        return self

    @property
    def is_tightening(self) -> bool:
        """True for overrides that can only reduce what may execute."""
        return self.kind.is_tightening


class Checkpoint(WireModel):
    """A signed anchor over the tenant's chain (``SEC-10``).

    Hash chaining detects tampering only relative to something trusted; the
    checkpoint is that something, which is why it is signed and optionally
    anchored externally.
    """

    last_seq: Annotated[int, Field(ge=0)]
    last_record_hash: Digest
    key_id: KeyId
    signature: Annotated[str, StringConstraints(max_length=MAX_SIGNATURE_LENGTH)]
    anchor_ref: StorageRef | None = None


# --- The record envelope -----------------------------------------------------

#: The eight payload blocks, as one name. Spelled once so the accessor below and
#: :data:`RECORD_PAYLOAD_FIELDS` cannot come to disagree about what a record may
#: carry: a ninth kind added to one and not the other is the defect this alias
#: removes the room for.
RecordPayload: TypeAlias = (
    "Evaluation | DecisionTokenRecord | Approval | ExecutionReceipt "
    "| Completion | EffectVerification | Override | Checkpoint"
)

#: Which payload field each record kind carries.
RECORD_PAYLOAD_FIELDS: Final[dict[RecordKind, str]] = {
    RecordKind.EVALUATION: "evaluation",
    RecordKind.TOKEN_ISSUED: "token_issued",
    RecordKind.APPROVAL: "approval",
    RecordKind.EXECUTION_RECEIPT: "execution_receipt",
    RecordKind.COMPLETION: "completion",
    RecordKind.EFFECT_VERIFICATION: "effect_verification",
    RecordKind.OVERRIDE: "override",
    RecordKind.CHECKPOINT: "checkpoint",
}

#: Kinds that describe one action and must therefore link to it. Overrides and
#: checkpoints are operational events about the harness itself, not about a
#: single decision, so they carry no ``decision_id``/``action_id``.
RECORD_KINDS_REQUIRING_DECISION_LINK: Final[frozenset[RecordKind]] = frozenset(
    {
        RecordKind.EVALUATION,
        RecordKind.TOKEN_ISSUED,
        RecordKind.APPROVAL,
        RecordKind.EXECUTION_RECEIPT,
        RecordKind.COMPLETION,
        RecordKind.EFFECT_VERIFICATION,
    }
)


class DecisionRecord(WireModel):
    """One entry in a tenant's append-only, hash-chained evidence log.

    ``kind`` discriminates the payload: exactly one payload block is carried,
    and it is the one ``kind`` names. ``prev_record_hash`` links this record to
    its predecessor and is ``null`` only for the genesis record, so a deletion
    anywhere in the chain is detectable (``SEC-10``, ``INV-09``).
    """

    record_id: UUID
    tenant_id: TenantId
    seq: Annotated[int, Field(ge=0)]
    prev_record_hash: Digest | None
    record_hash: Digest
    kind: RecordKind
    timestamp: AwareDatetime
    trace_id: TraceId
    schema_version: str = SchemaCompatibility.written_version(SchemaKind.RECORD)
    decision_id: UUID | None = None
    action_id: UUID | None = None
    evaluation: Evaluation | None = None
    token_issued: DecisionTokenRecord | None = None
    approval: Approval | None = None
    execution_receipt: ExecutionReceipt | None = None
    completion: Completion | None = None
    effect_verification: EffectVerification | None = None
    override: Override | None = None
    checkpoint: Checkpoint | None = None

    @field_validator("schema_version")
    @classmethod
    def _readable_version(cls, value: str) -> str:
        """Refuse a record version this build does not fully understand.

        Raises :class:`~neuroharness.errors.SchemaVersionError` rather than a
        ``ValueError``: reading only the recognised half of an evidence record
        is how an evidence field goes missing without anyone noticing
        (Constitution Art. II).
        """
        SchemaCompatibility.assert_readable(SchemaKind.RECORD, value)
        return value

    @model_validator(mode="after")
    def _kind_matches_payload(self) -> DecisionRecord:
        """Carry exactly the payload the kind names, and the links it needs.

        The schema requires the matching block. Forbidding the others is the
        model's own addition: a record carrying two payloads has two readings,
        and evidence with two readings decides nothing (Constitution Art. IV).
        """
        expected_field = RECORD_PAYLOAD_FIELDS[self.kind]
        if getattr(self, expected_field) is None:
            raise ValueError(
                f"record of kind {self.kind.value!r} must carry a "
                f"{expected_field!r} payload (FR-70, FR-72)"
            )
        extra = [
            field
            for kind, field in RECORD_PAYLOAD_FIELDS.items()
            if kind is not self.kind and getattr(self, field) is not None
        ]
        if extra:
            raise ValueError(
                f"record of kind {self.kind.value!r} also carries "
                f"{', '.join(sorted(extra))}; a record describes one event"
            )
        if self.kind in RECORD_KINDS_REQUIRING_DECISION_LINK:
            unlinked = [
                field
                for field, present in (
                    ("decision_id", self.decision_id is not None),
                    ("action_id", self.action_id is not None),
                )
                if not present
            ]
            if unlinked:
                raise ValueError(
                    f"record of kind {self.kind.value!r} must link to its action via "
                    f"{', '.join(unlinked)} (FR-72)"
                )
        return self

    @model_serializer(mode="wrap")
    def _always_emit_prev_record_hash(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Keep ``prev_record_hash`` present even for the genesis record.

        The schema makes it required and nullable. Dropping it under
        ``exclude_none`` would make "first record in the chain" and "link
        field omitted" indistinguishable, which is precisely the ambiguity a
        hash chain exists to prevent (``SEC-10``).
        """
        data: dict[str, Any] = handler(self)
        data.setdefault("prev_record_hash", None)
        return data

    @property
    def payload(self) -> RecordPayload:
        """The one payload block this record carries."""
        field = RECORD_PAYLOAD_FIELDS[self.kind]
        block = getattr(self, field)
        if block is None:  # pragma: no cover - _kind_matches_payload guarantees it
            # Not an ``assert``: ``python -O`` strips those, and a stripped
            # guard on the payload accessor would return ``None`` into a
            # signature that promises a payload. A record whose kind and
            # payload disagree is unrecordable, not silently empty.
            raise ValueError(f"record of kind {self.kind.value} carries no {field} payload")
        return cast(RecordPayload, block)

    @property
    def is_genesis(self) -> bool:
        """True for the first record in a tenant's chain (``SEC-10``)."""
        return self.prev_record_hash is None
