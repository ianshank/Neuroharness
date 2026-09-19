"""The gateway's local write-ahead log (``FR-23``, ``NFR-12``, specification 5.3).

The log never rescues an action: the action was already refused. What it rescues
is the *record of the refusal*, which is why replay has to restore the records
exactly, in order, and exactly once.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

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
    AmbiguousRecordError,
    InMemoryWriteAheadLog,
    ReplayReport,
    StagedRecord,
    WriteAheadLog,
    WriteAheadLogFullError,
)
from neuroharness.reason import ReasonName
from neuroharness.seams import DeterministicUuidGenerator, FrozenClock
from neuroharness.version import SchemaCompatibility, SchemaKind

_START = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
_TRACE_ID = "0" * 32
_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_OUTAGE_RECORDS = 3

#: The action a staged record describes. ``FR-72`` requires the link and the
#: schema types it as a UUID, so a staged record without one is a record the
#: store will refuse on replay -- which is the wedge this module tests, not the
#: shape its fixtures should have.
_DECISION_ID = str(UUID(int=0xDEC))
_ACTION_ID = str(UUID(int=0xAC))

#: The documented capacity of the local write-ahead log, pinned here so that
#: changing it is a reviewed act rather than a one-character edit.
EXPECTED_MAX_PENDING_RECORDS = 10_000

#: The version every well-formed fixture declares, taken from the one place that
#: declares it rather than repeated here (``F4``).
_RECORD_SCHEMA_VERSION = SchemaCompatibility.written_version(SchemaKind.RECORD)

#: A record-schema version no build reads. Derived, so that widening the readable
#: set can never quietly turn these fixtures into valid records.
_UNREADABLE_SCHEMA_VERSION = "9999.0"

#: A ``kind`` outside ``RecordKind``: the shape ``MalformedRecordError`` names.
_UNKNOWN_RECORD_KIND = "not-a-record-kind"


@pytest.fixture()
def clock() -> FrozenClock:
    return FrozenClock(_START)


@pytest.fixture()
def ids() -> DeterministicUuidGenerator:
    """UUIDs: the record schema types every identifier as one (``FR-72``)."""
    return DeterministicUuidGenerator()


@pytest.fixture()
def store(clock: FrozenClock, ids: DeterministicUuidGenerator) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=ids)


@pytest.fixture()
def wal(clock: FrozenClock) -> InMemoryWriteAheadLog:
    return InMemoryWriteAheadLog(clock=clock)


#: A stable UUID for a readable label. The record schema types every identifier
#: as a UUID (``FR-72``), but ``"rec-2"`` says which record a failing assertion
#: is about and ``0000...0002`` does not, so the tests keep the label and derive
#: the identifier from it.
_RECORD_NAMESPACE = uuid5(NAMESPACE_URL, "neuroharness/tests/write-ahead-log")


def _rid(label: str) -> str:
    return str(uuid5(_RECORD_NAMESPACE, label))


def _staged_record(record_id: str, tenant_id: str = _TENANT_A, **overrides: Any) -> dict[str, Any]:
    """A record as the writer stages it: identity and time already stamped."""
    record: dict[str, Any] = {
        "schema_version": _RECORD_SCHEMA_VERSION,
        "record_id": record_id,
        "tenant_id": tenant_id,
        "kind": RecordKind.EVALUATION.value,
        "timestamp": _START.isoformat(),
        "trace_id": _TRACE_ID,
        "decision_id": _DECISION_ID,
        "action_id": _ACTION_ID,
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
    first = wal.stage(_staged_record(_rid("rec-1")))
    clock.advance(5)
    second = wal.stage(_staged_record(_rid("rec-2")))

    assert isinstance(first, StagedRecord)
    assert [item.record_id for item in wal.pending()] == [_rid("rec-1"), _rid("rec-2")]
    assert first.staged_at == _START.isoformat()
    assert second.staged_at == clock.now().isoformat()
    assert len(wal) == 2


def test_staging_the_same_record_twice_is_one_entry(wal: InMemoryWriteAheadLog) -> None:
    """A retry of the same staging is not a second decision."""
    first = wal.stage(_staged_record(_rid("rec-1")))
    again = wal.stage(_staged_record(_rid("rec-1")))

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
    record = _staged_record(_rid("rec-1"))
    record.update(overrides)

    with pytest.raises(MalformedRecordError):
        wal.stage(record)
    assert wal.pending() == ()


def test_stage_refuses_more_than_the_configured_bound(clock: FrozenClock) -> None:
    """An unbounded log trades an evidence outage for a gateway outage."""
    small = InMemoryWriteAheadLog(clock=clock, max_pending=2)
    small.stage(_staged_record(_rid("rec-1")))
    small.stage(_staged_record(_rid("rec-2")))

    with pytest.raises(WriteAheadLogFullError) as raised:
        small.stage(_staged_record(_rid("rec-3")))

    # A full log is an evidence outage that has lasted too long, so callers that
    # already fail closed on EvidenceUnavailableError need no new branch.
    assert isinstance(raised.value, EvidenceUnavailableError)
    assert raised.value.reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE
    assert len(small.pending()) == 2


def test_the_default_bound_is_the_documented_value() -> None:
    """The bound is a capacity promise, so drifting it is an operational change.

    It is sized for a long evidence outage at the throughput the performance
    budget assumes. Quietly lowering it turns an evidence outage into a gateway
    outage sooner than anyone was told to expect; quietly raising it lets the
    gateway consume memory past the point the sizing covers. Either is a change
    a reviewer should have to see, so the value is pinned rather than merely
    asserted to be positive.
    """
    assert DEFAULT_MAX_PENDING_RECORDS == EXPECTED_MAX_PENDING_RECORDS


def test_the_constructor_default_is_that_same_bound(clock: FrozenClock) -> None:
    """A log whose default differs from the documented one is undocumented.

    Read through the private attribute deliberately: the only public way to
    observe the bound is to stage ten thousand records, and a test that slow is
    a test that gets skipped.
    """
    assert InMemoryWriteAheadLog(clock=clock)._max_pending == DEFAULT_MAX_PENDING_RECORDS


def test_a_zero_capacity_log_is_refused(clock: FrozenClock) -> None:
    with pytest.raises(ValueError):
        InMemoryWriteAheadLog(clock=clock, max_pending=0)


# --- replay -------------------------------------------------------------------


def test_replay_restores_the_exact_records_in_order(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    staged = [_staged_record(_rid(f"rec-{index}")) for index in range(_OUTAGE_RECORDS)]
    for record in staged:
        wal.stage(record)

    report = wal.replay(store)

    assert isinstance(report, ReplayReport)
    assert report.ok
    assert report.attempted == _OUTAGE_RECORDS
    assert report.appended_record_ids == tuple(record["record_id"] for record in staged)
    restored = store.read(_TENANT_A)
    assert [record["seq"] for record in restored] == [0, 1, 2]
    # ``strict``: a replay that drops a record would otherwise shorten the zip
    # and the loop would assert nothing about the missing one.
    for original, stored in zip(staged, restored, strict=True):
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
    store.append(_staged_record(_rid("before-outage")))
    wal.stage(_staged_record(_rid("after-outage")))

    report = wal.replay(store)

    assert report.ok
    assert [record["seq"] for record in store.read(_TENANT_A)] == [GENESIS_SEQ, GENESIS_SEQ + 1]
    assert store.verify(_TENANT_A).ok


def test_replay_spans_tenants_without_crossing_their_chains(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    wal.stage(_staged_record(_rid("a-1"), _TENANT_A))
    wal.stage(_staged_record(_rid("b-1"), _TENANT_B))
    wal.stage(_staged_record(_rid("a-2"), _TENANT_A))

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
        wal.stage(_staged_record(_rid(f"rec-{index}")))

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
    record = _staged_record(_rid("rec-1"))
    store.append(record)
    wal.stage(record)

    report = wal.replay(store)

    assert report.ok
    assert report.appended == ()
    assert report.duplicates == (_rid("rec-1"),)
    assert len(store) == 1


def test_replay_stops_at_the_first_failure_and_keeps_the_remainder(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    for index in range(4):
        wal.stage(_staged_record(_rid(f"rec-{index}")))
    flaky = _StoreFailingAfter(store, limit=2)

    report = wal.replay(flaky)

    assert not report.ok
    assert report.appended_record_ids == (_rid("rec-0"), _rid("rec-1"))
    assert report.failed_record_id == _rid("rec-2")
    assert report.failure_reason_code is not None
    assert report.failure_reason_code.name is ReasonName.EVIDENCE_UNAVAILABLE
    assert report.remaining == (_rid("rec-2"), _rid("rec-3"))
    assert [item.record_id for item in wal.pending()] == [_rid("rec-2"), _rid("rec-3")]
    assert store.verify(_TENANT_A).ok


def test_a_second_replay_after_recovery_finishes_the_remainder(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    for index in range(4):
        wal.stage(_staged_record(_rid(f"rec-{index}")))
    flaky = _StoreFailingAfter(store, limit=2)
    wal.replay(flaky)

    report = wal.replay(store)

    assert report.ok
    assert report.appended_record_ids == (_rid("rec-2"), _rid("rec-3"))
    assert [record["record_id"] for record in store.read(_TENANT_A)] == [
        _rid("rec-0"),
        _rid("rec-1"),
        _rid("rec-2"),
        _rid("rec-3"),
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


# --- un-appendable records: stopping without wedging ---------------------------


def test_an_unreadable_schema_version_does_not_propagate_out_of_replay(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """The wedge, reproduced.

    ``store.append`` refuses more than an evidence outage: a record declaring a
    schema version this build cannot write raises ``SchemaVersionError``, and a
    malformed one raises ``MalformedRecordError``. Both are ``FailClosedError``
    but neither is ``EvidenceUnavailableError``, so both used to escape
    ``replay()`` before it reached the block that drains ``_pending``. The
    records that *had* been appended therefore stayed staged, every later replay
    re-attempted them and changed nothing, and the log filled until
    ``WriteAheadLogFullError`` made the gateway abstain on everything (``FR-23``,
    Constitution Art. II).
    """
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    wal.stage(_staged_record(_rid("rec-good")))

    report = wal.replay(store)

    assert report.failed is True
    assert report.ok is False
    assert report.failed_record_id == _rid("rec-bad")
    assert report.failure_reason_code is not None
    assert report.failure_reason_code.name is ReasonName.SCHEMA_INVALID
    assert report.appended == ()
    assert report.remaining == (_rid("rec-bad"), _rid("rec-good"))


def test_replaying_a_wedged_log_again_changes_nothing(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """Which is why a release valve is needed and not merely nicer errors."""
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    wal.stage(_staged_record(_rid("rec-good")))

    first = wal.replay(store)
    second = wal.replay(store)

    assert first.remaining == second.remaining == (_rid("rec-bad"), _rid("rec-good"))
    assert len(store) == 0


def test_a_malformed_record_also_stops_rather_than_raises(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """``MalformedRecordError`` is the other escape the narrow catch missed."""
    wal.stage(_staged_record(_rid("rec-bad"), kind=_UNKNOWN_RECORD_KIND))
    wal.stage(_staged_record(_rid("rec-good")))

    report = wal.replay(store)

    assert report.failed_record_id == _rid("rec-bad")
    assert report.failure_reason_code is not None
    assert report.failure_reason_code.name is ReasonName.SCHEMA_INVALID
    assert report.remaining == (_rid("rec-bad"), _rid("rec-good"))


def test_records_appended_before_the_failure_are_not_re_attempted(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """The bookkeeping the propagating replay used to skip.

    Without it the successful appends stayed staged, so the next replay
    re-offered them and relied entirely on ``record_id`` deduplication to avoid
    double-counting evidence.
    """
    wal.stage(_staged_record(_rid("rec-good")))
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))

    report = wal.replay(store)

    assert report.appended_record_ids == (_rid("rec-good"),)
    assert report.remaining == (_rid("rec-bad"),)
    assert [item.record_id for item in wal.pending()] == [_rid("rec-bad")]


def test_quarantine_lets_the_records_behind_a_bad_one_drain(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """The release valve: one un-appendable record must not cost the rest."""
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    for index in range(_OUTAGE_RECORDS):
        wal.stage(_staged_record(_rid(f"rec-{index}")))

    blocked = wal.replay(store)
    assert blocked.failed_record_id == _rid("rec-bad")
    assert len(store) == 0

    quarantined = wal.quarantine(_rid("rec-bad"))
    assert quarantined is not None
    assert quarantined.record_id == _rid("rec-bad")

    report = wal.replay(store)

    assert report.ok
    assert report.appended_record_ids == tuple(
        _rid(f"rec-{index}") for index in range(_OUTAGE_RECORDS)
    )
    assert len(store) == _OUTAGE_RECORDS
    assert store.verify(_TENANT_A).ok
    assert wal.pending() == ()


def test_a_quarantined_record_is_kept_not_deleted(wal: InMemoryWriteAheadLog) -> None:
    """Quarantine is not a delete: the evidence never became durable (ADR-0006)."""
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))

    wal.quarantine(_rid("rec-bad"))

    assert [item.record_id for item in wal.quarantined()] == [_rid("rec-bad")]
    assert wal.pending() == ()
    # Still held by this log, so a backlog gauge cannot read empty.
    assert len(wal) == 1


def test_quarantining_an_unknown_record_reports_nothing(wal: InMemoryWriteAheadLog) -> None:
    assert wal.quarantine("rec-absent") is None


def test_a_quarantined_record_cannot_be_re_staged_into_the_queue(
    wal: InMemoryWriteAheadLog,
) -> None:
    """Otherwise the same un-appendable record wedges the log a second time."""
    staged = wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    wal.quarantine(_rid("rec-bad"))

    again = wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))

    assert again == staged
    assert wal.pending() == ()
    assert len(wal.quarantined()) == 1


def test_quarantined_records_still_count_against_the_bound(clock: FrozenClock) -> None:
    """Setting a record aside unblocks replay; it does not shrink the backlog."""
    small = InMemoryWriteAheadLog(clock=clock, max_pending=2)
    small.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    small.stage(_staged_record(_rid("rec-1")))
    small.quarantine(_rid("rec-bad"))

    with pytest.raises(WriteAheadLogFullError):
        small.stage(_staged_record(_rid("rec-2")))


def test_discard_can_clear_a_quarantined_record(wal: InMemoryWriteAheadLog) -> None:
    """Accepting the loss is a separate, named decision."""
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    wal.quarantine(_rid("rec-bad"))

    assert wal.discard(_rid("rec-bad")) is True
    assert wal.quarantined() == ()
    assert len(wal) == 0


def test_discard_all_clears_quarantined_records_too(wal: InMemoryWriteAheadLog) -> None:
    wal.stage(_staged_record(_rid("rec-bad"), schema_version=_UNREADABLE_SCHEMA_VERSION))
    wal.stage(_staged_record(_rid("rec-1")))
    wal.quarantine(_rid("rec-bad"))

    assert wal.discard_all() == 2
    assert len(wal) == 0


# --- discarding ---------------------------------------------------------------


def test_discard_removes_one_named_record(wal: InMemoryWriteAheadLog) -> None:
    wal.stage(_staged_record(_rid("rec-1")))
    wal.stage(_staged_record(_rid("rec-2")))

    assert wal.discard(_rid("rec-1")) is True
    assert wal.discard(_rid("rec-1")) is False
    assert [item.record_id for item in wal.pending()] == [_rid("rec-2")]


def test_discard_all_empties_the_log(wal: InMemoryWriteAheadLog) -> None:
    wal.stage(_staged_record(_rid("rec-1")))
    wal.stage(_staged_record(_rid("rec-2")))

    assert wal.discard_all() == 2
    assert wal.pending() == ()


# --- the outage path end to end ------------------------------------------------


@pytest.mark.mutation
def test_outage_then_recovery_restores_every_refused_decision(
    store: InMemoryEvidenceStore,
    wal: InMemoryWriteAheadLog,
    clock: FrozenClock,
    ids: DeterministicUuidGenerator,
) -> None:
    """``MUT-13``/``MUT-21`` end to end: refused during the outage, recorded after."""
    writer = EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal)
    writer.write(
        {
            "tenant_id": _TENANT_A,
            "kind": RecordKind.EVALUATION.value,
            "decision_id": _DECISION_ID,
            "action_id": _ACTION_ID,
        }
    )

    store.set_available(False)
    for _ in range(_OUTAGE_RECORDS):
        with pytest.raises(EvidenceUnavailableError):
            writer.write(
        {
            "tenant_id": _TENANT_A,
            "kind": RecordKind.EVALUATION.value,
            "decision_id": _DECISION_ID,
            "action_id": _ACTION_ID,
        }
    )
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


def test_stage_refuses_something_that_is_not_a_record(wal: InMemoryWriteAheadLog) -> None:
    with pytest.raises(MalformedRecordError):
        wal.stage(["tenant-a", "evaluation"])  # type: ignore[arg-type]


def test_discard_all_on_an_empty_log_reports_nothing_dropped(
    wal: InMemoryWriteAheadLog,
) -> None:
    assert wal.discard_all() == 0


def test_replay_treats_a_refused_duplicate_as_a_duplicate(
    store: InMemoryEvidenceStore, wal: InMemoryWriteAheadLog
) -> None:
    """Belt and braces: the store's own refusal is honoured, not turned into a failure.

    A durable store that cannot answer ``has_record`` truthfully (a lagging
    replica, say) must not cause a replay to abort or to double-write; the
    store's unique-identity check is the backstop.
    """

    class _ForgetfulStore(_StoreFailingAfter):
        def has_record(self, tenant_id: str, record_id: str) -> bool:
            return False

    record = _staged_record(_rid("rec-1"))
    store.append(record)
    wal.stage(record)

    report = wal.replay(_ForgetfulStore(store, limit=10))

    assert report.ok
    assert report.duplicates == (_rid("rec-1"),)
    assert report.appended == ()
    assert len(store) == 1
    assert wal.pending() == ()


# --- a staged record is a statement, not a draft ------------------------------


def test_a_staged_record_cannot_be_edited_before_replay(
    wal: InMemoryWriteAheadLog,
) -> None:
    """``frozen=True`` froze the field, not the payload (Constitution Art. IV).

    The staged payload is the decision the store refused to take. Between the
    refusal and the recovery it sits in process memory, and while it was a plain
    ``dict`` anyone holding a ``pending()`` result could rewrite it -- so the
    replay would append, and the chain would hash, something other than what was
    decided. The chain would then be perfectly consistent about a lie, which is
    the one failure a hash chain cannot help with.
    """
    wal.stage(_staged_record(_rid("rec-1")))
    payload = wal.pending()[0].record

    with pytest.raises(TypeError):
        payload["evaluation"]["verdict"] = "ALLOW"
    with pytest.raises(TypeError):
        payload["tenant_id"] = _TENANT_B
    with pytest.raises((TypeError, AttributeError)):
        payload["evaluation"]["reason_codes"].append("forged")

    assert wal.pending()[0].record["evaluation"]["verdict"] == "ABSTAIN"


def test_the_stager_cannot_edit_the_record_through_its_own_reference(
    wal: InMemoryWriteAheadLog,
) -> None:
    """Freezing is also the copy, or the caller keeps a live handle on evidence."""
    submitted = _staged_record(_rid("rec-1"))
    wal.stage(submitted)

    submitted["evaluation"]["verdict"] = "ALLOW"

    assert wal.pending()[0].record["evaluation"]["verdict"] == "ABSTAIN"


# --- record_id is not an identity; (tenant, record_id) is --------------------


def test_two_tenants_may_stage_the_same_record_id(wal: InMemoryWriteAheadLog) -> None:
    """``NFR-15``: tenants are separate chains and may mint the same identifier.

    The store scopes ``record_id`` per tenant. While the log deduplicated on the
    bare id, tenant B's record was treated as a retry of tenant A's and returned
    A's entry -- so B's evidence was dropped, silently, at the exact moment the
    store was already unavailable and the log was the only copy.
    """
    a = wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_A))
    b = wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_B, evaluation={
        "verdict": "DENY", "reason_codes": ["CLASS_HALTED"]
    }))

    assert a is not b
    assert len(wal.pending()) == 2
    assert sorted(item.tenant_id for item in wal.pending()) == [_TENANT_A, _TENANT_B]
    assert {item.record["evaluation"]["verdict"] for item in wal.pending()} == {
        "ABSTAIN",
        "DENY",
    }


def test_restaging_the_same_tenant_and_id_is_still_a_retry(
    wal: InMemoryWriteAheadLog,
) -> None:
    """The deduplication that makes replay idempotent must survive the fix."""
    first = wal.stage(_staged_record(_rid("rec-1")))
    again = wal.stage(_staged_record(_rid("rec-1")))

    assert first is again
    assert len(wal.pending()) == 1


def test_replaying_one_tenants_record_does_not_drop_the_others(
    wal: InMemoryWriteAheadLog, store: InMemoryEvidenceStore
) -> None:
    """Replay bookkeeping keys on the pair too, or the fix stops at ``stage``."""
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_A))
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_B))

    report = wal.replay(store)

    assert len(report.appended) == 2
    assert not wal.pending()
    assert len(store.read(_TENANT_A)) == 1
    assert len(store.read(_TENANT_B)) == 1


@pytest.mark.parametrize("operation", ["quarantine", "discard"])
def test_an_ambiguous_record_id_is_refused_rather_than_guessed(
    wal: InMemoryWriteAheadLog, operation: str
) -> None:
    """Choosing a tenant for the operator would set aside, or delete, the wrong evidence.

    Both operations are deliberate acts on one piece of evidence. With two
    tenants holding the same id, picking the first match would act on whichever
    happened to be staged earlier -- and for ``discard`` that is a deletion the
    operator neither named nor saw.
    """
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_A))
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_B))

    with pytest.raises(AmbiguousRecordError, match="name the tenant"):
        getattr(wal, operation)(_rid("shared"))

    assert len(wal.pending()) == 2, "the refusal must not have changed anything"


@pytest.mark.parametrize("operation", ["quarantine", "discard"])
def test_naming_the_tenant_acts_on_exactly_that_record(
    wal: InMemoryWriteAheadLog, operation: str
) -> None:
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_A))
    wal.stage(_staged_record(_rid("shared"), tenant_id=_TENANT_B))

    assert getattr(wal, operation)(_rid("shared"), tenant_id=_TENANT_B)

    assert [item.tenant_id for item in wal.pending()] == [_TENANT_A]


def test_an_unambiguous_id_still_needs_no_tenant(wal: InMemoryWriteAheadLog) -> None:
    """A single-tenant deployment must not be made to carry the argument."""
    wal.stage(_staged_record(_rid("rec-1")))

    assert wal.quarantine(_rid("rec-1")) is not None
    assert wal.discard(_rid("rec-1"))
    assert not wal.pending() and not wal.quarantined()
