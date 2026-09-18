"""The killing fixtures: each one breaks a gate and proves the gate notices.

Constitution Article III: *a gate without a killing fixture does not exist.*
These are those fixtures, for the gates this increment builds. Each test names
the catalogue identifier it kills, carries ``@pytest.mark.mutation`` so the
mutation stage selects it, and asserts the refusal the catalogue declares -
never merely that *something* failed, because a fixture that accepts any error
passes against a harness broken in an unrelated way and outlives the gate it
was written for.

The declarations in ``tests/fixtures/mutations/`` and these tests are checked
against each other by :mod:`tests.unit.test_mutation_fixtures`: a state cannot
claim a gate that nothing exercises, and a test cannot claim a fixture nobody
declared.

A ``partial`` fixture kills only the clause this increment owns. Each one below
says which clause that is and which component still owes the rest, so that a
green mutation stage is never mistaken for a complete gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import pytest
from pydantic import ValidationError

from neuroharness.errors import (
    RegistryValidationError,
    TokenModeMismatchError,
    TokenRevokedError,
)
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.models.record import Override
from neuroharness.reason import ReasonName
from neuroharness.registry.loader import compute_registry_digest, load_registry
from neuroharness.registry.resource_keys import ResourceKeyRegistry, render_template
from neuroharness.resolve.inputs import ResolutionRequest, SimpleClassPolicy
from neuroharness.resolve.resolver import resolve
from neuroharness.seams import DeterministicUuidGenerator, FrozenClock
from neuroharness.tokens.nonce import ConsumeOutcome, InMemoryNonceStore, InMemoryRevocationList
from neuroharness.tokens.service import TokenService
from neuroharness.tokens.signer import HmacSigner

pytestmark = pytest.mark.mutation

FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures"
REGISTRY_FIXTURE: Final[Path] = FIXTURE_DIR / "registry" / "reference_deploy_registry.json"
MUTATION_DIR: Final[Path] = FIXTURE_DIR / "mutations"

ANCHOR: Final[datetime] = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
HMAC_SECRET: Final[bytes] = b"mutation-fixture-secret-32-bytes"
ENVELOPE_DIGEST: Final[Digest] = Digest.from_hex("a" * 64)
OTHER_ENVELOPE_DIGEST: Final[Digest] = Digest.from_hex("f" * 64)
PROPOSAL_DIGEST: Final[Digest] = Digest.from_hex("b" * 64)
BUNDLE_DIGEST: Final[Digest] = Digest.from_hex("c" * 64)
RECORD_HASH: Final[Digest] = Digest.from_hex("e" * 64)
TENANT_ID: Final[str] = "acme"
BROKER_ID: Final[str] = "broker-1"

#: The two spellings threat ``T-17`` is named for. Two spellings of production
#: are two policies, one of which nobody reviewed.
UNCANONICAL_TARGETS: Final[tuple[str, ...]] = (
    "Production",
    "PRODUCTION",
    "production-eu",
    "prod",
    "prod-eu",
    " production",
)


def declaration(fixture_id: str) -> dict[str, Any]:
    """The checked-in declaration, so a test cannot drift from its catalogue entry."""
    return json.loads((MUTATION_DIR / f"{fixture_id}.json").read_text(encoding="utf-8"))


def registry_document() -> dict[str, Any]:
    return json.loads(REGISTRY_FIXTURE.read_text(encoding="utf-8"))


def resign(document: dict[str, Any]) -> dict[str, Any]:
    """Recompute the digest, so the mutation is tested and not the signature."""
    document["digest"] = str(compute_registry_digest(document))
    return document


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(ANCHOR)


@pytest.fixture()
def revocations() -> InMemoryRevocationList:
    return InMemoryRevocationList()


@pytest.fixture()
def nonces() -> InMemoryNonceStore:
    return InMemoryNonceStore()


@pytest.fixture()
def tokens(clock: FrozenClock, nonces: InMemoryNonceStore, revocations) -> TokenService:
    return TokenService(
        signer=HmacSigner(key_id="k1", secret=HMAC_SECRET),
        nonce_store=nonces,
        revocation_list=revocations,
        clock=clock,
        id_generator=DeterministicUuidGenerator(),
    )


def issue(
    service: TokenService,
    *,
    mode: Mode = Mode.ENFORCE,
    verdict: Verdict = Verdict.ALLOW,
    decision: int = 1,
):
    """Mint one token. ``decision`` varies the decision id.

    At most one token is issued per decision (``FR-20``), so a fixture that
    needs two live tokens must be about two decisions; reusing the id would
    exercise that rule instead of the one under test.
    """
    return service.issue(
        decision_id=str(UUID(int=decision)),
        envelope_digest=ENVELOPE_DIGEST,
        proposal_digest=PROPOSAL_DIGEST,
        policy_bundle_digest=BUNDLE_DIGEST,
        record_hash=RECORD_HASH,
        tenant_id=TENANT_ID,
        mode=mode,
        verdict=verdict,
    )


# --- MUT-07 (partial): an open argument schema has nowhere to hide -----------


def test_mut_07_an_open_argument_schema_is_refused_at_load() -> None:
    """``FR-02``. Owned here: the registry. Still missing: the envelope builder.

    The catalogue's mutation is an extra argument on a call. This increment has
    no component that validates a call's arguments, so what it can kill is the
    precondition that makes that validation meaningful: an action class whose
    ``argument_schema`` leaves ``additionalProperties`` open would accept an
    undeclared argument *by design*, and no later check could tell the
    difference between an argument the author meant to allow and one nobody
    thought about.

    Killing this at load is why the runtime check, when ``P1-02`` lands, has
    something to enforce.
    """
    assert declaration("MUT-07")["state"] == "partial"

    document = registry_document()
    document["action_classes"][0]["argument_schema"]["additionalProperties"] = True

    with pytest.raises(RegistryValidationError, match="additionalProperties"):
        load_registry(resign(document))


def test_mut_07_an_argument_schema_that_simply_omits_the_bound_is_refused_too() -> None:
    """Omission is the likelier mutation, and JSON Schema's default is open."""
    document = registry_document()
    del document["action_classes"][0]["argument_schema"]["additionalProperties"]

    with pytest.raises(RegistryValidationError, match="additionalProperties"):
        load_registry(resign(document))


