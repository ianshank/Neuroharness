"""At-most-once issuance, single use, and revocation (``FR-21``, ``FR-22``, ``MUT-09``).

Three behaviours carry the weight here. Issuance must be atomic per decision,
because a decision that two callers can both mint for authorises two executions
of an action that was evaluated once (``ADR-0008``). Consumption must be atomic
under concurrency, for the same reason one step later. And a benign retry must
not be reported as a replay, because an alert that fires on ordinary network
behaviour is an alert operators learn to close unread.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from neuroharness import defaults
from neuroharness.config import SigningAlgorithm
from neuroharness.errors import EvidenceUnavailableError
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.tokens.model import DecisionToken
from neuroharness.tokens.nonce import (
    ConsumeOutcome,
    InMemoryIssuanceLedger,
    InMemoryNonceStore,
    InMemoryRevocationList,
    IssuanceEntry,
    IssuanceEvidence,
    IssuanceLedger,
    NonceStore,
    RecordingIssuanceLedger,
    RevocationList,
    RevocationScope,
)

START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
TOKEN_ID = "tok-00000001"
OTHER_TOKEN_ID = "tok-00000002"
THIRD_TOKEN_ID = "tok-00000003"
BROKER = "broker-1"
TENANT = "acme"
OTHER_TENANT = "globex"
DECISION = "dec-0001"
OTHER_DECISION = "dec-0002"

#: Enough concurrent callers that an interleaving will be found if the
#: conditional insert is not one step. Mirrors the nonce-store atomicity tests.
CONCURRENT_WORKERS = 32
POOL_SIZE = 8


def digest(seed: str) -> Digest:
    return Digest.from_hex(hashlib.sha256(seed.encode()).hexdigest())


ENVELOPE = digest("envelope")
OTHER_ENVELOPE = digest("other-envelope")

#: Far enough past any token lifetime that a retention-based purge would have
#: dropped the entry. Named rather than inlined so the intent is the constant.
DAYS_WELL_PAST_ANY_TOKEN_LIFETIME = 365

#: A token lifetime short enough that a test can step across the retention
#: horizon one second at a time rather than simulating an hour.
SHORT_TTL_SECONDS = 2

#: A retention window strictly longer than that lifetime, as every real
#: configuration is (the store's floor is ``retention >= ttl``). Longer than the
#: ttl on purpose: a horizon computed from the wrong one of the two would still
#: look right if they were equal.
SHORT_RETENTION_SECONDS = 6


def claim(
    ledger: InMemoryIssuanceLedger,
    *,
    tenant_id: str = TENANT,
    decision_id: str = DECISION,
    token_id: str = TOKEN_ID,
    envelope_digest: Digest = ENVELOPE,
    at: datetime = START,
) -> object:
    """Claim a decision, returning ``None`` on success or the holding entry."""
    return ledger.claim(
        tenant_id=tenant_id,
        decision_id=decision_id,
        token_id=token_id,
        envelope_digest=envelope_digest,
        now=at,
    )


def consume(
    store: InMemoryNonceStore,
    *,
    token_id: str = TOKEN_ID,
    envelope_digest: Digest = ENVELOPE,
    broker_id: str = BROKER,
    at: datetime = START,
) -> ConsumeOutcome:
    return store.consume(
        token_id, envelope_digest=envelope_digest, broker_id=broker_id, now=at
    )


class TestNonceStoreContract:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryNonceStore(), NonceStore)

    def test_defaults_to_the_documented_token_lifetime(self) -> None:
        assert InMemoryNonceStore().ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS

    def test_non_positive_ttl_is_refused(self) -> None:
        with pytest.raises(ValueError):
            InMemoryNonceStore(ttl_seconds=0)

    def test_retention_shorter_than_the_ttl_is_refused(self) -> None:
        # Forgetting a nonce while a token bearing it can still be presented
        # would turn a replay into a first use.
        with pytest.raises(ValueError):
            InMemoryNonceStore(ttl_seconds=60, retention_seconds=30)


class TestFirstUse:
    def test_an_unseen_token_is_a_first_use(self) -> None:
        store = InMemoryNonceStore()
        outcome = consume(store)
        assert outcome is ConsumeOutcome.FIRST_USE
        assert outcome.permits_dispatch

    def test_the_entry_records_what_was_consumed(self) -> None:
        store = InMemoryNonceStore()
        consume(store)
        entry = store.entry_for(TOKEN_ID)
        assert entry is not None
        assert entry.envelope_digest == ENVELOPE
        assert entry.broker_id == BROKER
        assert entry.first_consumed_at == START
        assert entry.delivery_count == 1

    def test_distinct_tokens_do_not_interfere(self) -> None:
        store = InMemoryNonceStore()
        assert consume(store, token_id="tok-1") is ConsumeOutcome.FIRST_USE
        assert consume(store, token_id="tok-2") is ConsumeOutcome.FIRST_USE

    def test_an_unconsumed_token_has_no_entry(self) -> None:
        assert InMemoryNonceStore().entry_for(TOKEN_ID) is None


class TestDuplicateDelivery:
    def test_identical_redelivery_in_window_is_benign(self) -> None:
        store = InMemoryNonceStore(ttl_seconds=60)
        consume(store)
        outcome = consume(store, at=START + timedelta(seconds=30))
        assert outcome is ConsumeOutcome.DUPLICATE_DELIVERY
        assert not outcome.is_replay

    def test_duplicate_delivery_still_does_not_authorise_dispatch(self) -> None:
        # FR-22: single use means single execution. The retry is answered from
        # the first outcome, never by running the action again.
        store = InMemoryNonceStore()
        consume(store)
        assert not consume(store).permits_dispatch

    def test_redeliveries_are_counted(self) -> None:
        store = InMemoryNonceStore()
        consume(store)
        consume(store)
        consume(store)
        entry = store.entry_for(TOKEN_ID)
        assert entry is not None
        assert entry.delivery_count == 3


class TestReplay:
    def test_a_different_broker_is_a_replay(self) -> None:
        store = InMemoryNonceStore()
        consume(store)
        assert consume(store, broker_id="broker-2") is ConsumeOutcome.REPLAY

    def test_a_different_envelope_is_a_replay(self) -> None:
        store = InMemoryNonceStore()
        consume(store)
        assert consume(store, envelope_digest=OTHER_ENVELOPE) is ConsumeOutcome.REPLAY

    def test_an_identical_redelivery_after_the_window_is_a_replay(self) -> None:
        store = InMemoryNonceStore(ttl_seconds=60)
        consume(store)
        assert consume(store, at=START + timedelta(seconds=61)) is ConsumeOutcome.REPLAY

    def test_a_late_replay_inside_retention_never_looks_like_a_first_use(self) -> None:
        store = InMemoryNonceStore(ttl_seconds=60, retention_seconds=3600)
        consume(store)
        assert consume(store, at=START + timedelta(seconds=3599)) is ConsumeOutcome.REPLAY

    def test_replay_is_the_alerting_outcome(self) -> None:
        store = InMemoryNonceStore()
        consume(store)
        assert consume(store, broker_id="broker-2").is_replay


class TestRetention:
    """How long a spent nonce is remembered, and what the purge takes with it.

    The store's memory is bounded by age and never by count: evicting the oldest
    entries under pressure would forget nonces exactly when the system is
    busiest, which is when a replay is least likely to be noticed. The window is
    what makes forgetting safe at all, so where it ends is a property, not an
    implementation detail.
    """

    def test_a_replay_at_the_retention_horizon_is_still_a_replay(self) -> None:
        """An entry dropped one second early is a replay that dispatches.

        The horizon runs from the first consumption. A store that forgot an
        entry as it reached the window, rather than after it, would have nothing
        left to compare against for the last presentation it exists to refuse -
        and a token the store has never seen is a first use, which executes.
        """
        store = InMemoryNonceStore(
            ttl_seconds=SHORT_TTL_SECONDS, retention_seconds=SHORT_RETENTION_SECONDS
        )
        consume(store)

        at_horizon = START + timedelta(seconds=SHORT_RETENTION_SECONDS)
        assert consume(store, at=at_horizon) is ConsumeOutcome.REPLAY
        entry = store.entry_for(TOKEN_ID)
        assert entry is not None
        assert entry.first_consumed_at == START

    def test_an_entry_is_forgotten_only_once_past_the_horizon(self) -> None:
        """Bounded memory is a real requirement; where the bound bites is the risk.

        Forgetting is safe only because the window outlasts any token that could
        still be presented, so a purge driven by the wrong duration - the token
        lifetime rather than the retention window, say - would drop entries
        while the tokens that wrote them are still verifiable.
        """
        store = InMemoryNonceStore(
            ttl_seconds=SHORT_TTL_SECONDS, retention_seconds=SHORT_RETENTION_SECONDS
        )
        consume(store)

        past_horizon = START + timedelta(seconds=SHORT_RETENTION_SECONDS + 1)
        assert consume(store, at=past_horizon) is ConsumeOutcome.FIRST_USE
        entry = store.entry_for(TOKEN_ID)
        assert entry is not None
        assert entry.first_consumed_at == past_horizon

    def test_the_purge_drops_only_the_entries_past_the_horizon(self) -> None:
        """One token ageing out must not take a live token's entry with it.

        The purge runs on every consumption, so it runs while other tokens are
        mid-flight. A purge that cleared more than it should would silently
        re-arm every one of them: each would consume again as a first use.
        """
        store = InMemoryNonceStore(
            ttl_seconds=SHORT_TTL_SECONDS, retention_seconds=SHORT_RETENTION_SECONDS
        )
        consume(store, token_id=TOKEN_ID)
        later = START + timedelta(seconds=SHORT_RETENTION_SECONDS)
        consume(store, token_id=OTHER_TOKEN_ID, at=later)

        # An unrelated consumption, present only to run the purge under the lock.
        consume(
            store,
            token_id=THIRD_TOKEN_ID,
            at=START + timedelta(seconds=SHORT_RETENTION_SECONDS + 1),
        )

        assert store.entry_for(TOKEN_ID) is None
        surviving = store.entry_for(OTHER_TOKEN_ID)
        assert surviving is not None
        assert surviving.first_consumed_at == later

    def test_the_shortest_permitted_retention_still_outlasts_its_token(self) -> None:
        """This overlap is the whole reason forgetting a nonce is safe (``FR-22``).

        The floor the constructor enforces is ``retention >= ttl``, and an entry
        is remembered through the instant ``retention`` seconds after it was
        written. A token consumed at that instant was issued no later than it,
        so it has already reached its own expiry, which the token service checks
        before it consumes. Move either boundary in by one second and the two
        windows stop overlapping: there is a moment at which the token still
        verifies and the store has forgotten that it was spent.
        """
        store = InMemoryNonceStore(
            ttl_seconds=SHORT_TTL_SECONDS, retention_seconds=SHORT_TTL_SECONDS
        )
        consume(store)

        outcome = consume(store, at=START + timedelta(seconds=SHORT_TTL_SECONDS))
        assert outcome is ConsumeOutcome.DUPLICATE_DELIVERY
        assert not outcome.permits_dispatch


class TestAtomicity:
    def test_exactly_one_winner_among_concurrent_distinct_brokers(self) -> None:
        store = InMemoryNonceStore()
        workers = 32

        def attempt(index: int) -> ConsumeOutcome:
            return consume(store, broker_id=f"broker-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(workers)))

        assert outcomes.count(ConsumeOutcome.FIRST_USE) == 1
        assert outcomes.count(ConsumeOutcome.REPLAY) == workers - 1

    def test_exactly_one_winner_among_concurrent_identical_deliveries(self) -> None:
        store = InMemoryNonceStore()
        workers = 32

        def attempt(_: int) -> ConsumeOutcome:
            return consume(store)

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(workers)))

        assert outcomes.count(ConsumeOutcome.FIRST_USE) == 1
        assert sum(1 for o in outcomes if o.permits_dispatch) == 1
        entry = store.entry_for(TOKEN_ID)
        assert entry is not None
        assert entry.delivery_count == workers


class TestRevocationList:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryRevocationList(), RevocationList)

    def test_nothing_is_revoked_by_default(self) -> None:
        revocations = InMemoryRevocationList()
        assert not revocations.is_revoked(token_id=TOKEN_ID, key_id="key-a")
        assert revocations.revocation_scope(token_id=TOKEN_ID, key_id="key-a") is None

    def test_revoking_one_token(self) -> None:
        revocations = InMemoryRevocationList()
        revocations.revoke_token(TOKEN_ID, reason="incident-42")
        assert revocations.is_revoked(token_id=TOKEN_ID, key_id="key-a")
        assert (
            revocations.revocation_scope(token_id=TOKEN_ID, key_id="key-a")
            is RevocationScope.TOKEN
        )
        assert revocations.reason_for(token_id=TOKEN_ID, key_id="key-a") == "incident-42"
        assert not revocations.is_revoked(token_id="tok-other", key_id="key-a")

    def test_revoking_a_key_covers_every_token_minted_under_it(self) -> None:
        revocations = InMemoryRevocationList()
        revocations.revoke_key("key-a", reason="key-compromise")
        assert revocations.is_revoked(token_id="anything", key_id="key-a")
        assert (
            revocations.revocation_scope(token_id="anything", key_id="key-a")
            is RevocationScope.KEY
        )
        assert not revocations.is_revoked(token_id="anything", key_id="key-b")

    def test_revocations_are_listed_for_the_operator(self) -> None:
        revocations = InMemoryRevocationList()
        revocations.revoke_token(TOKEN_ID)
        revocations.revoke_key("key-a")
        assert revocations.revoked_token_ids == {TOKEN_ID}
        assert revocations.revoked_key_ids == {"key-a"}


class TestIssuanceLedger:
    """One evaluation authorises exactly one action (``ADR-0008``, ``FR-20``).

    Single use is enforced per ``token_id``, so it cannot see two *different*
    tokens minted for one decision. That is this ledger's job, and these are the
    tests that failed before it existed.
    """

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemoryIssuanceLedger(), IssuanceLedger)

    def test_first_claim_succeeds(self) -> None:
        """``None`` means "the key was free and is now yours"."""
        ledger = InMemoryIssuanceLedger()
        assert claim(ledger) is None
        entry = ledger.entry_for(tenant_id=TENANT, decision_id=DECISION)
        assert entry is not None
        assert entry.token_id == TOKEN_ID
        assert entry.envelope_digest == ENVELOPE
        assert entry.issued_at == START

    def test_second_claim_returns_the_holder_and_does_not_overwrite(self) -> None:
        """The conflicting entry is returned so the refusal can name it."""
        ledger = InMemoryIssuanceLedger()
        claim(ledger)
        existing = claim(ledger, token_id=OTHER_TOKEN_ID, envelope_digest=OTHER_ENVELOPE)
        assert existing is not None
        assert existing.token_id == TOKEN_ID
        entry = ledger.entry_for(tenant_id=TENANT, decision_id=DECISION)
        assert entry is not None
        assert entry.token_id == TOKEN_ID

    def test_a_different_decision_is_a_different_key(self) -> None:
        ledger = InMemoryIssuanceLedger()
        assert claim(ledger) is None
        assert claim(ledger, decision_id=OTHER_DECISION, token_id=OTHER_TOKEN_ID) is None
        assert ledger.issued_count == 2

    def test_the_key_includes_the_tenant(self) -> None:
        """One tenant's decision id must never deny another tenant's mint."""
        ledger = InMemoryIssuanceLedger()
        assert claim(ledger) is None
        assert claim(ledger, tenant_id=OTHER_TENANT, token_id=OTHER_TOKEN_ID) is None

    def test_an_unclaimed_decision_has_no_entry(self) -> None:
        ledger = InMemoryIssuanceLedger()
        assert ledger.entry_for(tenant_id=TENANT, decision_id=DECISION) is None

    def test_claims_normalise_the_instant_to_utc(self) -> None:
        """Replay compares recorded instants, so the ledger stores one zone."""
        ledger = InMemoryIssuanceLedger()
        elsewhere = START.astimezone(timezone(timedelta(hours=9)))
        claim(ledger, at=elsewhere)
        entry = ledger.entry_for(tenant_id=TENANT, decision_id=DECISION)
        assert entry is not None
        assert entry.issued_at == START
        assert entry.issued_at.tzinfo == UTC

    def test_an_issuance_is_never_forgotten(self) -> None:
        """Unlike a nonce, an issuance has no expiry backstop.

        The token service refuses an expired token before the nonce store sees
        it, so forgetting a spent nonce is safe. Nothing plays that role for a
        mint: were the ledger to forget, the second ``issue`` would produce a
        *fresh* token with a fresh lifetime, which is the whole defect.
        """
        ledger = InMemoryIssuanceLedger()
        claim(ledger)
        much_later = START + timedelta(days=DAYS_WELL_PAST_ANY_TOKEN_LIFETIME)
        existing = claim(ledger, token_id=OTHER_TOKEN_ID, at=much_later)
        assert existing is not None
        assert existing.token_id == TOKEN_ID

    def test_exactly_one_winner_among_concurrent_claims(self) -> None:
        """The atomicity requirement, stated as a race.

        A read-then-write ledger lets several threads all observe "unheld" and
        all mint. One winner is the whole contract.
        """
        ledger = InMemoryIssuanceLedger()

        def attempt(index: int) -> object:
            return claim(ledger, token_id=f"tok-{index:08d}")

        with ThreadPoolExecutor(max_workers=POOL_SIZE) as pool:
            results = list(pool.map(attempt, range(CONCURRENT_WORKERS)))

        assert results.count(None) == 1
        holders = {r.token_id for r in results if r is not None}
        assert len(holders) == 1
        assert ledger.issued_count == 1


class FakeIssuanceEvidence:
    """A stand-in for the durable ``token_issued`` record of one decision.

    It is deliberately able to do the thing that makes this hard: ``lands``
    decides whether a write that *reports* failure nevertheless made the record
    durable. That is the case a compensating release cannot distinguish - a
    store that timed out after committing - and it is the case the whole design
    has to survive, so it is modelled here rather than assumed away.
    """

    def __init__(self, *, holding: IssuanceEntry | None = None) -> None:
        self.holding = holding
        self.writes = 0
        self.reads = 0
        #: When set, ``record_issuance`` raises it.
        self.failure: Exception | None = None
        #: Whether a failing write still made the record durable.
        self.lands = False

    def recorded_issuance(self) -> IssuanceEntry | None:
        self.reads += 1
        return self.holding

    def record_issuance(self, token: DecisionToken) -> None:
        self.writes += 1
        entry = IssuanceEntry(
            tenant_id=token.tenant_id,
            decision_id=token.decision_id,
            token_id=token.token_id,
            envelope_digest=token.envelope_digest,
            issued_at=token.issued_at,
        )
        if self.failure is None:
            self.holding = entry
            return
        if self.lands:
            self.holding = entry
        raise self.failure


def decision_token(
    *,
    token_id: str = TOKEN_ID,
    tenant_id: str = TENANT,
    decision_id: str = DECISION,
    envelope_digest: Digest = ENVELOPE,
    at: datetime = START,
) -> DecisionToken:
    """A syntactically complete token, since the ledger keys off its fields."""
    return DecisionToken(
        token_id=token_id,
        decision_id=decision_id,
        envelope_digest=envelope_digest,
        proposal_digest=digest("proposal"),
        policy_bundle_digest=digest("bundle"),
        record_hash=digest("record"),
        tenant_id=tenant_id,
        mode=Mode.ENFORCE,
        verdict=Verdict.ALLOW,
        issued_at=at,
        expires_at=at + timedelta(seconds=defaults.DEFAULT_TOKEN_TTL_SECONDS),
        key_id="key-a",
        key_alg=SigningAlgorithm.HMAC_SHA256,
        shadow=False,
    )


class TestClaimingByRecording:
    """``D-5``, option (c): the issuance record *is* the claim (``FR-23``).

    The defect these tests exist for: the ledger claimed, the caller then wrote
    the ``token_issued`` record, and an evidence outage between the two left a
    claim standing for a token nobody holds - permanently, because the ledger
    never purges, and unrecoverably, because the refusal was reported as an
    infrastructure reason that cannot escalate.

    The repair does not release the claim; it never takes one until the record
    is durable. So the two properties below have to hold *together*, and either
    one alone is easy: a decision whose record failed must be mintable again,
    and a decision whose record landed must not be mintable again - including
    when the write that landed it reported failure.
    """

    def test_satisfies_the_recording_protocol(self) -> None:
        ledger = InMemoryIssuanceLedger()
        assert isinstance(ledger, RecordingIssuanceLedger)
        assert isinstance(ledger, IssuanceLedger), "the narrower contract still holds"

    def test_satisfies_the_evidence_protocol(self) -> None:
        """The fake is held to the protocol, or it proves nothing about it."""
        assert isinstance(FakeIssuanceEvidence(), IssuanceEvidence)

    def test_a_claim_is_taken_only_once_the_record_is_durable(self) -> None:
        evidence = FakeIssuanceEvidence()
        ledger = InMemoryIssuanceLedger()

        assert (
            ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)
            is None
        )

        assert evidence.writes == 1
        entry = ledger.entry_for(tenant_id=TENANT, decision_id=DECISION)
        assert entry is not None
        assert entry.token_id == TOKEN_ID

    def test_a_record_that_failed_leaves_no_claim_behind(self) -> None:
        """The wedge, at its source: nothing is held, so nothing is stuck."""
        evidence = FakeIssuanceEvidence()
        evidence.failure = EvidenceUnavailableError("the store is down")
        ledger = InMemoryIssuanceLedger()

        with pytest.raises(EvidenceUnavailableError):
            ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)

        assert ledger.entry_for(tenant_id=TENANT, decision_id=DECISION) is None
        assert ledger.issued_count == 0

    def test_the_decision_is_mintable_again_once_the_store_recovers(self) -> None:
        """The retry that had no test, at the ledger's own level."""
        evidence = FakeIssuanceEvidence()
        evidence.failure = EvidenceUnavailableError("the store is down")
        ledger = InMemoryIssuanceLedger()

        with pytest.raises(EvidenceUnavailableError):
            ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)

        evidence.failure = None
        later = START + timedelta(seconds=defaults.DEFAULT_TOKEN_TTL_SECONDS)
        retry = ledger.claim_recorded(
            token=decision_token(token_id=OTHER_TOKEN_ID, at=later),
            evidence=evidence,
            now=later,
        )

        assert retry is None, "a decision whose record never landed was not authorised"
        entry = ledger.entry_for(tenant_id=TENANT, decision_id=DECISION)
        assert entry is not None
        assert entry.token_id == OTHER_TOKEN_ID

    @pytest.mark.mutation
    def test_a_record_that_landed_while_reporting_failure_refuses_the_retry(self) -> None:
        """The case a compensating release cannot handle, and this one can.

        The store commits and then the call times out, so the caller is told the
        write failed while the record is durable. Releasing a claim on failure
        would mint a second token here - a fresh token with a fresh lifetime,
        which is exactly what the never-purge rule exists to prevent. Reading
        the record instead of a ledger's memory is what makes the difference.
        """
        evidence = FakeIssuanceEvidence()
        evidence.failure = EvidenceUnavailableError("committed, then timed out")
        evidence.lands = True
        ledger = InMemoryIssuanceLedger()

        with pytest.raises(EvidenceUnavailableError):
            ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)
        assert ledger.entry_for(tenant_id=TENANT, decision_id=DECISION) is None

        evidence.failure = None
        holder = ledger.claim_recorded(
            token=decision_token(token_id=OTHER_TOKEN_ID), evidence=evidence, now=START
        )

        assert holder is not None, "the chain already authorises this decision"
        assert holder.token_id == TOKEN_ID
        assert evidence.writes == 1, "the second mint never reached the store"

    @pytest.mark.mutation
    def test_an_issuance_this_process_never_saw_is_still_refused(self) -> None:
        """A restart empties the ledger; it does not empty the chain.

        This is why :meth:`IssuanceEvidence.recorded_issuance` may not answer
        from a cache. A ledger that trusted its own memory would mint a second
        token for every decision that was authorised before the process
        restarted, and for every decision another gateway authorised.
        """
        recorded = IssuanceEntry(
            tenant_id=TENANT,
            decision_id=DECISION,
            token_id=TOKEN_ID,
            envelope_digest=ENVELOPE,
            issued_at=START,
        )
        evidence = FakeIssuanceEvidence(holding=recorded)
        ledger = InMemoryIssuanceLedger()

        holder = ledger.claim_recorded(
            token=decision_token(token_id=OTHER_TOKEN_ID), evidence=evidence, now=START
        )

        assert holder is recorded
        assert evidence.writes == 0

    def test_a_recorded_issuance_is_never_forgotten(self) -> None:
        """The never-purge property, on the recorded path.

        A year is far past any token lifetime, so a retention-based purge would
        have dropped the entry - and the second mint would be a fresh token with
        a fresh lifetime. Nothing here expires.
        """
        evidence = FakeIssuanceEvidence()
        ledger = InMemoryIssuanceLedger()
        ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)

        much_later = START + timedelta(days=DAYS_WELL_PAST_ANY_TOKEN_LIFETIME)
        holder = ledger.claim_recorded(
            token=decision_token(token_id=OTHER_TOKEN_ID, at=much_later),
            evidence=evidence,
            now=much_later,
        )

        assert holder is not None
        assert holder.token_id == TOKEN_ID
        assert evidence.writes == 1

    def test_the_cached_claim_answers_without_touching_the_store(self) -> None:
        """A duplicate refusal must not depend on the store being reachable.

        Once this process has recorded the issuance it knows the answer, and an
        evidence outage is not a reason to start minting second tokens.
        """
        evidence = FakeIssuanceEvidence()
        ledger = InMemoryIssuanceLedger()
        ledger.claim_recorded(token=decision_token(), evidence=evidence, now=START)
        reads_after_first = evidence.reads

        holder = ledger.claim_recorded(
            token=decision_token(token_id=OTHER_TOKEN_ID), evidence=evidence, now=START
        )

        assert holder is not None
        assert evidence.reads == reads_after_first

    def test_a_different_decision_is_still_a_different_key(self) -> None:
        """Fail-closed must not become fail-shut for unrelated decisions."""
        ledger = InMemoryIssuanceLedger()
        assert (
            ledger.claim_recorded(
                token=decision_token(), evidence=FakeIssuanceEvidence(), now=START
            )
            is None
        )
        assert (
            ledger.claim_recorded(
                token=decision_token(decision_id=OTHER_DECISION, token_id=OTHER_TOKEN_ID),
                evidence=FakeIssuanceEvidence(),
                now=START,
            )
            is None
        )
        assert ledger.issued_count == 2

    @pytest.mark.mutation
    def test_exactly_one_record_is_written_under_concurrent_claims(self) -> None:
        """Atomic at mint time: the read of the chain and the write are one step.

        Were they two, several threads would each read "no issuance" and each
        write one, and the chain would carry two authorisations for a decision
        that was evaluated once (``ADR-0008``).
        """
        evidence = FakeIssuanceEvidence()
        ledger = InMemoryIssuanceLedger()

        def attempt(index: int) -> object:
            return ledger.claim_recorded(
                token=decision_token(token_id=f"tok-{index:08d}"),
                evidence=evidence,
                now=START,
            )

        with ThreadPoolExecutor(max_workers=POOL_SIZE) as pool:
            results = list(pool.map(attempt, range(CONCURRENT_WORKERS)))

        assert results.count(None) == 1
        assert evidence.writes == 1
        assert ledger.issued_count == 1
