"""The gateway's local write-ahead log for evidence (``FR-23``, ``NFR-12``).

Specification 5.3 closes the resolution procedure with one more step: if the
evaluation record cannot be durably written, the response is overridden to
``ABSTAIN`` (``EVIDENCE_UNAVAILABLE``), no token is issued, and the gateway's
local write-ahead log replays the record when the store recovers.

The log exists for the evidence, not for the decision. Nothing here can rescue
an action: the action was already refused, in every rollout mode (``INV-11``,
``MUT-21``). What would otherwise be lost is the *record that the refusal
happened*, and an unrecorded refusal is as much a hole in the audit trail as an
unrecorded allow (ADR-0006).

Two properties matter and both are enforced here.

**Replay is idempotent.** A crash can land between a successful append and the
removal of the staged copy, so recovery must assume it may be replaying records
the store already holds. Deduplication is on ``record_id``, checked against the
store, so replaying twice appends nothing twice.

**Replay stops at the first failure.** Records are appended in the order they
were staged and the remainder stays pending. Skipping past a failure would
reorder evidence and would hide a store that is still unwell.

Stopping is not the same as wedging, and the difference is what the quarantine
exists for. A failure the store will recover from - it is down, it is slow - is
answered by replaying again later. A failure it will *never* recover from - the
record declares a schema version this build cannot write, or is malformed - would
otherwise block every record staged behind it for the life of the process, until
the log fills and :class:`WriteAheadLogFullError` turns one bad record into a
gateway that abstains on everything. :meth:`InMemoryWriteAheadLog.quarantine`
moves such a record aside, deliberately and by name, so the queue behind it can
drain. Quarantined records are still undurable evidence: they are counted, they
are logged at error level, and they are not discarded by the log itself.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Final, Mapping, Protocol, Sequence, runtime_checkable

from neuroharness.errors import EvidenceUnavailableError, FailClosedError
from neuroharness.evidence.chain import (
    FIELD_KIND,
    FIELD_RECORD_ID,
    FIELD_TENANT_ID,
    MalformedRecordError,
    plain_value,
)
from neuroharness.evidence.store import AppendResult, DuplicateRecordError, EvidenceStore
from neuroharness.observability.logging import get_logger
from neuroharness.reason import ReasonCode
from neuroharness.seams import Clock

__all__ = [
    "DEFAULT_MAX_PENDING_RECORDS",
    "StagedRecord",
    "ReplayReport",
    "WriteAheadLogFullError",
    "WriteAheadLog",
    "InMemoryWriteAheadLog",
]

_LOG = get_logger("neuroharness.evidence.wal")

#: How many records the local log holds before it refuses more.
#:
#: A write-ahead log with no bound trades one failure for a worse one: the
#: gateway runs out of memory or disk while the evidence store is already down.
#: The bound is generous enough to cover a long outage at the throughput the
#: performance budget assumes, and small enough that hitting it is an alert
#: rather than an outage.
DEFAULT_MAX_PENDING_RECORDS: Final[int] = 10_000


class WriteAheadLogFullError(EvidenceUnavailableError):
    """The local log cannot hold another record.

    A subclass of :class:`EvidenceUnavailableError` so that a caller which
    already fails closed on an evidence outage needs no new branch: a full log
    is an evidence outage that has lasted too long.
    """


@dataclass(frozen=True, slots=True)
class StagedRecord:
    """A record waiting to be written, with the moment it was staged."""

    record_id: str
    tenant_id: str
    staged_at: str
    record: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What one replay attempt achieved. Frozen: a report is not editable."""

    attempted: int
    appended: tuple[AppendResult, ...] = ()
    duplicates: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    failed_record_id: str | None = None
    failure_reason_code: ReasonCode | None = None

    @property
    def ok(self) -> bool:
        """True when nothing is left pending and nothing failed."""
        return self.failed_record_id is None and not self.remaining

    @property
    def failed(self) -> bool:
        """True when one record stopped the replay.

        ``failure_reason_code`` says whether waiting will help:
        ``EVIDENCE_UNAVAILABLE`` is a store that may recover, while
        ``SCHEMA_INVALID`` is a record this build will never be able to write and
        therefore a candidate for :meth:`InMemoryWriteAheadLog.quarantine`.
        """
        return self.failed_record_id is not None

    @property
    def appended_record_ids(self) -> tuple[str, ...]:
        return tuple(result.record_id for result in self.appended)


