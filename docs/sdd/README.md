# Spec-Driven Development (SDD) package — how to use it

This directory is the **source of truth** for what Neuroharness is, why, and how it will be built. Code, policies and tests are derived from it, not the other way round.

## Why Spec-Driven Development

In 2026 most implementation work on a project like this will be done by humans directing AI coding agents. That only works when the intent is written down precisely enough that an agent (or a new engineer) can implement, test and review against it without oral tradition. SDD makes the specification the executable contract: every requirement has a stable ID, an acceptance scenario, and a test or fixture that proves it. The constitution constrains every downstream decision; the plan turns the spec into architecture; the work breakdown turns the plan into tasks with exit gates.

```
00-constitution ──► 01-specification ──► 02-technical-plan ──► 03-work-breakdown ──► code + policy + fixtures
       ▲                    ▲                     ▲
       │                    │                     │
  04-threat-model    05-evaluation-plan   06-delivery-and-governance        adr/  schemas/
```

## Document status

| Document | Status | Owner (role) | Last changed |
|---|---|---|---|
| `00-constitution.md` | v0.2 — for ratification | Tech lead + Security lead | 2026-09-18 |
| `01-specification.md` | v0.2 — revised after round-two review | Product owner + Tech lead | 2026-09-18 |
| `02-technical-plan.md` | v0.2 | Tech lead | 2026-09-18 |
| `03-work-breakdown.md` | v0.2 — re-baselined to 24 weeks | Delivery lead | 2026-09-18 |
| `04-threat-model.md` | v0.2 — needs human security review | Security lead | 2026-09-18 |
| `05-evaluation-plan.md` | v0.2 | Evaluation lead | 2026-09-18 |
| `06-delivery-and-governance.md` | v0.2 | Delivery lead | 2026-09-18 |
| `adr/` | ADR-0001..0022; 0007, 0008, 0009, 0010 superseded | Tech lead | 2026-09-18 |
| `schemas/*.schema.json` | v1.1 | Tech lead | 2026-09-18 |
| `07-increment-1-plan.md` | Revised after peer review; implementation in progress | Tech lead | 2026-09-18 |
| `09-fact-provider-specification.md` | v0.1 — closes `P0-06`, unblocks `P1-18` | Tech lead + Security lead | 2026-09-18 |

Both review rounds are in `../review/`: round 1 reviewed the research, round 2 reviewed the specification that answered it and re-audited round 1.

## From specification to code

Increment 1 (`07-increment-1-plan.md`) implements the deterministic core in
`src/neuroharness/`. The mapping is deliberately one-to-one so that a reader can
go from a requirement to the code that satisfies it without a search:

| Requirement | Module |
|---|---|
| `FR-04` two digests | `canonical/digest.py` |
| `FR-05`, §5.3 resolution | `resolve/resolver.py`, `resolve/safety.py` |
| `FR-20`–`FR-23` tokens | `tokens/` |
| `FR-70`–`FR-72` evidence | `evidence/` |
| `FR-30`–`FR-35` registry | `registry/` |
| `INV-05`, `INV-11` ordering | `pipeline/decision.py` |
| `SEC-07` typed reasons | `reason.py` |
| `NFR-18` redaction | `observability/logging.py` |
| `NFR-13` clock failure | `seams.py` |

Implementation has already fed one correction back into the specification: §5.3
step 0 demoted every hard critic in a shadow *class*, which made the verdict
mode-dependent and contradicted `INV-11` and scenario `A-16`. See the
specification change log, version 0.4.

## ID conventions

| Prefix | Meaning | Defined in |
|---|---|---|
| `INV-nn` | Harness invariant (non-negotiable property of the harness itself) | `01-specification.md` §4 |
| `WF-nn` | Workflow invariant (property of the governed reference workflow) | `01-specification.md` §3.5 |
| `FR-nn` | Functional requirement | `01-specification.md` §6 |
| `NFR-nn` | Non-functional requirement | `01-specification.md` §6 |
| `SEC-nn` | Security requirement | `01-specification.md` §6, `04-threat-model.md` |
| `A-nn` | Acceptance scenario | `01-specification.md` §8 |
| `R2-Sn/Cn/Dn/Bn` | Round-two review finding | `../review/2026-09-18-round-2-deep-dive-review.md` |
| `MUT-nn` | Negative mutation fixture | `05-evaluation-plan.md` §4 |
| `T-nn` | Threat | `04-threat-model.md` |
| `R-nn` | Risk | `06-delivery-and-governance.md` §9 |
| `Pk-nn` | Task in phase k | `03-work-breakdown.md` |
| `ADR-nnnn` | Architecture decision record | `adr/` |
| `OQ-nn` | Open question | `01-specification.md` §10 |

IDs are never reused. A withdrawn requirement keeps its ID and is marked *withdrawn* with a reason.

## How to change something

1. **A requirement:** edit `01-specification.md`, bump the change log, keep the ID. If it changes a hard gate, add or update the mutation fixture in the same change.
2. **An architectural decision:** write a new ADR that supersedes the old one. Do not edit accepted ADRs except to mark them superseded.
3. **A task:** edit `03-work-breakdown.md`. Tasks are Done only when their acceptance criteria *and* the Definition of Done are met.
4. **The constitution:** requires the amendment process in `00-constitution.md`.

## SDLC practices adopted (and where they are specified)

| Practice | Where |
|---|---|
| Spec-Driven Development with executable acceptance scenarios | this directory; `01-specification.md` §8 |
| Architecture Decision Records (MADR) | `adr/` |
| Trunk-based development, Conventional Commits, semantic release | `06-delivery-and-governance.md` §2 |
| Policy-as-code with unit tests, lint, coverage and negative fixtures as CI gates | `05-evaluation-plan.md` §4; `06-delivery-and-governance.md` §3 |
| Mutation testing with a fixture lifecycle (reserved → active → retired) | `05-evaluation-plan.md` §1a, §4; `02-technical-plan.md` §8 |
| Evaluation-driven development for AI systems (replay, `pass^k`, harness-level metrics) | `05-evaluation-plan.md` |
| Threat modelling before implementation (STRIDE, trust boundaries, TCB inventory) | `04-threat-model.md` |
| Supply-chain security: pinned lockfiles, SBOM, signed policy bundles, build provenance | `06-delivery-and-governance.md` §4 |
| Observability by design (OpenTelemetry traces/metrics, structured decision records) | `02-technical-plan.md` §7 |
| Progressive delivery that never weakens fail-closed, plus a halt lever | ADR-0016; `06-delivery-and-governance.md` §5 |
| Definition of Ready / Definition of Done | `06-delivery-and-governance.md` §6 |
| Compliance-as-code mapping (EU AI Act record-keeping, NIST AI RMF, ISO/IEC 42001) | `06-delivery-and-governance.md` §7 |
| Agent-readable repository conventions | `../../CLAUDE.md` |
