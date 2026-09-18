# ADR-0009: Reference implementation stack

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead · **Origin:** technical plan §6

## Context
The design is language-agnostic (JSON Schemas, Rego, MCP), but a reference implementation must choose concrete components with mature bindings for SMT, MCP and evaluation tooling, and an operational story enterprises accept.

## Decision
- **Language/runtime:** Python 3.12+, `mypy --strict`, Pydantic v2 (models export JSON Schema 2020-12), `uv` with lockfile.
- **Policy engine:** OPA with Rego v1 syntax, run as a sidecar with **signed bundles** verified on load; an in-process WASM evaluation of the same bundle is an accepted alternative for library deployments, with one conformance suite for both.
- **SMT:** Z3 via `z3-solver`; QF_LIA/QF_BV; per-call timeout, fixed seed, worker resource limits.
- **Prolog (prototype, soft):** SWI-Prolog in a sandboxed container; harness-owned signed rulebase; query-only MCP wrapper.
- **Temporal monitor:** bounded LTLf subset compiled to DFAs at bundle-build time; state in PostgreSQL (v1).
- **Evidence store:** PostgreSQL append-only table with row hash chain; signed checkpoints to object storage.
- **Tokens:** Ed25519 (or HMAC-SHA256 single-deployment) with keys in KMS; nonce table in PostgreSQL.
- **Interception:** Python MCP SDK gateway; hook adapter library.
- **Observability:** OpenTelemetry; Prometheus metrics; structured JSON logs.
- **Testing:** pytest, hypothesis, pytest-bdd, testcontainers, `opa test`, Regal, mutmut, custom mutation-fixture runner, k6/locust.
- **Supply chain:** distroless images, CycloneDX SBOM, provenance attestations, cosign for images and bundles.

## Alternatives considered
- **TypeScript/Node.** Viable (MCP SDK is first-class); weaker SMT bindings and evaluation tooling. May be chosen for the hook adapter where the governed framework is TypeScript.
- **Go.** Strong OPA integration (in-process), weaker SMT/evaluation ecosystem; reconsider if the sidecar hop proves costly.
- **Embedded Rego engine (Rust-based) instead of OPA.** Kept as an option under the same conformance suite; not the default because OPA's bundle signing, tooling and audit familiarity are decisive for enterprise adoption.
- **Redis for nonces/monitor state.** Deferred; PostgreSQL suffices at v1 scale and reduces the operational footprint.

## Consequences
- Positive: mature tooling for every TCB component; one language for the harness core; simple deployment.
- Negative: Python performance ceiling (mitigated by stateless horizontal scaling, `NFR-06`); sidecar hop adds ~ms latency; Prolog adds a container.
