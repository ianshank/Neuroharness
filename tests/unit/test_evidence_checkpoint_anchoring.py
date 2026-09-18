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
