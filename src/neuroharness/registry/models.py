"""The action-class registry data model (``FR-30``--``FR-35``).

This module is what makes "no hardcoded values" structural. Every policy knob
the decision path reads at runtime -- the enforcement mode of a class, whether a
human may approve it, how many repair iterations it gets, how long its token
lives, which facts it needs and how fresh, which critics run and which
natural-language requirement each one encodes -- is a field here, loaded from a
signed document. Nothing in the resolver, the token service or the broker may
branch on a literal that could have been one of these fields.

The models are frozen and ``extra="forbid"``. Frozen because a registry entry
that can be mutated after load is a registry entry whose signature proves
nothing; ``extra="forbid"`` because a key the loader does not understand is a
policy the operator believes is in force and is not (Constitution Art. II).

Validation lives on the models rather than only in the loader, so that an
``ActionClass`` cannot exist in an inconsistent state anywhere in the process --
including in a test fixture assembled by hand. The loader
(:mod:`neuroharness.registry.loader`) re-raises these failures as
:class:`neuroharness.errors.RegistryValidationError` naming the offending class.

``ActionClass`` deliberately exposes ``mode``, ``approvable``, ``escalate_on``
and ``repair_budget`` with exactly the names and types the resolver's
``ClassPolicy`` protocol expects, so the two subsystems share a shape without
sharing an import.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Collection, Final, Iterator, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from neuroharness import defaults
from neuroharness.config import UnregisteredClassPolicy
from neuroharness.errors import UnregisteredActionClassError
from neuroharness.models.common import (
    CriticKind,
    Digest,
    EffectClass,
    FrozenJsonMapping,
    Mode,
)
from neuroharness.reason import ESCALATABLE_REASONS, ReasonName
from neuroharness.registry.resource_keys import (
    ResourceKeyRegistry,
    render_template,
    template_placeholders,
)
from neuroharness.version import SchemaCompatibility, SchemaKind

__all__ = [
    "ConnectorKind",
    "BatchPolicy",
    "FactRequirement",
    "CriticRef",
    "ActionClass",
    "ActionClassRegistry",
    "ClassKey",
    "FACT_ESCALATION_REASONS",
]

#: The identity of an action class: ``(tool, intent)`` (``FR-30``).
ClassKey = tuple[str, str]

#: Escalation reasons whose eligibility depends on a *fact* being marked
#: escalatable (section 5.5), as opposed to a solver outcome.
FACT_ESCALATION_REASONS: Final[frozenset[ReasonName]] = frozenset(
    {ReasonName.FACT_MISSING, ReasonName.FACT_STALE}
)

#: Identifier shape shared by tools, intents, critic ids and fact names. Narrow
#: on purpose: these tokens are emitted as reason-code subjects (section 5.6),
#: which never carry free text (``SEC-07``).
_IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"

#: ``source_requirement`` may name a ``WF-`` id or a policy document, so it
#: allows ``/`` and ``#`` in addition to the identifier charset (``FR-32``).
_SOURCE_REQUIREMENT_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._/#-]{0,127}$"


class ConnectorKind(str, Enum):
    """Whether the tool returns its outcome inline or out of band (``FR-26``).

    An ``async`` connector's receipt arrives after dispatch, which is why the
    broker holds the lease past the call and why a retry of an unresolved
    dispatch must abstain (``FR-27``).
    """

    SYNC = "sync"
    ASYNC = "async"


class BatchPolicy(str, Enum):
    """How members of a batched call relate to one another (``FR-07``)."""

    INDEPENDENT = "independent"
    ALL_OR_NOTHING = "all_or_nothing"


class FactRequirement(BaseModel):
    """A fact the class needs, and how fresh it must be (``FR-30``, ``FR-11``).

    ``key`` names the *arguments* that identify the fact instance, so a CI
    result for one ``(service, version)`` can never be read as evidence for
    another. ``max_age_seconds`` is per fact rather than global because the
    useful lifetime of a CI result and of an in-flight deployment flag differ by
    an order of magnitude.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    key: tuple[str, ...] = Field(min_length=1)
    max_age_seconds: int = Field(ge=0)
    required: bool = True
    escalatable: bool = False

    @model_validator(mode="after")
    def _check_escalation(self) -> "FactRequirement":
        """Rule (j): a required fact may never be escalatable (section 5.5).

        Section 5.5 permits ``FACT_MISSING``/``FACT_STALE`` escalation only for
        facts the class marks ``escalatable``, and says "never for evidence
        facts marked ``required: true``". The reason is substantive, not
        bookkeeping: a required fact *is* the evidence the gate exists to check.
        Letting a human wave through its absence converts the gate into a
        formality, which is precisely the failure mode ``WF-04`` (CI evidence)
        and ``WF-05`` (change record) are there to prevent.
        """
        if self.required and self.escalatable:
            raise ValueError(
                f"fact {self.name!r} is both required and escalatable; a required fact "
                "is the evidence the gate checks, so its absence may never be escalated "
                "to a human (section 5.5)"
            )
        return self

    @model_validator(mode="after")
    def _check_key_arguments(self) -> "FactRequirement":
        """Key components must be argument names, not free strings (``FR-14``)."""
        if len(set(self.key)) != len(self.key):
            raise ValueError(f"fact {self.name!r} has a duplicated key component")
        return self


