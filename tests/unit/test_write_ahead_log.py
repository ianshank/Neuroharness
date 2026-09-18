"""The gateway's local write-ahead log (``FR-23``, ``NFR-12``, specification 5.3).

The log never rescues an action: the action was already refused. What it rescues
is the *record of the refusal*, which is why replay has to restore the records
exactly, in order, and exactly once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Sequence

import pytest

from neuroharness.errors import EvidenceUnavailableError
from neuroharness.evidence.chain import GENESIS_SEQ, MalformedRecordError, RecordKind
from neuroharness.evidence.store import (
    AppendResult,
    EvidenceStore,
    EvidenceWriter,
    InMemoryEvidenceStore,
)
from neuroharness.evidence.wal import (
    DEFAULT_MAX_PENDING_RECORDS,
    InMemoryWriteAheadLog,
    ReplayReport,
    StagedRecord,
    WriteAheadLog,
    WriteAheadLogFullError,
)
from neuroharness.reason import ReasonName
from neuroharness.seams import FrozenClock, SequenceIdGenerator

_START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
_TRACE_ID = "0" * 32
_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_OUTAGE_RECORDS = 3


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(_START)


@pytest.fixture()
def ids() -> SequenceIdGenerator:
    return SequenceIdGenerator("rec")


@pytest.fixture()
def store(clock: FrozenClock, ids: SequenceIdGenerator) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=ids)


@pytest.fixture()
def wal(clock: FrozenClock) -> InMemoryWriteAheadLog:
    return InMemoryWriteAheadLog(clock=clock)


def _staged_record(record_id: str, tenant_id: str = _TENANT_A, **overrides: Any) -> dict[str, Any]:
    """A record as the writer stages it: identity and time already stamped."""
    record: dict[str, Any] = {
        "schema_version": "1.1",
        "record_id": record_id,
        "tenant_id": tenant_id,
        "kind": RecordKind.EVALUATION.value,
        "timestamp": _START.isoformat(),
        "trace_id": _TRACE_ID,
        "evaluation": {"verdict": "ABSTAIN", "reason_codes": ["EVIDENCE_UNAVAILABLE"]},
    }
    record.update(overrides)
    return record


class _StoreFailingAfter:
    """An evidence store that accepts ``limit`` appends and then goes down.

    Used to prove that replay stops at the first failure instead of skipping
    past it, which would reorder evidence and hide a store that is still unwell.
    """

    def __init__(self, inner: InMemoryEvidenceStore, *, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self.accepted = 0

    def append(self, record: Mapping[str, Any]) -> AppendResult:
        if self.accepted >= self._limit:
            raise EvidenceUnavailableError("simulated store failure during replay")
        self.accepted += 1
        return self._inner.append(record)

    def read(self, tenant_id: str, **kwargs: Any) -> Sequence[Mapping[str, Any]]:
        return self._inner.read(tenant_id, **kwargs)

    def latest(self, tenant_id: str) -> Mapping[str, Any] | None:
        return self._inner.latest(tenant_id)

    def export(self, tenant_id: str, **kwargs: Any) -> Iterator[Mapping[str, Any]]:
        return self._inner.export(tenant_id, **kwargs)

    def has_record(self, tenant_id: str, record_id: str) -> bool:
        return self._inner.has_record(tenant_id, record_id)


# --- staging ------------------------------------------------------------------


def test_in_memory_wal_satisfies_the_protocol(wal: InMemoryWriteAheadLog) -> None:
    assert isinstance(wal, WriteAheadLog)


def test_stage_keeps_order_and_records_the_staging_time(
    wal: InMemoryWriteAheadLog, clock: FrozenClock
) -> None:
    first = wal.stage(_staged_record("rec-1"))
    clock.advance(5)
    second = wal.stage(_staged_record("rec-2"))

    assert isinstance(first, StagedRecord)
    assert [item.record_id for item in wal.pending()] == ["rec-1", "rec-2"]
    assert first.staged_at == _START.isoformat()
    assert second.staged_at == clock.now().isoformat()
    assert len(wal) == 2


def test_staging_the_same_record_twice_is_one_entry(wal: InMemoryWriteAheadLog) -> None:
    """A retry of the same staging is not a second decision."""
    first = wal.stage(_staged_record("rec-1"))
    again = wal.stage(_staged_record("rec-1"))

    assert again is first
    assert len(wal.pending()) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"record_id": None}, id="no-record-id"),
        pytest.param({"record_id": ""}, id="empty-record-id"),
        pytest.param({"tenant_id": None}, id="no-tenant-id"),
    ],
)
def test_stage_refuses_a_record_without_identity(
    wal: InMemoryWriteAheadLog, overrides: dict[str, Any]
) -> None:
    """Without an identity a replay could not tell a retry from a new record."""
    record = _staged_record("rec-1")
    record.update(overrides)

    with pytest.raises(MalformedRecordError):
        wal.stage(record)
    assert wal.pending() == ()


def test_stage_refuses_more_than_the_configured_bound(clock: FrozenClock) -> None:
    """An unbounded log trades an evidence outage for a gateway outage."""
    small = InMemoryWriteAheadLog(clock=clock, max_pending=2)
    small.stage(_staged_record("rec-1"))
    small.stage(_staged_record("rec-2"))

    with pytest.raises(WriteAheadLogFullError) as raised:
        small.stage(_staged_record("rec-3"))

    # A full log is an evidence outage that has lasted too long, so callers that
    # already fail closed on EvidenceUnavailableError need no new branch.
    assert isinstance(raised.value, EvidenceUnavailableError)
    assert raised.value.reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE
    assert len(small.pending()) == 2


def test_the_default_bound_is_a_named_value() -> None:
    assert DEFAULT_MAX_PENDING_RECORDS > 0


def test_a_zero_capacity_log_is_refused(clock: FrozenClock) -> None:
    with pytest.raises(ValueError):
        InMemoryWriteAheadLog(clock=clock, max_pending=0)


# --- replay -------------------------------------------------------------------


def test_replay_restores_the_exact_records_in_order(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    staged = [_staged_record(f"rec-{index}") for index in range(_OUTAGE_RECORDS)]
    for record in staged:
        wal.stage(record)

    report = wal.replay(store)

    assert isinstance(report, ReplayReport)
    assert report.ok
    assert report.attempted == _OUTAGE_RECORDS
    assert report.appended_record_ids == tuple(record["record_id"] for record in staged)
    restored = store.read(_TENANT_A)
    assert [record["seq"] for record in restored] == [0, 1, 2]
    for original, stored in zip(staged, restored):
        assert stored["record_id"] == original["record_id"]
        assert stored["timestamp"] == original["timestamp"]
        # Stored sequences come back as tuples: a stored record is read-only
        # all the way down, and a list would not be.
        assert list(stored["evaluation"]["reason_codes"]) == ["EVIDENCE_UNAVAILABLE"]
    assert store.verify(_TENANT_A).ok
    assert wal.pending() == ()


def test_replay_chains_staged_records_onto_the_existing_head(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """Records written before the outage keep their place; the log extends them."""
    store.append(_staged_record("before-outage"))
    wal.stage(_staged_record("after-outage"))

    report = wal.replay(store)

    assert report.ok
    assert [record["seq"] for record in store.read(_TENANT_A)] == [GENESIS_SEQ, GENESIS_SEQ + 1]
    assert store.verify(_TENANT_A).ok


def test_replay_spans_tenants_without_crossing_their_chains(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    wal.stage(_staged_record("a-1", _TENANT_A))
    wal.stage(_staged_record("b-1", _TENANT_B))
    wal.stage(_staged_record("a-2", _TENANT_A))

    report = wal.replay(store)

    assert report.ok
    assert [record["seq"] for record in store.read(_TENANT_A)] == [0, 1]
    assert [record["seq"] for record in store.read(_TENANT_B)] == [0]
    assert store.verify(_TENANT_A).ok
    assert store.verify(_TENANT_B).ok


def test_replaying_twice_does_not_duplicate_records(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    for index in range(_OUTAGE_RECORDS):
        wal.stage(_staged_record(f"rec-{index}"))

    first = wal.replay(store)
    second = wal.replay(store)

    assert first.ok and second.ok
    assert len(first.appended) == _OUTAGE_RECORDS
    assert second.attempted == 0
    assert second.appended == ()
    assert len(store) == _OUTAGE_RECORDS


def test_replay_dedupes_on_record_id_against_the_store(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """A crash between the append and the discard must not double-count evidence."""
    record = _staged_record("rec-1")
    store.append(record)
    wal.stage(record)

    report = wal.replay(store)

    assert report.ok
    assert report.appended == ()
    assert report.duplicates == ("rec-1",)
    assert len(store) == 1


def test_replay_stops_at_the_first_failure_and_keeps_the_remainder(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    for index in range(4):
        wal.stage(_staged_record(f"rec-{index}"))
    flaky = _StoreFailingAfter(store, limit=2)

    report = wal.replay(flaky)

    assert not report.ok
    assert report.appended_record_ids == ("rec-0", "rec-1")
    assert report.failed_record_id == "rec-2"
    assert report.failure_reason_code is not None
    assert report.failure_reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE
    assert report.remaining == ("rec-2", "rec-3")
    assert [item.record_id for item in wal.pending()] == ["rec-2", "rec-3"]
    assert store.verify(_TENANT_A).ok


def test_a_second_replay_after_recovery_finishes_the_remainder(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    for index in range(4):
        wal.stage(_staged_record(f"rec-{index}"))
    flaky = _StoreFailingAfter(store, limit=2)
    wal.replay(flaky)

    report = wal.replay(store)

    assert report.ok
    assert report.appended_record_ids == ("rec-2", "rec-3")
    assert [record["record_id"] for record in store.read(_TENANT_A)] == [
        "rec-0",
        "rec-1",
        "rec-2",
        "rec-3",
    ]
    assert store.verify(_TENANT_A).ok


def test_replay_of_an_empty_log_is_a_successful_no_op(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    report = wal.replay(store)

    assert report.ok
    assert report.attempted == 0
    assert len(store) == 0


def test_a_replay_report_is_frozen(store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog) -> None:
    report = wal.replay(store)

    with pytest.raises(AttributeError):
        report.attempted = 5  # type: ignore[misc]


# --- discarding ---------------------------------------------------------------


def test_discard_removes_one_named_record(wal: InMemoryWriteAheadLog) -> None:
    wal.stage(_staged_record("rec-1"))
    wal.stage(_staged_record("rec-2"))

    assert wal.discard("rec-1") is True
    assert wal.discard("rec-1") is False
    assert [item.record_id for item in wal.pending()] == ["rec-2"]


def test_discard_all_empties_the_log(wal: InMemoryWriteAheadLog) -> None:
    wal.stage(_staged_record("rec-1"))
    wal.stage(_staged_record("rec-2"))

    assert wal.discard_all() == 2
    assert wal.pending() == ()


# --- the outage path end to end ------------------------------------------------


def test_outage_then_recovery_restores_every_refused_decision(
    store: InMemoryEvidenceStore,
    wal: InMemoryWriteAheadLog,
    clock: FrozenClock,
    ids: SequenceIdGenerator,
) -> None:
    """``MUT-13``/``MUT-21`` end to end: refused during the outage, recorded after."""
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)
    writer.write({"tenant_id": _TENANT_A, "kind": RecordKind.EVALUATION.value})

    store.set_available(False)
    for _ in range(_OUTAGE_RECORDS):
        with pytest.raises(EvidenceUnavailableError):
            writer.write({"tenant_id": _TENANT_A, "kind": RecordKind.EVALUATION.value})
        clock.advance(1)

    assert len(store) == 1
    assert len(wal.pending()) == _OUTAGE_RECORDS

    store.set_available(True)
    report = writer.recover()

    assert report is not None and report.ok
    assert len(store) == 1 + _OUTAGE_RECORDS
    assert store.verify(_TENANT_A).ok
    assert wal.pending() == ()
    assert isinstance(store, EvidenceStore)