# --- MUT-30 (active): two spellings of production are two policies -----------


@pytest.fixture()
def resource_keys() -> ResourceKeyRegistry:
    return ResourceKeyRegistry(enumerations=registry_document()["resource_keys"]["enumerations"])


@pytest.mark.parametrize("target", UNCANONICAL_TARGETS)
def test_mut_30_an_uncanonical_target_never_renders_a_resource_key(
    resource_keys: ResourceKeyRegistry, target: str
) -> None:
    """``FR-02``, ``FR-34``, scenario ``A-35``, threat ``T-17``.

    The enumeration is the whole control. ``production`` is reviewed;
    ``Production`` is not, and a case-insensitive or prefix-tolerant membership
    test would quietly admit it - producing a *different* lease identity for
    what an operator would read as the same target, so two concurrent
    productions deploys would not even exclude each other (``FR-25``).

    Parametrised over case, suffix and whitespace because the enumeration
    permits uppercase in the *grammar*: an identifier that is well-formed but
    unenumerated is precisely the case a shape check waves through.
    """
    with pytest.raises(RegistryValidationError):
        resource_keys.render(
            "service:{service}/target:{target}", {"service": "checkout", "target": target}
        )


@pytest.mark.parametrize("target", UNCANONICAL_TARGETS)
def test_mut_30_an_uncanonical_target_is_not_a_known_key(
    resource_keys: ResourceKeyRegistry, target: str
) -> None:
    """The same rule through the lookup the broker's lease would use."""
    key = f"service:checkout/target:{target}"
    assert not resource_keys.is_known(key)
    with pytest.raises(RegistryValidationError, match="canonical"):
        resource_keys.assert_known(key)


def test_mut_30_the_canonical_spelling_still_works(
    resource_keys: ResourceKeyRegistry,
) -> None:
    """The control must reject the variants without rejecting the real thing."""
    rendered = resource_keys.render(
        "service:{service}/target:{target}", {"service": "checkout", "target": "production"}
    )
    assert rendered == "service:checkout/target:production"
    assert resource_keys.is_known(rendered)


