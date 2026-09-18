# Neuroharness

**A fail-closed policy-and-verification harness for LLM agent workflows.**

Neuroharness sits between an agent and the tools it wants to call. The agent proposes an action; the harness evaluates it against deterministic policy and a small bank of bounded symbolic critics; the harness (never the model) decides `ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN`, or asks for a `REPAIR`; and every decision leaves a replayable, tamper-evident evidence record.

> The cognitive plane proposes; the harness disposes.

## Status

**Phase: Specification (Spec-Driven Development), revision 2.** No runtime code exists yet. This repository holds the research input, two rounds of peer review, and the SDD package that implementation will be driven from. Round two put the specification through four adversarial reviews; the resulting v0.2 changed how approvals bind, how rollout modes interact with failure, how mutual exclusion is enforced, and what the project claims to verify.

## Document map

| Path | What it is | Read it when |
|---|---|---|
| `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` | The research synthesis that motivated the project (kept verbatim for provenance). | You want the evidence base and the model-perspective analysis. |
| `docs/review/2026-09-18-peer-review-research-synthesis.md` | Round-one peer review of the synthesis: verdict, major/minor issues, citation audit. | You want to know what the research got right and what it left out. |
| `docs/review/2026-09-18-round-2-deep-dive-review.md` | Round-two review of the specification itself: 71 findings from four adversarial lenses, plus an audit that corrects round one. | You want to know how the design was attacked and what broke. |
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

An **action envelope** (agent-authored proposal + harness-authored context) is canonicalized and hashed twice: a **proposal digest** identifying what is asked and by whom, and an **envelope digest** identifying one evaluation. A **policy decision point** (OPA/Rego) evaluates allowlists, identity and delegation, environment, approval and evidence rules against **facts the harness fetched itself** from registered providers, never against agent claims and never against evidence the agent could have manufactured. A **critic bank** of narrow, versioned verifiers checks only invariants registered as formalizable for that action class. Verdicts resolve in a fixed order along the safety order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`. Human approval binds to the **proposal** digest and triggers a **fresh evaluation** before anything executes, so oversight never underwrites stale evidence. An `ALLOW` yields a **single-use token** carrying the verdict and rollout mode; the **broker** holds the only credentials, refuses to execute without a matching token, and takes a **lease** on the resource so two sessions cannot act at once. After execution an **effect critic** checks that what happened is what was authorized. Every step appends to a **hash-chained record**. Anything the harness cannot evaluate is **fail-closed in every rollout mode**: no token, no execution.

## Contributing

Read `CLAUDE.md` (conventions for human and AI contributors) and `docs/sdd/06-delivery-and-governance.md` before opening a change. Changes to hard-gate policy require a negative mutation fixture and two-person review.

## License

To be decided in Phase 0 (see `docs/sdd/03-work-breakdown.md`, task P0-11).
