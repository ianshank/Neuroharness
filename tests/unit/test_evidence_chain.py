"""Hash-chain behaviour: what tampering it detects, and what it refuses to hide.

Covers ``FR-70`` (per-tenant chained records), ``SEC-10`` (signed checkpoints)
and the part of ``INV-05`` that only means something if a stored record can be
shown to be the record that was written.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from neuroharness.errors import ConfigurationError
from neuroharness.evidence.chain import (
    GENESIS_SEQ,
    SCHEMA_VERSION,
    ChainBreak,
    MalformedRecordError,
    RecordKind,
    checkpoint_signing_bytes,
    compute_record_hash,
    create_checkpoint,
    verify_chain,
    verify_checkpoint,
)
from neuroharness.models.common import Digest
from neuroharness.seams import FrozenClock

_START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
_TRACE_ID = "0" * 32
_SIGNING_KEY = b"test-checkpoint-key"
_KEY_ID = "evidence-checkpoint-2026-09"
_CHAIN_LENGTH = 4


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(_START)


def _hmac_signer(payload: bytes) -> str:
    """Stand-in for the KMS signer. Deterministic, so tests can assert on it."""
    return hmac.new(_SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def _build_chain(
    tenant_id: str,
    count: int,
    *,
    clock: FrozenClock,
    start_seq: int = GENESIS_SEQ,
) -> list[dict[str, Any]]:
    """Build a well-formed chain without going through the store.

    The chain tests must fail when the chain primitives are wrong, not when the
    store is wrong, so they build their own records.
    """
    records: list[dict[str, Any]] = []
    previous: Digest | None = None
    for offset in range(count):
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "record_id": f"{tenant_id}-record-{offset:04d}",
            "tenant_id": tenant_id,
            "seq": start_seq + offset,
            "prev_record_hash": str(previous) if previous is not None else None,
            "kind": RecordKind.EVALUATION.value,
            "timestamp": clock.now().isoformat(),
            "trace_id": _TRACE_ID,
            "evaluation": {"verdict": "ALLOW", "reason_codes": []},
        }
        record_hash = compute_record_hash(record, prev_hash=previous)
        record["record_hash"] = str(record_hash)
        previous = record_hash
        records.append(record)
        clock.advance(1)
    return records


# --- compute_record_hash ------------------------------------------------------


def test_record_hash_omits_the_record_hash_field(clock: FrozenClock) -> None:
    """A field cannot commit to itself, so it is excluded from the digest."""
    [record] = _build_chain("acme", 1, clock=clock)
    without = {key: value for key, value in record.items() if key != "record_hash"}

    assert compute_record_hash(without, prev_hash=None) == Digest(record["record_hash"])


def test_record_hash_binds_the_previous_hash(clock: FrozenClock) -> None:
    """The same content in a different chain position is a different digest.

    This is what stops a record being lifted out of one position and filed in
    another: its hash is a statement about where it sits, not only what it says.
    """
    [record] = _build_chain("acme", 1, clock=clock)
    elsewhere = compute_record_hash(record, prev_hash=Digest("sha256:" + "ab" * 32))

    assert elsewhere != Digest(record["record_hash"])


def test_record_hash_binds_the_tenant(clock: FrozenClock) -> None:
    """Relabelling the tenant changes the digest, so a chain cannot be crossed."""
    [record] = _build_chain("acme", 1, clock=clock)
    relabelled = dict(record, tenant_id="other")

    assert compute_record_hash(relabelled, prev_hash=None) != Digest(record["record_hash"])


def test_record_hash_ignores_the_supplied_prev_field(clock: FrozenClock) -> None:
    """The passed predecessor wins over the field, so the two cannot disagree."""
    [record] = _build_chain("acme", 1, clock=clock)
    lying = dict(record, prev_record_hash="sha256:" + "cd" * 32)

    assert compute_record_hash(lying, prev_hash=None) == Digest(record["record_hash"])


def test_record_hash_requires_chain_identity() -> None:
    with pytest.raises(MalformedRecordError):
        compute_record_hash({"kind": "evaluation"}, prev_hash=None)


# --- verify_chain -------------------------------------------------------------


def test_valid_chain_verifies(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    result = verify_chain(records, expected_tenant_id="acme")

    assert result.ok
    assert bool(result) is True
    assert result.checked == _CHAIN_LENGTH
    assert result.first_broken_index is None
    assert result.reason is None


def test_empty_chain_verifies() -> None:
    """An empty read is not evidence of tampering."""
    result = verify_chain([])

    assert result.ok
    assert result.checked == 0


def test_partial_chain_from_a_later_seq_verifies(clock: FrozenClock) -> None:
    """Reads with a start_seq hand back a mid-chain run; it must still verify."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)

    assert verify_chain(records[2:], expected_tenant_id="acme").ok