@pytest.mark.parametrize("service", ["Checkout", "checkout2", "check-out"])
def test_mut_30_the_rule_is_not_specific_to_one_enumeration(
    resource_keys: ResourceKeyRegistry, service: str
) -> None:
    """A gate that only guards ``target`` would pass the test above and leak here."""
    with pytest.raises(RegistryValidationError):
        render_template(
            "service:{service}/target:{target}",
            {"service": service, "target": "production"},
            resource_keys.enumerations,
        )


# --- MUT-34 (active): a class nobody may approve denies instead of asking ----


def test_mut_34_approval_on_a_non_approvable_class_denies() -> None:
    """``FR-45``, resolution step 6.

    The mutation is a policy that asks for approval on a class whose registry
    entry says no human may authorise it. The tempting failure is to ask anyway
    and let the approver decide - which routes an unauthorisable action to a
    person who has no way to know they are not permitted to grant it, and whose
    grant the harness would then honour.
    """
    resolution = resolve(
        ResolutionRequest(
            policy=SimpleClassPolicy(mode=Mode.ENFORCE, approvable=False),
            approval_required=True,
            approval_rule_id="WF-02",
        )
    )

    assert resolution.verdict is Verdict.DENY
    assert resolution.primary_reason is not None
    assert resolution.primary_reason.name is ReasonName.APPROVAL_NOT_PERMITTED
    assert resolution.primary_reason.subject == "WF-02", (
        "the reason must name the rule that asked, or an operator cannot find it"
    )


def test_mut_34_the_same_request_on_an_approvable_class_asks_instead() -> None:
    """The control has to distinguish the two, not deny everything."""
    resolution = resolve(
        ResolutionRequest(
            policy=SimpleClassPolicy(mode=Mode.ENFORCE, approvable=True),
            approval_required=True,
            approval_rule_id="WF-02",
        )
    )

    assert resolution.verdict is Verdict.REQUIRES_APPROVAL
    assert resolution.primary_reason is not None
    assert resolution.primary_reason.name is ReasonName.APPROVAL_REQUIRED


# --- MUT-20 (active): a shadow token cannot straddle a promotion -------------


def test_mut_20_a_shadow_token_is_refused_once_the_class_enforces(
    tokens: TokenService,
) -> None:
    """``FR-21`` step 6.

    A class is promoted from ``shadow`` to ``enforce`` while a token minted
    under the old mode is still in flight. That token was issued *without* the
    verdict being consulted, so honouring it after promotion executes exactly
    the action the promotion was meant to start blocking - the rollout would
    have a window in which enforcement is announced and absent.
    """
    signed = issue(tokens, mode=Mode.SHADOW, verdict=Verdict.DENY)
    assert signed.token.shadow, "a shadow class must mint a shadow token (ADR-0016)"

    with pytest.raises(TokenModeMismatchError) as raised:
        tokens.verify(
            signed,
            envelope_digest=ENVELOPE_DIGEST,
            tenant_id=TENANT_ID,
            current_mode=Mode.ENFORCE,
            current_bundle_digest=BUNDLE_DIGEST,
        )
    assert raised.value.reason_code.render() == "TOKEN_INVALID:mode_mismatch"


def test_mut_20_an_enforce_token_is_refused_after_a_demotion(
    tokens: TokenService,
) -> None:
    """The binding is equality, not "at least as strict": both directions refuse.

    A one-sided check would let an ``enforce`` token be spent against a class
    that has since been demoted, and the receipt would record an enforcement
    that was no longer in force.
    """
    signed = issue(tokens, mode=Mode.ENFORCE)

    with pytest.raises(TokenModeMismatchError):
        tokens.verify(
            signed,
            envelope_digest=ENVELOPE_DIGEST,
            tenant_id=TENANT_ID,
            current_mode=Mode.ADVISORY,
            current_bundle_digest=BUNDLE_DIGEST,
        )


# --- MUT-36 (active): revocation outranks the token's own claims -------------


