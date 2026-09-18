"""Append-only, per-tenant chained evidence, and the write-ahead discipline.

Covers ``FR-70``-``FR-73``, ``INV-05`` (no token without a durable record),
``NFR-12`` (evidence store unavailable) and the behaviour the mutation fixtures
``MUT-13`` and ``MUT-21`` exist to kill.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

import pytest

from neuroharness.errors import (
    ClockUnavailableError,
    EvidenceUnavailableError,
    FailClosedError,
    SchemaVersionError,
)
from neuroharness.evidence.chain import (
    GENESIS_SEQ,
    SCHEMA_VERSION,
    MalformedRecordError,
    RecordKind,
)
from neuroharness.evidence.store import (
    AppendOnlyViolationError,
    AppendResult,
    DuplicateRecordError,
    EvidenceStore,
    EvidenceWriter,
    InMemoryEvidenceStore,
    to_jsonl,
)
from neuroharness.evidence.wal import InMemoryWriteAheadLog
from neuroharness.reason import ReasonName
from neuroharness.seams import FrozenClock, SequenceIdGenerator
from neuroharness import version as schema_version_module
from neuroharness.version import SchemaCompatibility, SchemaKind

_START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
_TRACE_ID = "0" * 32
_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_KEY_ID = "evidence-checkpoint-2026-09"

#: A record-schema version no build reads, used to exercise the refusal.
_UNREADABLE_SCHEMA_VERSION = "0.9"

#: A plausible *next* record-schema version, used to widen the readable set.
_NEXT_SCHEMA_VERSION = "1.2"

#: Epoch seconds: a timestamp that is not even a string.
_NUMERIC_TIMESTAMP = 1_726_660_800


def _widened_record_versions() -> frozenset[str]:
    """The readable record versions plus one more, as widening ``_READABLE`` does."""
    return frozenset(
        {*SchemaCompatibility.readable_versions(SchemaKind.RECORD), _NEXT_SCHEMA_VERSION}
    )


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(_START)


@pytest.fixture()
def ids() -> SequenceIdGenerator:
    return SequenceIdGenerator("rec")


@pytest.fixture()
def store(clock: FrozenClock, ids: SequenceIdGenerator) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=ids)


def _record(tenant_id: str = _TENANT_A, **overrides: Any) -> dict[str, Any]:
    """A minimally valid submitted record: no chain fields, the store adds those."""
    record: dict[str, Any] = {
        "tenant_id": tenant_id,
        "kind": RecordKind.EVALUATION.value,
        "trace_id": _TRACE_ID,
        "evaluation": {"verdict": "ALLOW", "reason_codes": []},
    }
    record.update(overrides)
    return record


def _signer(payload: bytes) -> str:
    import hashlib
    import hmac

    return hmac.new(b"test-checkpoint-key", payload, hashlib.sha256).hexdigest()


# --- appending ----------------------------------------------------------------


def test_in_memory_store_satisfies_the_protocol(store: InMemoryEvidenceStore) -> None:
    assert isinstance(store, EvidenceStore)


def test_append_assigns_increasing_seq_per_tenant(store: InMemoryEvidenceStore) -> None:
    results = [store.append(_record()) for _ in range(3)]

    assert [result.seq for result in results] == [GENESIS_SEQ, GENESIS_SEQ + 1, GENESIS_SEQ + 2]
    assert results[0].prev_record_hash is None
    assert results[1].prev_record_hash == results[0].record_hash
    assert results[2].prev_record_hash == results[1].record_hash
    assert store.verify(_TENANT_A).ok


def test_tenants_have_independent_chains(store: InMemoryEvidenceStore) -> None:
    """Each tenant's chain starts at genesis and links only to itself (``NFR-15``)."""
    a_first = store.append(_record(_TENANT_A))
    b_first = store.append(_record(_TENANT_B))
    a_second = store.append(_record(_TENANT_A))
    b_second = store.append(_record(_TENANT_B))

    assert (a_first.seq, b_first.seq) == (GENESIS_SEQ, GENESIS_SEQ)
    assert (a_second.seq, b_second.seq) == (GENESIS_SEQ + 1, GENESIS_SEQ + 1)
    assert a_second.prev_record_hash == a_first.record_hash
    assert b_second.prev_record_hash == b_first.record_hash
    assert store.verify(_TENANT_A).ok
    assert store.verify(_TENANT_B).ok
    assert store.tenants() == (_TENANT_A, _TENANT_B)


