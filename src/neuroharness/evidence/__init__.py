"""Evidence subsystem: hash-chained, append-only decision records.

Enforcement produces evidence as a byproduct (ADR-0006, Constitution Article
III). This package holds the three pieces that make that true:

* :mod:`neuroharness.evidence.chain` - the hash chain that binds each record to
  its predecessor and to its tenant, plus signed checkpoints (``SEC-10``).
* :mod:`neuroharness.evidence.store` - the append-only store and the
  :class:`EvidenceWriter` that refuses to let a caller continue past a failed
  write (``INV-05``).
* :mod:`neuroharness.evidence.wal` - the gateway's local write-ahead log, which
  keeps the record of a refusal that the store could not take (``FR-23``).
"""

from neuroharness.evidence.chain import (
    CHECKPOINT_SIGNING_DOMAIN,
    GENESIS_SEQ,
    SCHEMA_VERSION,
    ChainBreak,
    ChainVerification,
    MalformedRecordError,
    RecordKind,
    Signer,
    checkpoint_signing_bytes,
    compute_record_hash,
    create_checkpoint,
    verify_chain,
    verify_checkpoint,
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
from neuroharness.evidence.wal import (
    DEFAULT_MAX_PENDING_RECORDS,
    InMemoryWriteAheadLog,
    ReplayReport,
    StagedRecord,
    WriteAheadLog,
    WriteAheadLogFullError,
)

__all__ = [
    # chain
    "CHECKPOINT_SIGNING_DOMAIN",
    "GENESIS_SEQ",
    "SCHEMA_VERSION",
    "ChainBreak",
    "ChainVerification",
    "MalformedRecordError",
    "RecordKind",
    "Signer",
    "checkpoint_signing_bytes",
    "compute_record_hash",
    "create_checkpoint",
    "verify_chain",
    "verify_checkpoint",
    # store
    "AppendOnlyViolationError",
    "AppendResult",
    "DuplicateRecordError",
    "EvidenceStore",
    "EvidenceWriter",
    "InMemoryEvidenceStore",
    "to_jsonl",
    # write-ahead log
    "DEFAULT_MAX_PENDING_RECORDS",
    "InMemoryWriteAheadLog",
    "ReplayReport",
    "StagedRecord",
    "WriteAheadLog",
    "WriteAheadLogFullError",
]