class CriticRef(BaseModel):
    """One critic the class runs, and the requirement it encodes (``FR-32``).

    ``source_requirement`` is mandatory. Constitution Article V asks of every
    critic: "where is the source requirement it encodes?". A critic that cannot
    answer is an unreviewable gate -- nobody can say whether it still matches the
    rule a human wrote, so nobody can say whether changing it is safe.

    ``timeout_ms`` is a wall-clock backstop only. For a solver the primary bound
    is ``rlimit``, because a deterministic resource limit replays identically
    and a wall clock does not (Constitution Art. VIII).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: CriticKind
    hard: bool
    mode: Mode
    timeout_ms: int = Field(default=defaults.DEFAULT_CRITIC_TIMEOUT_MS, ge=1)

    #: Rule (e): mandatory, and an identifier rather than prose. The pattern is
    #: the enforcement -- it has no default, rejects the empty and whitespace-only
    #: string, and rejects a sentence -- because a critic that cannot name the
    #: requirement it encodes is an unreviewable gate (``FR-32``, Art. V), and a
    #: value quoted into a decision record may never be free text (``SEC-07``).
    source_requirement: str = Field(pattern=_SOURCE_REQUIREMENT_PATTERN, min_length=1)

    rlimit: int | None = Field(default=None, ge=1)
    properties: tuple[str, ...] = ()
    certified_model: str | None = None


class ActionClass(BaseModel):
    """Everything policy knows about one ``(tool, intent)`` pair (``FR-30``).

    Structurally satisfies the resolver's ``ClassPolicy`` protocol: it exposes
    ``mode: Mode``, ``approvable: bool``, ``escalate_on: frozenset[ReasonName]``
    and ``repair_budget: int`` under exactly those names, so the resolver can
    consume a registry entry without importing this module.

    ``mode`` has no default. Every other knob has a documented default in
    :mod:`neuroharness.defaults`, but a class whose enforcement posture was
    never stated is exactly the implicit value this subsystem exists to
    eliminate: the operator must write it down.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- identity -------------------------------------------------------------
    tool: str = Field(pattern=_IDENTIFIER_PATTERN)
    intent: str = Field(pattern=_IDENTIFIER_PATTERN)
    effect_class: EffectClass

    # -- contract -------------------------------------------------------------
    #: Read-only: the registry is signed and its digest covers this schema, so a
    #: schema that can be mutated after load is a schema whose signature proves
    #: nothing (``FR-31``, ``FR-32``).
    argument_schema: FrozenJsonMapping
    resource_key_template: str | None = None
    connector_kind: ConnectorKind = ConnectorKind.SYNC
    lease_timeout_seconds: int = Field(default=defaults.DEFAULT_LEASE_TIMEOUT_SECONDS, ge=1)

    # -- evaluation -----------------------------------------------------------
    required_facts: tuple[FactRequirement, ...] = ()
    critics: tuple[CriticRef, ...] = ()

    # -- policy (the ClassPolicy shape) ---------------------------------------
    mode: Mode
    approvable: bool = False
    escalate_on: frozenset[ReasonName] = frozenset()
    repair_budget: int = Field(default=defaults.DEFAULT_REPAIR_BUDGET, ge=0, le=defaults.MAX_REPAIR_BUDGET)

    # -- approval and execution -----------------------------------------------
    approver_groups: tuple[str, ...] = ()
    batch_policy: BatchPolicy = BatchPolicy.INDEPENDENT
    token_ttl_seconds: int = Field(default=defaults.DEFAULT_TOKEN_TTL_SECONDS, ge=1)
    approval_ttl_seconds: int = Field(default=defaults.DEFAULT_APPROVAL_TTL_SECONDS, ge=1)

    # -- validation -----------------------------------------------------------

    @model_validator(mode="after")
    def _check_escalation_reasons(self) -> "ActionClass":
        """Rule (a): ``escalate_on`` is a subset of ``ESCALATABLE_REASONS``.

        Section 5.5 fixes the escalatable set to ``SOLVER_UNKNOWN``,
        ``SOLVER_TIMEOUT``, ``FACT_MISSING`` and ``FACT_STALE``. The complement
        is the infrastructure set, and it is non-escalating by construction: a
        human cannot vouch for a policy engine that is down or a bundle whose
        signature does not verify. Allowing ``POLICY_ENGINE_UNAVAILABLE`` to
        escalate would turn every outage into an approval queue that grants
        exactly what the outage prevented anyone from checking.
        """
        illegal = sorted(r.value for r in self.escalate_on - ESCALATABLE_REASONS)
        if illegal:
            raise ValueError(
                f"escalate_on lists non-escalatable reason(s) {illegal}; only "
                f"{sorted(r.value for r in ESCALATABLE_REASONS)} may escalate (section 5.5)"
            )
        return self

    @model_validator(mode="after")
    def _check_escalation_requires_approvability(self) -> "ActionClass":
        """Rule (b): ``escalate_on`` must be empty on a non-approvable class.

        ``FR-33`` names this as the loader's job. Escalation means "turn this
        ABSTAIN into REQUIRES_APPROVAL"; on a class no human may approve, the
        escalation is unreachable and the declaration reads as oversight that
        does not exist. Silent no-ops in a policy document are how a control is
        believed to be in force for a year before anyone checks.
        """
        if self.escalate_on and not self.approvable:
            raise ValueError(
                "escalate_on is set on a class that is not approvable; an abstention "
                "cannot escalate to an approval nobody is permitted to give (FR-33)"
            )
        return self

    @model_validator(mode="after")
    def _check_escalatable_facts_exist(self) -> "ActionClass":
        """Rule (b2): fact escalation needs at least one escalatable fact.

        Section 5.5 allows ``FACT_MISSING``/``FACT_STALE`` escalation only for
        facts the class marks ``escalatable: true``. Declaring the reason with
        no such fact is inert, and an inert declaration is indistinguishable
        from a working one until the day it matters (``FR-33``).
        """
        declared = self.escalate_on & FACT_ESCALATION_REASONS
        if declared and not any(fact.escalatable for fact in self.required_facts):
            raise ValueError(
                f"escalate_on lists {sorted(r.value for r in declared)} but no required "
                "fact is marked escalatable, so the escalation can never apply "
                "(section 5.5)"
            )
        return self

    @model_validator(mode="after")
    def _check_critic_modes(self) -> "ActionClass":
        """Rule (c): ``halted`` is a class-level lever, never a critic's mode.

        The class mode is **not** a ceiling on critic modes. Section 5.3 step 0
        demotes a hard critic on the critic's *own* declared mode, so an
        ``enforce`` critic inside a ``shadow`` class is not a contradiction: it
        is the configuration a shadow rollout exists to run. The critic blocks
        in the verdict, the record carries the ``DENY`` the rollout is there to
        measure, and the broker -- reading the *class* mode -- executes anyway
        (``INV-11``, scenario ``A-16``, fixture ``MUT-16``). Refusing that
        combination made the rollout unrepresentable and left shadow data
        describing a program that always allowed.

        Dropping the comparison gives nothing away. A critic cannot raise
        enforcement past what the rollout approved, because enforcement is
        decided at the broker on the class mode alone, downstream of every
        critic; the demotion lever an operator reaches for during a false-block
        spike is that class mode, and it still works with enforcing critics
        underneath it (``FR-80``, ADR-0016).

        What survives is ``halted``. It is the incident lever and it belongs to
        a class: a halted class denies at step 0 before any critic is consulted
        (``FR-49``). On a critic the word is not merely inert, it is inverted --
        :attr:`CriticOutcome.counts_as_hard` demotes every mode that is not
        ``enforce``, so a critic declared ``halted`` would quietly stop blocking.
        The strongest word in the vocabulary would turn the gate off, and a
        silent no-op in a signed policy document is how a control is believed to
        be in force for a year before anyone checks (``FR-33``).
        """
        for critic in self.critics:
            if critic.mode is Mode.HALTED:
                raise ValueError(
                    f"critic {critic.id!r} declares mode {Mode.HALTED.value!r}, which is a "
                    "class-level incident lever rather than a critic rollout mode; such a "
                    "critic would be demoted to soft and stop blocking entirely "
                    "(FR-33, FR-49)"
                )
        return self

    @model_validator(mode="after")
    def _check_effect_class_policy(self) -> "ActionClass":
        """Rules (f) and (g): effect class constrains approvability (``FR-35``).

        (f) ``none``/``read`` classes take the policy-only fast path. They are
        never approvable, so an ``approver_groups`` list on one is a control
        that will never be exercised -- and worse, it suggests to a reviewer that
        reads are overseen when they are not. Reading a tool result is not an
        action.

        (g) ``destructive`` classes must be approvable or halted. A destructive
        action with no human in the loop and no halt is an unrecoverable effect
        that the harness would grant on policy alone; ``FR-35`` requires the
        registry to close that door at load time rather than at the first
        incident.
        """
        if self.effect_class.takes_fast_path:
            if self.approvable:
                raise ValueError(
                    f"effect_class {self.effect_class.value!r} is never approvable; "
                    "reading a tool result is not an action (FR-35)"
                )
            if self.approver_groups:
                raise ValueError(
                    f"effect_class {self.effect_class.value!r} declares approver_groups "
                    f"{list(self.approver_groups)}, but such a class is never approvable "
                    "and the groups would never be consulted (FR-35)"
                )
        if self.effect_class is EffectClass.DESTRUCTIVE and not (self.approvable or self.is_halted):
            raise ValueError(
                "effect_class 'destructive' must be approvable or halted; an "
                "unrecoverable effect may not be granted on policy alone (FR-35)"
            )
        return self

    @model_validator(mode="after")
    def _check_approvability_consistency(self) -> "ActionClass":
        """An approvable class names who may approve it (``FR-42``).

        Approval is only a control if the approver set is bounded and recorded.
        "Anyone may approve" is not a policy, it is the absence of one.
        """
        if self.approvable and not self.approver_groups:
            raise ValueError(
                "an approvable class must name at least one approver group; an unbounded "
                "approver set is not an oversight control (FR-42)"
            )
        return self

    @model_validator(mode="after")
    def _check_argument_schema(self) -> "ActionClass":
        """Rule (h): the argument schema must be closed (``FR-02``).

        ``FR-02`` requires ``additionalProperties: false``. An open schema means
        an argument nobody declared travels to the tool unvalidated and
        unexamined by policy, while the decision record shows a clean
        validation. That is the classic smuggling channel (scenario ``A-35``,
        mutation ``MUT-30``), and it is the one place where being permissive
        costs the harness its entire contract.
        """
        schema = self.argument_schema
        if not isinstance(schema, Mapping) or not schema:
            raise ValueError("argument_schema must be a non-empty JSON Schema object (FR-02)")
        if schema.get("type") != "object":
            raise ValueError(
                f"argument_schema declares type {schema.get('type')!r}; an action class's "
                "arguments are a JSON object (FR-02)"
            )
        if schema.get("additionalProperties") is not False:
            raise ValueError(
                "argument_schema must set additionalProperties to false; an open schema "
                "lets an undeclared argument reach the tool unvalidated (FR-02)"
            )
        return self

    @model_validator(mode="after")
    def _check_resource_key_template(self) -> "ActionClass":
        """Rule (k): a template may only reference declared arguments (``FR-34``).

        The rendered key is the broker's lease identity (``FR-25``). A
        placeholder naming an argument the schema does not declare can never be
        filled, so the lease would either fail at dispatch -- after the decision
        was recorded ALLOW -- or, worse, render to a degenerate key shared by
        every call of the class, serialising nothing.
        """
        if self.resource_key_template is None:
            return self
        placeholders = template_placeholders(self.resource_key_template)
        if not placeholders:
            raise ValueError(
                f"resource_key_template {self.resource_key_template!r} has no placeholders; "
                "a constant key would serialise every call of the class against itself (FR-25)"
            )
        declared = self.argument_schema.get("properties")
        if isinstance(declared, Mapping):
            undeclared = sorted(name for name in placeholders if name not in declared)
            if undeclared:
                raise ValueError(
                    f"resource_key_template {self.resource_key_template!r} references "
                    f"undeclared argument(s) {undeclared}; a resource key is built from "
                    "typed, enumerated arguments, not free strings (FR-34)"
                )
        return self

    @model_validator(mode="after")
    def _check_unique_names(self) -> "ActionClass":
        """Critic ids and fact names are reason-code subjects, so they must be unique.

        ``SOLVER_TIMEOUT:<critic_id>`` and ``FACT_STALE:<fact>`` (section 5.6)
        identify the thing that abstained. Two critics sharing an id make the
        record ambiguous, and an ambiguous record cannot be replayed to the same
        verdict (Art. VIII).
        """
        critic_ids = [critic.id for critic in self.critics]
        if len(set(critic_ids)) != len(critic_ids):
            raise ValueError("duplicate critic id; critic ids are reason-code subjects")
        fact_names = [fact.name for fact in self.required_facts]
        if len(set(fact_names)) != len(fact_names):
            raise ValueError("duplicate required fact name; fact names are reason-code subjects")
        return self

    # -- derived accessors ----------------------------------------------------

    @property
    def key(self) -> ClassKey:
        """The ``(tool, intent)`` identity of this class (``FR-30``)."""
        return (self.tool, self.intent)

    @property
    def qualified_name(self) -> str:
        """``tool/intent`` -- the form used in logs and decision records."""
        return f"{self.tool}/{self.intent}"

    @property
    def hard_critics(self) -> tuple[CriticRef, ...]:
        """Critics whose non-``PASS`` result can block (section 5.3).

        Mode normalisation (step 0) may still demote a hard critic to soft for
        resolution; that is the resolver's call, not the registry's.
        """
        return tuple(critic for critic in self.critics if critic.hard)

    @property
    def soft_critics(self) -> tuple[CriticRef, ...]:
        """Critics that never change the verdict but are always recorded."""
        return tuple(critic for critic in self.critics if not critic.hard)

    @property
    def is_halted(self) -> bool:
        """``True`` when an operator has halted this class (``FR-49``).

        A halted class resolves every proposal to ``DENY`` and issues no token
        of any kind. Halt is the incident lever; it is never a rollout stage.
        """
        return self.mode is Mode.HALTED

    @property
    def source_requirements(self) -> tuple[str, ...]:
        """Every requirement this class's critics encode, deduplicated (``FR-32``)."""
        seen: dict[str, None] = {}
        for critic in self.critics:
            seen.setdefault(critic.source_requirement, None)
        return tuple(seen)

    def fact_requirement(self, name: str) -> FactRequirement | None:
        """Return the named fact requirement, or ``None`` if the class has none."""
        for fact in self.required_facts:
            if fact.name == name:
                return fact
        return None

    def critic(self, critic_id: str) -> CriticRef | None:
        """Return the named critic, or ``None``.

        Used by the gateway to validate a reason-code subject against the
        registry before it is recorded (``SEC-07``).
        """
        for critic in self.critics:
            if critic.id == critic_id:
                return critic
        return None

    def resource_key_for(
        self,
        arguments: Mapping[str, Any],
        *,
        enumerations: Mapping[str, Collection[str]] | None = None,
    ) -> str | None:
        """Render this class's resource key for ``arguments`` (``FR-25``).

        Returns ``None`` when the class declares no template, which means the
        broker takes no lease for it. Raises
        :class:`neuroharness.errors.RegistryValidationError` when the template
        cannot be rendered into a well-formed, enumerated key -- never a
        best-effort string, because the rendered key *is* the mutual-exclusion
        identity (``WF-06c``) and a wrong one silently permits two concurrent
        deployments of the same service.
        """
        if self.resource_key_template is None:
            return None
        return render_template(self.resource_key_template, arguments, enumerations)


