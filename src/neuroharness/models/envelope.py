"""The action envelope: the canonical description of one proposed action.

The envelope is the single document every later artifact points back to. It has
exactly two halves and the split is the whole point:

* ``proposal`` is written by the governed model. It is **untrusted**. It selects
  *which* rules apply (by ``tool``/``intent``/``arguments``); it never satisfies
  one (``INV-01``, ``SEC-01``).
* ``context`` is written by the harness: identity, session tree, action class,
  facts, bundle and critic versions. It is the only half policy may rely on.

Two digests are taken over this document and the distinction matters
(specification section 2, ``FR-04``):

* the **proposal digest** covers *what is being asked, by whom, and under which
  rules*. The projection itself is
  :data:`neuroharness.canonical.digest.PROPOSAL_DIGEST_PROJECTION`, where every
  inclusion and every omission is justified field by field; ``ADR-0020``
  narrowed it from the wider projection ``ADR-0015`` first proposed. The field
  list is deliberately not restated here, because a second copy of the
  definition of what a human approval authorises is a second copy that can
  drift. Approvals bind to this digest, so a re-fetched fact does not silently
  invalidate a human decision.
* the **envelope digest** covers the whole object, facts and timestamps
  included - *one evaluation*. Decision tokens bind to it.

Neither digest is computed here: canonicalisation (RFC 8785) is a separate
concern with its own published test vectors. These models only guarantee that a
document which cannot be digested honestly cannot be constructed in the first
place.

Authoritative source: ``docs/sdd/schemas/action-envelope.schema.json``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import to_jsonable_python

from neuroharness import grammar
from neuroharness.defaults import MAX_REPAIR_BUDGET
from neuroharness.models.common import (
    Digest,
    EffectClass,
    FactStatus,
    FrozenJsonMapping,
    FrozenMappingSerializer,
    FrozenMappingValidator,
    Mode,
    Principal,
    RevalidatingModel,
)
from neuroharness.reason import ESCALATABLE_REASONS, ReasonName
from neuroharness.version import SchemaCompatibility, SchemaKind

__all__ = [
    "ABSENT",
    "Absent",
    "WireModel",
    "TenantId",
    "SessionId",
    "AgentId",
    "TraceId",
    "FactName",
    "ToolName",
    "ResourceKey",
    "SourceName",
    "VersionString",
    "ClaimValue",
    "FactKeyValue",
    "CriticVersionKey",
    "BatchPolicy",
    "CredentialStatus",
    "ConnectorKind",
    "Environment",
    "VersionedArtifact",
    "ModelIdentity",
    "Claim",
    "DelegationHop",
    "Actor",
    "ActionClassRef",
    "Fact",
    "BatchPosition",
    "Proposal",
    "Context",
    "ActionEnvelope",
    "MAX_CLAIMS",
    "MAX_BATCH_DEPENDENCIES",
    "MAX_FACTS",
    "MAX_DELEGATION_HOPS",
    "MAX_STRIPPED_KEYS",
    "MAX_BATCH_SIZE",
    "MAX_ESCALATE_ON",
]


# --- Presence sentinel -------------------------------------------------------


class Absent:
    """Marker for "this key is not present", as distinct from a JSON ``null``.

    ``FR-11`` turns the *presence* of :attr:`Fact.value` into a security
    property: a stale fact must arrive as a status-only stub so that a rule
    which forgot its freshness check has nothing to read. ``None`` cannot carry
    that meaning, because ``null`` is itself a legal fact value and because the
    wire form is emitted with ``exclude_none``. A dedicated sentinel keeps
    "absent" and "present and null" distinguishable end to end.
    """

    __slots__ = ()
    _instance: Absent | None = None

    def __new__(cls) -> Absent:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "ABSENT"


#: The single :class:`Absent` instance; compare with ``is``.
ABSENT: Final[Absent] = Absent()


# --- Base model --------------------------------------------------------------


class WireModel(RevalidatingModel):
    """Base for every wire model in this package.

    ``frozen`` because an envelope that can be edited after it is digested has
    no identity, and an identity that can drift is not evidence (Constitution
    Art. IV). ``extra="forbid"`` because a field the harness does not recognise
    is a field it cannot evaluate, and Art. II says an inability to evaluate
    ends in no execution rather than in a silent pass (``FR-02``, ``FR-03``).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        # `model_identity`, `model_id` and `model_version` are schema field
        # names, not pydantic namespace collisions.
        protected_namespaces=(),
    )


