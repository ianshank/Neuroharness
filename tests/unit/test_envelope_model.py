"""Unit tests for the action-envelope models.

These cover the behaviour the JSON Schema cannot express on its own: the typed
exception on an unreadable version, immutability, and the domain helpers that
downstream rules will read. The helper tests are not conveniences - the
distinction :attr:`Context.human_principals` draws between a claimed human and a
*proven* one is ``SEC-13``, and a helper that got it wrong would hand every
authorization rule a false positive.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import pytest
from pydantic import ValidationError

from neuroharness.canonical.digest import PROPOSAL_DIGEST_PROJECTION, proposal_digest
from neuroharness.envelope import canonical_document
from neuroharness.errors import SchemaVersionError
from neuroharness.models import envelope as envelope_module
from neuroharness.models.common import FactStatus, Principal
from neuroharness.models.envelope import (
    ABSENT,
    ActionClassRef,
    ActionEnvelope,
    Actor,
    Context,
    CredentialStatus,
    DelegationHop,
    Fact,
    Proposal,
    VersionedArtifact,
)
from neuroharness.version import SchemaCompatibility, SchemaKind

AT: Final[datetime] = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
TRACE_ID: Final[str] = "4bf92f3577b34da6a3ce929d0e0e4736"
ACTION_ID: Final[UUID] = UUID("018f3e5c-1a2b-7c3d-8e4f-000000000001")

#: An envelope version sharing its MAJOR component with the one this build reads
#: and differing in the MINOR. Kept as a literal so the test still discriminates
#: if the readable set is edited; the companion assertion in
#: ``test_schema_versions.py`` pins the readable set it is chosen against.
UNKNOWN_MINOR_VERSION: Final[str] = "1.99"


def digest(marker: int) -> str:
    """A syntactically valid ``sha256:`` digest, stable across runs."""
    return "sha256:" + (f"{marker:02x}" * 32)


def fresh_fact(**overrides: Any) -> Fact:
    """A complete fresh fact; ``overrides`` remove or replace parts of it."""
    fields: dict[str, Any] = {
        "name": "change_ticket",
        "key": {"service": "example-api"},
        "source": "change-management",
        "provider_version": "1.4.0",
        "status": "fresh",
        "value": {"ticket": "CHG-9"},
        "fetched_at": AT - timedelta(seconds=10),
        "ttl_seconds": 300,
        "digest": digest(0xB4),
    }
    fields.update(overrides)
    return Fact(**{key: value for key, value in fields.items() if value is not ABSENT})


def stub_fact(status: str = "stale", **overrides: Any) -> Fact:
    """A status-only stub, as ``FR-11`` requires for a non-fresh fact."""
    fields: dict[str, Any] = {
        "name": "quota",
        "key": {"target": "production"},
        "source": "quota-service",
        "provider_version": "0.9.1",
        "status": status,
    }
    fields.update(overrides)
    return Fact(**fields)


def actor(
    chain: tuple[DelegationHop, ...] | None = None,
    principal: str = "user:alice",
) -> Actor:
    """An actor with a single verified human hop unless told otherwise."""
    return Actor(
        agent_id="agent-7",
        agent_version="2.1.0",
        principal=principal,
        delegation_chain=chain
        or (DelegationHop(principal="user:alice", credential_status="verified"),),
        environment="production",
    )


def context(**overrides: Any) -> Context:
    """A minimal but complete harness-authored context."""
    fields: dict[str, Any] = {
        "tenant_id": "acme",
        "session_id": "session-root-0",
        "session_root_id": "session-root-0",
        "trace_id": TRACE_ID,
        "proposed_at": AT,
        "repair_iteration": 0,
        "actor": actor(),
        "action_class": ActionClassRef(registered=True, mode="enforce", effect_class="write"),
        "facts": (fresh_fact(),),
        "policy_bundle": VersionedArtifact(version="2026.09.1", digest=digest(0xA3)),
        "registry": VersionedArtifact(version="2026.09.1", digest=digest(0xA4)),
        "critic_versions": {"smt.deploy": "1.2.0+z3-4.13.0"},
    }
    fields.update(overrides)
    return Context(**fields)


def proposal(**overrides: Any) -> Proposal:
    """A minimal agent-authored proposal."""
    fields: dict[str, Any] = {
        "tool": "deploy.service",
        "intent": "rollout",
        "arguments": {"service": "example-api"},
    }
    fields.update(overrides)
    return Proposal(**fields)


def envelope(**overrides: Any) -> ActionEnvelope:
    """A complete, valid envelope."""
    fields: dict[str, Any] = {
        "action_id": ACTION_ID,
        "proposal": proposal(),
        "context": context(),
    }
    fields.update(overrides)
    return ActionEnvelope(**fields)


# --- Schema version ----------------------------------------------------------


def test_schema_version_defaults_to_the_version_this_build_writes() -> None:
    """A model built in code carries the build's own wire version."""
    assert envelope().schema_version == SchemaCompatibility.written_version(SchemaKind.ENVELOPE)


