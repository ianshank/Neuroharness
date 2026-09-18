# Neuroharness

**A fail-closed policy-and-verification harness for LLM agent workflows.**

Neuroharness sits between an agent and the tools it wants to call. The agent proposes an action; the harness evaluates it against deterministic policy and a small bank of bounded symbolic critics; the harness (never the model) decides `ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN`, or asks for a `REPAIR`; and every decision leaves a replayable, tamper-evident evidence record.

> The cognitive plane proposes; the harness disposes.

## Status

**Phase: Specification (Spec-Driven Development).** No runtime code exists yet. This repository currently holds the research input, an independent peer review of that input, and the full SDD package (constitution, specification, technical plan, work breakdown, threat model, evaluation plan, delivery/governance model, ADRs, and JSON Schemas) that implementation will be driven from.

## Document map

| Path | What it is | Read it when |
|---|---|---|
| `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` | The research synthesis that motivated the project (kept verbatim for provenance). | You want the evidence base and the model-perspective analysis. |
| `docs/review/2026-09-18-peer-review-research-synthesis.md` | Independent peer review of the synthesis: verdict, major/minor issues, citation audit, requested changes. | You want to know what the research got right, what it left out, and why the spec differs from it. |
| `docs/sdd/README.md` | How Spec-Driven Development works in this repo and the status of each SDD document. | You are about to change anything under `docs/sdd/`. |
| `docs/sdd/00-constitution.md` | Non-negotiable principles every design and code change must satisfy. | Always. Start here. |
| `docs/sdd/01-specification.md` | Functional/non-functional requirements, invariant registry, verdict semantics, acceptance scenarios. | You are implementing or testing a requirement. |
| `docs/sdd/02-technical-plan.md` | Architecture, components, interfaces, data model, technology decisions, testing strategy. | You are designing or building a component. |
| `docs/sdd/03-work-breakdown.md` | Phased task list with dependencies, acceptance criteria and phase exit gates. | You are planning or picking up work. |
| `docs/sdd/04-threat-model.md` | Assets, adversaries, STRIDE analysis, controls and residual risk. | You are touching a trust boundary. |
| `docs/sdd/05-evaluation-plan.md` | Metrics, quality gates, mutation matrix, benchmarks, replay protocol. | You are writing tests or reporting results. |
| `docs/sdd/06-delivery-and-governance.md` | SDLC process: branching, CI gates, DoR/DoD, policy change management, progressive rollout, compliance mapping, RACI, risk register. | You are shipping or governing a change. |
| `docs/sdd/adr/` | Architecture Decision Records (MADR format). | You want to know *why* a decision was made, or you are proposing to change one. |
| `docs/sdd/schemas/` | JSON Schema (2020-12) for the action envelope and decision record. | You are producing or consuming harness data. |

## The one-paragraph design

An **action envelope** (agent-authored proposal + harness-authored context) is canonicalized and hashed. A **policy decision point** (OPA/Rego) evaluates allowlists, identity/delegation, environment, approval and evidence rules against **authoritative facts** the harness fetched itself. A **critic bank** of narrow, versioned verifiers (Z3/SMT contracts, a read-only Prolog rulebase, a finite-state trajectory monitor) checks only invariants that are formalizable for that action class. Verdicts compose by a fixed **resolution order** (`DENY` › `REPAIR` while budget remains › `ABSTAIN` › `REQUIRES_APPROVAL` › `ALLOW`). An `ALLOW` yields a **single-use decision token** bound to the envelope digest; the **tool broker** executes nothing without a valid token. Every step appends to a **hash-chained decision record**. Anything the harness cannot evaluate (solver `unknown`, timeout, stale fact, policy engine unavailable, audit write failure) is **fail-closed**: no token, no execution.

## Contributing

Read `CLAUDE.md` (conventions for human and AI contributors) and `docs/sdd/06-delivery-and-governance.md` before opening a change. Changes to hard-gate policy require a negative mutation fixture and two-person review.

## License

To be decided in Phase 0 (see `docs/sdd/03-work-breakdown.md`, task P0-11).
