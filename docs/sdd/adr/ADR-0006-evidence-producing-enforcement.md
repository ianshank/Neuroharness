# ADR-0006: Enforcement writes hash-chained evidence before any token is issued

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** research ADR-006

## Context
Enforcement without replayable evidence cannot be governed, debugged, or defended to auditors. Regulatory regimes increasingly require traceability and human-oversight records. The research proposed an audit record but did not bind it to execution or protect its integrity.

## Decision
Every evaluation produces a decision record (schema in `schemas/decision-record.schema.json`) that is persisted durably **before** a decision token is issued (`INV-05`). Records are append-only, per-tenant hash-chained (`prev_record_hash`), and periodically checkpointed with a signature. Approvals, execution receipts and human overrides are appended as linked records. Replay reproduces verdicts from records and referenced versions (`INV-09`). Personal data is minimized and erasure is supported by crypto-shredding without breaking the chain.

## Alternatives considered
- **Best-effort asynchronous logging.** Rejected: an unrecorded `ALLOW` is possible; violates Article III.
- **Mutable audit tables.** Rejected: no tamper evidence.
- **External ledger / blockchain.** Rejected: operational cost without added assurance beyond signed checkpoints.

## Consequences
- Positive: audit-grade evidence as a byproduct; replay enables regression testing of policy changes.
- Negative: evidence-store availability is now on the critical path (fail-closed by design; `NFR-12`); storage growth requires retention policy (`NFR-16`).