@runtime_checkable
class WriteAheadLog(Protocol):
    """A local, ordered holding area for records the store could not take."""

    def stage(self, record: Mapping[str, Any]) -> StagedRecord:
        """Hold ``record`` until a replay can append it."""
        ...

    def pending(self) -> Sequence[StagedRecord]:
        """Return the staged records, oldest first."""
        ...

    def replay(self, store: EvidenceStore) -> ReplayReport:
        """Append staged records in order, stopping at the first failure.

        Never raises on a record's own failure. A replay that propagated would
        skip its own bookkeeping, so the records it *did* append would stay
        staged and be retried forever; the failure is reported in the returned
        :class:`ReplayReport` instead.
        """
        ...

    def quarantine(self, record_id: str) -> StagedRecord | None:
        """Move one staged record aside so the queue behind it can drain."""
        ...

    def quarantined(self) -> Sequence[StagedRecord]:
        """Return the quarantined records, oldest first."""
        ...

    def discard(self, record_id: str) -> bool:
        """Drop one staged record without appending it. True if it was there."""
        ...


class InMemoryWriteAheadLog:
    """Reference in-memory implementation of :class:`WriteAheadLog`.

    In-memory because the unit of recovery is the gateway process: a durable
    implementation writes the same records to local disk with the same ordering
    and the same ``record_id`` deduplication, and is exercised by the same
    tests through this protocol.
    """

    __slots__ = ("_clock", "_lock", "_pending", "_quarantined", "_max_pending")

    def __init__(
        self,
        *,
        clock: Clock,
        max_pending: int = DEFAULT_MAX_PENDING_RECORDS,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: list[StagedRecord] = []
        self._quarantined: list[StagedRecord] = []
        self._max_pending = max_pending

    def stage(self, record: Mapping[str, Any]) -> StagedRecord:
        """Hold ``record`` for replay.

        A staged record must already carry its ``record_id``, ``tenant_id`` and
        its own timestamp: identity is what makes replay idempotent, and a
        record that were stamped at replay time would claim the decision
        happened when the store recovered.
        """
        if not isinstance(record, Mapping):
            raise MalformedRecordError("a decision record must be a mapping")
        record_id = record.get(FIELD_RECORD_ID)
        tenant_id = record.get(FIELD_TENANT_ID)
        if not isinstance(record_id, str) or not record_id:
            raise MalformedRecordError(
                "cannot stage a record without a record_id; replay would duplicate it"
            )
        if not isinstance(tenant_id, str) or not tenant_id:
            raise MalformedRecordError("cannot stage a record without a tenant_id")

        staged = StagedRecord(
            record_id=record_id,
            tenant_id=tenant_id,
            staged_at=self._clock.now().isoformat(),
            record=plain_value(record),
        )
        with self._lock:
            already = next(
                (
                    item
                    for item in (*self._pending, *self._quarantined)
                    if item.record_id == record_id
                ),
                None,
            )
            if already is not None:
                # Staging the same record twice is a retry, not a second record.
                # Quarantined records are included: re-staging one would put the
                # same un-appendable record back at the head of the queue and
                # wedge the log again.
                return already
            # Quarantined records count against the bound. They are evidence
            # that is still not durable, so setting one aside unblocks the
            # replay queue without pretending the backlog shrank.
            held = len(self._pending) + len(self._quarantined)
            if held >= self._max_pending:
                _LOG.error(
                    "evidence.wal.full",
                    tenant_id=tenant_id,
                    record_id=record_id,
                    pending=len(self._pending),
                    quarantined=len(self._quarantined),
                    max_pending=self._max_pending,
                )
                raise WriteAheadLogFullError(
                    f"write-ahead log is full at {self._max_pending} records; "
                    f"record {record_id} was not staged"
                )
            self._pending.append(staged)
            depth = len(self._pending)
        _LOG.debug(
            "evidence.wal.stage",
            tenant_id=tenant_id,
            record_id=record_id,
            kind=record.get(FIELD_KIND),
            pending=depth,
        )
        return staged

    def pending(self) -> Sequence[StagedRecord]:
        with self._lock:
            return tuple(self._pending)

    def replay(self, store: EvidenceStore) -> ReplayReport:
        """Append everything staged, in order, stopping at the first failure.

        Every fail-closed error a record can provoke is caught, not just an
        evidence outage. ``store.append`` also refuses a record whose schema
        version this build cannot write (:class:`SchemaVersionError`) or whose
        shape it cannot chain (:class:`MalformedRecordError`), and a propagating
        replay would skip the bookkeeping below: the records it had already
        appended would stay staged, every later replay would re-attempt them,
        nothing would ever drain, and the log would fill until
        :class:`WriteAheadLogFullError` made the gateway abstain on everything
        (Constitution Art. II - an inability to evaluate must stop *execution*,
        not the evidence path).

        The failure is reported in the returned :class:`ReplayReport` instead;
        ``failure_reason_code`` tells the operator whether to wait for the store
        or to :meth:`quarantine` the record.
        """
        with self._lock:
            queue = list(self._pending)

        appended: list[AppendResult] = []
        duplicates: list[str] = []
        failed_record_id: str | None = None
        failure_reason_code: ReasonCode | None = None
        written: set[str] = set()

        for staged in queue:
            try:
                if store.has_record(staged.tenant_id, staged.record_id):
                    # Already durable: a crash between the append and the discard.
                    duplicates.append(staged.record_id)
                    written.add(staged.record_id)
                    continue
                appended.append(store.append(staged.record))
            except DuplicateRecordError:
                duplicates.append(staged.record_id)
                written.add(staged.record_id)
                continue
            except FailClosedError as exc:
                failed_record_id = staged.record_id
                failure_reason_code = exc.reason_code
                _LOG.error(
                    "evidence.wal.replay.failed",
                    tenant_id=staged.tenant_id,
                    record_id=staged.record_id,
                    reason_code=exc.reason_code.render(),
                    appended=len(appended),
                )
                break
            written.add(staged.record_id)

        with self._lock:
            self._pending = [item for item in self._pending if item.record_id not in written]
            remaining = tuple(item.record_id for item in self._pending)

        report = ReplayReport(
            attempted=len(queue),
            appended=tuple(appended),
            duplicates=tuple(duplicates),
            remaining=remaining,
            failed_record_id=failed_record_id,
            failure_reason_code=failure_reason_code,
        )
        _LOG.debug(
            "evidence.wal.replay",
            attempted=report.attempted,
            appended=len(report.appended),
            duplicates=len(report.duplicates),
            remaining=len(report.remaining),
            ok=report.ok,
        )
        return report

    def quarantine(self, record_id: str) -> StagedRecord | None:
        """Move one staged record out of the replay queue. ``None`` if absent.

        This is the release valve for a record the store will never accept: a
        schema version this build cannot write, or a shape it cannot chain. Such
        a record stops every replay at the same point forever, so without a way
        to set it aside one un-appendable record costs the audit trail every
        record staged behind it, and then - once the log fills - every decision
        the gateway would otherwise have made.

        It is deliberately *not* automatic and deliberately not a delete. The
        caller names the record, the move is logged at error level, and the
        record stays readable through :meth:`quarantined` and countable against
        ``max_pending``, because it is still evidence that never became durable
        (ADR-0006). Discarding it is a separate, equally named decision.
        """
        wanted = str(record_id)
        with self._lock:
            staged = next((item for item in self._pending if item.record_id == wanted), None)
            if staged is None:
                return None
            self._pending = [item for item in self._pending if item.record_id != wanted]
            self._quarantined.append(staged)
            held = len(self._quarantined)
        _LOG.error(
            "evidence.wal.quarantine",
            tenant_id=staged.tenant_id,
            record_id=staged.record_id,
            staged_at=staged.staged_at,
            quarantined=held,
        )
        return staged

    def quarantined(self) -> Sequence[StagedRecord]:
        """Return the quarantined records, oldest first."""
        with self._lock:
            return tuple(self._quarantined)

    def discard(self, record_id: str) -> bool:
        """Drop one staged or quarantined record without appending it.

        Used when a record is known to be durable by another route, or when an
        operator has accepted the loss of a quarantined one. It is not a way to
        make an inconvenient record go away: the caller has to name it, and the
        drop is logged.
        """
        wanted = str(record_id)
        with self._lock:
            pending = [item for item in self._pending if item.record_id != wanted]
            quarantined = [item for item in self._quarantined if item.record_id != wanted]
            found = len(pending) != len(self._pending) or len(quarantined) != len(
                self._quarantined
            )
            self._pending = pending
            self._quarantined = quarantined
        if found:
            _LOG.debug("evidence.wal.discard", record_id=wanted)
        return found

    def discard_all(self) -> int:
        """Drop every staged and quarantined record and return how many."""
        with self._lock:
            dropped = len(self._pending) + len(self._quarantined)
            self._pending = []
            self._quarantined = []
        if dropped:
            _LOG.debug("evidence.wal.discard_all", dropped=dropped)
        return dropped

    def __len__(self) -> int:
        """How many records are still waiting to become durable.

        Quarantined records are included: they are held by this log and have not
        reached the store, and a length that hid them would let a monitoring
        dashboard report an empty backlog while evidence was missing.
        """
        with self._lock:
            return len(self._pending) + len(self._quarantined)
