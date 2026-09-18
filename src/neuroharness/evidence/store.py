"""The evidence store: append-only, per-tenant hash-chained decision records.

``FR-70``-``FR-74``, ``INV-05``, ``NFR-05``, ``NFR-12``, ``NFR-16``, ADR-0006.

Three properties are enforced here in code rather than left to convention.

**Append-only.** The reference deployment rejects updates and deletes with
database triggers (technical plan 4.11); an in-process store has no such
backstop, so this one exposes no mutating operation at all, hands out records as
read-only views, and keeps :meth:`InMemoryEvidenceStore.update` and
:meth:`InMemoryEvidenceStore.delete` only so that calling them raises
:class:`AppendOnlyViolationError` instead of quietly doing nothing. A convention
that evidence "should not" be edited is not a control; a method that raises is.

**A record is copied on the way in.** ``append`` stores a frozen deep copy, so a
caller that keeps a reference to the mapping it submitted cannot retroactively
change what was recorded. Without that, evidence would be a live view of the
caller's memory rather than a statement about a past decision.

**Unavailability fails closed.** :meth:`set_available` simulates the outage that
``MUT-13`` and ``MUT-21`` exercise. An append during an outage raises
:class:`EvidenceUnavailableError`, and no token may be issued for a decision
whose record does not exist (``INV-05``, ``FR-23``). The gateway's response is
overridden to ``ABSTAIN`` and the record is staged in the write-ahead log for
replay on recovery (specification 5.3); :class:`EvidenceWriter` is the piece
that makes that discipline hard to skip.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Protocol, Sequence, runtime_checkable

from neuroharness.errors import EvidenceUnavailableError, FailClosedError
from neuroharness.evidence.chain import (
    FIELD_KIND,
    FIELD_PREV_RECORD_HASH,
    FIELD_RECORD_HASH,
    FIELD_RECORD_ID,
    FIELD_SCHEMA_VERSION,
    FIELD_SEQ,
    FIELD_TENANT_ID,
    FIELD_TIMESTAMP,
    GENESIS_SEQ,
    MAX_TENANT_ID_LENGTH,
    RESERVED_CHAIN_FIELDS,
    ChainVerification,
    MalformedRecordError,
    RecordKind,
    Signer,
    compute_record_hash,
    create_checkpoint,
    plain_value,
    verify_chain,
)
from neuroharness.models.common import Digest
from neuroharness.observability.logging import get_logger
from neuroharness.reason import ReasonName
from neuroharness.seams import Clock, IdGenerator
from neuroharness.version import SchemaCompatibility, SchemaKind

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps store independent of wal
    from neuroharness.evidence.wal import ReplayReport, WriteAheadLog

__all__ = [
    "AppendResult",
    "AppendOnlyViolationError",
    "DuplicateRecordError",
    "EvidenceStore",
    "InMemoryEvidenceStore",
    "EvidenceWriter",
    "to_jsonl",
]

_LOG = get_logger("neuroharness.evidence.store")

#: Export format for auditors (``FR-73``, technical plan 4.11): one canonical
#: JSON object per line, in chain order, alongside the signed checkpoint.
_JSONL_SEPARATORS = (",", ":")


class AppendOnlyViolationError(FailClosedError):
    """Something tried to change or remove a record that was already written.

    Raised rather than ignored: evidence that can be edited is not evidence, and
    an attempt to edit it is a security event worth surfacing (ADR-0006).
    """

    reason_name = ReasonName.HARNESS_UNHEALTHY


class DuplicateRecordError(FailClosedError):
    """A record with this ``record_id`` is already in this tenant's chain.

    Refused rather than appended twice: ``record_id`` is what makes write-ahead
    replay idempotent, so two records sharing one would let a replay silently
    double-count evidence.
    """

    reason_name = ReasonName.SCHEMA_INVALID


@dataclass(frozen=True, slots=True)
class AppendResult:
    """What the store assigned to an appended record."""

    tenant_id: str
    record_id: str
    seq: int
    record_hash: Digest
    prev_record_hash: Digest | None
    record: Mapping[str, Any]


@runtime_checkable
class EvidenceStore(Protocol):
    """The durable home of decision records.

    Deliberately narrow: append, read, head, export, and the identity lookup
    that makes replay idempotent. There is no update and no delete, so no
    implementation of this protocol can offer one by accident.
    """

    def append(self, record: Mapping[str, Any]) -> AppendResult:
        """Append ``record`` to its tenant's chain and return its position.

        Raises :class:`EvidenceUnavailableError` when the record cannot be made
        durable. Callers must treat that as "the decision was not made".
        """
        ...

    def read(
        self,
        tenant_id: str,
        *,
        start_seq: int = GENESIS_SEQ,
        limit: int | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return this tenant's records from ``start_seq``, in chain order."""
        ...

    def latest(self, tenant_id: str) -> Mapping[str, Any] | None:
        """Return the head of this tenant's chain, or ``None`` if it is empty."""
        ...

    def export(
        self,
        tenant_id: str,
        *,
        start_seq: int = GENESIS_SEQ,
        limit: int | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        """Stream this tenant's records for audit (``FR-73``)."""
        ...

    def has_record(self, tenant_id: str, record_id: str) -> bool:
        """True when this ``record_id`` is already in this tenant's chain."""
        ...


class InMemoryEvidenceStore:
    """Reference in-memory implementation of :class:`EvidenceStore`.

    Used by unit tests, the replay harness and the mutation fixtures. The
    PostgreSQL implementation has the same contract; anything this class can do
    that the database cannot (or the other way round) is a bug in one of them.

    Thread safety: one lock serialises every operation. Per-tenant chaining
    requires that the read of the head and the write of the successor are one
    atomic step, otherwise two concurrent appends can claim the same ``seq``.
    The production design reaches the same guarantee with a single writer per
    tenant (technical plan 4.11).
    """

    __slots__ = ("_clock", "_ids", "_lock", "_records", "_ids_seen", "_available")

    def __init__(
        self,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        available: bool = True,
    ) -> None:
        self._clock = clock
        self._ids = id_generator
        self._lock = threading.RLock()
        self._records: dict[str, list[Mapping[str, Any]]] = {}
        self._ids_seen: dict[str, set[str]] = {}
        self._available = available

    # --- availability --------------------------------------------------------

    def set_available(self, available: bool) -> None:
        """Simulate an evidence-store outage and recovery (``MUT-13``, ``MUT-21``).

        This is a test seam, not an operational switch: it exists so the
        fail-closed path can be exercised deterministically rather than by
        unplugging a database.
        """
        with self._lock:
            self._available = available

    @property
    def available(self) -> bool:
        with self._lock:
            return self._available

    # --- writes --------------------------------------------------------------

    def append(self, record: Mapping[str, Any]) -> AppendResult:
        """Append ``record`` to its tenant's chain.

        The store - never the caller - assigns ``seq``, ``prev_record_hash`` and
        ``record_hash``. A caller that could choose its own chain position could
        choose to be unlinked, which is the one thing the chain must deny.
        """
        prepared = prepare_record(record, clock=self._clock, id_generator=self._ids)
        tenant_id = str(prepared[FIELD_TENANT_ID])
        record_id = str(prepared[FIELD_RECORD_ID])

        with self._lock:
            if not self._available:
                _LOG.error(
                    "evidence.append.unavailable",
                    tenant_id=tenant_id,
                    record_id=record_id,
                    kind=prepared.get(FIELD_KIND),
                )
                raise EvidenceUnavailableError(
                    f"evidence store is unavailable; record {record_id} was not written"
                )

            chain = self._records.setdefault(tenant_id, [])
            seen = self._ids_seen.setdefault(tenant_id, set())
            if record_id in seen:
                _LOG.error(
                    "evidence.append.duplicate", tenant_id=tenant_id, record_id=record_id
                )
                raise DuplicateRecordError(
                    f"record {record_id} is already in tenant {tenant_id}'s chain"
                )

            head = chain[-1] if chain else None
            prev_hash = Digest(str(head[FIELD_RECORD_HASH])) if head is not None else None
            seq = GENESIS_SEQ if head is None else int(head[FIELD_SEQ]) + 1

            prepared[FIELD_SEQ] = seq
            prepared[FIELD_PREV_RECORD_HASH] = str(prev_hash) if prev_hash is not None else None
            record_hash = compute_record_hash(prepared, prev_hash=prev_hash)
            prepared[FIELD_RECORD_HASH] = str(record_hash)

            stored = _freeze(prepared)
            chain.append(stored)
            seen.add(record_id)

        _LOG.debug(
            "evidence.append",
            tenant_id=tenant_id,
            record_id=record_id,
            kind=stored.get(FIELD_KIND),
            seq=seq,
            record_hash=str(record_hash),
        )
        return AppendResult(
            tenant_id=tenant_id,
            record_id=record_id,
            seq=seq,
            record_hash=record_hash,
            prev_record_hash=prev_hash,
            record=stored,
        )

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Always raises. Present so that trying to edit evidence is an error."""
        raise AppendOnlyViolationError("decision records are append-only; updates are refused")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        """Always raises. Retention is enforced by policy, never by deletion here."""
        raise AppendOnlyViolationError("decision records are append-only; deletes are refused")

    # --- reads ---------------------------------------------------------------

    def read(
        self,
        tenant_id: str,
        *,
        start_seq: int = GENESIS_SEQ,
        limit: int | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")
        if start_seq < GENESIS_SEQ:
            raise ValueError(f"start_seq must be at least {GENESIS_SEQ}")
        with self._lock:
            chain = self._records.get(str(tenant_id), ())
            selected = [record for record in chain if int(record[FIELD_SEQ]) >= start_seq]
        return tuple(selected if limit is None else selected[:limit])

    def latest(self, tenant_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            chain = self._records.get(str(tenant_id), ())
            return chain[-1] if chain else None

    def export(
        self,
        tenant_id: str,
        *,
        start_seq: int = GENESIS_SEQ,
        limit: int | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        """Stream records for audit (``FR-73``).

        The snapshot is taken under the lock and then yielded, so an export
        cannot be torn by concurrent appends and cannot hold the lock for as
        long as an auditor takes to consume it.
        """
        return iter(self.read(tenant_id, start_seq=start_seq, limit=limit))

    def has_record(self, tenant_id: str, record_id: str) -> bool:
        with self._lock:
            return str(record_id) in self._ids_seen.get(str(tenant_id), frozenset())

    def tenants(self) -> tuple[str, ...]:
        """Tenants with at least one record, sorted for deterministic reporting."""
        with self._lock:
            return tuple(sorted(self._records))

    def __len__(self) -> int:
        with self._lock:
            return sum(len(chain) for chain in self._records.values())

    # --- integrity -----------------------------------------------------------

    def verify(self, tenant_id: str, *, start_seq: int = GENESIS_SEQ) -> ChainVerification:
        """Verify this tenant's chain as stored (``SEC-10``)."""
        return verify_chain(
            self.read(tenant_id, start_seq=start_seq), expected_tenant_id=str(tenant_id)
        )

    def checkpoint(
        self,
        tenant_id: str,
        *,
        key_id: str,
        signer: Signer,
        anchor_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """Sign the current head of this tenant's chain (``SEC-10``).

        Returns ``None`` for an empty chain: there is nothing to attest to, and
        a checkpoint over nothing would be a claim that no decisions were made.
        Appending the result as a ``checkpoint`` record is the caller's choice,
        because doing so moves the head the checkpoint just attested to.
        """
        head = self.latest(tenant_id)
        if head is None:
            return None
        return create_checkpoint(
            tenant_id=str(tenant_id),
            last_seq=int(head[FIELD_SEQ]),
            last_record_hash=Digest(str(head[FIELD_RECORD_HASH])),
            key_id=key_id,
            signer=signer,
            anchor_ref=anchor_ref,
        )


class EvidenceWriter:
    """Append with the write-ahead discipline of specification 5.3.

    The rule it encapsulates: try to append; if the record cannot be made
    durable, stage it in the local write-ahead log **and re-raise**. Re-raising
    is the point. A helper that swallowed the failure would let a caller carry
    on to issue a token for a decision with no record, which is exactly what
    ``INV-05`` forbids; the exception is what forces the ``ABSTAIN``
    (``EVIDENCE_UNAVAILABLE``) at the gateway.

    Identity and time are stamped *before* the attempt, so a record replayed
    after recovery carries the moment the decision was made rather than the
    moment the store came back.
    """

    __slots__ = ("_store", "_wal", "_clock", "_ids")

    def __init__(
        self,
        store: EvidenceStore,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        wal: WriteAheadLog | None = None,
    ) -> None:
        self._store = store
        self._wal = wal
        self._clock = clock
        self._ids = id_generator

    @property
    def store(self) -> EvidenceStore:
        return self._store

    @property
    def wal(self) -> WriteAheadLog | None:
        return self._wal

    def write(self, record: Mapping[str, Any]) -> AppendResult:
        """Append ``record``, staging it for replay if the store is unavailable."""
        prepared = prepare_record(record, clock=self._clock, id_generator=self._ids)
        tenant_id = str(prepared[FIELD_TENANT_ID])
        record_id = str(prepared[FIELD_RECORD_ID])
        try:
            result = self._store.append(prepared)
        except EvidenceUnavailableError as exc:
            staged = self._wal is not None
            if self._wal is not None:
                self._wal.stage(prepared)
            _LOG.error(
                "evidence.write.failed",
                tenant_id=tenant_id,
                record_id=record_id,
                kind=prepared.get(FIELD_KIND),
                staged=staged,
                reason_code=exc.reason_code.render(),
            )
            # Re-raised on purpose: no token without a durable record.
            raise
        _LOG.debug(
            "evidence.write",
            tenant_id=tenant_id,
            record_id=record_id,
            kind=prepared.get(FIELD_KIND),
            seq=result.seq,
        )
        return result

    def recover(self) -> ReplayReport | None:
        """Replay whatever the outage left staged. ``None`` when there is no WAL."""
        if self._wal is None:
            return None
        return self._wal.replay(self._store)


def prepare_record(
    record: Mapping[str, Any],
    *,
    clock: Clock,
    id_generator: IdGenerator,
) -> dict[str, Any]:
    """Validate a submitted record and fill in its identity and timestamp.

    Returns a plain mutable copy: the store still owns the chain fields, and
    this function refuses a record that tries to bring its own.
    """
    if not isinstance(record, Mapping):
        raise MalformedRecordError("a decision record must be a mapping")

    present_reserved = [field for field in RESERVED_CHAIN_FIELDS if field in record]
    if present_reserved:
        raise MalformedRecordError(
            "the store assigns chain position; record must not carry "
            + ", ".join(present_reserved)
        )

    prepared = {str(key): plain_value(value) for key, value in record.items()}

    tenant_id = prepared.get(FIELD_TENANT_ID)
    if not isinstance(tenant_id, str) or not tenant_id:
        raise MalformedRecordError(f"tenant_id is not an identifier: {tenant_id!r}")
    if len(tenant_id) > MAX_TENANT_ID_LENGTH:
        raise MalformedRecordError(
            f"tenant_id exceeds {MAX_TENANT_ID_LENGTH} characters"
        )

    kind = prepared.get(FIELD_KIND)
    try:
        RecordKind(kind)
    except ValueError as exc:
        raise MalformedRecordError(f"unknown decision-record kind: {kind!r}") from exc

    # The version a record is *stamped* with and the versions the store will
    # *accept* are different questions, and asking them of the same constant is
    # how the two drift. Stamping uses the one version this build writes;
    # acceptance is delegated to the compatibility declaration, so widening the
    # readable set stays the backwards-compatible change it claims to be and the
    # store no longer refuses records DecisionRecord happily validates (``F4``).
    declared_version = prepared.setdefault(
        FIELD_SCHEMA_VERSION, SchemaCompatibility.written_version(SchemaKind.RECORD)
    )
    SchemaCompatibility.assert_readable(SchemaKind.RECORD, str(declared_version))

    record_id = prepared.get(FIELD_RECORD_ID)
    if record_id is None:
        prepared[FIELD_RECORD_ID] = id_generator.new_id()
    elif not isinstance(record_id, str) or not record_id:
        raise MalformedRecordError(f"record_id is not an identifier: {record_id!r}")

    timestamp = prepared.get(FIELD_TIMESTAMP)
    if timestamp is None:
        # Through the Clock seam so that replay can drive time (``FR-71``).
        prepared[FIELD_TIMESTAMP] = clock.now().isoformat()
    else:
        _assert_replayable_timestamp(timestamp)

    return prepared


def _assert_replayable_timestamp(timestamp: Any) -> None:
    """Refuse a ``timestamp`` that cannot be read back as an instant.

    ``FR-71`` replays a recorded decision "using the record's timestamp as now",
    so this string is not a label: it is an input to a later evaluation, and it
    is hash-chained the moment it is accepted. A truthy string was the only bar,
    which admitted ``"yesterday"`` - unreplayable - and, worse, a naive local
    timestamp, which replays *successfully* against whatever offset the replaying
    host happens to have. Evidence is append-only (``ADR-0006``), so either one
    becomes permanent.

    An offset is therefore required, not merely a parseable shape. Raises
    :class:`MalformedRecordError` rather than coercing: a record whose instant
    the harness had to guess is not evidence of when anything happened.
    """
    if not isinstance(timestamp, str) or not timestamp:
        raise MalformedRecordError(f"timestamp is not a timestamp: {timestamp!r}")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise MalformedRecordError(
            f"timestamp is not an ISO-8601 instant: {timestamp!r}; "
            "FR-71 replays a decision using this value as 'now'"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedRecordError(
            f"timestamp {timestamp!r} carries no UTC offset; a naive instant "
            "replays against the replaying host's zone, not the deciding host's "
            "(FR-71, NFR-13)"
        )


def to_jsonl(records: Sequence[Mapping[str, Any]] | Iterator[Mapping[str, Any]]) -> Iterator[str]:
    """Render records as the documented JSONL export format (``FR-73``).

    Keys are sorted so that an export is byte-stable and two exports of the same
    records can be diffed.
    """
    for record in records:
        yield json.dumps(plain_value(record), sort_keys=True, separators=_JSONL_SEPARATORS)


def _freeze(value: Any) -> Any:
    """Return a deeply read-only copy of ``value``.

    Freezing is also the copy: every container is rebuilt, so the stored record
    shares no mutable structure with the mapping the caller submitted.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value
