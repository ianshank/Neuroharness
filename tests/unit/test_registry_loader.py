"""Registry loader tests (``FR-30``--``FR-34``, ``SEC-05``, section 3.5).

The loader is the only door into the registry, so it is the only place that can
guarantee the three properties everything downstream assumes: the document is a
version this build understands, it is the document an operator signed, and every
entry in it satisfies the rules in :mod:`neuroharness.registry.models`.

The reference fixture is the deployment workflow of specification section 3.5.
It is the canonical example other suites reuse, so its shape is asserted here
rather than assumed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from neuroharness import defaults
from neuroharness.canonical import canonicalize, digest_value
from neuroharness.config import UnregisteredClassPolicy
from neuroharness.errors import (
    CanonicalizationError,
    RegistryIntegrityError,
    RegistryValidationError,
    SchemaVersionError,
    UnregisteredActionClassError,
)
from neuroharness.models.common import CriticKind, Digest, EffectClass, Mode
from neuroharness.reason import ReasonName
from neuroharness.registry.loader import (
    UNSIGNED_FIELDS,
    DigestVerifier,
    NullVerifier,
    SignatureVerifier,
    canonical_bytes,
    compute_registry_digest,
    load_registry,
    load_registry_file,
)
from neuroharness.registry.models import BatchPolicy, ConnectorKind
from neuroharness.version import SchemaCompatibility, SchemaKind

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "registry" / "reference_deploy_registry.json"

DEPLOY = ("deployment.apply", "deploy_service")
ROLLBACK = ("deployment.apply", "rollback")
STATUS = ("deployment.status", "get_status")

#: Every ``WF-`` id in specification section 3.5.
WORKFLOW_REQUIREMENTS = (
    "WF-01",
    "WF-02",
    "WF-03",
    "WF-04",
    "WF-05",
    "WF-06a",
    "WF-06b",
    "WF-06c",
)


@pytest.fixture()
def document() -> dict[str, Any]:
    """A fresh, mutable copy of the reference document for each test."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def resign(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute the digest after a deliberate, legitimate edit."""
    document["digest"] = str(compute_registry_digest(document))
    return document


# --- the reference fixture (section 3.5) ------------------------------------


def test_reference_fixture_loads_with_digest_verification() -> None:
    registry = load_registry_file(FIXTURE)
    assert registry.schema_version == SchemaCompatibility.written_version(SchemaKind.REGISTRY)
    assert registry.registry_version == "2026.09.18-2"
    assert registry.unregistered_class_policy is UnregisteredClassPolicy.STRICT
    assert len(registry) == 3
    assert DEPLOY in registry and ROLLBACK in registry and STATUS in registry


def test_reference_fixture_digest_matches_its_content() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert Digest(document["digest"]) == compute_registry_digest(document)


def test_reference_deploy_class_matches_the_technical_plan(document: dict[str, Any]) -> None:
    registry = load_registry(document)
    deploy = registry.get(*DEPLOY)

    assert deploy.effect_class is EffectClass.WRITE
    assert deploy.mode is Mode.ENFORCE
    assert deploy.approvable and deploy.approver_groups == ("release-managers",)
    assert deploy.escalate_on == frozenset()
    assert deploy.connector_kind is ConnectorKind.ASYNC
    assert deploy.batch_policy is BatchPolicy.INDEPENDENT
    assert deploy.lease_timeout_seconds == defaults.DEFAULT_LEASE_TIMEOUT_SECONDS
    assert deploy.repair_budget == defaults.DEFAULT_REPAIR_BUDGET
    assert deploy.token_ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS
    assert deploy.approval_ttl_seconds == defaults.DEFAULT_APPROVAL_TTL_SECONDS
    assert deploy.argument_schema["additionalProperties"] is False


def test_reference_deploy_class_covers_every_workflow_requirement(document: dict[str, Any]) -> None:
    """``FR-32``/Art. V: every ``WF-`` id of section 3.5 is encoded by a critic."""
    deploy = load_registry(document).get(*DEPLOY)
    covered = set(deploy.source_requirements)
    assert set(WORKFLOW_REQUIREMENTS) <= covered
    assert all(critic.source_requirement for critic in deploy.critics)


