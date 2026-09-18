"""Truncation detection through the store (``SEC-10``).

A hash chain proves that no record was altered and that none was removed from
the *middle*. It cannot prove that none was removed from the end: each record
commits to what precedes it and to nothing that follows, so lopping off the last
few decisions leaves a chain that verifies perfectly. During an incident those
last few decisions are the ones anybody wants.

The signed checkpoint is the statement made before the deletion, under a key the
store does not hold, that the chain had reached a particular record. These tests
drive that comparison through :class:`InMemoryEvidenceStore`, because a
primitive nothing calls is a primitive that gets deleted in a cleanup.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Final
from uuid import UUID

import pytest

from neuroharness.evidence.chain import FIELD_RECORD_HASH, FIELD_SEQ, ChainBreak, RecordKind
from neuroharness.evidence.store import InMemoryEvidenceStore
from neuroharness.seams import DeterministicUuidGenerator, FrozenClock

ANCHOR: Final[datetime] = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
TENANT: Final[str] = "acme"
OTHER_TENANT: Final[str] = "globex"
KEY_ID: Final[str] = "evidence-checkpoint-2026-09"
SIGNING_KEY: Final[bytes] = b"checkpoint-anchoring-test-key"

#: Enough records that a truncation removes something and still leaves a chain.
CHAIN_LENGTH: Final[int] = 4


def signer(payload: bytes) -> str:
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(ANCHOR)


@pytest.fixture()
def store(clock: FrozenClock) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=DeterministicUuidGenerator())


def append(store: InMemoryEvidenceStore, clock: FrozenClock, tenant: str = TENANT) -> Any:
    result = store.append(
        {
            "tenant_id": tenant,
            "kind": RecordKind.EVALUATION.value,
            "trace_id": "0" * 32,
            "decision_id": str(UUID(int=0xDEC)),
            "action_id": str(UUID(int=0xAC)),
            "evaluation": {"verdict": "ALLOW", "reason_codes": []},
        }
    )
    clock.advance(1)
    return result


def fill(store: InMemoryEvidenceStore, clock: FrozenClock, count: int = CHAIN_LENGTH) -> None:
    for _ in range(count):
        append(store, clock)


def truncate(store: InMemoryEvidenceStore, tenant: str = TENANT) -> None:
    """Remove the head of a tenant's chain, reaching past the append-only API.

    The store offers no way to do this, which is the point of the store. The
    threat model's actor here is someone with the database, not someone with the
    API, so the test has to be that actor.
    """
    store._records[tenant].pop()  # noqa: SLF001 - simulating a store-level tamper


def test_a_healthy_chain_verifies_against_its_own_checkpoint(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """The control. Without it, a check that always failed would look correct."""
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    result = store.verify_against_checkpoint(TENANT, checkpoint, signer=signer)

    assert result.ok
    assert result.checked == CHAIN_LENGTH


def test_truncating_the_chain_is_caught(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """The tamper the hash chain cannot see, and the reason checkpoints exist.

    Both assertions matter: the chain still verifies on its own, *and* the
    checkpoint catches it. Asserting only the second would pass even if the
    chain had somehow caught it first, and then nobody would know which control
    was load bearing.
    """
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    truncate(store)

    assert store.verify(TENANT).ok, "a truncated chain is internally consistent"

    result = store.verify_against_checkpoint(TENANT, checkpoint, signer=signer)
    assert not result.ok
    assert result.reason is ChainBreak.TRUNCATED


def test_truncating_to_nothing_is_caught(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """An empty read is not evidence of tampering - unless a checkpoint says otherwise.

    ``verify_chain`` returns ``ok`` for an empty run, because nothing
    contradicts nothing. That is the right default and the wrong answer when a
    signed statement exists that the chain held four records.
    """
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    for _ in range(CHAIN_LENGTH):
        truncate(store)

    assert store.verify(TENANT).ok
    result = store.verify_against_checkpoint(TENANT, checkpoint, signer=signer)
    assert not result.ok
    assert result.reason is ChainBreak.TRUNCATED


def test_appending_after_a_checkpoint_does_not_look_like_tampering(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """The ordinary case. A check that cries wolf here would be switched off."""
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    fill(store, clock, count=2)

    assert store.verify_against_checkpoint(TENANT, checkpoint, signer=signer).ok


def test_a_checkpoint_does_not_travel_between_tenants(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """``NFR-15``: tenants are separate chains, and the separation must be checkable.

    The attack the tenant binding stops: present another tenant's longer chain
    as evidence that yours was not truncated, or vice versa. The tenant is
    inside the signed payload, so neither direction verifies.
    """
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    for _ in range(CHAIN_LENGTH):
        append(store, clock, tenant=OTHER_TENANT)

    result = store.verify_against_checkpoint(OTHER_TENANT, checkpoint, signer=signer)
    assert not result.ok
    assert result.reason is ChainBreak.CHECKPOINT_INVALID


def test_the_checkpoint_names_the_head_it_signed(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """What the checkpoint asserts, stated once so the tests above mean something."""
    fill(store, clock)
    head = store.latest(TENANT)
    assert head is not None

    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    assert checkpoint["last_seq"] == int(head[FIELD_SEQ])
    assert checkpoint["last_record_hash"] == str(head[FIELD_RECORD_HASH])


# --- the other end of the chain ----------------------------------------------


def truncate_prefix(store: InMemoryEvidenceStore, count: int, tenant: str = TENANT) -> None:
    """Remove the first ``count`` records, reaching past the append-only API."""
    del store._records[tenant][:count]  # noqa: SLF001 - simulating a store-level tamper


def test_deleting_the_front_of_the_chain_is_caught(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """The mirror of truncation, and it was the one that got through.

    A hash chain commits to what precedes each record and nothing commits to the
    chain having a beginning, so deleting records 0, 1 and 2 leaves records 3, 4
    and 5 each still linked to the one before it. The suffix is internally
    perfect, its head still matches the signed checkpoint, and both checks
    reported it healthy -- while the decisions that opened the incident were
    gone.

    Catching it needs nothing cryptographic, only the caller saying it asked for
    the whole chain: a run that claims to be complete must start at the genesis
    record.
    """
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    truncate_prefix(store, 3)

    result = store.verify(TENANT)
    assert not result.ok
    assert result.reason is ChainBreak.MISSING_GENESIS

    anchored = store.verify_against_checkpoint(TENANT, checkpoint, signer=signer)
    assert not anchored.ok, "the checkpoint's head still matches, so only the anchor catches this"
    assert anchored.reason is ChainBreak.MISSING_GENESIS


def test_deleting_the_front_and_the_back_together_is_still_caught(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """Hollowing out a chain from both ends leaves a run that links perfectly."""
    fill(store, clock)
    checkpoint = store.checkpoint(TENANT, key_id=KEY_ID, signer=signer)
    assert checkpoint is not None

    truncate_prefix(store, 1)
    truncate(store)

    result = store.verify_against_checkpoint(TENANT, checkpoint, signer=signer)
    assert not result.ok
    assert result.reason is ChainBreak.MISSING_GENESIS


def test_an_explicitly_partial_read_is_not_reported_as_tampering(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """A verifier that cries wolf on a legitimate read is a verifier switched off.

    ``read(start_seq=...)`` is how an auditor pages through a long chain, and its
    first record's predecessor is legitimately absent. Only the caller knows
    which it asked for, which is why the anchor is opt-in rather than always on.
    """
    fill(store, clock)

    assert store.verify(TENANT, start_seq=2).ok
    assert store.verify(TENANT).ok, "and the default read is still the anchored one"