# --- Shared scalar and string types -----------------------------------------

TenantId = Annotated[str, StringConstraints(max_length=64)]
SessionId = Annotated[str, StringConstraints(max_length=128)]
AgentId = Annotated[str, StringConstraints(max_length=128)]
VersionString = Annotated[str, StringConstraints(max_length=64)]
SourceName = Annotated[str, StringConstraints(max_length=128)]

#: W3C trace id: 32 lowercase hex characters.
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]

#: Snake-case identifier used for fact names, intents and claim names.
FactName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]

#: Dotted snake-case tool name, e.g. ``deploy.service``.
ToolName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", max_length=128)
]

def _is_resource_key(value: str) -> str:
    """Validate against ``grammar.RESOURCE_KEY_PATTERN`` itself (``ADR-0023``).

    A validator rather than ``StringConstraints(pattern=...)`` because pydantic
    v2 compiles string patterns with the Rust ``regex`` crate, which has no
    look-around, and the grammar's length guard is a look-ahead. Handing it the
    source would fail at class construction, and the tempting repair there is to
    strip the guard - which is how a sixth spelling of the grammar gets written.
    Running the compiled pattern means the model and the registry share one
    object and cannot drift by so much as a character.
    """
    if grammar.RESOURCE_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{value!r} is not a well-formed resource key: expected "
            "kind:id(/kind:id)* per the resource-key registry (FR-34, ADR-0023)"
        )
    return value


#: Canonical resource identifier from the resource-key registry (``FR-34``),
#: e.g. ``service:example-api/target:production``. Leases and mutual-exclusion
#: properties key on it, so its shape is fixed rather than free-form.
#:
#: This alias was the fifth source of the resource-key grammar, and the one the
#: "four sources become one" repair missed. It admitted ``_`` in a kind segment,
#: refused ``-`` there, let an identifier begin with ``-`` or ``.``, and bounded
#: the whole key at 256 rather than 158 - so it disagreed with the signed
#: registry in both directions. ``models/record.py`` imports it, so that
#: disagreement reached ``ExecutionLease.resource_key`` (``FR-25``'s lease
#: identity) and the required ``EffectVerification.resource_key``: a lease on
#: ``cluster-prod:svc-a`` could be taken and never written down, which
#: Constitution Art. III says is a decision that was not made.
ResourceKey = Annotated[
    str,
    StringConstraints(max_length=grammar.MAX_RESOURCE_KEY_LENGTH),
    AfterValidator(_is_resource_key),
    # `AfterValidator` has no JSON Schema representation, so without this the
    # generated schema for this alias is `{"type": "string", "maxLength": 158}`
    # -- it would accept `s3:bucket_name` while the model refuses it. Nothing in
    # the tree calls `model_json_schema()` today, so the regression is latent
    # rather than live; it is restored anyway, because a model whose
    # self-description is wider than its behaviour is the same defect this alias
    # was just repaired for, one layer out. The pattern is ECMA-262 (the
    # look-ahead included, per `grammar.py`), which is what JSON Schema reads.
    WithJsonSchema(
        {
            "type": "string",
            "pattern": grammar.RESOURCE_KEY_PATTERN.pattern,
            "maxLength": grammar.MAX_RESOURCE_KEY_LENGTH,
        }
    ),
]