def test_reference_deploy_class_declares_all_four_required_facts(document: dict[str, Any]) -> None:
    deploy = load_registry(document).get(*DEPLOY)
    names = [fact.name for fact in deploy.required_facts]
    assert names == ["ci_result", "change_approval", "deploy_state", "harness_approval"]

    ci = deploy.fact_requirement("ci_result")
    assert ci is not None and ci.key == ("service", "version") and ci.max_age_seconds == 900
    assert ci.required and not ci.escalatable

    approval = deploy.fact_requirement("harness_approval")
    assert approval is not None and approval.key == ("proposal_digest",)
    assert not approval.required


def test_reference_deploy_class_has_a_bounded_solver_critic(document: dict[str, Any]) -> None:
    """A solver's primary bound is a deterministic rlimit, not a wall clock (Art. VIII)."""
    smt = load_registry(document).get(*DEPLOY).critic("smt.version-contract")
    assert smt is not None
    assert smt.kind is CriticKind.SMT and smt.hard
    assert smt.rlimit == 2_000_000
    assert smt.timeout_ms == defaults.DEFAULT_CRITIC_TIMEOUT_MS


def test_reference_monitor_critic_is_advisory_inside_an_enforce_class(
    document: dict[str, Any],
) -> None:
    """Phase 1 enforces ``WF-06a`` at token issue; the monitor is advisory until Phase 3."""
    fsa = load_registry(document).get(*DEPLOY).critic("fsa.deploy-order")
    assert fsa is not None
    assert fsa.kind is CriticKind.MONITOR and fsa.hard
    assert fsa.mode is Mode.ADVISORY
    assert fsa.mode.rank < Mode.ENFORCE.rank
    assert fsa.properties == ("WF-06a",)


def test_reference_rollback_class_is_destructive_and_approvable(document: dict[str, Any]) -> None:
    """Section 3.5: rollback is a distinct class requiring approval on a short TTL."""
    rollback = load_registry(document).get(*ROLLBACK)
    assert rollback.effect_class is EffectClass.DESTRUCTIVE
    assert rollback.approvable
    assert rollback.approval_ttl_seconds < defaults.DEFAULT_APPROVAL_TTL_SECONDS
    assert rollback.batch_policy is BatchPolicy.ALL_OR_NOTHING
    assert rollback.repair_budget == 0
    assert rollback.escalate_on == frozenset(
        {ReasonName.SOLVER_TIMEOUT, ReasonName.FACT_STALE}
    )
    escalatable = [fact.name for fact in rollback.required_facts if fact.escalatable]
    assert escalatable == ["deploy_state"]


def test_reference_read_class_takes_the_fast_path(document: dict[str, Any]) -> None:
    status = load_registry(document).get(*STATUS)
    assert status.effect_class.takes_fast_path
    assert not status.approvable and status.approver_groups == ()
    assert status.resource_key_template is None
    assert status.resource_key_for({"service": "checkout"}) is None


def test_reference_fixture_renders_the_lease_identity(document: dict[str, Any]) -> None:
    registry = load_registry(document)
    arguments = {"service": "checkout", "version": "1.4.0", "target": "production", "replicas": 3}
    assert registry.resource_key_for(*DEPLOY, arguments) == "service:checkout/target:production"


def test_reference_fixture_rejects_an_unenumerated_target(document: dict[str, Any]) -> None:
    """Scenario ``A-35``: an aliased target is not a canonical resource (``FR-34``)."""
    registry = load_registry(document)
    with pytest.raises(RegistryValidationError, match="canonical target enumeration"):
        registry.resource_key_for(*DEPLOY, {"service": "checkout", "target": "prod"})


# --- schema version (version.py, Art. II) -----------------------------------


def test_unreadable_schema_version_raises(document: dict[str, Any]) -> None:
    document["schema_version"] = "99.0"
    with pytest.raises(SchemaVersionError) as exc:
        load_registry(resign(document), verifier=NullVerifier())
    assert exc.value.schema_kind == SchemaKind.REGISTRY
    assert exc.value.found_version == "99.0"
    assert exc.value.reason_code.name is ReasonName.SCHEMA_INVALID


