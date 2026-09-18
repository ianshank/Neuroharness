# Architecture Decision Records

Format: [MADR](https://adr.github.io/madr/) (Context → Decision → Alternatives → Consequences). Accepted ADRs are immutable; to change a decision, write a new ADR that supersedes it.

| ID | Title | Status | Origin |
|---|---|---|---|
| [ADR-0001](ADR-0001-external-policy-decision-point.md) | Policy is decided by an external decision point before tool dispatch | Proposed | Research ADR-001, revised |
| [ADR-0002](ADR-0002-bounded-symbolic-checks.md) | Symbolic verification is bounded to registered action classes | Proposed | Research ADR-002, revised (adds registry) |
| [ADR-0003](ADR-0003-constrained-decoding-is-syntax-control.md) | Constrained decoding is syntax control, outside the harness | Proposed | Research ADR-003 |
| [ADR-0004](ADR-0004-no-differentiable-coupling-in-control-plane.md) | No differentiable or gradient-coupled components in the control plane | Proposed (reject for v1) | Research ADR-004 |
| [ADR-0005](ADR-0005-defer-ontology-first.md) | Ontology / knowledge-graph work is deferred out of the critical path | Proposed (defer) | Research ADR-005 |
| [ADR-0006](ADR-0006-evidence-producing-enforcement.md) | Enforcement writes hash-chained evidence before any token is issued | Proposed | Research ADR-006, revised (write-ahead, chain) |
| [ADR-0007](ADR-0007-verdict-semantics-and-fail-closed.md) | Verdict semantics, resolution order and fail-closed mapping | Proposed | Peer review M1, M4 |
| [ADR-0008](ADR-0008-digest-bound-decision-tokens.md) | Digest-bound single-use decision tokens and approval binding | Proposed | Peer review M2, M9 |
| [ADR-0009](ADR-0009-reference-implementation-stack.md) | Reference implementation stack: Python, OPA sidecar with signed bundles, PostgreSQL evidence store | Proposed | Technical plan |
| [ADR-0010](ADR-0010-progressive-enforcement-rollout.md) | Progressive enforcement rollout: shadow → advisory → enforce | Proposed | Peer review M10; SDLC practice |
| ADR-0011 | Reference workflow selection | Planned (`P0-02`) | — |
| ADR-0012 | License and contribution model | Planned (`P0-11`) | — |
