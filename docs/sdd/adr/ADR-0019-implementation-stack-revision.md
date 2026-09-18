# ADR-0019: Implementation stack revision: key algorithm, deterministic solver bounding, Prolog deferral

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, SRE · **Supersedes:** ADR-0009 · **Origin:** round-two findings `R2-D5`, `R2-D16`, `R2-D17`

## Context
Three stack choices in ADR-0009 did not survive review. (1) It specified Ed25519 token signing "via KMS", but the managed key services most target environments use do not expose Ed25519 signing; teams would silently fall back to a shared secret, which puts the same key in the token service and the broker and dissolves the separation the token design depends on. (2) It bounded the solver by wall-clock time, which makes `unknown` load-dependent: replay then diffs under continuous-integration contention, and a routine solver upgrade fails the replay gate with no permitted override. (3) It included a Prolog critic that by design cannot change a verdict, at the cost of a container, a sandbox, a signed rulebase and a language nobody on the team is listed as knowing.

## Decision
1. **Token signing: ECDSA P-256 by default**, Ed25519 where the key service supports it, HMAC-SHA256 only for single-process deployments. The algorithm is recorded in the token (`key_alg`) and in the record. Key provisioning moves to Phase 0 (`P0-13`), because it gates the broker.
2. **Solver bounding: Z3's deterministic `rlimit` is the primary bound**, with a wall-clock backstop that maps to `TIMEOUT` rather than `UNKNOWN`. The rlimit consumed is recorded and the solver version is part of the critic version, so replay is reproducible and a version bump is a visible, procedural event (`P2-08`) rather than a mysterious red gate.
3. **Prolog is deferred** unless a named engineer owns it (`P2-04`). If it ships, it is invoked by the gateway only: there is no agent-facing query interface, because an agent-queryable prover is a policy-probing channel outside the repair budget.
4. Everything else in ADR-0009 (Python, OPA with signed bundles, PostgreSQL evidence store, MCP gateway, OpenTelemetry, the test tooling) stands.

## Alternatives considered
- **Keep Ed25519 and run a custom signer.** Rejected for v1: a bespoke signing service is more trusted-computing-base surface than the algorithm is worth.
- **Keep wall-clock bounding and exclude near-timeout decisions from replay.** Rejected: "exclude the hard cases from the determinism check" defeats the check.
- **Ship Prolog as planned for explanation ergonomics.** Rejected on cost: the same explanations come from typed counterexamples, which are already required.

## Consequences
- Positive: works on the key services teams actually have; replay survives load and dependency updates; one fewer language and container in the supply chain.
- Negative: ECDSA signatures are larger and slower to verify than Ed25519 (immaterial at the token rate involved); rlimit tuning is less intuitive than milliseconds and needs a documented calibration.
- Follow-ups: `FR-20`, `FR-51`, `NFR-07`, `NFR-17`, `OQ-03`, `P0-13`, `P2-08`, risk `R-13`.