#: Fact key components: bounded scalars only. A nested object here would be an
#: unbounded, un-enumerable policy input.
FactKeyValue = bool | int | float | Annotated[str, StringConstraints(max_length=256)]

#: Critic identifier used as a key in ``context.critic_versions``.
CriticVersionKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]*$")]

#: Claim values are bounded strings; a claim is recorded, never evaluated
#: (``FR-12``), but it still may not be an unbounded ingestion channel.
ClaimValue = bool | int | float | Annotated[str, StringConstraints(max_length=512)] | None


# --- Bounds declared by the schema ------------------------------------------

MAX_CLAIMS: Final[int] = 32
MAX_BATCH_DEPENDENCIES: Final[int] = 32
MAX_FACTS: Final[int] = 64
MAX_DELEGATION_HOPS: Final[int] = 16
MAX_STRIPPED_KEYS: Final[int] = 32
MAX_BATCH_SIZE: Final[int] = 64
MAX_ESCALATE_ON: Final[int] = 8
MAX_STRIPPED_KEY_LENGTH: Final[int] = 64


# --- Enumerations local to the envelope --------------------------------------


class BatchPolicy(str, Enum):
    """How a batch's members relate to one another (``FR-07``)."""

    INDEPENDENT = "independent"
    ALL_OR_NOTHING = "all_or_nothing"


class CredentialStatus(str, Enum):
    """Result of validating one delegation hop's credential (``SEC-13``)."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"

    @property
    def is_verified(self) -> bool:
        """Only a verified hop may satisfy an authorization rule (``SEC-13``).

        An expired credential is *not* a weaker verified one: it is an
        unproven claim of delegation, and treating it as authority is exactly
        the confused-deputy path the delegation chain exists to close.
        """
        return self is CredentialStatus.VERIFIED


class ConnectorKind(str, Enum):
    """Whether the broker's connector completes inline (``FR-26``)."""

    SYNC = "sync"
    ASYNC = "async"


class Environment(str, Enum):
    """Deployment environment the action would touch."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


# --- Shared structures -------------------------------------------------------


class VersionedArtifact(WireModel):
    """A signed artifact referenced by version *and* digest.

    Both are recorded because the version is what humans review and the digest
    is what replay binds to (``FR-71``, ``INV-09``); a version alone can be
    re-pointed at different bytes.
    """

    version: VersionString
    digest: Digest


class ModelIdentity(WireModel):
    """Identity of the governed model that authored the proposal.

    Recorded for monitor-certification matching (``FR-54``) and per-model
    metrics. It is provenance, never authority: nothing about the model may
    move a verdict toward ALLOW (``INV-01``).
    """

    model_id: Annotated[str, StringConstraints(max_length=128)] | None = None
    model_version: VersionString | None = None
    prompt_family_digest: Digest | None = None


# --- Proposal (agent-authored, untrusted) ------------------------------------


class Claim(WireModel):
    """A statement made by the agent, e.g. "CI passed".

    Claims are recorded in the decision record and are *never* placed in the
    policy input document (``FR-12``, ``INV-08``). They exist so that an
    auditor can see what the model asserted next to what the harness verified.
    """

    name: FactName
    value: ClaimValue

    @model_serializer(mode="wrap")
    def _always_emit_value(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Keep ``value`` present even when it is ``null``.

        The schema makes ``value`` required and explicitly nullable. Callers
        dump with ``exclude_none=True`` (absent optional keys are how the
        conditional fact rules are expressed), which would otherwise delete a
        legitimately null claim and produce a document the schema rejects.
        """
        data: dict[str, Any] = handler(self)
        data.setdefault("value", None)
        return data