def test_mut_36_a_revoked_token_is_refused(
    tokens: TokenService, revocations: InMemoryRevocationList
) -> None:
    """``FR-21``, ``FR-48``.

    An operator revokes a token that is otherwise perfect: signature valid, not
    expired, right tenant, right envelope, right mode. If any of those checks
    could satisfy verification on its own, revocation would be advice rather
    than a control, and the incident lever would not work during the incident.
    """
    signed = issue(tokens)
    tokens.verify(
        signed,
        envelope_digest=ENVELOPE_DIGEST,
        tenant_id=TENANT_ID,
        current_mode=Mode.ENFORCE,
        current_bundle_digest=BUNDLE_DIGEST,
    )

    revocations.revoke_token(signed.token.token_id, reason="incident-4471")

    with pytest.raises(TokenRevokedError) as raised:
        tokens.verify(
            signed,
            envelope_digest=ENVELOPE_DIGEST,
            tenant_id=TENANT_ID,
            current_mode=Mode.ENFORCE,
            current_bundle_digest=BUNDLE_DIGEST,
        )
    assert raised.value.reason_code.render() == "TOKEN_INVALID:revoked"


def test_mut_36_revoking_a_key_refuses_every_token_it_signed(
    tokens: TokenService, revocations: InMemoryRevocationList
) -> None:
    """Key compromise is the case revocation exists for, and it is not per-token."""
    first, second = issue(tokens, decision=1), issue(tokens, decision=2)

    revocations.revoke_key("k1", reason="key-compromise")

    for signed in (first, second):
        with pytest.raises(TokenRevokedError):
            tokens.verify(
                signed,
                envelope_digest=ENVELOPE_DIGEST,
                tenant_id=TENANT_ID,
                current_mode=Mode.ENFORCE,
                current_bundle_digest=BUNDLE_DIGEST,
            )


# --- MUT-09 (partial): one token, one execution ------------------------------


def test_mut_09_a_consumed_token_cannot_be_consumed_again(
    tokens: TokenService, clock: FrozenClock
) -> None:
    """``FR-22``. Owned here: the nonce store. Still missing: the broker (``P1-08``).

    The replay is the same token presented twice for the same envelope by two
    different brokers - the shape a retry storm or a duplicated queue message
    produces, and the one an attacker reproduces deliberately. The first
    consumption dispatches; the second must not, or "single use" is a comment.
    """
    assert declaration("MUT-09")["state"] == "partial"

    signed = issue(tokens)
    first = tokens.consume(
        signed,
        envelope_digest=ENVELOPE_DIGEST,
        broker_id=BROKER_ID,
        tenant_id=TENANT_ID,
        current_mode=Mode.ENFORCE,
        current_bundle_digest=BUNDLE_DIGEST,
    )
    assert first.permits_dispatch

    clock.advance(1)
    from neuroharness.errors import TokenConsumedError

    with pytest.raises(TokenConsumedError) as raised:
        tokens.consume(
            signed,
            envelope_digest=ENVELOPE_DIGEST,
            broker_id="broker-2",
            tenant_id=TENANT_ID,
            current_mode=Mode.ENFORCE,
            current_bundle_digest=BUNDLE_DIGEST,
        )
    assert raised.value.reason_code.render() == "TOKEN_INVALID:consumed"


def test_mut_09_a_duplicate_delivery_is_not_a_replay(tokens: TokenService) -> None:
    """The distinction the gate must keep, or every retry looks like an attack.

    Same broker, same envelope: a redelivered message. It answers from the
    original outcome and does not dispatch again, which is different from
    refusing - and confusing the two either pages someone for a retry or lets a
    real replay through as one.
    """
    signed = issue(tokens)
    arguments = {
        "envelope_digest": ENVELOPE_DIGEST,
        "broker_id": BROKER_ID,
        "tenant_id": TENANT_ID,
        "current_mode": Mode.ENFORCE,
        "current_bundle_digest": BUNDLE_DIGEST,
    }
    tokens.consume(signed, **arguments)
    again = tokens.consume(signed, **arguments)

    assert again.duplicate_delivery
    assert not again.permits_dispatch
    assert again.outcome is ConsumeOutcome.DUPLICATE_DELIVERY


# --- MUT-10 (partial): the token is bound to one envelope --------------------


