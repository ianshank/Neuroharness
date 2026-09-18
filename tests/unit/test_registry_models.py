"""Action-class model tests (``FR-30``--``FR-35``, ``FR-49``, section 5.5).

Every validation rule below is a hard gate on what an operator can put into a
signed registry. Each one is tested twice: once proving the good case is
accepted, once proving the bad case is refused. A rule with no failing case is
a rule that does not exist (Constitution Art. IV).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pytest
from pydantic import ValidationError

from neuroharness import defaults
from neuroharness.config import UnregisteredClassPolicy
from neuroharness.errors import (
    RegistryValidationError,
    SchemaVersionError,
    UnregisteredActionClassError,
)
from neuroharness.models.common import CriticKind, EffectClass, Mode
from neuroharness.reason import ESCALATABLE_REASONS, ReasonName
from neuroharness.registry.models import (
    ActionClass,
    ActionClassRegistry,
    BatchPolicy,
    ConnectorKind,
    CriticRef,
    FactRequirement,
)
from neuroharness.registry.resource_keys import ResourceKeyRegistry
from neuroharness.version import SchemaCompatibility, SchemaKind

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["service", "target"],
    "properties": {
        "service": {"enum": ["checkout", "payments"]},
        "target": {"enum": ["staging", "production"]},
    },
}

ENUMERATIONS = {"service": ("checkout", "payments"), "target": ("staging", "production")}

#: Taken from the single place that declares compatibility, not spelled out here.
REGISTRY_SCHEMA_VERSION = SchemaCompatibility.written_version(SchemaKind.REGISTRY)

#: A registry-schema version no build reads.
UNREADABLE_SCHEMA_VERSION = "9.9"


def critic(**overrides: Any) -> CriticRef:
    base: dict[str, Any] = {
        "id": "pdp.deploy",
        "kind": CriticKind.REGO,
        "hard": True,
        "mode": Mode.ENFORCE,
        "source_requirement": "WF-01",
    }
    return CriticRef(**{**base, **overrides})


def action_class(**overrides: Any) -> ActionClass:
    """A minimal valid WRITE class; overrides exercise one rule at a time."""
    base: dict[str, Any] = {
        "tool": "deployment.apply",
        "intent": "deploy_service",
        "effect_class": EffectClass.WRITE,
        "argument_schema": SCHEMA,
        "resource_key_template": "service:{service}/target:{target}",
        "connector_kind": ConnectorKind.ASYNC,
        "critics": (critic(),),
        "mode": Mode.ENFORCE,
        "approvable": True,
        "approver_groups": ("release-managers",),
    }
    return ActionClass(**{**base, **overrides})


def message(exc: ValidationError) -> str:
    return str(exc.value if isinstance(exc, pytest.ExceptionInfo) else exc)


# --- the ClassPolicy protocol shape -----------------------------------------


@runtime_checkable
class ClassPolicy(Protocol):
    """A local copy of the resolver's protocol.

    Copied rather than imported on purpose: ``resolve`` and ``registry`` are
    coupled by *shape*, not by import, so this test fails the moment the shape
    drifts even though neither module depends on the other.
    """

    mode: Mode
    approvable: bool
    escalate_on: frozenset[ReasonName]
    repair_budget: int


def test_action_class_satisfies_the_class_policy_protocol() -> None:
    entry = action_class()
    assert isinstance(entry, ClassPolicy)

    assert isinstance(entry.mode, Mode)
    assert isinstance(entry.approvable, bool)
    assert isinstance(entry.escalate_on, frozenset)
    assert isinstance(entry.repair_budget, int) and not isinstance(entry.repair_budget, bool)

    escalating = action_class(escalate_on=[ReasonName.SOLVER_TIMEOUT])
    assert all(isinstance(reason, ReasonName) for reason in escalating.escalate_on)


# --- rule (a): escalate_on subset of ESCALATABLE_REASONS (section 5.5) -------


@pytest.mark.parametrize("reason", sorted(ESCALATABLE_REASONS, key=lambda r: r.value))
def test_rule_a_accepts_every_escalatable_reason(reason: ReasonName) -> None:
    facts = (FactRequirement(name="deploy_state", key=("service",), max_age_seconds=60,
                             required=False, escalatable=True),)
    entry = action_class(escalate_on=[reason], required_facts=facts)
    assert entry.escalate_on == frozenset({reason})


@pytest.mark.parametrize(
    "reason",
    [
        ReasonName.POLICY_ENGINE_UNAVAILABLE,
        ReasonName.BUNDLE_INTEGRITY_FAILED,
        ReasonName.CRITIC_ERROR,
        ReasonName.HARNESS_UNHEALTHY,
        ReasonName.RULE_FAILED,
    ],
)
def test_rule_a_rejects_a_non_escalatable_reason(reason: ReasonName) -> None:
    """Infrastructure abstentions are terminal: no human can vouch for a dead engine."""
    with pytest.raises(ValidationError) as exc:
        action_class(escalate_on=[reason])
    assert "non-escalatable reason" in message(exc)
    assert "section 5.5" in message(exc)


# --- rule (b): escalate_on requires approvable (FR-33) ----------------------


def test_rule_b_accepts_escalation_on_an_approvable_class() -> None:
    facts = (FactRequirement(name="deploy_state", key=("service",), max_age_seconds=60,
                             required=False, escalatable=True),)
    entry = action_class(approvable=True, escalate_on=[ReasonName.FACT_STALE], required_facts=facts)
    assert entry.escalate_on == frozenset({ReasonName.FACT_STALE})


def test_rule_b_rejects_escalation_on_a_non_approvable_class() -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(approvable=False, approver_groups=(), escalate_on=[ReasonName.SOLVER_UNKNOWN])
    assert "not approvable" in message(exc)
    assert "FR-33" in message(exc)


def test_rule_b2_rejects_fact_escalation_with_no_escalatable_fact() -> None:
    """Section 5.5 permits FACT_* escalation only for facts marked escalatable."""
    required_only = (
        FactRequirement(name="ci_result", key=("service",), max_age_seconds=900, required=True),
    )
    with pytest.raises(ValidationError) as exc:
        action_class(escalate_on=[ReasonName.FACT_MISSING], required_facts=required_only)
    assert "no required fact is marked escalatable" in message(exc)
    assert "section 5.5" in message(exc)


def test_rule_b2_allows_solver_escalation_without_any_fact() -> None:
    entry = action_class(escalate_on=[ReasonName.SOLVER_TIMEOUT], required_facts=())
    assert ReasonName.SOLVER_TIMEOUT in entry.escalate_on


# --- rule (c): halted is a class lever, not a critic mode (FR-49, INV-11) ---

#: Every rollout mode a *critic* may declare. ``halted`` is absent on purpose:
#: it is the class-level incident lever, and rule (c) refuses it on a critic.
CRITIC_ROLLOUT_MODES = (Mode.SHADOW, Mode.ADVISORY, Mode.ENFORCE)

#: Every rollout mode a *class* may declare.
CLASS_ROLLOUT_MODES = (Mode.SHADOW, Mode.ADVISORY, Mode.ENFORCE, Mode.HALTED)


@pytest.mark.parametrize("class_mode", CLASS_ROLLOUT_MODES)
@pytest.mark.parametrize("critic_mode", CRITIC_ROLLOUT_MODES)
def test_rule_c_accepts_every_critic_mode_in_every_class_mode(
    class_mode: Mode, critic_mode: Mode
) -> None:
    """The class mode is not a ceiling on its critics' modes.

    Making it one costs the operator the rollout: section 5.3 step 0 demotes a
    hard critic on the critic's *own* mode, so a class whose critics were all
    forced down to the class mode would resolve every shadow proposal to
    ``ALLOW``. The rollout report would then read as a clean week when the
    program had simply stopped evaluating.
    """
    entry = action_class(
        mode=class_mode,
        effect_class=EffectClass.WRITE,
        critics=(critic(mode=critic_mode),),
    )
    assert entry.critics[0].mode is critic_mode


def test_rule_c_admits_an_enforcing_critic_inside_a_shadow_class() -> None:
    """Scenario ``A-16``: this exact entry is what a shadow rollout measures.

    A shadow class exists to answer "what would enforcement have denied this
    week?". It can only answer while its hard critics still block in the
    verdict, because the class mode is consulted at the broker, after the
    record is written (``INV-11``). A registry that refuses to hold an
    ``enforce`` critic inside a ``shadow`` class makes the question
    unaskable, and an operator would promote the class on evidence that never
    existed.
    """
    entry = action_class(
        mode=Mode.SHADOW,
        effect_class=EffectClass.WRITE,
        critics=(critic(id="pdp.deploy", mode=Mode.ENFORCE),),
    )

    assert entry.mode is Mode.SHADOW
    assert not entry.mode.blocks_on_verdict
    # The critic keeps its own declared mode, which is what step 0 reads.
    assert entry.hard_critics == entry.critics
    assert entry.critics[0].mode is Mode.ENFORCE


@pytest.mark.parametrize("class_mode", CLASS_ROLLOUT_MODES)
def test_rule_c_rejects_a_critic_declaring_the_halted_lever(class_mode: Mode) -> None:
    """``halted`` on a critic silently disarms it instead of arming it.

    Resolution demotes every critic mode that is not ``enforce``, so the
    strongest-sounding word in the vocabulary would turn the gate off. An
    operator halting a runaway critic that way would believe the action was
    stopped while every proposal sailed through it.
    """
    with pytest.raises(ValidationError) as exc:
        action_class(mode=class_mode, critics=(critic(mode=Mode.HALTED),))
    assert Mode.HALTED.value in message(exc)
    assert "FR-49" in message(exc)


def test_mode_rank_orders_enforcement_without_constraining_critics() -> None:
    """``rank`` is a report ordering, not the gate it used to be.

    Kept because rollout tooling sorts by it. If it silently became a
    comparison between a class and its critics again, the shadow rollout of
    scenario ``A-16`` would stop being expressible.
    """
    assert Mode.SHADOW.rank < Mode.ADVISORY.rank < Mode.ENFORCE.rank < Mode.HALTED.rank
    assert action_class(mode=Mode.SHADOW, critics=(critic(mode=Mode.ENFORCE),)).mode is Mode.SHADOW


# --- rule (d): repair budget bounds (section 5.4, FR-91) --------------------


@pytest.mark.parametrize("budget", [0, 1, defaults.DEFAULT_REPAIR_BUDGET, defaults.MAX_REPAIR_BUDGET])
def test_rule_d_accepts_a_budget_inside_the_bounds(budget: int) -> None:
    assert action_class(repair_budget=budget).repair_budget == budget


@pytest.mark.parametrize("budget", [-1, defaults.MAX_REPAIR_BUDGET + 1, 1000])
def test_rule_d_rejects_a_budget_outside_the_bounds(budget: int) -> None:
    """Beyond the ceiling the repair loop is a probing channel, not a correction."""
    with pytest.raises(ValidationError):
        action_class(repair_budget=budget)


def test_repair_budget_defaults_to_the_documented_value() -> None:
    assert action_class().repair_budget == defaults.DEFAULT_REPAIR_BUDGET


# --- rule (e): every critic names its source requirement (FR-32, Art. V) ----


def test_rule_e_accepts_a_wf_id_or_a_document_reference() -> None:
    assert critic(source_requirement="WF-06a").source_requirement == "WF-06a"
    assert (
        critic(source_requirement="01-specification.md#3.5-typed-contract").source_requirement
        == "01-specification.md#3.5-typed-contract"
    )


@pytest.mark.parametrize("value", ["", "   ", None])
def test_rule_e_rejects_a_missing_source_requirement(value: Any) -> None:
    with pytest.raises(ValidationError):
        critic(source_requirement=value)


def test_rule_e_rejects_an_omitted_source_requirement() -> None:
    with pytest.raises(ValidationError) as exc:
        CriticRef(id="pdp.deploy", kind=CriticKind.REGO, hard=True, mode=Mode.ENFORCE)
    assert "source_requirement" in message(exc)


def test_rule_e_rejects_free_text_as_a_source_requirement() -> None:
    """Identifiers, not prose: the value is quoted into records (``SEC-07``)."""
    with pytest.raises(ValidationError):
        critic(source_requirement="the rule the security team told us about")


# --- rules (f) and (g): effect class constrains approvability (FR-35) -------


@pytest.mark.parametrize("effect", [EffectClass.NONE, EffectClass.READ])
def test_rule_f_accepts_a_non_approvable_read_class(effect: EffectClass) -> None:
    entry = action_class(
        effect_class=effect, approvable=False, approver_groups=(), resource_key_template=None
    )
    assert entry.effect_class.takes_fast_path
    assert not entry.approvable


@pytest.mark.parametrize("effect", [EffectClass.NONE, EffectClass.READ])
def test_rule_f_rejects_an_approvable_read_class(effect: EffectClass) -> None:
    """Reading a tool result is not an action (``FR-35``)."""
    with pytest.raises(ValidationError) as exc:
        action_class(effect_class=effect, approvable=True, approver_groups=("release-managers",))
    assert "never approvable" in message(exc)
    assert "FR-35" in message(exc)


@pytest.mark.parametrize("effect", [EffectClass.NONE, EffectClass.READ])
def test_rule_f_rejects_approver_groups_on_a_read_class(effect: EffectClass) -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(effect_class=effect, approvable=False, approver_groups=("release-managers",))
    assert "approver_groups" in message(exc)
    assert "FR-35" in message(exc)


def test_rule_g_accepts_an_approvable_destructive_class() -> None:
    entry = action_class(
        effect_class=EffectClass.DESTRUCTIVE, approvable=True, approver_groups=("release-managers",)
    )
    assert entry.effect_class is EffectClass.DESTRUCTIVE


def test_rule_g_accepts_a_halted_destructive_class() -> None:
    """``FR-49``: halt is the other permitted state for a destructive class."""
    entry = action_class(
        effect_class=EffectClass.DESTRUCTIVE,
        mode=Mode.HALTED,
        approvable=False,
        approver_groups=(),
    )
    assert entry.is_halted


def test_rule_g_rejects_a_destructive_class_that_is_neither() -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(
            effect_class=EffectClass.DESTRUCTIVE,
            mode=Mode.ENFORCE,
            approvable=False,
            approver_groups=(),
        )
    assert "must be approvable or halted" in message(exc)
    assert "FR-35" in message(exc)


def test_an_approvable_class_must_name_an_approver_group() -> None:
    """``FR-42``: an unbounded approver set is not an oversight control."""
    with pytest.raises(ValidationError) as exc:
        action_class(approvable=True, approver_groups=())
    assert "approver group" in message(exc)


# --- rule (h): closed argument schema (FR-02) -------------------------------


def test_rule_h_accepts_a_closed_schema() -> None:
    assert action_class().argument_schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}},
        {"type": "object", "additionalProperties": True, "properties": {}},
        {"type": "object", "additionalProperties": {"type": "string"}, "properties": {}},
    ],
)
def test_rule_h_rejects_an_open_schema(schema: dict[str, Any]) -> None:
    """An open schema lets an undeclared argument reach the tool unvalidated."""
    with pytest.raises(ValidationError) as exc:
        action_class(argument_schema=schema, resource_key_template=None)
    assert "additionalProperties" in message(exc)
    assert "FR-02" in message(exc)


@pytest.mark.parametrize("schema", [{}, {"type": "string", "additionalProperties": False}])
def test_rule_h_rejects_a_schema_that_is_not_an_object(schema: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(argument_schema=schema, resource_key_template=None)
    assert "FR-02" in message(exc)


# --- rule (j): a required fact is never escalatable (section 5.5) -----------


def test_rule_j_accepts_a_required_non_escalatable_fact() -> None:
    fact = FactRequirement(
        name="ci_result", key=("service", "version"), max_age_seconds=900,
        required=True, escalatable=False,
    )
    assert fact.required and not fact.escalatable


def test_rule_j_accepts_an_optional_escalatable_fact() -> None:
    fact = FactRequirement(
        name="deploy_state", key=("service", "target"), max_age_seconds=60,
        required=False, escalatable=True,
    )
    assert fact.escalatable and not fact.required


def test_rule_j_rejects_a_required_escalatable_fact() -> None:
    """A required fact *is* the evidence the gate checks (``WF-04``, ``WF-05``)."""
    with pytest.raises(ValidationError) as exc:
        FactRequirement(
            name="ci_result", key=("service", "version"), max_age_seconds=900,
            required=True, escalatable=True,
        )
    assert "required and escalatable" in message(exc)
    assert "section 5.5" in message(exc)


def test_fact_requirement_rejects_a_duplicated_key_component() -> None:
    with pytest.raises(ValidationError):
        FactRequirement(name="ci_result", key=("service", "service"), max_age_seconds=900)


def test_fact_requirement_rejects_an_empty_key() -> None:
    with pytest.raises(ValidationError):
        FactRequirement(name="ci_result", key=(), max_age_seconds=900)


# --- rule (i): duplicate (tool, intent) pairs (FR-30) -----------------------


def registry(**overrides: Any) -> ActionClassRegistry:
    base: dict[str, Any] = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_version": "2026.09.18-1",
        "action_classes": (action_class(),),
        "resource_keys": ResourceKeyRegistry(enumerations=ENUMERATIONS),
    }
    return ActionClassRegistry(**{**base, **overrides})


def test_rule_i_accepts_distinct_intents_on_one_tool() -> None:
    loaded = registry(
        action_classes=(
            action_class(intent="deploy_service"),
            action_class(intent="rollback", effect_class=EffectClass.DESTRUCTIVE),
        )
    )
    assert len(loaded) == 2


def test_rule_i_rejects_a_duplicate_tool_intent_pair() -> None:
    """With a duplicate, which entry governs depends on lookup order (``FR-30``)."""
    with pytest.raises(ValidationError) as exc:
        registry(action_classes=(action_class(), action_class(token_ttl_seconds=30)))
    assert "duplicate action class" in message(exc)
    assert "FR-30" in message(exc)


# --- rule (k): resource-key template references declared arguments (FR-34) --


def test_rule_k_accepts_a_template_over_declared_arguments() -> None:
    assert action_class().resource_key_template == "service:{service}/target:{target}"


def test_rule_k_rejects_a_template_naming_an_undeclared_argument() -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(resource_key_template="service:{service}/region:{region}")
    assert "undeclared argument" in message(exc)
    assert "FR-34" in message(exc)


def test_rule_k_skips_the_cross_check_when_the_schema_declares_no_properties() -> None:
    """Nothing to cross-check against: a schema with no ``properties`` and
    ``additionalProperties: false`` accepts no arguments at all, so the class is
    unusable for an independent reason rather than misconfigured here."""
    entry = action_class(
        argument_schema={"type": "object", "additionalProperties": False},
        resource_key_template="service:{service}",
    )
    assert entry.resource_key_template == "service:{service}"


def test_rule_k_rejects_a_constant_template() -> None:
    """A constant key would serialise every call of the class against itself."""
    with pytest.raises(ValidationError) as exc:
        action_class(resource_key_template="service:all")
    assert "no placeholders" in message(exc)


def test_registry_rejects_a_template_kind_the_catalogue_does_not_enumerate() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"service": {"enum": ["checkout"]}, "shard": {"type": "integer"}},
    }
    with pytest.raises(ValidationError) as exc:
        registry(
            action_classes=(
                action_class(
                    argument_schema=schema, resource_key_template="shard:{shard}"
                ),
            )
        )
    assert "the resource-key registry does not enumerate" in message(exc)
    assert "FR-34" in message(exc)


def test_registry_without_enumerations_does_not_constrain_template_kinds() -> None:
    """An empty catalogue constrains nothing; it must not deny every class."""
    loaded = registry(resource_keys=ResourceKeyRegistry())
    assert loaded.get("deployment.apply", "deploy_service").resource_key_template is not None


def test_a_class_without_a_template_skips_the_kind_check() -> None:
    entry = action_class(
        effect_class=EffectClass.READ,
        approvable=False,
        approver_groups=(),
        resource_key_template=None,
    )
    assert registry(action_classes=(entry,)).get("deployment.apply", "deploy_service") is entry


# --- duplicate critic ids and fact names (SEC-07, Art. VIII) ----------------


def test_duplicate_critic_ids_are_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        action_class(critics=(critic(id="pdp.deploy"), critic(id="pdp.deploy")))
    assert "duplicate critic id" in message(exc)


def test_duplicate_fact_names_are_rejected() -> None:
    fact = FactRequirement(name="ci_result", key=("service",), max_age_seconds=900)
    with pytest.raises(ValidationError) as exc:
        action_class(required_facts=(fact, fact))
    assert "duplicate required fact name" in message(exc)


# --- derived accessors -------------------------------------------------------


def test_key_and_qualified_name() -> None:
    entry = action_class()
    assert entry.key == ("deployment.apply", "deploy_service")
    assert entry.qualified_name == "deployment.apply/deploy_service"


def test_hard_and_soft_critics_partition_the_critic_set() -> None:
    entry = action_class(
        critics=(
            critic(id="pdp.deploy", hard=True),
            critic(id="smt.contract", kind=CriticKind.SMT, hard=True),
            critic(id="prolog.hints", kind=CriticKind.PROLOG, hard=False, mode=Mode.ADVISORY),
        )
    )
    assert [c.id for c in entry.hard_critics] == ["pdp.deploy", "smt.contract"]
    assert [c.id for c in entry.soft_critics] == ["prolog.hints"]
    assert len(entry.hard_critics) + len(entry.soft_critics) == len(entry.critics)


def test_is_halted_tracks_the_class_mode() -> None:
    """``FR-49``: a halted class denies every proposal and issues no token."""
    assert not action_class(mode=Mode.ENFORCE).is_halted
    halted = action_class(mode=Mode.HALTED)
    assert halted.is_halted
    assert halted.mode.blocks_on_verdict


def test_fact_requirement_lookup() -> None:
    fact = FactRequirement(name="ci_result", key=("service",), max_age_seconds=900)
    entry = action_class(required_facts=(fact,))
    assert entry.fact_requirement("ci_result") is fact
    assert entry.fact_requirement("nope") is None


def test_critic_lookup_validates_a_reason_subject() -> None:
    entry = action_class()
    assert entry.critic("pdp.deploy") is entry.critics[0]
    assert entry.critic("pdp.unknown") is None


def test_source_requirements_are_deduplicated_in_order() -> None:
    entry = action_class(
        critics=(
            critic(id="a", source_requirement="WF-01"),
            critic(id="b", source_requirement="WF-02"),
            critic(id="c", source_requirement="WF-01"),
        )
    )
    assert entry.source_requirements == ("WF-01", "WF-02")


# --- resource_key_for (FR-25) ------------------------------------------------


def test_resource_key_for_renders_the_lease_identity() -> None:
    entry = action_class()
    key = entry.resource_key_for(
        {"service": "checkout", "target": "production"}, enumerations=ENUMERATIONS
    )
    assert key == "service:checkout/target:production"


def test_resource_key_for_rejects_a_value_outside_the_enumeration() -> None:
    """``WF-06c``: a wrong lease identity silently permits two concurrent deploys."""
    entry = action_class()
    with pytest.raises(RegistryValidationError, match="not in the canonical target enumeration"):
        entry.resource_key_for({"service": "checkout", "target": "prod"}, enumerations=ENUMERATIONS)


def test_resource_key_for_returns_none_when_no_template_is_declared() -> None:
    entry = action_class(effect_class=EffectClass.READ, approvable=False, approver_groups=(),
                         resource_key_template=None)
    assert entry.resource_key_for({"service": "checkout"}, enumerations=ENUMERATIONS) is None


def test_resource_key_for_rejects_a_missing_argument() -> None:
    entry = action_class()
    with pytest.raises(RegistryValidationError, match="does not supply"):
        entry.resource_key_for({"service": "checkout"}, enumerations=ENUMERATIONS)


# --- registry lookup and FR-31 ----------------------------------------------


def test_get_returns_a_registered_class() -> None:
    loaded = registry()
    assert loaded.get("deployment.apply", "deploy_service").key == (
        "deployment.apply",
        "deploy_service",
    )


def test_get_fails_closed_on_an_unregistered_pair() -> None:
    with pytest.raises(UnregisteredActionClassError) as exc:
        registry().get("shell.exec", "run")
    assert exc.value.reason_code.name is ReasonName.ACTION_CLASS_UNREGISTERED


def test_try_get_never_raises() -> None:
    loaded = registry()
    assert loaded.try_get("shell.exec", "run") is None
    assert loaded.try_get("deployment.apply", "deploy_service") is not None


def test_resolve_abstains_under_strict_policy() -> None:
    """``FR-31``: an action nobody classified is an action nobody reviewed."""
    loaded = registry(unregistered_class_policy=UnregisteredClassPolicy.STRICT)
    assert loaded.is_strict
    with pytest.raises(UnregisteredActionClassError):
        loaded.resolve("shell.exec", "run")


def test_resolve_falls_back_to_policy_only_under_permissive_policy() -> None:
    loaded = registry(unregistered_class_policy=UnregisteredClassPolicy.PERMISSIVE)
    assert not loaded.is_strict
    assert loaded.resolve("shell.exec", "run") is None
    assert loaded.resolve("deployment.apply", "deploy_service") is not None


def test_strict_is_the_default_policy() -> None:
    assert registry().unregistered_class_policy is UnregisteredClassPolicy.STRICT


def test_contains_len_iter_and_keys() -> None:
    entry = action_class()
    loaded = registry(action_classes=(entry,))
    assert ("deployment.apply", "deploy_service") in loaded
    assert ("shell.exec", "run") not in loaded
    assert entry in loaded
    assert "deployment.apply" not in loaded
    assert len(loaded) == 1
    assert list(loaded) == [entry]
    assert loaded.keys() == (("deployment.apply", "deploy_service"),)


def test_registry_resource_key_for_uses_the_shared_enumerations() -> None:
    loaded = registry()
    assert (
        loaded.resource_key_for(
            "deployment.apply", "deploy_service", {"service": "checkout", "target": "production"}
        )
        == "service:checkout/target:production"
    )
    with pytest.raises(RegistryValidationError):
        loaded.resource_key_for(
            "deployment.apply", "deploy_service", {"service": "checkout", "target": "prod"}
        )


# --- immutability (SEC-05) ---------------------------------------------------


def test_models_are_frozen() -> None:
    """A mutable registry entry is one whose signature proves nothing."""
    entry = action_class()
    with pytest.raises(ValidationError):
        entry.mode = Mode.SHADOW  # type: ignore[misc]
    with pytest.raises(ValidationError):
        entry.critics[0].hard = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        registry().registry_version = "tampered"  # type: ignore[misc]


def test_unknown_fields_are_refused() -> None:
    """An unrecognised key is a policy the operator believes is in force."""
    with pytest.raises(ValidationError):
        action_class(auto_approve=True)
    with pytest.raises(ValidationError):
        critic(bypass=True)


def test_defaults_come_from_the_defaults_module() -> None:
    entry = action_class()
    assert entry.token_ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS
    assert entry.approval_ttl_seconds == defaults.DEFAULT_APPROVAL_TTL_SECONDS
    assert entry.lease_timeout_seconds == defaults.DEFAULT_LEASE_TIMEOUT_SECONDS
    assert entry.critics[0].timeout_ms == defaults.DEFAULT_CRITIC_TIMEOUT_MS
    assert entry.batch_policy is BatchPolicy.INDEPENDENT


def test_mode_has_no_default_and_must_be_stated() -> None:
    """A class whose enforcement posture was never written down is an implicit value."""
    payload = {
        "tool": "deployment.apply",
        "intent": "deploy_service",
        "effect_class": EffectClass.WRITE,
        "argument_schema": SCHEMA,
        "approvable": False,
    }
    with pytest.raises(ValidationError) as exc:
        ActionClass(**payload)
    assert "mode" in message(exc)


# --- F5: a signed document must not be editable after it is loaded -----------


def test_argument_schema_cannot_be_mutated_in_place() -> None:
    """A registry entry that can be mutated after load proves nothing (``FR-31``).

    The registry is signed and its digest covers this schema, but pydantic's
    ``frozen=True`` only blocks attribute assignment:
    ``entry.argument_schema["additionalProperties"] = True`` used to succeed and
    silently widen what the class accepts, under a signature taken over the
    narrower document.
    """
    entry = action_class()

    with pytest.raises(TypeError):
        entry.argument_schema["additionalProperties"] = True
    with pytest.raises(TypeError):
        del entry.argument_schema["required"]
    with pytest.raises(AttributeError):
        entry.argument_schema.update({"additionalProperties": True})


def test_a_nested_part_of_the_argument_schema_is_frozen_too() -> None:
    """Widening an enum one level down is the same attack with an extra key."""
    entry = action_class()

    with pytest.raises(TypeError):
        entry.argument_schema["properties"]["target"]["enum"] = ("anything",)
    # Sequences inside a frozen document become tuples.
    assert entry.argument_schema["required"] == ("service", "target")
    with pytest.raises(TypeError):
        entry.argument_schema["required"][0] = "something-else"


def test_the_entry_does_not_share_structure_with_the_loaded_document() -> None:
    """Freezing is also the copy, or the loader keeps a live handle on it."""
    supplied: dict[str, Any] = {"type": "object", "additionalProperties": False}
    entry = action_class(argument_schema=supplied)

    supplied["additionalProperties"] = True

    assert entry.argument_schema["additionalProperties"] is False


def test_the_argument_schema_still_dumps_as_a_plain_document() -> None:
    """The freeze may not leak into the wire form or into a JSON Schema validator."""
    dumped = action_class().model_dump(mode="json")

    assert type(dumped["argument_schema"]) is dict
    assert dumped["argument_schema"] == SCHEMA


# --- F6: the registry validates its own schema_version ----------------------


def test_the_registry_refuses_a_schema_version_it_cannot_read() -> None:
    """On the model, not only in ``load_registry`` (``FR-83``).

    ``ActionEnvelope`` and ``DecisionRecord`` both validate theirs on the model.
    The registry did not, so a hot reload, a cache rehydration or a hand-built
    fixture could construct a registry declaring any version at all -- and this
    object decides which action classes are enforced.
    """
    with pytest.raises(SchemaVersionError) as raised:
        registry(schema_version=UNREADABLE_SCHEMA_VERSION)

    assert raised.value.schema_kind == SchemaKind.REGISTRY
    assert raised.value.found_version == UNREADABLE_SCHEMA_VERSION
    assert raised.value.reason_code.render() == "SCHEMA_INVALID"


def test_the_registry_accepts_the_version_this_build_reads() -> None:
    assert registry().schema_version == REGISTRY_SCHEMA_VERSION


def test_the_refusal_also_applies_to_a_rehydrated_document() -> None:
    """``model_validate`` is the cache-rehydration path, and it is the same gate."""
    document = registry().model_dump(mode="json")
    document["schema_version"] = UNREADABLE_SCHEMA_VERSION

    with pytest.raises(SchemaVersionError):
        ActionClassRegistry.model_validate(document)