class Proposal(WireModel):
    """The agent-authored half of the envelope. Untrusted by construction.

    ``extra="forbid"`` is load-bearing here: ``FR-03`` requires that
    context-shaped keys in the raw payload are stripped before validation and
    listed in :attr:`Context.stripped_proposal_keys`. A proposal that still
    carries an ``actor`` or ``context`` key has not been through that step, and
    accepting it would let the model nominate its own identity.
    """

    tool: ToolName
    intent: FactName
    #: Read-only: the proposal digest is taken over these arguments, so a
    #: mapping the caller could still edit afterwards would give one proposal
    #: two identities (``FR-04``, ``ADR-0020``).
    arguments: FrozenJsonMapping
    claims: Annotated[tuple[Claim, ...], Field(max_length=MAX_CLAIMS)] | None = None
    batch_dependencies: (
        Annotated[
            tuple[Annotated[int, Field(ge=0)], ...],
            Field(max_length=MAX_BATCH_DEPENDENCIES),
        ]
        | None
    ) = None


# --- Context (harness-authored) ---------------------------------------------


class DelegationHop(WireModel):
    """One link in the chain from a human principal to the acting agent.

    Every hop is backed by a verifiable delegation credential (``SEC-13``).
    The credential's validation *result* travels with the hop precisely so that
    an unverified hop is visible to policy rather than indistinguishable from a
    proven one; see :attr:`Context.human_principals`.
    """

    principal: Principal
    credential_status: CredentialStatus
    credential_digest: Digest | None = None

    @property
    def is_authoritative(self) -> bool:
        """True only for a verified hop (``SEC-13``)."""
        return self.credential_status.is_verified

    @property
    def is_verified_human(self) -> bool:
        """A human principal whose delegation credential actually verified.

        Both halves are required. A human identifier on an unverified hop is a
        claim about delegation, not evidence of it, and an authorization rule
        that counted it would be satisfiable by anyone who could assert a name.
        """
        return self.is_authoritative and self.principal.is_human


class Actor(WireModel):
    """Who is acting, resolved by the identity layer - never by the proposal."""

    agent_id: AgentId
    agent_version: VersionString
    principal: Principal
    delegation_chain: Annotated[
        tuple[DelegationHop, ...], Field(min_length=1, max_length=MAX_DELEGATION_HOPS)
    ]
    environment: Environment
    model_identity: ModelIdentity | None = None

    @property
    def chain_principals(self) -> tuple[Principal, ...]:
        """Every principal in the delegation chain, in order.

        ``SEC-11`` (fact laundering) forbids evidence asserted by a principal in
        this set, and ``FR-48`` forbids an override authorised by one. Both
        checks need the chain flattened, verified or not: an *unverified* hop
        confers no authority but still taints evidence it asserted.
        """
        return tuple(hop.principal for hop in self.delegation_chain)


class ActionClassRef(WireModel):
    """The registry's view of this ``(tool, intent)`` class at evaluation time."""

    registered: bool
    mode: Mode
    effect_class: EffectClass
    resource_key: ResourceKey | None = None
    repair_budget: Annotated[int, Field(ge=0, le=MAX_REPAIR_BUDGET)] | None = None
    approvable: bool | None = None
    escalate_on: (
        Annotated[tuple[ReasonName, ...], Field(max_length=MAX_ESCALATE_ON)] | None
    ) = None
    connector_kind: ConnectorKind | None = None

    @field_validator("escalate_on")
    @classmethod
    def _only_escalatable_reasons(
        cls, value: tuple[ReasonName, ...] | None
    ) -> tuple[ReasonName, ...] | None:
        """Restrict ``escalate_on`` to the fixed escalatable set (section 5.5).

        Infrastructure abstentions are deliberately *not* escalatable: a human
        cannot vouch for a policy engine that is down or a bundle whose
        signature does not verify, so offering them an approval button would
        manufacture consent for an unevaluated action.
        """
        if value is None:
            return None
        for reason in value:
            if reason not in ESCALATABLE_REASONS:
                raise ValueError(
                    f"{reason.value} may not be escalated to human approval; "
                    f"escalatable reasons are "
                    f"{sorted(name.value for name in ESCALATABLE_REASONS)}"
                )
        return value