def test_mut_10_an_edited_envelope_invalidates_the_token(tokens: TokenService) -> None:
    """``FR-21``. Owned here: verification. Still missing: the broker (``P1-08``).

    The envelope is edited after the ``ALLOW``. The token is genuine and
    unexpired; what has changed is the thing it authorised. Verification is
    handed the digest recomputed from what is about to run, never the one the
    token carries, because comparing a token to itself proves nothing.
    """
    assert declaration("MUT-10")["state"] == "partial"

    signed = issue(tokens)

    from neuroharness.errors import TokenDigestMismatchError

    with pytest.raises(TokenDigestMismatchError) as raised:
        tokens.verify(
            signed,
            envelope_digest=OTHER_ENVELOPE_DIGEST,
            tenant_id=TENANT_ID,
            current_mode=Mode.ENFORCE,
            current_bundle_digest=BUNDLE_DIGEST,
        )
    assert raised.value.reason_code.render() == "TOKEN_INVALID:digest_mismatch"


def test_mut_10_a_refused_presentation_does_not_burn_the_nonce(
    tokens: TokenService,
) -> None:
    """Otherwise a failed attack is a successful denial of service.

    Presenting a stolen token against the wrong envelope would destroy the
    legitimate authorisation if consumption preceded verification.
    """
    signed = issue(tokens)
    from neuroharness.errors import TokenDigestMismatchError

    with pytest.raises(TokenDigestMismatchError):
        tokens.consume(
            signed,
            envelope_digest=OTHER_ENVELOPE_DIGEST,
            broker_id="attacker",
            tenant_id=TENANT_ID,
            current_mode=Mode.ENFORCE,
            current_bundle_digest=BUNDLE_DIGEST,
        )

    result = tokens.consume(
        signed,
        envelope_digest=ENVELOPE_DIGEST,
        broker_id=BROKER_ID,
        tenant_id=TENANT_ID,
        current_mode=Mode.ENFORCE,
        current_bundle_digest=BUNDLE_DIGEST,
    )
    assert result.permits_dispatch, "the legitimate holder must still be able to spend it"


# --- MUT-26 (partial): a loosening override is never one person's decision ---


def _demotion(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "override_id": str(UUID(int=26)),
        "kind": "demote_mode",
        "target": "deployment.apply/deploy_service",
        "authorized_by": ("user:alice", "user:bob"),
        "reason_code": "CLASS_HALTED",
        "effective_at": ANCHOR,
        "expires_at": ANCHOR + timedelta(minutes=30),
        "ticket_ref": "INC-4471",
    }
    fields.update(overrides)
    return fields


def test_mut_26_a_single_principal_demotion_is_refused() -> None:
    """``FR-48``, ``SEC-14``. Owned here: the override model. Missing: eligibility.

    ``demote_mode`` is the only override that *reduces* enforcement, so it is
    the only one a compromised operator account would want. Two principals is
    the control; one is the mutation.
    """
    assert declaration("MUT-26")["state"] == "partial"

    with pytest.raises(ValidationError, match="distinct"):
        Override(**_demotion(authorized_by=("user:alice",)))


def test_mut_26_two_signatures_from_one_principal_are_one_decision() -> None:
    """The likelier real-world mutation: distinctness, not arity.

    A count check passes here, and one person wearing two hats is exactly what
    two-person review exists to prevent.
    """
    with pytest.raises(ValidationError, match="distinct"):
        Override(**_demotion(authorized_by=("user:alice", "user:alice")))


@pytest.mark.parametrize("dropped", ["expires_at", "ticket_ref"])
def test_mut_26_a_demotion_must_expire_and_cite_a_ticket(dropped: str) -> None:
    """An override that outlives its incident is an unreviewed policy change."""
    fields = _demotion()
    del fields[dropped]

    with pytest.raises(ValidationError, match=dropped):
        Override(**fields)


def test_mut_26_a_tightening_override_needs_only_one_principal() -> None:
    """The control must not make halting an incident harder than causing one."""
    halt = Override(
        override_id=str(UUID(int=27)),
        kind="halt_class",
        target="deployment.apply/deploy_service",
        authorized_by=("user:alice",),
        reason_code="CLASS_HALTED",
        effective_at=ANCHOR,
    )
    assert halt.is_tightening