def test_append_stamps_identity_and_time_through_the_seams(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    result = store.append(_record())

    assert result.record_id == "rec-00000001"
    assert result.record["timestamp"] == _START.isoformat()
    assert result.record["schema_version"] == SCHEMA_VERSION
    clock.advance(60)
    assert store.append(_record()).record["timestamp"] == clock.now().isoformat()


def test_append_fails_closed_when_the_clock_is_unavailable(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """A record with no trustworthy time is not a record (``NFR-13``)."""
    clock.set_healthy(False)

    with pytest.raises(ClockUnavailableError):
        store.append(_record())
    assert len(store) == 0


def test_append_records_every_kind(store: InMemoryEvidenceStore) -> None:
    """Approvals, tokens, receipts and the rest are linked records (``FR-72``)."""
    for kind in RecordKind:
        store.append(_record(kind=kind.value))

    assert len(store) == len(RecordKind)
    assert store.verify(_TENANT_A).ok


# --- what the store refuses ---------------------------------------------------


@pytest.mark.parametrize("field", ["seq", "prev_record_hash", "record_hash"])
def test_append_refuses_a_caller_supplied_chain_position(
    store: InMemoryEvidenceStore, field: str
) -> None:
    """A caller that could choose its position could choose to be unlinked."""
    with pytest.raises(MalformedRecordError):
        store.append(_record(**{field: 0 if field == "seq" else None}))
    assert len(store) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kind": "audit_note"}, id="unknown-kind"),
        pytest.param({"kind": None}, id="missing-kind"),
        pytest.param({"tenant_id": ""}, id="empty-tenant"),
        pytest.param({"tenant_id": "t" * 65}, id="oversized-tenant"),
        pytest.param({"record_id": ""}, id="empty-record-id"),
        pytest.param({"timestamp": _NUMERIC_TIMESTAMP}, id="numeric-timestamp"),
    ],
)
def test_append_refuses_a_malformed_record(
    store: InMemoryEvidenceStore, overrides: dict[str, Any]
) -> None:
    with pytest.raises(MalformedRecordError):
        store.append(_record(**overrides))
    assert len(store) == 0


def test_append_refuses_an_unreadable_schema_version(store: InMemoryEvidenceStore) -> None:
    with pytest.raises(SchemaVersionError):
        store.append(_record(schema_version=_UNREADABLE_SCHEMA_VERSION))


def test_the_store_stamps_the_version_this_build_writes() -> None:
    """``chain.SCHEMA_VERSION`` is derived, not a second declaration (``F4``)."""
    assert SCHEMA_VERSION == SchemaCompatibility.written_version(SchemaKind.RECORD)