class Fact(WireModel):
    """A statement obtained by the harness from a registered provider.

    The only kind of statement policy may read (``FR-10``, ``INV-08``). The
    conditional value rules below are ``FR-11`` in model form.
    """

    name: FactName
    #: Read-only: the fact key is part of the envelope digest and of the
    #: provider cache identity; editing it after the fact was fetched would
    #: relabel someone else's answer as this fact's (``FR-10``, ``INV-08``).
    key: Annotated[
        Mapping[str, FactKeyValue], FrozenMappingValidator, FrozenMappingSerializer
    ]
    source: SourceName
    provider_version: VersionString
    status: FactStatus
    value: Any = Field(default=ABSENT, exclude=True)
    asserted_by: Principal | None = None
    observed_at: AwareDatetime | None = None
    fetched_at: AwareDatetime | None = None
    ttl_seconds: Annotated[int, Field(ge=0)] | None = None
    max_age_seconds: Annotated[int, Field(ge=0)] | None = None
    digest: Digest | None = None

    @model_validator(mode="after")
    def _enforce_freshness_contract(self) -> Fact:
        """A fresh fact is complete; a non-fresh fact is a status-only stub.

        ``FR-11``. The second half is the security-relevant one: stale, missing
        and errored facts reach the PDP and the critics with no ``value`` at
        all, so a rule whose author forgot the freshness check cannot read a
        stale value and reach a conclusion the world no longer supports.
        """
        if self.status is FactStatus.FRESH:
            missing = [
                field
                for field, present in (
                    ("value", self.value is not ABSENT),
                    ("fetched_at", self.fetched_at is not None),
                    ("ttl_seconds", self.ttl_seconds is not None),
                    ("digest", self.digest is not None),
                )
                if not present
            ]
            if missing:
                raise ValueError(
                    f"fresh fact {self.name!r} is missing {', '.join(missing)}; "
                    "a fact policy may read must carry its provenance (FR-10, FR-11)"
                )
        elif self.value is not ABSENT:
            raise ValueError(
                f"fact {self.name!r} has status {self.status.value} and must be a "
                "status-only stub with no value (FR-11)"
            )
        return self

    @model_serializer(mode="wrap")
    def _emit_value_only_when_present(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Serialise ``value`` if and only if it is present.

        The field is excluded from ordinary serialisation and re-added here so
        that a present-but-null value survives ``exclude_none`` while an absent
        one stays absent - the exact distinction ``FR-11`` rests on.
        """
        data: dict[str, Any] = handler(self)
        if self.value is not ABSENT:
            data["value"] = to_jsonable_python(self.value)
        return data

    @property
    def has_value(self) -> bool:
        """True when a ``value`` key is present (fresh facts only)."""
        return self.value is not ABSENT

    @property
    def is_usable(self) -> bool:
        """True when a rule may read this fact's value.

        Both conditions are checked rather than trusting the status alone: the
        status is what the freshness computation concluded, the presence of a
        value is what the document actually carries, and a gate is worth more
        when it does not depend on an earlier gate having run.
        """
        return self.status.is_usable and self.has_value


class BatchPosition(WireModel):
    """Where this call sits in a batched tool call (``FR-07``)."""

    batch_id: UUID
    batch_index: Annotated[int, Field(ge=0)]
    batch_size: Annotated[int, Field(ge=1, le=MAX_BATCH_SIZE)]
    batch_policy: BatchPolicy


class Context(WireModel):
    """The harness-authored half of the envelope.

    Everything policy is allowed to rely on lives here. ``session_id`` is
    derived server-side from the authenticated agent-host connection and is
    never supplied by the agent (``FR-06``, ``SEC-12``).
    """

    tenant_id: TenantId
    session_id: SessionId
    session_root_id: SessionId
    trace_id: TraceId
    proposed_at: AwareDatetime
    repair_iteration: Annotated[int, Field(ge=0, le=MAX_REPAIR_BUDGET)]
    actor: Actor
    action_class: ActionClassRef
    facts: Annotated[tuple[Fact, ...], Field(max_length=MAX_FACTS)]
    policy_bundle: VersionedArtifact
    registry: VersionedArtifact
    #: Read-only: these versions are the record of *which* critics produced the
    #: verdict, so an edit after evaluation rewrites the evidence (``FR-70``).
    critic_versions: Annotated[
        Mapping[CriticVersionKey, VersionString],
        FrozenMappingValidator,
        FrozenMappingSerializer,
    ]
    parent_session_id: SessionId | None = None
    prior_decision_id: UUID | None = None
    batch: BatchPosition | None = None
    stripped_proposal_keys: (
        Annotated[
            tuple[Annotated[str, StringConstraints(max_length=MAX_STRIPPED_KEY_LENGTH)], ...],
            Field(max_length=MAX_STRIPPED_KEYS),
        ]
        | None
    ) = None

    @property
    def human_principals(self) -> tuple[Principal, ...]:
        """Human principals whose delegation credential verified (``SEC-13``).

        This is the set an authorization or separation-of-duties rule may draw
        on. A human identifier carried on an ``unverified`` or ``expired`` hop
        is deliberately *not* in it: the chain records who was claimed, and the
        credential status records who was proven, and only the second confers
        authority.
        """
        return tuple(
            hop.principal for hop in self.actor.delegation_chain if hop.is_verified_human
        )

    @property
    def has_unverified_hop(self) -> bool:
        """True when any hop failed credential validation (``SEC-13``).

        A single unproven link breaks the chain of custody for the whole
        delegation, so this is reported for the chain rather than per hop.
        """
        return any(not hop.is_authoritative for hop in self.actor.delegation_chain)

    @property
    def usable_facts(self) -> tuple[Fact, ...]:
        """Facts whose value a rule may read (``FR-11``)."""
        return tuple(fact for fact in self.facts if fact.is_usable)

    def fact(self, name: str) -> Fact | None:
        """Return the fact with ``name``, usable or not.

        Non-fresh facts are returned rather than hidden: a caller must be able
        to tell "stale" from "never requested", because those abstain for
        different reasons (``FACT_STALE`` vs ``FACT_MISSING``).
        """
        for candidate in self.facts:
            if candidate.name == name:
                return candidate
        return None


class ActionEnvelope(WireModel):
    """One proposed agent action, in the form every later artifact points at."""

    action_id: UUID
    proposal: Proposal
    context: Context
    schema_version: str = Field(
        default_factory=lambda: SchemaCompatibility.written_version(SchemaKind.ENVELOPE)
    )

    @field_validator("schema_version")
    @classmethod
    def _readable_version(cls, value: str) -> str:
        """Refuse a version this build does not fully understand.

        Raises :class:`~neuroharness.errors.SchemaVersionError`, not a
        ``ValueError``, because this is a fail-closed decision outcome carrying
        the reason code ``SCHEMA_INVALID`` rather than an input typo
        (Constitution Art. II).
        """
        SchemaCompatibility.assert_readable(SchemaKind.ENVELOPE, value)
        return value

    @property
    def action_class_key(self) -> tuple[str, str]:
        """The registry key for this action: ``(tool, intent)``.

        Taken from the proposal because ``(tool, intent)`` is what *selects*
        the action class. Selecting rules is all the proposal is ever permitted
        to do; the class's mode, effect class and approvability come from the
        registry through :attr:`Context.action_class` (``INV-01``).
        """
        return (self.proposal.tool, self.proposal.intent)

    @property
    def is_registered(self) -> bool:
        """True when the registry recognised this action class (``FR-30``)."""
        return self.context.action_class.registered