def test_altered_payload_is_detected_at_its_index(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    records[2]["evaluation"] = {"verdict": "DENY", "reason_codes": []}

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.first_broken_index == 2
    assert result.reason is ChainBreak.RECORD_HASH_MISMATCH


def test_reordering_is_detected(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    records[1], records[2] = records[2], records[1]

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.first_broken_index == 1
    # Every sequence number is still present, so nothing was removed: the run
    # is out of order rather than incomplete.
    assert result.reason is ChainBreak.SEQUENCE_OUT_OF_ORDER


def test_a_repeated_sequence_number_is_reported_as_disorder_not_a_gap(
    clock: FrozenClock,
) -> None:
    """A repetition is not a deletion, and the label is what operators branch on.

    ``[0, 1, 1]`` is not contiguous, so a contiguity test alone diagnosed a
    repeated record as ``SEQUENCE_GAP`` -- "a record was deleted", which starts a
    very different investigation from "a record was presented twice".
    ``SEQUENCE_OUT_OF_ORDER`` documents itself as covering "reordered or
    repeated" (``FR-70``).
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    records[2] = copy.deepcopy(records[1])

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.reason is ChainBreak.SEQUENCE_OUT_OF_ORDER


def test_a_record_presented_twice_in_a_row_is_disorder_not_a_gap(
    clock: FrozenClock,
) -> None:
    """The duplicate-delivery shape: the same record appended to the run."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    records.append(copy.deepcopy(records[-1]))

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.reason is ChainBreak.SEQUENCE_OUT_OF_ORDER


def test_a_repetition_alongside_a_deletion_is_still_not_a_clean_gap(
    clock: FrozenClock,
) -> None:
    """When both happened, say the thing that is certainly true.

    A repeated number is direct evidence; a gap inferred from a run whose length
    a repetition has already inflated is not.
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    del records[2]
    records.append(copy.deepcopy(records[0]))

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.reason is ChainBreak.SEQUENCE_OUT_OF_ORDER


def test_deleting_a_middle_record_is_detected(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    del records[2]

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.first_broken_index == 2
    assert result.reason is ChainBreak.SEQUENCE_GAP


def test_deleting_the_last_record_is_detected_by_the_checkpoint(clock: FrozenClock) -> None:
    """Truncation is invisible to the chain alone; the signed head is what catches it."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = create_checkpoint(
        tenant_id="acme",
        last_seq=int(records[-1]["seq"]),
        last_record_hash=Digest(records[-1]["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
    )
    truncated = records[:-1]

    assert verify_chain(truncated, expected_tenant_id="acme").ok
    assert verify_checkpoint(checkpoint, tenant_id="acme", signer=_hmac_signer)
    assert int(truncated[-1]["seq"]) != checkpoint["last_seq"]


def test_a_record_spliced_from_another_tenant_is_detected(clock: FrozenClock) -> None:
    """The splice a multi-tenant deployment has to survive (``NFR-15``)."""
    tenant_a = _build_chain("tenant-a", _CHAIN_LENGTH, clock=clock)
    tenant_b = _build_chain("tenant-b", _CHAIN_LENGTH, clock=clock)
    tenant_b[1] = tenant_a[1]

    result = verify_chain(tenant_b, expected_tenant_id="tenant-b")

    assert not result.ok
    assert result.first_broken_index == 1
    assert result.reason is ChainBreak.TENANT_MISMATCH


def test_a_spliced_record_relabelled_to_the_target_tenant_is_still_detected(
    clock: FrozenClock,
) -> None:
    """Rewriting ``tenant_id`` to hide the splice invalidates the stored hash.

    This is the reason the tenant is inside the hashed body rather than merely
    alongside it.
    """
    tenant_a = _build_chain("tenant-a", _CHAIN_LENGTH, clock=clock)
    tenant_b = _build_chain("tenant-b", _CHAIN_LENGTH, clock=clock)
    tenant_b[1] = dict(
        tenant_a[1],
        tenant_id="tenant-b",
        prev_record_hash=tenant_b[0]["record_hash"],
    )

    result = verify_chain(tenant_b, expected_tenant_id="tenant-b")

    assert not result.ok
    assert result.first_broken_index == 1
    assert result.reason is ChainBreak.RECORD_HASH_MISMATCH


def test_a_wholly_foreign_run_is_detected_only_with_the_expected_tenant(
    clock: FrozenClock,
) -> None:
    tenant_a = _build_chain("tenant-a", _CHAIN_LENGTH, clock=clock)

    assert verify_chain(tenant_a).ok
    foreign = verify_chain(tenant_a, expected_tenant_id="tenant-b")
    assert not foreign.ok
    assert foreign.first_broken_index == 0
    assert foreign.reason is ChainBreak.TENANT_MISMATCH


def test_a_broken_link_with_intact_sequence_is_detected(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    forged = dict(records[2], prev_record_hash="sha256:" + "ef" * 32)
    forged["record_hash"] = str(
        compute_record_hash(forged, prev_hash=Digest(forged["prev_record_hash"]))
    )
    records[2] = forged

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.first_broken_index == 2
    assert result.reason is ChainBreak.PREV_HASH_MISMATCH


def test_the_genesis_record_must_not_link_to_a_predecessor(clock: FrozenClock) -> None:
    records = _build_chain("acme", 1, clock=clock)
    forged = dict(records[0], prev_record_hash="sha256:" + "11" * 32)
    forged["record_hash"] = str(
        compute_record_hash(forged, prev_hash=Digest(forged["prev_record_hash"]))
    )

    result = verify_chain([forged], expected_tenant_id="acme")

    assert not result.ok
    assert result.reason is ChainBreak.PREV_HASH_MISMATCH


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"record_hash": None}, id="no-record-hash"),
        pytest.param({"seq": "1"}, id="seq-not-an-integer"),
        pytest.param({"seq": True}, id="seq-is-a-boolean"),
        pytest.param({"tenant_id": ""}, id="empty-tenant"),
        pytest.param({"record_hash": "not-a-digest"}, id="record-hash-not-a-digest"),
    ],
)
def test_a_malformed_record_does_not_raise_but_fails_verification(
    clock: FrozenClock, mutation: Mapping[str, Any]
) -> None:
    """Verification reports; it never raises, so a finding cannot be swallowed."""
    records = _build_chain("acme", 2, clock=clock)
    records[1].update(mutation)
    if mutation.get("record_hash", "keep") is None:
        del records[1]["record_hash"]

    result = verify_chain(records, expected_tenant_id="acme")

    assert not result.ok
    assert result.first_broken_index == 1
    assert result.reason is ChainBreak.MALFORMED_RECORD


