# Neuroharness

**A fail-closed policy-and-verification harness for LLM agent workflows.**

Neuroharness sits between an agent and the tools it wants to call. The agent proposes an action; the harness evaluates it against deterministic policy and a small bank of bounded symbolic critics; the harness (never the model) decides `ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN`, or asks for a `REPAIR`; and every decision leaves a replayable, tamper-evident evidence record.

> The cognitive plane proposes; the harness disposes.

## Status

**Phase: increment 1, the deterministic core.** The specification package is at revision 2, and the part of the harness that is pure computation now exists in `src/neuroharness/`: the closed reason-code catalogue, the typed fail-closed error hierarchy, the injection seams, RFC 8785 canonicalization and the proposal and envelope digests, the action-class and resource-key registries, the verdict resolver, the token service, the hash-chained evidence store with its write-ahead log, and the pipeline that sequences resolve -> record -> token. Unit, property-based and mutation suites run against it: `PYTHONPATH=src python3 -m pytest`.

**Not built yet:** every component that talks to something outside the process. The MCP gateway, the policy decision point client, the critic bank and the broker are absent, and with them the `policy/` bundles and the `critics/` packs; they attach to the core through the protocol seams it already defines. `docs/sdd/07-increment-1-plan.md` scopes the increment, and its section 4a lists which of the twelve specified CI stages run today and which do not.

This repository also holds the research input and five rounds of peer review. Round two put the specification through four adversarial reviews; the resulting v0.2 changed how approvals bind, how rollout modes interact with failure, how mutual exclusion is enforced, and what the project claims to verify. Round three is the first to check a research synthesis against running code rather than against the specification alone; round four attacks the implementation itself, and found a resource-key grammar the increment-2 repair had left running in three record fields; round five turns the same attention on the repository — its gates, its hygiene and the artefacts it deliberately does not have.

## Document map

| Path | What it is | Read it when |
|---|---|---|
| `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` | The research synthesis that motivated the project (kept verbatim for provenance). | You want the evidence base and the model-perspective analysis. |
| `docs/review/2026-09-18-peer-review-research-synthesis.md` | Round-one peer review of the synthesis: verdict, major/minor issues, citation audit. | You want to know what the research got right and what it left out. |
| `docs/review/2026-09-18-round-2-deep-dive-review.md` | Round-two review of the specification itself: 71 findings from four adversarial lenses, plus an audit that corrects round one. | You want to know how the design was attacked and what broke. |
| `docs/review/2026-09-19-round-3-synthesis-vs-code-review.md` | Round-three review: a second, multi-model research synthesis checked claim by claim against the tree at `944e077`. | You want to know which outside recommendations this repository does not already hold, and which of those are right. |
| `docs/review/2026-09-19-round-4-code-adversarial-review.md` | Round-four review: the implementation attacked and executed — a live grammar defect, three specification defects, and the security properties that held under test. | You want to know what the deterministic core actually does under attack, not what it claims. |
| `docs/review/2026-09-19-round-5-repository-gap-analysis.md` | Round-five review of the repository itself: which of the twelve CI stages run, the tech-debt register, and what is deliberately absent with the component that unblocks each. | You want to know why there is no Dockerfile, or what is owed and by whom. |
| `docs/sdd/README.md` | How Spec-Driven Development works in this repo and the status of each SDD document. | You are about to change anything under `docs/sdd/`. |
| `docs/sdd/00-constitution.md` | Non-negotiable principles every design and code change must satisfy. | Always. Start here. |
| `docs/sdd/01-specification.md` | Functional/non-functional requirements, invariant registry, verdict semantics, acceptance scenarios. | You are implementing or testing a requirement. |
| `docs/sdd/02-technical-plan.md` | Architecture, components, interfaces, data model, technology decisions, testing strategy. | You are designing or building a component. |
| `docs/sdd/03-work-breakdown.md` | Phased task list with dependencies, acceptance criteria and phase exit gates. | You are planning or picking up work. |
| `docs/sdd/04-threat-model.md` | Assets, adversaries, STRIDE analysis, controls and residual risk. | You are touching a trust boundary. |
| `docs/sdd/05-evaluation-plan.md` | Metrics, quality gates, mutation matrix, benchmarks, replay protocol. | You are writing tests or reporting results. |
| `docs/sdd/06-delivery-and-governance.md` | SDLC process: branching, CI gates, DoR/DoD, policy change management, progressive rollout, compliance mapping, RACI, risk register. | You are shipping or governing a change. |
| `docs/sdd/07-increment-1-plan.md` | What increment 1 builds, what it deliberately excludes, and which CI stages that leaves running. | You want to know which parts of the harness exist. |
| `docs/sdd/adr/` | Architecture Decision Records (MADR format). | You want to know *why* a decision was made, or you are proposing to change one. |
| `docs/sdd/schemas/` | JSON Schema (2020-12) for the action envelope and decision record. | You are producing or consuming harness data. |

## The one-paragraph design

An **action envelope** (agent-authored proposal + harness-authored context) is canonicalized and hashed twice: a **proposal digest** identifying what is asked and by whom, and an **envelope digest** identifying one evaluation. A **policy decision point** (OPA/Rego) evaluates allowlists, identity and delegation, environment, approval and evidence rules against **facts the harness fetched itself** from registered providers, never against agent claims and never against evidence the agent could have manufactured. A **critic bank** of narrow, versioned verifiers checks only invariants registered as formalizable for that action class. Verdicts resolve in a fixed order along the safety order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`. Human approval binds to the **proposal** digest and triggers a **fresh evaluation** before anything executes, so oversight never underwrites stale evidence. An `ALLOW` yields a **single-use token** carrying the verdict and rollout mode; the **broker** holds the only credentials, refuses to execute without a matching token, and takes a **lease** on the resource so two sessions cannot act at once. After execution an **effect critic** checks that what happened is what was authorized. Every step appends to a **hash-chained record**. Anything the harness cannot evaluate is **fail-closed in every rollout mode**: no token, no execution.

## Contributing

Read `CONTRIBUTING.md` first, then `CLAUDE.md` (conventions for human and AI contributors) and `docs/sdd/06-delivery-and-governance.md`. Changes to hard-gate policy require a negative mutation fixture and two-person review.

```
make install   # the package and its dev extra
make gate      # every gate CI blocks on, in CI's order
make help      # one target per CI job
```

`.claude/skills/` holds the procedures that touch several files at once — adding a reason code, a record kind or a mutation fixture, and running the gates. They are checked against the tree by `tests/unit/test_skills_are_current.py`, so a skill that goes stale fails CI rather than misleading the next contributor.

## License

To be decided in Phase 0 (see `docs/sdd/03-work-breakdown.md`, task P0-11).