def test_schema_version_is_checked_before_integrity(document: dict[str, Any]) -> None:
    """An unreadable document cannot be meaningfully verified, so version comes first."""
    document["schema_version"] = "99.0"
    document["digest"] = "sha256:" + "0" * 64
    with pytest.raises(SchemaVersionError):
        load_registry(document)


def test_missing_schema_version_is_refused(document: dict[str, Any]) -> None:
    del document["schema_version"]
    with pytest.raises(RegistryValidationError, match="declares no schema_version"):
        load_registry(document, verifier=NullVerifier())


def test_every_readable_version_is_accepted(document: dict[str, Any]) -> None:
    for version in SchemaCompatibility.readable_versions(SchemaKind.REGISTRY):
        document["schema_version"] = version
        assert load_registry(resign(document)).schema_version == version


# --- integrity (SEC-05, FR-33) ----------------------------------------------


def test_digest_verifier_accepts_an_untampered_document(document: dict[str, Any]) -> None:
    DigestVerifier().verify(document)


def test_digest_verifier_catches_a_tampered_policy_field(document: dict[str, Any]) -> None:
    """The registry decides what is enforced; an unverified one is attacker policy."""
    document["action_classes"][0]["mode"] = "shadow"
    with pytest.raises(RegistryIntegrityError, match="digest mismatch"):
        load_registry(document)


def test_digest_verifier_catches_a_tampered_approvability(document: dict[str, Any]) -> None:
    document["action_classes"][2]["approvable"] = True
    with pytest.raises(RegistryIntegrityError, match="digest mismatch"):
        DigestVerifier().verify(document)


def test_digest_verifier_catches_an_added_action_class(document: dict[str, Any]) -> None:
    smuggled = copy.deepcopy(document["action_classes"][0])
    smuggled["intent"] = "deploy_anything"
    document["action_classes"].append(smuggled)
    with pytest.raises(RegistryIntegrityError, match="digest mismatch"):
        load_registry(document)


def test_digest_verifier_refuses_a_document_with_no_digest(document: dict[str, Any]) -> None:
    """'No evidence of tampering' and 'no evidence' are the same thing here."""
    del document["digest"]
    with pytest.raises(RegistryIntegrityError, match="carries no digest"):
        load_registry(document)


def test_digest_verifier_refuses_a_malformed_digest(document: dict[str, Any]) -> None:
    document["digest"] = "md5:deadbeef"
    with pytest.raises(RegistryIntegrityError, match="malformed"):
        load_registry(document)


def test_digest_verifier_can_pin_an_out_of_band_digest(document: dict[str, Any]) -> None:
    pinned = DigestVerifier(document["digest"])
    pinned.verify(document)
    assert pinned.expected == Digest(document["digest"])

    other = copy.deepcopy(document)
    other["registry_version"] = "2026.09.19-1"
    resign(other)
    with pytest.raises(RegistryIntegrityError, match="not the pinned digest"):
        load_registry(other, verifier=pinned)


def test_digest_verification_is_the_default(document: dict[str, Any]) -> None:
    """Skipping verification must be an explicit, greppable choice."""
    document["registry_version"] = "tampered"
    with pytest.raises(RegistryIntegrityError):
        load_registry(document)
    assert load_registry(document, verifier=NullVerifier()).registry_version == "tampered"


def test_integrity_failure_carries_the_infrastructure_reason(document: dict[str, Any]) -> None:
    """``REGISTRY_INTEGRITY_FAILED`` is non-escalating (section 5.5)."""
    del document["digest"]
    with pytest.raises(RegistryIntegrityError) as exc:
        load_registry(document)
    assert exc.value.reason_code.name is ReasonName.REGISTRY_INTEGRITY_FAILED
    assert exc.value.reason_code.is_infrastructure


def test_unsigned_fields_are_excluded_from_the_digest(document: dict[str, Any]) -> None:
    baseline = compute_registry_digest(document)
    for field in UNSIGNED_FIELDS:
        probe = copy.deepcopy(document)
        probe[field] = "whatever"
        assert compute_registry_digest(probe) == baseline