def test_unknown_schema_version_raises_schema_version_error() -> None:
    """An unreadable version is a fail-closed outcome, not a field error.

    Constitution Art. II: partially understanding a document is how an evidence
    field goes missing unnoticed, so the refusal is typed and carries the
    ``SCHEMA_INVALID`` reason code rather than arriving as a generic
    ``ValidationError`` a caller might treat as a malformed input.
    """
    with pytest.raises(SchemaVersionError) as raised:
        envelope(schema_version="9.9")
    assert raised.value.schema_kind == SchemaKind.ENVELOPE
    assert raised.value.found_version == "9.9"
    assert raised.value.reason_code.render() == "SCHEMA_INVALID"


def test_a_known_major_with_an_unknown_minor_is_refused_too() -> None:
    """A minor revision is still a revision this build has not been taught.

    The negative above changes the MAJOR component, as every other schema
    negative in the suite does, so a build that compared only the major would
    satisfy all of them while accepting an envelope written by a future minor.
    It would then read that envelope with this build's field list: whatever the
    newer minor added - a fact, a claim, a delegation hop a rule reads - is
    absent, and the evaluation proceeds as if it had never existed.
    """
    with pytest.raises(SchemaVersionError) as raised:
        envelope(schema_version=UNKNOWN_MINOR_VERSION)
    assert raised.value.found_version == UNKNOWN_MINOR_VERSION
    assert raised.value.reason_code.render() == "SCHEMA_INVALID"


def test_unknown_schema_version_is_rejected_on_parse_too() -> None:
    """The same refusal applies to a document read off the wire."""
    document = envelope().model_dump(mode="json")
    document["schema_version"] = "2.0"
    with pytest.raises(SchemaVersionError):
        ActionEnvelope.model_validate(document)


# --- Immutability and closed shape ------------------------------------------


@pytest.mark.parametrize(
    "model,field,value",
    [
        (envelope(), "action_id", UUID("018f3e5c-1a2b-7c3d-8e4f-000000000099")),
        (proposal(), "tool", "other.tool"),
        (context(), "tenant_id", "attacker"),
        (fresh_fact(), "status", FactStatus.STALE),
    ],
)
def test_envelope_models_are_frozen(model: Any, field: str, value: Any) -> None:
    """A digested document that can be edited afterwards has no identity.

    Both digests (``FR-04``) are taken over these objects; mutability would let
    what was evaluated differ from what was recorded.
    """
    with pytest.raises(ValidationError):
        setattr(model, field, value)


# --- The module docstring must not restate a superseded projection (F10) -----


def _proposal_digest_paragraph() -> str:
    """Return the part of the envelope module docstring about the proposal digest."""
    doc = envelope_module.__doc__ or ""
    _, _, after = doc.partition("* the **proposal digest**")
    paragraph, _, _ = after.partition("* the **envelope digest**")
    assert paragraph, "the envelope module docstring no longer describes the two digests"
    return paragraph


def test_the_docstring_points_at_the_live_proposal_digest_projection() -> None:
    """``ADR-0020`` narrowed ``ADR-0015``'s projection; the prose said otherwise.

    The docstring repeated ``proposal + context.actor + context.action_class +
    context.policy_bundle``, which is the superseded projection. The definition
    of what a human approval authorises may exist in exactly one place, and that
    place is the constant the code actually uses (``FR-04``, ``FR-43``).
    """
    paragraph = _proposal_digest_paragraph()

    assert "PROPOSAL_DIGEST_PROJECTION" in paragraph
    assert "ADR-0020" in paragraph


