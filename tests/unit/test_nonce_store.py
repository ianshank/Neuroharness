"""Single-use consumption and revocation (``FR-21``, ``FR-22``, ``MUT-09``).

Two behaviours carry the weight here. Consumption must be atomic under
concurrency, because a token that two brokers can both win authorises two
executions of an action that was approved once. And a benign retry must not be
reported as a replay, because an alert that fires on ordinary network behaviour
is an alert operators learn to close unread.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from neuroharness import defaults
from neuroharness.models.common import Digest
from neuroharness.tokens.nonce import (
    ConsumeOutcome,
    InMemoryNonceStore,
    InMemoryRevocationList,
    NonceStore,
    RevocationList,
    RevocationScope,
)

START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
TOKEN_ID = "tok-00000001"
BROKER = "broker-1"


def digest(seed: str) -> Digest:
    return Digest.from_hex(hashlib.sha256(seed.encode()).hexdigest())


ENVELOPE = digest("envelope")
OTHER_ENVELOPE = digest("other-envelope")


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