# --- checkpoints (SEC-10) -----------------------------------------------------


def test_checkpoint_verifies(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    head = records[-1]

    checkpoint = create_checkpoint(
        tenant_id="acme",
        last_seq=int(head["seq"]),
        last_record_hash=Digest(head["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
        anchor_ref="s3://evidence/acme/2026-09-18T12.json",
    )

    assert checkpoint["last_seq"] == _CHAIN_LENGTH - 1
    assert checkpoint["last_record_hash"] == head["record_hash"]
    assert checkpoint["key_id"] == _KEY_ID
    assert verify_checkpoint(checkpoint, tenant_id="acme", signer=_hmac_signer)


def test_checkpoint_fails_on_a_tampered_last_hash(clock: FrozenClock) -> None:
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    head = records[-1]
    checkpoint = create_checkpoint(
        tenant_id="acme",
        last_seq=int(head["seq"]),
        last_record_hash=Digest(head["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
    )

    tampered = dict(checkpoint, last_record_hash="sha256:" + "22" * 32)

    assert not verify_checkpoint(tampered, tenant_id="acme", signer=_hmac_signer)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"last_seq": 99}, id="last-seq"),
        pytest.param({"key_id": "another-key"}, id="key-id"),
        pytest.param({"signature": "00" * 32}, id="signature"),
        pytest.param({"anchor_ref": "s3://elsewhere"}, id="anchor-ref"),
        pytest.param({"last_record_hash": "not-a-digest"}, id="malformed-hash"),
    ],
)
def test_checkpoint_fails_on_any_tampered_field(
    clock: FrozenClock, mutation: Mapping[str, Any]
) -> None:
    records = _build_chain("acme", 2, clock=clock)
    head = records[-1]
    checkpoint = create_checkpoint(
        tenant_id="acme",
        last_seq=int(head["seq"]),
        last_record_hash=Digest(head["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
        anchor_ref="s3://evidence/acme.json",
    )

    assert not verify_checkpoint(
        dict(checkpoint, **mutation), tenant_id="acme", signer=_hmac_signer
    )


def test_checkpoint_does_not_verify_against_another_tenant(clock: FrozenClock) -> None:
    """A checkpoint names its chain, so it cannot be moved to a shorter one."""
    records = _build_chain("tenant-a", 2, clock=clock)
    head = records[-1]
    checkpoint = create_checkpoint(
        tenant_id="tenant-a",
        last_seq=int(head["seq"]),
        last_record_hash=Digest(head["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
    )

    assert not verify_checkpoint(checkpoint, tenant_id="tenant-b", signer=_hmac_signer)


def test_checkpoint_verifies_with_a_public_key_style_verifier(clock: FrozenClock) -> None:
    """The verifying side need hold no signing material."""
    records = _build_chain("acme", 2, clock=clock)
    head = records[-1]
    checkpoint = create_checkpoint(
        tenant_id="acme",
        last_seq=int(head["seq"]),
        last_record_hash=Digest(head["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
    )

    def verifier(payload: bytes, signature: str) -> bool:
        return hmac.compare_digest(_hmac_signer(payload), signature)

    assert verify_checkpoint(checkpoint, tenant_id="acme", verifier=verifier)


def test_checkpoint_missing_fields_do_not_verify() -> None:
    assert not verify_checkpoint({"last_seq": 1}, tenant_id="acme", signer=_hmac_signer)


def test_verify_checkpoint_refuses_an_ambiguous_configuration() -> None:
    """Defaulting either way would make an unverified checkpoint look verified."""
    with pytest.raises(ConfigurationError):
        verify_checkpoint({}, tenant_id="acme")
    with pytest.raises(ConfigurationError):
        verify_checkpoint({}, tenant_id="acme", signer=_hmac_signer, verifier=lambda _p, _s: True)


def test_checkpoint_signing_bytes_are_domain_separated() -> None:
    """A signature over a checkpoint must not be reusable over anything else."""
    payload = checkpoint_signing_bytes(
        tenant_id="acme",
        last_seq=1,
        last_record_hash=Digest("sha256:" + "33" * 32),
        key_id=_KEY_ID,
    )
    other_domain = checkpoint_signing_bytes(
        tenant_id="acme",
        last_seq=1,
        last_record_hash=Digest("sha256:" + "33" * 32),
        key_id=_KEY_ID,
        anchor_ref="anything",
    )

    assert payload != other_domain
    assert isinstance(payload, bytes)


def test_create_checkpoint_rejects_a_sequence_below_genesis() -> None:
    with pytest.raises(ConfigurationError):
        create_checkpoint(
            tenant_id="acme",
            last_seq=GENESIS_SEQ - 1,
            last_record_hash=Digest("sha256:" + "44" * 32),
            key_id=_KEY_ID,
            signer=_hmac_signer,
        )


def test_compute_record_hash_refuses_a_non_mapping() -> None:
    with pytest.raises(MalformedRecordError):
        compute_record_hash(["not", "a", "record"], prev_hash=None)  # type: ignore[arg-type]


def test_verify_chain_reports_a_non_mapping_element(clock: FrozenClock) -> None:
    records = _build_chain("acme", 2, clock=clock)

    result = verify_chain([records[0], "not a record"])  # type: ignore[list-item]

    assert not result.ok
    assert result.first_broken_index == 1
    assert result.reason is ChainBreak.MALFORMED_RECORD