def test_custom_verifier_is_honoured(document: dict[str, Any]) -> None:
    class RefusingVerifier:
        def verify(self, doc: Any) -> None:
            raise RegistryIntegrityError("this deployment refuses every registry")

    assert isinstance(RefusingVerifier(), SignatureVerifier)
    with pytest.raises(RegistryIntegrityError, match="refuses every registry"):
        load_registry(document, verifier=RefusingVerifier())


# --- canonicalization --------------------------------------------------------


def test_canonical_bytes_are_key_order_independent() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})
    assert canonical_bytes({"a": 1}) == b'{"a":1}'


def test_canonical_bytes_delegate_to_the_shared_canonicaliser(document: dict[str, Any]) -> None:
    """One definition of a document's byte identity, not two (``FR-04``)."""
    assert canonical_bytes(document) == canonicalize(document)
    payload = {k: v for k, v in document.items() if k not in UNSIGNED_FIELDS}
    assert compute_registry_digest(document) == digest_value(payload)


def test_canonical_bytes_reject_an_unserialisable_value() -> None:
    """A value with no canonical form has no stable identity to sign."""
    with pytest.raises(CanonicalizationError, match="has no JSON form"):
        canonical_bytes({"action_classes": [{"critics": object()}]})
    with pytest.raises(CanonicalizationError):
        compute_registry_digest({"action_classes": object()})


def test_digest_is_stable_across_reserialisation(document: dict[str, Any]) -> None:
    reserialised = json.loads(json.dumps(document, sort_keys=True))
    assert compute_registry_digest(reserialised) == compute_registry_digest(document)


# --- validation rules surfaced by the loader (FR-33) ------------------------


def deploy_entry(document: dict[str, Any]) -> dict[str, Any]:
    return document["action_classes"][0]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda e: e.__setitem__("escalate_on", ["POLICY_ENGINE_UNAVAILABLE"]),
            "non-escalatable reason",
            id="rule-a-non-escalatable-reason",
        ),
        pytest.param(
            lambda e: (e.__setitem__("approvable", False), e.__setitem__("approver_groups", []),
                       e.__setitem__("escalate_on", ["SOLVER_UNKNOWN"])),
            "not approvable",
            id="rule-b-escalation-on-non-approvable-class",
        ),
        pytest.param(
            lambda e: e["critics"].append(
                {"id": "fsa.halt", "kind": "monitor", "hard": True, "mode": "halted",
                 "source_requirement": "WF-06a"}
            ),
            "more enforcing than the class mode",
            id="rule-c-critic-more-enforcing-than-class",
        ),
        pytest.param(
            lambda e: e.__setitem__("repair_budget", defaults.MAX_REPAIR_BUDGET + 1),
            "less than or equal to",
            id="rule-d-repair-budget-above-ceiling",
        ),
        pytest.param(
            lambda e: e.__setitem__("repair_budget", -1),
            "greater than or equal to",
            id="rule-d-negative-repair-budget",
        ),
        pytest.param(
            lambda e: e["critics"][0].__setitem__("source_requirement", ""),
            "source_requirement",
            id="rule-e-empty-source-requirement",
        ),
        pytest.param(
            lambda e: e["critics"][0].pop("source_requirement"),
            "source_requirement",
            id="rule-e-missing-source-requirement",
        ),
        pytest.param(
            lambda e: e.__setitem__("effect_class", "read"),
            "never approvable",
            id="rule-f-approvable-read-class",
        ),
        pytest.param(
            lambda e: (e.__setitem__("effect_class", "destructive"),
                       e.__setitem__("approvable", False),
                       e.__setitem__("approver_groups", [])),
            "must be approvable or halted",
            id="rule-g-destructive-neither-approvable-nor-halted",
        ),
        pytest.param(
            lambda e: e["argument_schema"].__setitem__("additionalProperties", True),
            "additionalProperties",
            id="rule-h-open-argument-schema",
        ),
        pytest.param(
            lambda e: e["argument_schema"].pop("additionalProperties"),
            "additionalProperties",
            id="rule-h-missing-additional-properties",
        ),
        pytest.param(
            lambda e: e["required_facts"][0].__setitem__("escalatable", True),
            "required and escalatable",
            id="rule-j-required-escalatable-fact",
        ),
    ],
)
def test_loader_rejects_an_invalid_entry_naming_the_class_and_rule(
    document: dict[str, Any], mutate: Any, expected: str
) -> None:
    mutate(deploy_entry(document))
    with pytest.raises(RegistryValidationError) as exc:
        load_registry(resign(document))
    assert expected in str(exc.value)
    assert "'deployment.apply'/'deploy_service'" in str(exc.value)
    assert exc.value.reason_code.name is ReasonName.REGISTRY_INTEGRITY_FAILED