class ActionClassRegistry(BaseModel):
    """A loaded, verified action-class registry (``FR-30``, ``FR-33``).

    ``digest`` is the identity the decision record cites, so an auditor can say
    which registry produced a verdict. It is optional on the model only so an
    in-memory registry can be assembled in a test; the loader's default verifier
    requires it (see :class:`neuroharness.registry.loader.DigestVerifier`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    registry_version: str = Field(min_length=1, max_length=64)
    digest: Digest | None = None
    unregistered_class_policy: UnregisteredClassPolicy = UnregisteredClassPolicy.STRICT
    action_classes: tuple[ActionClass, ...] = ()
    resource_keys: ResourceKeyRegistry = ResourceKeyRegistry()

    _by_key: dict[ClassKey, ActionClass] = PrivateAttr(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _readable_version(cls, value: str) -> str:
        """Refuse a version this build does not fully understand.

        On the *model*, as :class:`~neuroharness.models.envelope.ActionEnvelope`
        and :class:`~neuroharness.models.record.DecisionRecord` both do, and not
        only in :func:`~neuroharness.registry.loader.load_registry`. The loader
        is one way a registry comes into being; a hot reload (``FR-83``), a cache
        rehydration and a hand-built fixture are others, and each of them used to
        construct an ``ActionClassRegistry`` declaring any version at all. This
        object decides which classes are enforced, so a shape this build does not
        understand must not be constructible (Constitution Art. II).

        Raises :class:`~neuroharness.errors.SchemaVersionError`, not a
        ``ValueError``: it is a fail-closed outcome carrying ``SCHEMA_INVALID``.
        """
        SchemaCompatibility.assert_readable(SchemaKind.REGISTRY, value)
        return value

    @model_validator(mode="after")
    def _index_classes(self) -> "ActionClassRegistry":
        """Rule (i): reject duplicate ``(tool, intent)`` pairs.

        ``FR-30`` makes ``(tool, intent)`` the identity of an action class. With
        a duplicate, which entry governs a call depends on lookup order -- so the
        enforcing entry and the permissive one are both "the policy", and which
        one applied is not recoverable from the record. Fail at load, where an
        operator can see it, rather than at a verdict nobody can explain.
        """
        index: dict[ClassKey, ActionClass] = {}
        for action_class in self.action_classes:
            if action_class.key in index:
                raise ValueError(
                    f"duplicate action class {action_class.qualified_name!r}; "
                    "(tool, intent) is the identity of a class (FR-30)"
                )
            index[action_class.key] = action_class
        self._by_key = index
        return self

    @model_validator(mode="after")
    def _check_resource_keys(self) -> "ActionClassRegistry":
        """Every declared template must render against the shared enumerations.

        Checked at load rather than at dispatch: a template that names a
        resource kind the signed resource-key registry does not enumerate is a
        policy referencing a catalogue entry that does not exist (``FR-34``).
        """
        known_kinds = set(self.resource_keys.enumerations)
        if not known_kinds:
            return self
        for action_class in self.action_classes:
            template = action_class.resource_key_template
            if template is None:
                continue
            for kind in _template_kinds(template):
                if kind not in known_kinds:
                    raise ValueError(
                        f"action class {action_class.qualified_name!r} keys on resource "
                        f"kind {kind!r}, which the resource-key registry does not "
                        "enumerate (FR-34)"
                    )
        return self

    # -- lookup ---------------------------------------------------------------

    def get(self, tool: str, intent: str) -> ActionClass:
        """Return the class for ``(tool, intent)``, or fail closed.

        Raises :class:`UnregisteredActionClassError` when the pair is not
        registered. This is the strict path (``FR-31``) and the default: there
        is no stand-in ``ActionClass`` to return, and inventing one would be the
        hardcoded policy this subsystem exists to prevent.

        Callers that must honour a deployment's ``permissive`` setting call
        :meth:`resolve` instead.
        """
        action_class = self._by_key.get((tool, intent))
        if action_class is None:
            raise UnregisteredActionClassError(tool, intent)
        return action_class

    def try_get(self, tool: str, intent: str) -> ActionClass | None:
        """Return the class for ``(tool, intent)``, or ``None``. Never raises."""
        return self._by_key.get((tool, intent))

    def resolve(self, tool: str, intent: str) -> ActionClass | None:
        """Apply this registry's unregistered-class policy (``FR-31``).

        * ``strict`` (default): an unregistered pair raises
          :class:`UnregisteredActionClassError`, which the gateway records as
          ``ABSTAIN`` with reason ``ACTION_CLASS_UNREGISTERED``. An action
          nobody classified is an action nobody reviewed.
        * ``permissive``: returns ``None``, meaning policy-only evaluation. This
          exists for deployments mid-migration and is a deliberate, recorded
          reduction in coverage -- never a convenience.
        """
        action_class = self._by_key.get((tool, intent))
        if action_class is not None:
            return action_class
        if self.unregistered_class_policy is UnregisteredClassPolicy.STRICT:
            raise UnregisteredActionClassError(tool, intent)
        return None

    @property
    def is_strict(self) -> bool:
        """``True`` when unregistered classes abstain (``FR-31``)."""
        return self.unregistered_class_policy is UnregisteredClassPolicy.STRICT

    def keys(self) -> tuple[ClassKey, ...]:
        """Every registered ``(tool, intent)`` pair, in document order."""
        return tuple(self._by_key)

    def resource_key_for(self, tool: str, intent: str, arguments: Mapping[str, Any]) -> str | None:
        """Render a class's resource key against the shared enumerations (``FR-25``)."""
        return self.get(tool, intent).resource_key_for(
            arguments, enumerations=self.resource_keys.enumerations
        )

    def __contains__(self, key: object) -> bool:
        """``("deployment.apply", "deploy_service") in registry``."""
        if isinstance(key, ActionClass):
            return self._by_key.get(key.key) == key
        if isinstance(key, tuple) and len(key) == 2:
            return (key[0], key[1]) in self._by_key
        return False

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[ActionClass]:  # type: ignore[override]
        """Iterate registered classes in document order.

        Overrides ``BaseModel.__iter__`` (which yields field pairs) because a
        registry reads as a collection of classes at every call site.
        """
        return iter(self.action_classes)


def _template_kinds(template: str) -> tuple[str, ...]:
    """Return the resource-key kinds a template names, e.g. ``("service", "target")``."""
    kinds: list[str] = []
    for segment in template.split("/"):
        kind, separator, _ = segment.partition(":")
        if separator and kind and "{" not in kind:
            kinds.append(kind)
    return tuple(kinds)
