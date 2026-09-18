"""Issuance preconditions and broker verification (``FR-20``-``FR-23``).

Each test names the exact exception type, because the type *is* the reason code
the decision record will carry: ``TOKEN_INVALID`` alone tells an operator
nothing, and a gate that refuses for the wrong stated reason is a gate nobody
can debug during an incident.

Time and identifiers come from :class:`FrozenClock` and
:class:`SequenceIdGenerator`, so every assertion below is about the harness and
none is about the machine it ran on.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from neuroharness import defaults
from neuroharness.config import SigningAlgorithm
from neuroharness.errors import (
    ClassHaltedError,
    ClockUnavailableError,
    ConfigurationError,
    EvidenceUnavailableError,
    TokenBundleStaleError,
    TokenConsumedError,
    TokenDigestMismatchError,
    TokenExpiredError,
    TokenModeMismatchError,
    TokenRevokedError,
    TokenSignatureError,
    TokenVerdictMismatchError,
)
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.reason import ReasonName, TokenInvalidReason
from neuroharness.seams import FrozenClock, SequenceIdGenerator
from neuroharness.tokens.model import DecisionToken, SignedToken
from neuroharness.tokens.nonce import (
    ConsumeOutcome,
    DuplicateIssuanceError,
    InMemoryIssuanceLedger,
    InMemoryNonceStore,
    InMemoryRevocationList,
    IssuanceLedger,
)
from neuroharness.tokens.service import MIN_TOKEN_TTL_SECONDS, ConsumeResult, TokenService
from neuroharness.tokens.signer import HmacSigner, MultiKeySigner

START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
TENANT = "acme"
OTHER_TENANT = "globex"
BROKER = "broker-1"

#: Enough concurrent minters that an interleaving will be found if the ledger's
#: conditional insert is not one step.
ISSUE_WORKERS = 32
ISSUE_POOL_SIZE = 8
SECRET_A = b"a" * 32
SECRET_B = b"b" * 32

#: A bundle-grace window (``FR-84``) long enough to step across one second at a
#: time. Mirrors the value the neighbouring grace tests configure.
GRACE_SECONDS = 30

#: A token lifetime comfortably past that window, so a refusal at the grace
#: boundary is about the superseded bundle and not about expiry.
GRACE_TTL_SECONDS = 300


def digest(seed: str) -> Digest:
    return Digest.from_hex(hashlib.sha256(seed.encode()).hexdigest())


#: The decision every helper mints for unless a test names another. At most one
#: token exists per decision (``ADR-0008``), so a test that needs two tokens
#: names two decisions.
DECISION = "dec-0001"
OTHER_DECISION = "dec-0002"

ENVELOPE = digest("envelope")
OTHER_ENVELOPE = digest("other-envelope")
PROPOSAL = digest("proposal")
BUNDLE = digest("bundle")
NEW_BUNDLE = digest("bundle-v2")
RECORD = digest("record")


class Harness:
    """A token service with every seam pinned, plus the pieces tests poke at."""

    def __init__(
        self,
        *,
        signer: HmacSigner | MultiKeySigner | None = None,
        ttl_seconds: int = defaults.DEFAULT_TOKEN_TTL_SECONDS,
        bundle_grace_seconds: int = defaults.DEFAULT_BUNDLE_GRACE_SECONDS,
        issuance_ledger: IssuanceLedger | None = None,
    ) -> None:
        self.clock = FrozenClock(START)
        self.signer = signer or HmacSigner(key_id="key-a", secret=SECRET_A)
        self.nonce_store = InMemoryNonceStore(ttl_seconds=ttl_seconds)
        self.revocations = InMemoryRevocationList()
        self.service = TokenService(
            signer=self.signer,
            nonce_store=self.nonce_store,
            revocation_list=self.revocations,
            clock=self.clock,
            id_generator=SequenceIdGenerator("tok"),
            issuance_ledger=issuance_ledger,
            ttl_seconds=ttl_seconds,
            bundle_grace_seconds=bundle_grace_seconds,
        )

    def issue(
        self,
        *,
        mode: Mode = Mode.ENFORCE,
        verdict: Verdict = Verdict.ALLOW,
        envelope_digest: Digest = ENVELOPE,
        policy_bundle_digest: Digest = BUNDLE,
        record_hash: Digest | None = RECORD,
        tenant_id: str = TENANT,
        decision_id: str = DECISION,
        ttl_seconds: int | None = None,
    ) -> SignedToken:
        return self.service.issue(
            decision_id=decision_id,
            envelope_digest=envelope_digest,
            proposal_digest=PROPOSAL,
            policy_bundle_digest=policy_bundle_digest,
            record_hash=record_hash,
            tenant_id=tenant_id,
            mode=mode,
            verdict=verdict,
            ttl_seconds=ttl_seconds,
        )

    def verify(self, signed: SignedToken, **overrides: object) -> DecisionToken:
        kwargs: dict[str, object] = {
            "envelope_digest": ENVELOPE,
            "tenant_id": TENANT,
            "current_mode": Mode.ENFORCE,
            "current_bundle_digest": BUNDLE,
        }
        kwargs.update(overrides)
        return self.service.verify(signed, **kwargs)  # type: ignore[arg-type]

    def consume(self, signed: SignedToken, **overrides: object) -> ConsumeResult:
        kwargs: dict[str, object] = {
            "envelope_digest": ENVELOPE,
            "tenant_id": TENANT,
            "current_mode": Mode.ENFORCE,
            "current_bundle_digest": BUNDLE,
            "broker_id": BROKER,
        }
        kwargs.update(overrides)
        return self.service.consume(signed, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def harness() -> Harness:
    return Harness()


class TestIssue:
    def test_issues_an_enforce_token_on_allow(self, harness: Harness) -> None:
        signed = harness.issue()
        token = signed.token
        assert token.token_id == "tok-00000001"
        assert token.decision_id == DECISION
        assert token.envelope_digest == ENVELOPE
        assert token.proposal_digest == PROPOSAL
        assert token.policy_bundle_digest == BUNDLE
        assert token.record_hash == RECORD
        assert token.tenant_id == TENANT
        assert token.mode is Mode.ENFORCE
        assert token.verdict is Verdict.ALLOW
        assert token.shadow is False
        assert token.key_id == "key-a"
        assert token.key_alg is SigningAlgorithm.HMAC_SHA256
        assert token.issued_at == START
        assert token.expires_at == START + timedelta(
            seconds=defaults.DEFAULT_TOKEN_TTL_SECONDS
        )
        assert signed.signature

    def test_identifiers_come_from_the_injected_generator(self, harness: Harness) -> None:
        # Two decisions, because one decision mints at most one token
        # (``ADR-0008``); the property under test is the identifier source.
        assert harness.issue(decision_id=DECISION).token.token_id == "tok-00000001"
        assert harness.issue(decision_id=OTHER_DECISION).token.token_id == "tok-00000002"

    def test_ttl_may_be_overridden_per_action_class(self, harness: Harness) -> None:
        token = harness.issue(ttl_seconds=15).token
        assert token.expires_at == START + timedelta(seconds=15)

    def test_default_ttl_is_the_documented_default_not_a_literal(self) -> None:
        assert Harness().service.ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS
        assert (
            Harness().service.bundle_grace_seconds == defaults.DEFAULT_BUNDLE_GRACE_SECONDS
        )

    def test_a_non_positive_ttl_refuses_to_start(self) -> None:
        with pytest.raises(ConfigurationError):
            TokenService(
                signer=HmacSigner(key_id="key-a", secret=SECRET_A),
                nonce_store=InMemoryNonceStore(),
                revocation_list=InMemoryRevocationList(),
                clock=FrozenClock(START),
                id_generator=SequenceIdGenerator(),
                ttl_seconds=0,
            )

    @pytest.mark.parametrize(
        "ttl_seconds",
        [
            pytest.param(MIN_TOKEN_TTL_SECONDS - 1, id="zero"),
            pytest.param(-MIN_TOKEN_TTL_SECONDS, id="negative"),
        ],
    )
    def test_a_non_positive_per_call_ttl_is_refused_with_a_typed_error(
        self, harness: Harness, ttl_seconds: int
    ) -> None:
        """A per-class ttl arrives from the signed registry, so it is operator input.

        The floor is checked at construction *and* here, because the per-call
        value bypasses the constructor entirely. Without this check a zero ttl
        would mint a token that is expired on arrival - an outage wearing a
        security control - and the refusal would surface as a raw pydantic
        ``ValueError`` from the model rather than as the typed
        :class:`ConfigurationError` that says "refuse to start", which is an
        untyped error on the decision path.
        """
        with pytest.raises(ConfigurationError, match="at least"):
            harness.issue(ttl_seconds=ttl_seconds)

    def test_a_negative_bundle_grace_refuses_to_start(self) -> None:
        """A negative grace is a window that ends before it opens (``FR-84``).

        Arithmetic on it does not fail: it silently makes every token carrying a
        superseded bundle stale slightly *before* it was issued, which looks
        exactly like a correctly enforced zero grace until the day someone widens
        the window and nothing changes.
        """
        with pytest.raises(ConfigurationError, match="bundle_grace_seconds"):
            TokenService(
                signer=HmacSigner(key_id="key-a", secret=SECRET_A),
                nonce_store=InMemoryNonceStore(),
                revocation_list=InMemoryRevocationList(),
                clock=FrozenClock(START),
                id_generator=SequenceIdGenerator(),
                bundle_grace_seconds=-MIN_TOKEN_TTL_SECONDS,
            )

    @pytest.mark.mutation
    def test_refuses_to_issue_without_a_durable_record(self, harness: Harness) -> None:
        # INV-05 / FR-23: the evaluation record precedes the token. MUT-13 makes
        # the evidence store fail; this is the precondition it depends on.
        with pytest.raises(EvidenceUnavailableError) as caught:
            harness.issue(record_hash=None)
        assert caught.value.reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE

    @pytest.mark.parametrize(
        "verdict",
        [Verdict.DENY, Verdict.ABSTAIN, Verdict.REQUIRES_APPROVAL, Verdict.REPAIR],
    )
    def test_refuses_to_issue_a_non_allow_token_in_enforce(
        self, harness: Harness, verdict: Verdict
    ) -> None:
        with pytest.raises(TokenVerdictMismatchError) as caught:
            harness.issue(mode=Mode.ENFORCE, verdict=verdict)
        assert caught.value.reason_code.subject == TokenInvalidReason.VERDICT_MISMATCH.value

    @pytest.mark.parametrize("mode", [Mode.SHADOW, Mode.ADVISORY])
    @pytest.mark.parametrize(
        "verdict", [Verdict.ALLOW, Verdict.DENY, Verdict.ABSTAIN, Verdict.REPAIR]
    )
    def test_shadow_and_advisory_issue_a_shadow_token_for_every_verdict(
        self, harness: Harness, mode: Mode, verdict: Verdict
    ) -> None:
        # ADR-0016 §1: the code path exercised in shadow is the path that will
        # enforce, so a token is minted whatever the verdict says.
        token = harness.issue(mode=mode, verdict=verdict).token
        assert token.shadow is True
        assert token.verdict is verdict
        assert token.mode is mode

    def test_halted_issues_nothing(self, harness: Harness) -> None:
        with pytest.raises(ClassHaltedError) as caught:
            harness.issue(mode=Mode.HALTED, verdict=Verdict.ALLOW)
        assert caught.value.reason_code.name is ReasonName.CLASS_HALTED

    def test_an_unhealthy_clock_mints_nothing(self, harness: Harness) -> None:
        harness.clock.set_healthy(False)
        with pytest.raises(ClockUnavailableError):
            harness.issue()


class TestAtMostOneTokenPerDecision:
    """``ADR-0008``: one evaluation authorises exactly one action.

    Single use (``FR-22``) is enforced per ``token_id``, so it cannot see two
    *different* tokens minted for one decision: before the issuance ledger, two
    ``issue`` calls with identical arguments produced two token ids that both
    verified and both consumed as a first use against the same envelope digest,
    which is one evaluation and two authorised executions.
    """

    @pytest.mark.mutation
    def test_a_second_issue_for_one_decision_is_refused(self, harness: Harness) -> None:
        harness.issue(decision_id=DECISION)
        with pytest.raises(DuplicateIssuanceError) as caught:
            harness.issue(decision_id=DECISION)
        assert caught.value.reason_code.name is ReasonName.HARNESS_UNHEALTHY

    @pytest.mark.mutation
    def test_two_issues_for_one_decision_cannot_both_dispatch(
        self, harness: Harness
    ) -> None:
        """The defect, stated as the property it breaks.

        Before the fix both tokens reached ``permits_dispatch``. Now the second
        mint never happens, so there is exactly one dispatchable token in
        existence for this decision.
        """
        first = harness.issue(decision_id=DECISION)
        with pytest.raises(DuplicateIssuanceError):
            harness.issue(decision_id=DECISION)

        assert harness.consume(first).permits_dispatch is True
        # And the one token that does exist still spends only once.
        assert harness.consume(first).permits_dispatch is False

    def test_the_refusal_is_not_widened_to_a_different_envelope(
        self, harness: Harness
    ) -> None:
        """A second mint is refused even when it asks for a different action.

        This is why ``issue`` raises rather than returning the first token:
        answering a request for envelope B with an authorisation for envelope A
        would be a substitution the caller never sees.
        """
        harness.issue(decision_id=DECISION, envelope_digest=ENVELOPE)
        with pytest.raises(DuplicateIssuanceError):
            harness.issue(decision_id=DECISION, envelope_digest=OTHER_ENVELOPE)

    def test_a_different_decision_still_mints(self, harness: Harness) -> None:
        """Fail-closed must not become fail-shut for unrelated decisions."""
        assert harness.issue(decision_id=DECISION).token.decision_id == DECISION
        assert harness.issue(decision_id=OTHER_DECISION).token.decision_id == OTHER_DECISION

    def test_a_different_tenant_may_reuse_a_decision_id(self, harness: Harness) -> None:
        """The ledger key is ``(tenant_id, decision_id)``.

        Keying on ``decision_id`` alone would let one tenant's identifier deny
        another tenant's legitimate mint: a fail-closed outage caused by an
        unrelated tenant.
        """
        harness.issue(decision_id=DECISION, tenant_id=TENANT)
        assert harness.issue(decision_id=DECISION, tenant_id=OTHER_TENANT) is not None

    def test_the_ledger_is_injectable_like_every_other_seam(self) -> None:
        """Default-on, but replaceable by the durable implementation."""
        ledger = InMemoryIssuanceLedger()
        harness = Harness(issuance_ledger=ledger)
        assert harness.service.issuance_ledger is ledger
        harness.issue(decision_id=DECISION)
        assert ledger.entry_for(tenant_id=TENANT, decision_id=DECISION) is not None

    def test_a_service_built_without_a_ledger_still_has_one(self) -> None:
        """The guarantee may not depend on whoever wired the service up."""
        service = TokenService(
            signer=HmacSigner(key_id="key-a", secret=SECRET_A),
            nonce_store=InMemoryNonceStore(),
            revocation_list=InMemoryRevocationList(),
            clock=FrozenClock(START),
            id_generator=SequenceIdGenerator("tok"),
        )
        assert isinstance(service.issuance_ledger, IssuanceLedger)

    @pytest.mark.mutation
    def test_concurrent_issue_yields_exactly_one_token(self) -> None:
        """Atomic at mint time, not merely checked at mint time.

        A read-then-write guard lets several threads all observe "unminted" and
        all mint, which is exactly the situation a retry storm produces.
        """
        harness = Harness()

        def attempt(_: int) -> SignedToken | None:
            try:
                return harness.issue(decision_id=DECISION)
            except DuplicateIssuanceError:
                return None

        with ThreadPoolExecutor(max_workers=ISSUE_POOL_SIZE) as pool:
            results = list(pool.map(attempt, range(ISSUE_WORKERS)))

        minted = [signed for signed in results if signed is not None]
        assert len(minted) == 1
        assert len({signed.token.token_id for signed in minted}) == 1


class TestVerify:
    def test_round_trip(self, harness: Harness) -> None:
        signed = harness.issue()
        assert harness.verify(signed) == signed.token

    def test_verification_is_pure_and_repeatable(self, harness: Harness) -> None:
        """Verifying must never be the thing that spends the token.

        The broker verifies before it consumes, and a mirrored or retried
        request can verify several times before one consumption. A ``verify``
        that recorded anything would burn the nonce without dispatching, so the
        legitimate delivery that followed would be refused as a replay: a failed
        request converted into a denial of the action it was authorising.
        """
        signed = harness.issue()
        first = harness.verify(signed)
        second = harness.verify(signed)
        assert first == second == signed.token
        assert harness.nonce_store.entry_for(signed.token.token_id) is None

    def test_a_tampered_payload_fails_the_signature_check(self, harness: Harness) -> None:
        signed = harness.issue()
        # Re-signing is impossible without the key, so the attacker keeps the
        # original signature over a payload they edited.
        tampered = SignedToken(
            token=signed.token.model_copy(update={"envelope_digest": OTHER_ENVELOPE}),
            signature=signed.signature,
        )
        with pytest.raises(TokenSignatureError):
            harness.verify(tampered, envelope_digest=OTHER_ENVELOPE)

    def test_a_token_from_a_foreign_key_is_refused(self, harness: Harness) -> None:
        # Same key id, different secret: the classic stolen-identifier forgery.
        other = Harness(signer=HmacSigner(key_id="key-a", secret=SECRET_B))
        signed = other.issue()
        with pytest.raises(TokenSignatureError):
            harness.verify(signed)

    def test_a_token_naming_an_unheld_key_is_refused(self, harness: Harness) -> None:
        other = Harness(signer=HmacSigner(key_id="key-z", secret=SECRET_B))
        signed = other.issue()
        with pytest.raises(TokenSignatureError):
            harness.verify(signed)

    def test_expiry_is_enforced(self, harness: Harness) -> None:
        signed = harness.issue(ttl_seconds=60)
        harness.clock.advance(59)
        harness.verify(signed)
        harness.clock.advance(1)
        with pytest.raises(TokenExpiredError) as caught:
            harness.verify(signed)
        assert caught.value.reason_code.subject == TokenInvalidReason.EXPIRED.value

    @pytest.mark.mutation
    def test_mut_10_a_modified_envelope_is_refused(self, harness: Harness) -> None:
        # MUT-10: the envelope is edited after the ALLOW. The token is genuine;
        # the digest the broker recomputed is not the one it binds.
        signed = harness.issue(envelope_digest=ENVELOPE)
        with pytest.raises(TokenDigestMismatchError) as caught:
            harness.verify(signed, envelope_digest=OTHER_ENVELOPE)
        assert caught.value.reason_code.subject == TokenInvalidReason.DIGEST_MISMATCH.value

    def test_a_token_from_another_tenant_is_refused(self, harness: Harness) -> None:
        signed = harness.issue(tenant_id="other-tenant")
        with pytest.raises(TokenDigestMismatchError):
            harness.verify(signed, tenant_id=TENANT)

    @pytest.mark.mutation
    def test_mut_20_a_shadow_token_is_refused_after_promotion_to_enforce(
        self, harness: Harness
    ) -> None:
        signed = harness.issue(mode=Mode.SHADOW, verdict=Verdict.ALLOW)
        assert signed.token.shadow is True
        with pytest.raises(TokenModeMismatchError) as caught:
            harness.verify(signed, current_mode=Mode.ENFORCE)
        assert caught.value.reason_code.subject == TokenInvalidReason.MODE_MISMATCH.value

    def test_an_enforce_token_is_refused_after_demotion(self, harness: Harness) -> None:
        signed = harness.issue(mode=Mode.ENFORCE)
        with pytest.raises(TokenModeMismatchError):
            harness.verify(signed, current_mode=Mode.ADVISORY)

    def test_a_shadow_token_verifies_while_the_class_is_still_shadow(
        self, harness: Harness
    ) -> None:
        signed = harness.issue(mode=Mode.SHADOW, verdict=Verdict.DENY)
        assert harness.verify(signed, current_mode=Mode.SHADOW).verdict is Verdict.DENY

    def test_the_broker_gate_does_not_rely_on_the_mint_gate(self, harness: Harness) -> None:
        # Defence in depth: even a correctly signed enforce token carrying a
        # DENY - which issue() refuses to mint - is refused at the broker.
        token = DecisionToken(
            token_id="tok-forged",
            decision_id="dec-0001",
            envelope_digest=ENVELOPE,
            proposal_digest=PROPOSAL,
            policy_bundle_digest=BUNDLE,
            record_hash=RECORD,
            tenant_id=TENANT,
            mode=Mode.ENFORCE,
            verdict=Verdict.DENY,
            issued_at=START,
            expires_at=START + timedelta(seconds=defaults.DEFAULT_TOKEN_TTL_SECONDS),
            key_id="key-a",
            key_alg=SigningAlgorithm.HMAC_SHA256,
            shadow=False,
        )
        signed = SignedToken(
            token=token, signature=harness.signer.sign(token.signing_payload())
        )
        with pytest.raises(TokenVerdictMismatchError) as caught:
            harness.verify(signed)
        assert caught.value.reason_code.subject == TokenInvalidReason.VERDICT_MISMATCH.value

    @pytest.mark.mutation
    def test_mut_36_a_revoked_token_is_refused(self, harness: Harness) -> None:
        signed = harness.issue()
        harness.revocations.revoke_token(signed.token.token_id, reason="incident-42")
        with pytest.raises(TokenRevokedError) as caught:
            harness.verify(signed)
        assert caught.value.reason_code.subject == TokenInvalidReason.REVOKED.value

    @pytest.mark.mutation
    def test_mut_36_revoking_a_key_refuses_every_token_under_it(
        self, harness: Harness
    ) -> None:
        signed = harness.issue()
        harness.revocations.revoke_key("key-a", reason="key-compromise")
        with pytest.raises(TokenRevokedError):
            harness.verify(signed)

    def test_revocation_outranks_expiry(self, harness: Harness) -> None:
        # The operator's refusal is reported as such even when the token would
        # have been refused anyway: the record has to name the real cause.
        signed = harness.issue(ttl_seconds=60)
        harness.revocations.revoke_token(signed.token.token_id)
        harness.clock.advance(120)
        with pytest.raises(TokenRevokedError):
            harness.verify(signed)

    def test_a_superseded_bundle_is_refused_with_no_grace(self, harness: Harness) -> None:
        signed = harness.issue(policy_bundle_digest=BUNDLE)
        with pytest.raises(TokenBundleStaleError) as caught:
            harness.verify(signed, current_bundle_digest=NEW_BUNDLE)
        assert caught.value.reason_code.subject == TokenInvalidReason.BUNDLE_STALE.value

    def test_a_superseded_bundle_is_accepted_inside_the_grace_window(self) -> None:
        harness = Harness(bundle_grace_seconds=30)
        signed = harness.issue(policy_bundle_digest=BUNDLE)
        harness.clock.advance(10)
        assert harness.verify(signed, current_bundle_digest=NEW_BUNDLE) == signed.token

    def test_the_grace_window_is_inclusive_at_its_last_second(self) -> None:
        """``FR-84`` fixes where the window ends, and both sides of it matter.

        One second early and a deployment that set a grace to cover its bundle
        rollout starts refusing in-flight tokens a second sooner than it
        configured - a fail-closed outage during exactly the transition the
        grace exists to smooth. One second late and a superseded bundle keeps
        authorising executions after the window an operator was told to rely on.
        """
        harness = Harness(bundle_grace_seconds=GRACE_SECONDS, ttl_seconds=GRACE_TTL_SECONDS)
        signed = harness.issue(policy_bundle_digest=BUNDLE)

        harness.clock.advance(GRACE_SECONDS)
        assert harness.verify(signed, current_bundle_digest=NEW_BUNDLE) == signed.token

        harness.clock.advance(1)
        with pytest.raises(TokenBundleStaleError):
            harness.verify(signed, current_bundle_digest=NEW_BUNDLE)

    def test_a_superseded_bundle_is_refused_past_the_grace_window(self) -> None:
        harness = Harness(bundle_grace_seconds=30, ttl_seconds=300)
        signed = harness.issue(policy_bundle_digest=BUNDLE)
        harness.clock.advance(31)
        with pytest.raises(TokenBundleStaleError):
            harness.verify(signed, current_bundle_digest=NEW_BUNDLE)

    def test_the_grace_window_may_be_supplied_per_action_class(
        self, harness: Harness
    ) -> None:
        signed = harness.issue(policy_bundle_digest=BUNDLE)
        harness.clock.advance(5)
        assert harness.verify(
            signed, current_bundle_digest=NEW_BUNDLE, grace_seconds=30
        ) == signed.token

    def test_an_unhealthy_clock_refuses_every_token(self, harness: Harness) -> None:
        signed = harness.issue()
        harness.clock.set_healthy(False)
        with pytest.raises(ClockUnavailableError):
            harness.verify(signed)


class TestConsume:
    def test_first_use_dispatches(self, harness: Harness) -> None:
        result = harness.consume(harness.issue())
        assert result.outcome is ConsumeOutcome.FIRST_USE
        assert result.permits_dispatch
        assert not result.duplicate_delivery

    @pytest.mark.mutation
    def test_mut_09_a_replayed_token_is_refused(self, harness: Harness) -> None:
        signed = harness.issue()
        harness.consume(signed)
        with pytest.raises(TokenConsumedError) as caught:
            harness.consume(signed, broker_id="broker-2")
        assert caught.value.reason_code.subject == TokenInvalidReason.CONSUMED.value

    def test_duplicate_delivery_is_not_treated_as_replay(self, harness: Harness) -> None:
        # FR-22: same token, same broker, same envelope, inside the TTL is a
        # benign redelivery. It must not raise - and must not dispatch twice.
        signed = harness.issue()
        harness.consume(signed)
        harness.clock.advance(5)
        result = harness.consume(signed)
        assert result.outcome is ConsumeOutcome.DUPLICATE_DELIVERY
        assert result.duplicate_delivery
        assert not result.permits_dispatch

    def test_a_different_envelope_after_consumption_is_a_replay(
        self, harness: Harness
    ) -> None:
        signed = harness.issue()
        harness.consume(signed)
        # Refused by the digest binding before the nonce is even consulted.
        with pytest.raises(TokenDigestMismatchError):
            harness.consume(signed, envelope_digest=OTHER_ENVELOPE)

    def test_a_failed_verification_does_not_burn_the_nonce(
        self, harness: Harness
    ) -> None:
        # Otherwise presenting a stolen token against the wrong envelope would
        # be a cheap denial of service against a legitimate authorisation.
        signed = harness.issue()
        with pytest.raises(TokenDigestMismatchError):
            harness.consume(signed, envelope_digest=OTHER_ENVELOPE)
        assert harness.nonce_store.entry_for(signed.token.token_id) is None
        assert harness.consume(signed).permits_dispatch

    def test_an_expired_token_is_refused_before_consumption(
        self, harness: Harness
    ) -> None:
        signed = harness.issue(ttl_seconds=60)
        harness.clock.advance(61)
        with pytest.raises(TokenExpiredError):
            harness.consume(signed)
        assert harness.nonce_store.entry_for(signed.token.token_id) is None

    def test_a_revoked_token_is_refused_before_consumption(
        self, harness: Harness
    ) -> None:
        signed = harness.issue()
        harness.revocations.revoke_token(signed.token.token_id)
        with pytest.raises(TokenRevokedError):
            harness.consume(signed)
        assert harness.nonce_store.entry_for(signed.token.token_id) is None


class TestKeyRotation:
    def test_signs_with_the_new_key_while_verifying_the_old(self) -> None:
        # NFR-17: rotation without downtime. A token minted seconds before the
        # rotation must still execute; a token minted after it carries key B.
        key_a = HmacSigner(key_id="key-a", secret=SECRET_A)
        key_b = HmacSigner(key_id="key-b", secret=SECRET_B)

        before = Harness(signer=key_a)
        old_token = before.issue()

        after = Harness(signer=MultiKeySigner(active=key_b, additional=[key_a]))
        assert after.service.signing_key_id == "key-b"

        assert after.verify(old_token) == old_token.token

        new_token = after.issue()
        assert new_token.token.key_id == "key-b"
        assert after.verify(new_token) == new_token.token

    def test_a_retired_key_stops_verifying(self) -> None:
        key_a = HmacSigner(key_id="key-a", secret=SECRET_A)
        key_b = HmacSigner(key_id="key-b", secret=SECRET_B)
        old_token = Harness(signer=key_a).issue()
        retired = Harness(
            signer=MultiKeySigner(active=key_b, additional=[key_a]).retire("key-a")
        )
        with pytest.raises(TokenSignatureError):
            retired.verify(old_token)

    def test_revoking_the_old_key_refuses_tokens_minted_under_it(self) -> None:
        key_a = HmacSigner(key_id="key-a", secret=SECRET_A)
        key_b = HmacSigner(key_id="key-b", secret=SECRET_B)
        old_token = Harness(signer=key_a).issue()
        after = Harness(signer=MultiKeySigner(active=key_b, additional=[key_a]))
        after.revocations.revoke_key("key-a", reason="rotation-compromise")
        with pytest.raises(TokenRevokedError):
            after.verify(old_token)
        # Revoking the compromised key must not take the deployment down: what
        # is minted under the surviving key still verifies, and under that key.
        # ``verify`` raises on refusal rather than returning ``None``, so an
        # identity check here would hold however the token came back.
        fresh = after.issue()
        assert fresh.token.key_id == "key-b"
        assert after.verify(fresh) == fresh.token


class TestTokenPayload:
    def test_a_token_never_carries_its_own_signature(self, harness: Harness) -> None:
        token = harness.issue().token
        assert "signature" not in token.model_dump()
        assert "signature" not in token.canonical_dict()
        assert b"signature" not in token.signing_payload()

    def test_the_payload_is_exactly_the_fr_20_field_list(self, harness: Harness) -> None:
        assert set(harness.issue().token.canonical_dict()) == {
            "token_id",
            "decision_id",
            "envelope_digest",
            "proposal_digest",
            "policy_bundle_digest",
            "record_hash",
            "tenant_id",
            "mode",
            "verdict",
            "issued_at",
            "expires_at",
            "key_id",
            "key_alg",
            "shadow",
        }

    def test_the_encoding_is_stable_and_sorted(self, harness: Harness) -> None:
        token = harness.issue().token
        payload = token.signing_payload()
        assert payload == token.model_copy().signing_payload()
        body = payload.split(b"\n", 1)[1].decode("utf-8")
        keys = [part.split('"')[1] for part in body.split(",") if part.startswith('"')]
        assert keys == sorted(keys)
        assert body.startswith('{"decision_id":')

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("token_id", "tok-99999999"),
            ("decision_id", "dec-9999"),
            ("envelope_digest", OTHER_ENVELOPE),
            ("proposal_digest", digest("other-proposal")),
            ("policy_bundle_digest", NEW_BUNDLE),
            ("record_hash", digest("other-record")),
            ("tenant_id", "other-tenant"),
            ("mode", Mode.ADVISORY),
            ("verdict", Verdict.DENY),
            ("issued_at", START - timedelta(seconds=1)),
            ("expires_at", START + timedelta(seconds=1)),
            ("key_id", "key-z"),
            ("key_alg", SigningAlgorithm.ED25519),
            ("shadow", True),
        ],
    )
    def test_every_field_is_covered_by_the_signature(
        self, harness: Harness, field: str, value: object
    ) -> None:
        # A field outside the signed bytes is a field an attacker may edit.
        token = harness.issue().token
        assert token.signing_payload() != token.model_copy(
            update={field: value}
        ).signing_payload()

    def test_expiry_is_inclusive_at_the_boundary(self, harness: Harness) -> None:
        token = harness.issue(ttl_seconds=60).token
        assert not token.is_expired(START + timedelta(seconds=59))
        assert token.is_expired(token.expires_at)

    def test_naive_timestamps_are_refused(self) -> None:
        with pytest.raises(ValueError):
            DecisionToken(
                token_id="tok-1",
                decision_id="dec-1",
                envelope_digest=ENVELOPE,
                proposal_digest=PROPOSAL,
                policy_bundle_digest=BUNDLE,
                record_hash=RECORD,
                tenant_id=TENANT,
                mode=Mode.ENFORCE,
                verdict=Verdict.ALLOW,
                issued_at=datetime(2026, 9, 18, 12, 0, 0),
                expires_at=START + timedelta(seconds=60),
                key_id="key-a",
                key_alg=SigningAlgorithm.HMAC_SHA256,
                shadow=False,
            )

    def test_an_expiry_before_issuance_is_refused(self) -> None:
        with pytest.raises(ValueError):
            DecisionToken(
                token_id="tok-1",
                decision_id="dec-1",
                envelope_digest=ENVELOPE,
                proposal_digest=PROPOSAL,
                policy_bundle_digest=BUNDLE,
                record_hash=RECORD,
                tenant_id=TENANT,
                mode=Mode.ENFORCE,
                verdict=Verdict.ALLOW,
                issued_at=START,
                expires_at=START,
                key_id="key-a",
                key_alg=SigningAlgorithm.HMAC_SHA256,
                shadow=False,
            )

    def test_a_token_is_immutable(self, harness: Harness) -> None:
        token = harness.issue().token
        with pytest.raises(ValueError):
            token.verdict = Verdict.ALLOW  # type: ignore[misc]


class TestEvidence:
    """A decision that is not recorded was not made (Constitution, Article IV)."""

    def test_issuance_is_logged_without_signature_material(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="neuroharness.tokens"):
            signed = harness.issue()
        events = {record.getMessage(): record for record in caplog.records}
        assert "token_issued" in events
        fields = events["token_issued"].fields  # type: ignore[attr-defined]
        assert fields["token_id"] == signed.token.token_id
        assert fields["key_id"] == "key-a"
        assert signed.signature not in str(fields)

    def test_every_refusal_records_its_reason_code(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        signed = harness.issue()
        with (
            caplog.at_level(logging.WARNING, logger="neuroharness.tokens"),
            pytest.raises(TokenDigestMismatchError),
        ):
            harness.verify(signed, envelope_digest=OTHER_ENVELOPE)
        refusals = [r for r in caplog.records if r.getMessage() == "token_verify_refused"]
        assert refusals
        assert refusals[-1].fields["reason"] == "TOKEN_INVALID:digest_mismatch"  # type: ignore[attr-defined]

    def test_a_replay_is_recorded_as_a_refusal(
        self, harness: Harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        signed = harness.issue()
        harness.consume(signed)
        with (
            caplog.at_level(logging.WARNING, logger="neuroharness.tokens"),
            pytest.raises(TokenConsumedError),
        ):
            harness.consume(signed, broker_id="broker-2")
        refusals = [r for r in caplog.records if r.getMessage() == "token_consume_refused"]
        assert refusals
        assert refusals[-1].fields["reason"] == "TOKEN_INVALID:consumed"  # type: ignore[attr-defined]