def test_loader_rejects_a_duplicate_tool_intent_pair(document: dict[str, Any]) -> None:
    """Rule (i): ``(tool, intent)`` is the identity of a class (``FR-30``)."""
    document["action_classes"].append(copy.deepcopy(document["action_classes"][0]))
    with pytest.raises(RegistryValidationError, match="duplicate action class"):
        load_registry(resign(document))


def test_loader_names_an_entry_it_cannot_identify(document: dict[str, Any]) -> None:
    document["action_classes"].append({"intent": "mystery", "mode": "enforce"})
    with pytest.raises(RegistryValidationError, match="at index 3"):
        load_registry(resign(document))


def test_loader_accepts_the_good_case_for_every_rule(document: dict[str, Any]) -> None:
    """The mirror of the rejection table: the unmutated fixture satisfies all of them."""
    registry = load_registry(document)
    for entry in registry:
        assert entry.escalate_on <= frozenset(
            {ReasonName.SOLVER_UNKNOWN, ReasonName.SOLVER_TIMEOUT,
             ReasonName.FACT_MISSING, ReasonName.FACT_STALE}
        )
        assert entry.approvable or not entry.escalate_on
        assert all(c.mode.rank <= entry.mode.rank for c in entry.critics)
        assert 0 <= entry.repair_budget <= defaults.MAX_REPAIR_BUDGET
        assert all(c.source_requirement.strip() for c in entry.critics)
        assert not (entry.effect_class.takes_fast_path and entry.approvable)
        assert not (entry.effect_class.takes_fast_path and entry.approver_groups)
        assert entry.effect_class is not EffectClass.DESTRUCTIVE or (
            entry.approvable or entry.is_halted
        )
        assert entry.argument_schema["additionalProperties"] is False
        assert all(not (f.required and f.escalatable) for f in entry.required_facts)
    assert len({e.key for e in registry}) == len(registry)


# --- document shape ----------------------------------------------------------


def test_unknown_top_level_field_is_refused(document: dict[str, Any]) -> None:
    document["default_mode"] = "advisory"
    with pytest.raises(RegistryValidationError, match="unknown top-level field"):
        load_registry(resign(document))


def test_missing_registry_version_is_refused(document: dict[str, Any]) -> None:
    del document["registry_version"]
    with pytest.raises(RegistryValidationError, match="declares no registry_version"):
        load_registry(resign(document))


def test_missing_action_classes_is_refused(document: dict[str, Any]) -> None:
    del document["action_classes"]
    with pytest.raises(RegistryValidationError, match="declares no action_classes"):
        load_registry(resign(document))


@pytest.mark.parametrize("value", [{}, "action_classes", 7])
def test_action_classes_must_be_a_list(document: dict[str, Any], value: Any) -> None:
    document["action_classes"] = value
    with pytest.raises(RegistryValidationError, match="must be a list|declares no action_classes"):
        load_registry(resign(document))


def test_non_mapping_document_is_refused() -> None:
    with pytest.raises(RegistryValidationError, match="must be a mapping"):
        load_registry(["not", "a", "registry"])  # type: ignore[arg-type]


def test_malformed_resource_keys_are_refused(document: dict[str, Any]) -> None:
    document["resource_keys"]["enumerations"]["target"] = []
    with pytest.raises(RegistryValidationError, match="resource_keys rejected"):
        load_registry(resign(document))