def test_the_docstring_names_no_field_the_projection_does_not_cover() -> None:
    """A structural check, so a future restatement cannot drift silently.

    Every ``context.<field>`` the proposal-digest paragraph names must be a
    prefix of a real path in the projection. ``context.action_class`` - the
    field ``ADR-0020`` removed - is not, which is how this defect reads.
    """
    named = set(re.findall(r"``context\.([A-Za-z0-9_.]+)``", _proposal_digest_paragraph()))
    covered = {
        ".".join(path[1:][: depth])
        for path in PROPOSAL_DIGEST_PROJECTION
        if path[0] == "context"
        for depth in range(1, len(path))
    }

    assert named <= covered, f"docstring names fields outside the projection: {named - covered}"


# --- Mutable containers inside frozen models (F5) ----------------------------


@pytest.mark.parametrize(
    ("model_factory", "field", "key", "value"),
    [
        pytest.param(proposal, "arguments", "service", "somewhere-else", id="proposal-arguments"),
        pytest.param(fresh_fact, "key", "service", "someone-elses-api", id="fact-key"),
        pytest.param(context, "critic_versions", "smt.deploy", "0.0.0", id="critic-versions"),
    ],
)
def test_mapping_fields_cannot_be_mutated_in_place(
    model_factory: Any, field: str, key: str, value: Any
) -> None:
    """``frozen=True`` stops attribute assignment and nothing else.

    A frozen model holding a plain ``dict`` is only as immutable as its
    shallowest field: ``proposal.arguments["service"] = ...`` succeeded and
    changed the proposal digest, which is what an approval binds to
    (``FR-04``, ``ADR-0020``). The mapping is now read-only all the way down.
    """
    model = model_factory()
    mapping = getattr(model, field)

    with pytest.raises(TypeError):
        mapping[key] = value
    with pytest.raises(TypeError):
        del mapping[key]
    with pytest.raises(AttributeError):
        mapping.update({key: value})


def test_a_nested_container_is_frozen_too() -> None:
    """Freezing only the outer level would move the hole one key deeper."""
    model = proposal(arguments={"plan": {"replicas": 1, "regions": ["eu-west-1"]}})

    with pytest.raises(TypeError):
        model.arguments["plan"]["replicas"] = 99
    # Sequences become tuples for the same reason a mapping becomes a proxy.
    assert model.arguments["plan"]["regions"] == ("eu-west-1",)
    with pytest.raises(TypeError):
        model.arguments["plan"]["regions"][0] = "us-east-1"


def test_the_model_does_not_share_structure_with_the_callers_mapping() -> None:
    """Freezing is also the copy.

    Otherwise the caller keeps a live handle on the model's own arguments and
    can edit the digested document through it, which is the same defect with an
    extra step.
    """
    supplied: dict[str, Any] = {"service": "example-api"}
    model = proposal(arguments=supplied)

    supplied["service"] = "somewhere-else"

    assert model.arguments["service"] == "example-api"


def test_the_proposal_digest_survives_an_attempted_mutation() -> None:
    """The property the freeze exists for, stated as a digest (``FR-04``).

    Digested through :func:`neuroharness.envelope.canonical_document` rather
    than a bare ``model_dump``. This test used to call ``model_dump(mode="json")``
    directly, which was the repository's *second* serialization convention - the
    one that does not conform to the published schema and produces a different
    envelope digest. It happened not to matter here, because the proposal digest
    is a narrow projection that excludes facts (``ADR-0020``); it mattered a
    great deal one function over. See ``tests/unit/test_envelope_wire.py``.
    """
    before = proposal_digest(canonical_document(envelope()))

    model = envelope()
    with pytest.raises(TypeError):
        model.proposal.arguments["service"] = "somewhere-else"

    assert proposal_digest(canonical_document(model)) == before


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        pytest.param("arguments", {"service": "example-api"}, id="arguments"),
        pytest.param("claims", None, id="untouched-optional"),
    ],
)
def test_frozen_mappings_still_dump_as_plain_json(field: str, expected: Any) -> None:
    """The freeze may not leak into the wire form.

    ``model_dump(mode="json")`` is handed to a JSON encoder, to a JSON Schema
    validator and to the canonicaliser, none of which know what a
    ``mappingproxy`` is.
    """
    dumped = proposal().model_dump(mode="json")
    assert dumped[field] == expected
    if expected is not None:
        assert type(dumped[field]) is dict


