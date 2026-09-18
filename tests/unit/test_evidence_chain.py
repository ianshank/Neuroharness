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
    CHECKPOINT_SIGNING_DOMAIN,
    ChainBreak,
    MalformedRecordError,
    RecordKind,
    checkpoint_signing_bytes,
    compute_record_hash,
    create_checkpoint,
    verify_against_checkpoint,
    verify_chain,
    verify_checkpoint,
)
from neuroharness.canonical.digest import digest_value
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
    trace_id: str = _TRACE_ID,
) -> list[dict[str, Any]]:
    """Build a well-formed chain without going through the store.

    The chain tests must fail when the chain primitives are wrong, not when the
    store is wrong, so they build their own records.

    ``trace_id`` varies the content so that a second call produces a chain that
    is internally perfect and *different* -- the rewrite an attacker with the
    whole store can perform, as opposed to an edit that breaks a link.
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
            "trace_id": trace_id,
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


def _checkpoint_at(records: list[dict[str, Any]], index: int = -1) -> dict[str, Any]:
    """Sign the head of ``records`` as it stands."""
    return create_checkpoint(
        tenant_id="acme",
        last_seq=int(records[index]["seq"]),
        last_record_hash=Digest(records[index]["record_hash"]),
        key_id=_KEY_ID,
        signer=_hmac_signer,
    )


def test_deleting_the_last_record_is_invisible_to_the_chain_alone(
    clock: FrozenClock,
) -> None:
    """The premise of the checkpoint, stated as the thing it has to defeat.

    Every record commits to what precedes it and to nothing that follows, so a
    truncated chain is internally perfect. This is why ``SEC-10`` exists, and it
    is worth asserting: if ``verify_chain`` ever *did* catch a truncation, the
    checkpoint machinery below would be solving a problem that had moved.
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)

    assert verify_chain(records[:-1], expected_tenant_id="acme").ok


def test_deleting_the_last_record_is_detected_against_the_checkpoint(
    clock: FrozenClock,
) -> None:
    """``SEC-10``: the signed head is what turns a perfect chain into a caught one.

    The incident shape: someone with the store removes the most recent
    decisions, which are the ones an investigation is about. The remaining
    chain verifies, so the only thing that can contradict it is a statement
    made before the deletion and signed with a key the store does not hold.
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = _checkpoint_at(records)

    result = verify_against_checkpoint(
        records[:-1], checkpoint, tenant_id="acme", signer=_hmac_signer
    )

    assert not result.ok
    assert result.reason is ChainBreak.TRUNCATED
    assert result.first_broken_index == len(records) - 1
    assert str(records[-1]["seq"]) in result.detail


def test_an_intact_chain_verifies_against_its_checkpoint(clock: FrozenClock) -> None:
    """The control: the check must accept the chain it was made from."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)

    result = verify_against_checkpoint(
        records, _checkpoint_at(records), tenant_id="acme", signer=_hmac_signer
    )

    assert result.ok
    assert result.checked == len(records)


def test_a_chain_that_has_grown_since_its_checkpoint_still_verifies(
    clock: FrozenClock,
) -> None:
    """A checkpoint is a floor, not a ceiling; the normal case is a longer chain."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = _checkpoint_at(records, index=1)

    assert verify_against_checkpoint(
        records, checkpoint, tenant_id="acme", signer=_hmac_signer
    ).ok


def test_rewriting_the_checkpointed_record_is_a_mismatch_not_a_truncation(
    clock: FrozenClock,
) -> None:
    """A rewrite and a deletion start different investigations.

    Here the chain is re-hashed end to end so it verifies on its own -- the
    attacker had the whole store -- and only the checkpoint disagrees. Reporting
    that as ``TRUNCATED`` would send an operator looking for records that were
    never removed.
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = _checkpoint_at(records)

    rewritten = _build_chain("acme", _CHAIN_LENGTH, clock=clock, trace_id="1" * 32)
    assert verify_chain(rewritten, expected_tenant_id="acme").ok

    result = verify_against_checkpoint(
        rewritten, checkpoint, tenant_id="acme", signer=_hmac_signer
    )

    assert not result.ok
    assert result.reason is ChainBreak.CHECKPOINT_MISMATCH
    assert result.first_broken_index == len(rewritten) - 1