def test_resource_keys_must_be_an_object(document: dict[str, Any]) -> None:
    document["resource_keys"] = ["service", "target"]
    with pytest.raises(RegistryValidationError, match="resource_keys must be an object"):
        load_registry(resign(document))


def test_a_document_may_omit_the_optional_fields(document: dict[str, Any]) -> None:
    """``resource_keys``, ``unregistered_class_policy`` and ``digest`` are optional.

    Omitting the digest is only possible with an explicit :class:`NullVerifier`;
    the policy then falls back to the fail-closed default.
    """
    minimal = {
        "schema_version": document["schema_version"],
        "registry_version": document["registry_version"],
        "action_classes": [document["action_classes"][2]],
    }
    registry = load_registry(minimal, verifier=NullVerifier())
    assert registry.digest is None
    assert registry.unregistered_class_policy is UnregisteredClassPolicy.STRICT
    assert registry.resource_keys.enumerations == {}
    assert len(registry) == 1


# --- FR-31 strict vs permissive ---------------------------------------------


def test_unregistered_class_abstains_under_strict(document: dict[str, Any]) -> None:
    """Scenario ``A-19``: an unregistered class abstains in strict mode."""
    registry = load_registry(document)
    assert registry.is_strict
    with pytest.raises(UnregisteredActionClassError) as exc:
        registry.resolve("shell.exec", "run")
    assert exc.value.reason_code.name is ReasonName.ACTION_CLASS_UNREGISTERED
    assert exc.value.reason_code.is_infrastructure


def test_unregistered_class_is_policy_only_under_permissive(document: dict[str, Any]) -> None:
    document["unregistered_class_policy"] = "permissive"
    registry = load_registry(resign(document))
    assert not registry.is_strict
    assert registry.resolve("shell.exec", "run") is None
    assert registry.resolve(*DEPLOY) is not None
    # get() still insists on a registered class; there is no stand-in to return.
    with pytest.raises(UnregisteredActionClassError):
        registry.get("shell.exec", "run")


def test_unknown_unregistered_class_policy_is_refused(document: dict[str, Any]) -> None:
    document["unregistered_class_policy"] = "lenient"
    with pytest.raises(RegistryValidationError):
        load_registry(resign(document))


# --- file loading ------------------------------------------------------------


def test_load_registry_file_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RegistryValidationError, match="cannot read registry file"):
        load_registry_file(tmp_path / "absent.json")


def test_load_registry_file_reports_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RegistryValidationError, match="malformed JSON"):
        load_registry_file(path)


def test_load_registry_file_refuses_a_repeated_object_key(tmp_path: Path) -> None:
    """A registry whose meaning depends on the parser has no single meaning.

    ``json.loads`` keeps the last of a repeated key and says nothing, so a
    document carrying ``"mode": "enforce"`` and ``"mode": "shadow"`` could be
    signed against one reading and enforced under another.
    """
    path = tmp_path / "registry.json"
    path.write_text(
        '{"schema_version": "1.0", "mode": "enforce", "mode": "shadow"}', encoding="utf-8"
    )
    with pytest.raises(RegistryValidationError, match="ambiguous JSON"):
        load_registry_file(path)


def test_load_registry_file_reports_a_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RegistryValidationError, match="does not contain a registry object"):
        load_registry_file(path)


def test_load_registry_file_reads_yaml_when_pyyaml_is_available(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    """The technical plan allows a signed YAML or JSON registry (section 4.4)."""
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    assert load_registry_file(path).registry_version == document["registry_version"]


def test_load_registry_file_reports_malformed_yaml(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "registry.yaml"
    path.write_text("action_classes: [\n  - unclosed", encoding="utf-8")
    with pytest.raises(RegistryValidationError, match="malformed YAML"):
        load_registry_file(path)


def test_load_registry_file_round_trips_a_resigned_document(
    tmp_path: Path, document: dict[str, Any]
) -> None:
    document["registry_version"] = "2026.09.19-1"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(resign(document)), encoding="utf-8")
    assert load_registry_file(path).registry_version == "2026.09.19-1"