def test_a_frozen_mapping_round_trips_through_its_own_dump() -> None:
    original = envelope()
    reparsed = ActionEnvelope.model_validate(original.model_dump(mode="json"))

    assert reparsed.proposal.arguments == original.proposal.arguments
    assert reparsed.context.critic_versions == original.context.critic_versions
    with pytest.raises(TypeError):
        reparsed.proposal.arguments["service"] = "somewhere-else"


def test_a_frozen_mapping_still_compares_equal_to_a_plain_dict() -> None:
    """Read-only must not mean differently-typed to every existing caller."""
    assert proposal().arguments == {"service": "example-api"}
    assert dict(proposal().arguments) == {"service": "example-api"}


def test_proposal_rejects_a_context_shaped_key() -> None:
    """``FR-03``: the model may not nominate its own identity.

    Context-shaped keys are stripped before validation and listed in
    ``stripped_proposal_keys``; a proposal that still carries one has not been
    through that step.
    """
    with pytest.raises(ValidationError):
        Proposal(
            tool="deploy.service",
            intent="rollout",
            arguments={},
            actor={"principal": "user:root"},
        )


def test_context_rejects_an_unknown_key() -> None:
    """An unrecognised context field is a field the harness cannot evaluate."""
    with pytest.raises(ValidationError):
        context(approved_by_model=True)


# --- Facts: FR-11 ------------------------------------------------------------


@pytest.mark.parametrize("missing", ["value", "fetched_at", "ttl_seconds", "digest"])
def test_fresh_fact_must_carry_its_value_and_provenance(missing: str) -> None:
    """``FR-10``/``FR-11``: a readable fact is a fully attributed one.

    Without the fetch time and TTL its staleness cannot be computed, and
    without the digest it cannot be replayed - so a "fresh" fact missing any of
    them is a value with no way to check it.
    """
    with pytest.raises(ValidationError) as raised:
        fresh_fact(**{missing: ABSENT if missing == "value" else None})
    assert missing in str(raised.value)


@pytest.mark.mutation
@pytest.mark.parametrize("status", ["stale", "missing", "provider_error"])
def test_non_fresh_fact_may_not_carry_a_value(status: str) -> None:
    """``FR-11``: stale, missing and errored facts are status-only stubs.

    This is the killing fixture for the rule: if a stale fact could carry a
    value, a rule whose author forgot the freshness check would read it and
    reach a conclusion the world no longer supports.
    """
    with pytest.raises(ValidationError) as raised:
        stub_fact(status=status, value={"limit": 10})
    assert "status-only stub" in str(raised.value)


def test_fresh_fact_may_carry_an_explicit_null_value() -> None:
    """``null`` is a legal fact value and is distinct from an absent one.

    The provider's ``value_schema`` decides what a value may be; the harness
    only decides whether one is present (``FR-11``, ``FR-14``).
    """
    fact = fresh_fact(value=None)
    assert fact.has_value
    assert fact.value is None
    assert fact.model_dump(mode="json", exclude_none=True)["value"] is None


def test_absent_and_null_fact_values_are_distinguishable() -> None:
    """The presence of ``value`` is the security property, not its content."""
    assert stub_fact().value is ABSENT
    assert not stub_fact().has_value
    assert "value" not in stub_fact().model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize(
    "fact,usable",
    [
        (fresh_fact(), True),
        (fresh_fact(value=None), True),
        (stub_fact("stale"), False),
        (stub_fact("missing"), False),
        (stub_fact("provider_error"), False),
    ],
)
def test_fact_is_usable_only_when_fresh_and_valued(fact: Fact, usable: bool) -> None:
    """``Fact.is_usable`` is what a rule consults before reading a value."""
    assert fact.is_usable is usable


def test_context_usable_facts_excludes_stubs() -> None:
    """A stale fact is still reported, but never as usable evidence."""
    ctx = context(facts=(fresh_fact(), stub_fact()))
    assert [fact.name for fact in ctx.usable_facts] == ["change_ticket"]
    assert ctx.fact("quota") is not None
    assert ctx.fact("quota").is_usable is False
    assert ctx.fact("never_requested") is None


# --- Delegation chain: SEC-13 ------------------------------------------------


def test_verified_human_hop_is_a_human_principal() -> None:
    """The baseline: a proven human delegation counts."""
    ctx = context(
        actor=actor(
            chain=(
                DelegationHop(principal="user:alice", credential_status="verified"),
                DelegationHop(principal="agent:deployer", credential_status="verified"),
            )
        )
    )
    assert ctx.human_principals == (Principal("user:alice"),)
    assert ctx.has_unverified_hop is False