def test_an_unverifiable_checkpoint_accuses_nobody(clock: FrozenClock) -> None:
    """A forged checkpoint must not be usable to discredit an intact chain.

    Checking the signature first is what stops the checkpoint becoming an attack
    of its own: anyone who could fabricate one could otherwise make a correct
    store look tampered with, which is a denial of service against the audit
    trail rather than against the harness.
    """
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    forged = dict(_checkpoint_at(records))
    forged["signature"] = "bm90LWEtc2lnbmF0dXJl"

    result = verify_against_checkpoint(
        records, forged, tenant_id="acme", signer=_hmac_signer
    )

    assert not result.ok
    assert result.reason is ChainBreak.CHECKPOINT_INVALID


def test_a_checkpoint_from_another_tenant_does_not_verify_here(
    clock: FrozenClock,
) -> None:
    """``NFR-15``: the tenant is inside the signed payload for exactly this."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = _checkpoint_at(records)

    result = verify_against_checkpoint(
        records, checkpoint, tenant_id="other", signer=_hmac_signer
    )

    assert not result.ok
    assert result.reason is ChainBreak.CHECKPOINT_INVALID


def test_a_broken_chain_is_reported_as_broken_not_as_a_checkpoint_problem(
    clock: FrozenClock,
) -> None:
    """The chain's own diagnosis survives: the checkpoint check adds, never replaces."""
    records = _build_chain("acme", _CHAIN_LENGTH, clock=clock)
    checkpoint = _checkpoint_at(records)
    records[1] = copy.deepcopy(records[1])
    records[1]["trace_id"] = "9" * 32

    result = verify_against_checkpoint(
        records, checkpoint, tenant_id="acme", signer=_hmac_signer
    )

    assert not result.ok
    assert result.reason is ChainBreak.RECORD_HASH_MISMATCH
    assert result.first_broken_index == 1


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


def test_checkpoint_signing_bytes_carry_the_domain_separator() -> None:
    """``SEC-10``: a checkpoint signature must not be reusable over anything else.

    The previous version of this test compared two checkpoints that differed by
    ``anchor_ref`` and then asserted the result was ``bytes``. It never
    referenced the separator, so deleting ``"domain"`` from the signed payload
    passed the entire suite -- the constant existed and nothing depended on it.

    What the separator buys: without it, the signed bytes are a digest of an
    ordinary object, and any other structure that canonicalised to the same
    document would produce a signature presentable as a checkpoint. The
    assertion is therefore that the bytes are *not* the bare digest of those
    fields.
    """
    fields = {
        "tenant_id": "acme",
        "last_seq": 1,
        "last_record_hash": Digest("sha256:" + "33" * 32),
        "key_id": _KEY_ID,
    }
    payload = checkpoint_signing_bytes(**fields)

    undomained = digest_value(
        {
            "tenant_id": fields["tenant_id"],
            "last_seq": fields["last_seq"],
            "last_record_hash": str(fields["last_record_hash"]),
            "key_id": fields["key_id"],
            "anchor_ref": None,
        }
    ).encode("utf-8")

    assert payload != undomained, (
        "the signed bytes are the digest of the checkpoint fields alone; the "
        "domain separator is not in the payload (SEC-10)"
    )
    assert CHECKPOINT_SIGNING_DOMAIN == "neuroharness/evidence-checkpoint/v1", (
        "the separator is part of the signature format: changing it invalidates "
        "every checkpoint ever signed, so it moves only with a version bump"
    )


def test_every_signed_field_changes_the_checkpoint_bytes() -> None:
    """A field outside the signature is a field an attacker may edit freely."""
    base = {
        "tenant_id": "acme",
        "last_seq": 1,
        "last_record_hash": Digest("sha256:" + "33" * 32),
        "key_id": _KEY_ID,
        "anchor_ref": None,
    }
    payload = checkpoint_signing_bytes(**base)

    for field, altered in (
        ("tenant_id", "other"),
        ("last_seq", 2),
        ("last_record_hash", Digest("sha256:" + "44" * 32)),
        ("key_id", "another-key"),
        ("anchor_ref", "https://example.invalid/anchor"),
    ):
        assert checkpoint_signing_bytes(**{**base, field: altered}) != payload, (
            f"{field} is not covered by the checkpoint signature"
        )


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
