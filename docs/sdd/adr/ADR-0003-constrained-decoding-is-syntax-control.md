# ADR-0003: Constrained decoding is syntax control, outside the harness

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead · **Supersedes:** research ADR-003

## Context
Grammar- or schema-constrained decoding guarantees output shape, not semantics, authorization or state validity. Some evidence suggests format instructions can degrade reasoning quality; the recommended pattern is to reason freely and constrain only at serialization.

## Decision
The harness does not perform or depend on constrained decoding. It validates the serialized proposal against the tool argument schema and the envelope schema at its boundary (`FR-02`) and rejects non-conforming input with `SCHEMA_INVALID`. How the governed runtime produces well-formed JSON is its own concern. Schema validity is never presented as a safety or correctness property (Constitution Art. X; spec §3.3).

## Alternatives considered
- **Harness-provided decoding grammars.** Rejected: couples the harness to model-serving infrastructure; adds nothing to enforcement.
- **Skip schema validation and rely on policy.** Rejected: malformed input must fail before evaluation to keep the PDP input deterministic.

## Consequences
- Positive: clean boundary; no coupling to inference stacks.
- Negative: agents with poor structured-output discipline will see more `SCHEMA_INVALID` rejections (visible in metrics).