@pytest.mark.mutation
@pytest.mark.parametrize("status", ["unverified", "expired"])
def test_unproven_human_hop_is_not_a_human_principal(status: str) -> None:
    """``SEC-13``: a claimed delegation is not a proven one.

    This is the killing fixture for the helper. A human identifier on an
    unverified or expired hop is an assertion about delegation, not evidence of
    it; counting it would make every authorization and separation-of-duties
    rule satisfiable by anyone who could assert a name. An *expired* credential
    is treated exactly like an unverified one for the same reason.
    """
    ctx = context(
        actor=actor(
            chain=(
                DelegationHop(principal="user:mallory", credential_status=status),
                DelegationHop(principal="agent:deployer", credential_status="verified"),
            )
        )
    )
    assert ctx.human_principals == ()
    assert ctx.has_unverified_hop is True


def test_non_human_verified_hop_is_not_a_human_principal() -> None:
    """``FR-42``: only a human principal can stand behind an approval."""
    ctx = context(
        actor=actor(
            chain=(
                DelegationHop(principal="service:scheduler", credential_status="verified"),
                DelegationHop(principal="agent:deployer", credential_status="verified"),
            )
        )
    )
    assert ctx.human_principals == ()
    assert ctx.has_unverified_hop is False


def test_chain_principals_keeps_every_hop_in_order() -> None:
    """``SEC-11``/``FR-48`` need the whole chain, verified or not.

    An unverified hop confers no authority but still taints evidence it
    asserted, so it must remain visible to the fact-laundering check.
    """
    chain = (
        DelegationHop(principal="user:alice", credential_status="verified"),
        DelegationHop(principal="service:scheduler", credential_status="unverified"),
        DelegationHop(principal="agent:deployer", credential_status="verified"),
    )
    assert actor(chain=chain).chain_principals == (
        Principal("user:alice"),
        Principal("service:scheduler"),
        Principal("agent:deployer"),
    )


def test_delegation_chain_may_not_be_empty() -> None:
    """Every action traces back to at least one principal (``FR-06``)."""
    with pytest.raises(ValidationError):
        Actor(
            agent_id="agent-7",
            agent_version="2.1.0",
            principal="user:alice",
            delegation_chain=(),
            environment="production",
        )


def test_credential_status_treats_expired_as_unproven() -> None:
    """An expired credential is not a weaker verified one (``SEC-13``)."""
    assert CredentialStatus.VERIFIED.is_verified is True
    assert CredentialStatus.UNVERIFIED.is_verified is False
    assert CredentialStatus.EXPIRED.is_verified is False


# --- Action class ------------------------------------------------------------


def test_action_class_key_comes_from_the_proposal() -> None:
    """``(tool, intent)`` selects which rules apply - and nothing more.

    ``INV-01``: the proposal may choose the class; the class's mode, effect
    class and approvability come from the registry, through the context.
    """
    assert envelope().action_class_key == ("deploy.service", "rollout")
    assert envelope().is_registered is True


def test_escalate_on_rejects_a_non_escalatable_reason() -> None:
    """Section 5.5: infrastructure abstentions may never reach a human.

    A human cannot vouch for a policy engine that is down, so offering them an
    approval button would manufacture consent for an unevaluated action.
    """
    with pytest.raises(ValidationError) as raised:
        ActionClassRef(
            registered=True,
            mode="enforce",
            effect_class="write",
            escalate_on=("POLICY_ENGINE_UNAVAILABLE",),
        )
    assert "POLICY_ENGINE_UNAVAILABLE" in str(raised.value)


def test_escalate_on_accepts_the_escalatable_set() -> None:
    """The four escalatable reasons are the only ones a class may declare."""
    action_class = ActionClassRef(
        registered=True,
        mode="enforce",
        effect_class="write",
        escalate_on=("SOLVER_UNKNOWN", "SOLVER_TIMEOUT", "FACT_MISSING", "FACT_STALE"),
    )
    assert len(action_class.escalate_on) == 4


def test_repair_iteration_is_bounded() -> None:
    """Section 5.4: beyond the ceiling the loop is a probing channel."""
    with pytest.raises(ValidationError):
        context(repair_iteration=11)