def test_widening_the_readable_set_makes_the_store_accept_the_new_version(
    store: InMemoryEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``version.py`` calls itself "the single place"; the store must obey it.

    Widening ``_READABLE`` is documented there as a backwards-compatible change.
    While the store compared against its own ``chain.SCHEMA_VERSION`` constant
    instead, widening it made :class:`DecisionRecord` accept the new version
    while the store still refused it - a record the harness can build and cannot
    write, which per ``F3`` then wedges the write-ahead log behind it.
    """
    monkeypatch.setitem(
        schema_version_module._READABLE, SchemaKind.RECORD, _widened_record_versions()
    )

    result = store.append(_record(schema_version=_NEXT_SCHEMA_VERSION))

    assert result.record["schema_version"] == _NEXT_SCHEMA_VERSION
    assert store.verify(_TENANT_A).ok


def test_widening_the_readable_set_does_not_change_what_is_stamped(
    store: InMemoryEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readable and written are different questions and must stay different.

    A build that read 1.2 and started *writing* it the moment the set widened
    would emit evidence older readers cannot parse, from a change the module
    documents as backwards compatible.
    """
    monkeypatch.setitem(
        schema_version_module._READABLE, SchemaKind.RECORD, _widened_record_versions()
    )

    result = store.append(_record())

    assert result.record["schema_version"] == SchemaCompatibility.written_version(
        SchemaKind.RECORD
    )


def test_narrowing_the_readable_set_makes_the_store_refuse(
    store: InMemoryEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check really is delegated, not merely coincidentally equal."""
    monkeypatch.setitem(
        schema_version_module._READABLE,
        SchemaKind.RECORD,
        frozenset({_NEXT_SCHEMA_VERSION}),
    )

    with pytest.raises(SchemaVersionError):
        store.append(
            _record(schema_version=SchemaCompatibility.written_version(SchemaKind.RECORD))
        )


# --- timestamps: FR-71 replays a decision using this value as "now" ----------


@pytest.mark.parametrize(
    "timestamp",
    [
        pytest.param("yesterday", id="prose"),
        pytest.param("2026-09-18", id="date-only-no-offset"),
        pytest.param("2026-09-18T12:00:00", id="naive-local"),
        pytest.param("2026-13-01T12:00:00Z", id="impossible-month"),
        pytest.param("   ", id="whitespace"),
        pytest.param(_NUMERIC_TIMESTAMP, id="epoch-seconds"),
    ],
)
def test_append_refuses_a_timestamp_it_could_not_replay(
    store: InMemoryEvidenceStore, timestamp: Any
) -> None:
    """``FR-71`` replays a decision using the record's timestamp as ``now``.

    Any non-empty string used to pass, and the value is hash-chained the moment
    it is accepted, so an unreplayable or zone-less instant became permanent,
    immutable evidence (``ADR-0006``). A naive local timestamp is the dangerous
    one: it does not fail the replay, it succeeds against the replaying host's
    offset instead of the deciding host's.
    """
    with pytest.raises(MalformedRecordError):
        store.append(_record(timestamp=timestamp))
    assert len(store) == 0


@pytest.mark.parametrize(
    "timestamp",
    [
        pytest.param("2026-09-18T12:00:00Z", id="zulu"),
        pytest.param("2026-09-18T12:00:00+00:00", id="explicit-utc"),
        pytest.param("2026-09-18T14:00:00+02:00", id="non-utc-offset"),
        pytest.param("2026-09-18T12:00:00.123456+00:00", id="microseconds"),
    ],
)
def test_append_accepts_any_offset_bearing_instant(
    store: InMemoryEvidenceStore, timestamp: str
) -> None:
    """Fail-closed must not become fail-shut: an offset is the bar, not UTC.

    The deciding host's own zone is legitimate evidence of where the decision
    was made, so it is preserved rather than normalised.
    """
    result = store.append(_record(timestamp=timestamp))

    assert result.record["timestamp"] == timestamp


def test_a_stamped_timestamp_is_accepted_by_its_own_validation(
    store: InMemoryEvidenceStore, clock: FrozenClock
) -> None:
    """The clock seam's own output must pass the gate it feeds."""
    stamped = store.append(_record()).record["timestamp"]

    assert store.append(_record(timestamp=stamped)).record["timestamp"] == stamped


def test_append_refuses_a_duplicate_record_id(store: InMemoryEvidenceStore) -> None:
    """``record_id`` is what makes replay idempotent; it must identify one record."""
    store.append(_record(record_id="fixed-id"))

    with pytest.raises(DuplicateRecordError):
        store.append(_record(record_id="fixed-id"))
    assert len(store) == 1


def test_the_same_record_id_may_exist_in_two_tenants(store: InMemoryEvidenceStore) -> None:
    store.append(_record(_TENANT_A, record_id="fixed-id"))
    store.append(_record(_TENANT_B, record_id="fixed-id"))

    assert store.has_record(_TENANT_A, "fixed-id")
    assert store.has_record(_TENANT_B, "fixed-id")


def test_update_and_delete_raise(store: InMemoryEvidenceStore) -> None:
    """Append-only is a control, not a convention: the methods exist and refuse."""
    store.append(_record())

    with pytest.raises(AppendOnlyViolationError):
        store.update(_TENANT_A, GENESIS_SEQ, {"kind": "override"})
    with pytest.raises(AppendOnlyViolationError):
        store.delete(_TENANT_A, GENESIS_SEQ)
    assert len(store) == 1
    assert store.verify(_TENANT_A).ok


def test_append_only_violations_are_fail_closed_errors(store: InMemoryEvidenceStore) -> None:
    with pytest.raises(FailClosedError):
        store.delete(_TENANT_A, GENESIS_SEQ)


# --- immutability of stored evidence ------------------------------------------


def test_a_returned_record_cannot_be_mutated(store: InMemoryEvidenceStore) -> None:
    """Read-only all the way down, so a reader cannot edit evidence in place."""
    result = store.append(_record())

    with pytest.raises(TypeError):
        result.record["kind"] = RecordKind.OVERRIDE.value  # type: ignore[index]
    with pytest.raises(TypeError):
        result.record["evaluation"]["verdict"] = "DENY"  # type: ignore[index]
    with pytest.raises(AttributeError):
        # Sequences are stored as tuples for the same reason.
        result.record["evaluation"]["reason_codes"].append("RULE_FAILED:WF-01")


def test_mutating_the_submitted_mapping_does_not_change_stored_evidence(
    store: InMemoryEvidenceStore,
) -> None:
    """Evidence is a statement about the past, not a live view of caller memory."""
    submitted = _record()
    result = store.append(submitted)

    submitted["evaluation"]["verdict"] = "DENY"
    submitted["kind"] = RecordKind.OVERRIDE.value

    stored = store.read(_TENANT_A)[0]
    assert stored["evaluation"]["verdict"] == "ALLOW"
    assert stored["kind"] == RecordKind.EVALUATION.value
    assert result.record["evaluation"]["verdict"] == "ALLOW"
    assert store.verify(_TENANT_A).ok


def test_appended_results_are_frozen_dataclasses(store: InMemoryEvidenceStore) -> None:
    result = store.append(_record())

    assert isinstance(result, AppendResult)
    with pytest.raises(AttributeError):
        result.seq = 99  # type: ignore[misc]


# --- reading and exporting ----------------------------------------------------


def test_read_honours_start_seq_and_limit(store: InMemoryEvidenceStore) -> None:
    for _ in range(5):
        store.append(_record())

    assert [record["seq"] for record in store.read(_TENANT_A)] == [0, 1, 2, 3, 4]
    assert [record["seq"] for record in store.read(_TENANT_A, start_seq=2)] == [2, 3, 4]
    assert [record["seq"] for record in store.read(_TENANT_A, start_seq=1, limit=2)] == [1, 2]
    assert store.read("unknown-tenant") == ()


@pytest.mark.parametrize("kwargs", [{"limit": -1}, {"start_seq": -1}])
def test_read_refuses_nonsense_windows(store: InMemoryEvidenceStore, kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        store.read(_TENANT_A, **kwargs)


def test_latest_returns_the_head_or_none(store: InMemoryEvidenceStore) -> None:
    assert store.latest(_TENANT_A) is None

    store.append(_record())
    last = store.append(_record())

    head = store.latest(_TENANT_A)
    assert head is not None
    assert head["record_hash"] == str(last.record_hash)


def test_export_streams_the_chain_in_order(store: InMemoryEvidenceStore) -> None:
    for _ in range(3):
        store.append(_record())
    store.append(_record(_TENANT_B))

    exported = list(store.export(_TENANT_A))

    assert [record["seq"] for record in exported] == [0, 1, 2]
    assert all(record["tenant_id"] == _TENANT_A for record in exported)


def test_jsonl_export_round_trips_and_verifies(store: InMemoryEvidenceStore) -> None:
    """The documented audit format is JSONL plus the checkpoint (``FR-73``)."""
    for _ in range(3):
        store.append(_record())

    lines = list(to_jsonl(store.export(_TENANT_A)))
    parsed = [json.loads(line) for line in lines]

    from neuroharness.evidence.chain import verify_chain

    assert len(lines) == 3
    assert verify_chain(parsed, expected_tenant_id=_TENANT_A).ok


def test_checkpoint_signs_the_current_head(store: InMemoryEvidenceStore) -> None:
    from neuroharness.evidence.chain import verify_checkpoint

    assert store.checkpoint(_TENANT_A, key_id=_KEY_ID, signer=_signer) is None

    store.append(_record())
    head = store.append(_record())
    checkpoint = store.checkpoint(_TENANT_A, key_id=_KEY_ID, signer=_signer)

    assert checkpoint is not None
    assert checkpoint["last_seq"] == head.seq
    assert checkpoint["last_record_hash"] == str(head.record_hash)
    assert verify_checkpoint(checkpoint, tenant_id=_TENANT_A, signer=_signer)


# --- outage: INV-05, NFR-12, MUT-13, MUT-21 -----------------------------------


@pytest.mark.mutation
def test_append_during_an_outage_raises_evidence_unavailable(
    store: InMemoryEvidenceStore,
) -> None:
    """``MUT-13``/``MUT-21``: no durable record, so no token, in any mode."""
    store.append(_record())
    store.set_available(False)

    with pytest.raises(EvidenceUnavailableError) as raised:
        store.append(_record())

    assert raised.value.reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE
    assert raised.value.reason_code.render() == "EVIDENCE_UNAVAILABLE"
    assert raised.value.reason_code.is_infrastructure
    assert len(store) == 1


def test_the_chain_is_intact_across_an_outage(store: InMemoryEvidenceStore) -> None:
    store.append(_record())
    store.set_available(False)
    with pytest.raises(EvidenceUnavailableError):
        store.append(_record())
    store.set_available(True)
    store.append(_record())

    assert store.verify(_TENANT_A).ok
    assert [record["seq"] for record in store.read(_TENANT_A)] == [0, 1]


# --- EvidenceWriter -----------------------------------------------------------


def test_writer_appends_through_the_store(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    writer = EvidenceWriter(store, clock=clock, id_generator=ids)

    result = writer.write(_record())

    assert result.seq == GENESIS_SEQ
    assert len(store) == 1


def test_writer_stages_to_the_wal_on_an_outage_and_re_raises(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    """Re-raising is the control: a swallowed failure would let a token be issued."""
    wal = InMemoryWriteAheadLog(clock=clock)
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)
    store.set_available(False)

    with pytest.raises(EvidenceUnavailableError):
        writer.write(_record())

    pending = wal.pending()
    assert len(pending) == 1
    assert pending[0].tenant_id == _TENANT_A
    assert pending[0].record["evaluation"]["verdict"] == "ALLOW"
    assert len(store) == 0


def test_writer_without_a_wal_still_fails_closed(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    writer = EvidenceWriter(store, clock=clock, id_generator=ids)
    store.set_available(False)

    with pytest.raises(EvidenceUnavailableError):
        writer.write(_record())
    assert writer.recover() is None


def test_writer_recovery_replays_the_staged_record_with_its_original_time(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    """The record must say when the decision happened, not when the store came back."""
    wal = InMemoryWriteAheadLog(clock=clock)
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)
    store.set_available(False)
    with pytest.raises(EvidenceUnavailableError):
        writer.write(_record())
    decision_time = clock.now().isoformat()

    clock.advance(900)
    store.set_available(True)
    report = writer.recover()

    assert report is not None and report.ok
    [stored] = store.read(_TENANT_A)
    assert stored["timestamp"] == decision_time
    assert stored["timestamp"] != clock.now().isoformat()
    assert wal.pending() == ()
    assert store.verify(_TENANT_A).ok


def test_writer_does_not_stage_a_malformed_record(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    """A record the store would never accept is a bug to surface, not to retry."""
    wal = InMemoryWriteAheadLog(clock=clock)
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)

    with pytest.raises(MalformedRecordError):
        writer.write(_record(kind="not-a-kind"))
    assert wal.pending() == ()


# --- concurrency --------------------------------------------------------------


def test_concurrent_appends_produce_one_unbroken_chain_per_tenant(
    store: InMemoryEvidenceStore,
) -> None:
    """Reading the head and writing its successor must be one atomic step."""
    writers = 8
    per_writer = 25
    barrier = threading.Barrier(writers)

    def append_many(tenant_id: str) -> None:
        barrier.wait()
        for _ in range(per_writer):
            store.append(_record(tenant_id))

    threads = [
        threading.Thread(target=append_many, args=(_TENANT_A if index % 2 else _TENANT_B,))
        for index in range(writers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for tenant_id in (_TENANT_A, _TENANT_B):
        records = store.read(tenant_id)
        assert [record["seq"] for record in records] == list(
            range(writers // 2 * per_writer)
        )
        assert store.verify(tenant_id).ok
    assert len({record["record_id"] for record in store.read(_TENANT_A)}) == (
        writers // 2 * per_writer
    )


def test_enum_values_are_stored_in_their_wire_form(store: InMemoryEvidenceStore) -> None:
    """The digest is over JSON, so a Python enum is normalised before hashing."""
    result = store.append(_record(kind=RecordKind.OVERRIDE))

    assert result.record["kind"] == "override"
    assert not isinstance(result.record["kind"], RecordKind)
    assert store.verify(_TENANT_A).ok


def test_append_refuses_something_that_is_not_a_record(store: InMemoryEvidenceStore) -> None:
    with pytest.raises(MalformedRecordError):
        store.append(["tenant-a", "evaluation"])  # type: ignore[arg-type]


def test_availability_is_readable(store: InMemoryEvidenceStore) -> None:
    assert store.available is True
    store.set_available(False)
    assert store.available is False


def test_writer_exposes_what_it_writes_through(
    store: InMemoryEvidenceStore, clock: FrozenClock, ids: SequenceIdGenerator
) -> None:
    wal = InMemoryWriteAheadLog(clock=clock)
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)

    assert writer.store is store
    assert writer.wal is wal
