# Architecture Decision Records

Format: [MADR](https://adr.github.io/madr/) (Context → Decision → Alternatives → Consequences). Accepted ADRs are immutable; to change a decision, write a new ADR that supersedes it. Only the status line of a superseded record is edited, to point at its replacement.

| ID | Title | Status | Origin |
|---|---|---|---|
| [ADR-0001](ADR-0001-external-policy-decision-point.md) | Policy is decided by an external decision point before tool dispatch | Proposed | Research ADR-001, revised |
| [ADR-0002](ADR-0002-bounded-symbolic-checks.md) | Symbolic verification is bounded to registered action classes | Proposed | Research ADR-002, revised |
| [ADR-0003](ADR-0003-constrained-decoding-is-syntax-control.md) | Constrained decoding is syntax control, outside the harness | Proposed | Research ADR-003 |
| [ADR-0004](ADR-0004-no-differentiable-coupling-in-control-plane.md) | No differentiable or gradient-coupled components in the control plane | Proposed (reject for v1) | Research ADR-004 |
| [ADR-0005](ADR-0005-defer-ontology-first.md) | Ontology / knowledge-graph work is deferred out of the critical path | Proposed (defer) | Research ADR-005 |
| [ADR-0006](ADR-0006-evidence-producing-enforcement.md) | Enforcement writes hash-chained evidence before any token is issued | Proposed | Research ADR-006, revised |
| [ADR-0007](ADR-0007-verdict-semantics-and-fail-closed.md) | Verdict semantics, resolution order and fail-closed mapping | **Superseded by ADR-0014** | Peer review M1, M4 |
| [ADR-0008](ADR-0008-digest-bound-decision-tokens.md) | Digest-bound single-use decision tokens and approval binding | **Superseded by ADR-0015** | Peer review M2, M9 |
| [ADR-0009](ADR-0009-reference-implementation-stack.md) | Reference implementation stack | **Superseded by ADR-0019** | Technical plan |
| [ADR-0010](ADR-0010-progressive-enforcement-rollout.md) | Progressive enforcement rollout: shadow → advisory → enforce | **Superseded by ADR-0016** | Peer review M10 |
| ADR-0011 | Reference workflow selection | Planned (`P0-02`) | — |
| ADR-0012 | Licence and contribution model | Planned (`P0-11`) | — |
| [ADR-0013](ADR-0013-temporal-property-compiler.md) | Temporal property compiler: bounded LTLf subset, built in-house | Proposed | Round 2 `R2-D17`, `R2-B8` |
| [ADR-0014](ADR-0014-verdict-resolution-order.md) | Verdict resolution order, safety order and escalation limits | Proposed (supersedes 0007) | Round 2 `R2-S3`, `R2-C1`, `R2-C7`, `R2-C8` |
| [ADR-0015](ADR-0015-proposal-digest-and-approval-reevaluation.md) | Two digests, and approval as a fact that triggers re-evaluation | Proposed (supersedes 0008) | Round 2 `R2-S2`, `R2-C2`, `R2-B1` |
| [ADR-0016](ADR-0016-mode-independent-fail-closed.md) | Rollout modes never weaken fail-closed; halt is the incident lever | Proposed (supersedes 0010) | Round 2 `R2-S1` (critical), `R2-S7`, `R2-B2` |
| [ADR-0017](ADR-0017-broker-enforced-resource-leases.md) | Mutual exclusion is a broker lease, not a monitor property | Proposed | Round 2 `R2-S4` |
| [ADR-0018](ADR-0018-post-execution-effect-verification.md) | Verify effects after execution where an authoritative state fact exists | Proposed | Round 2 `R2-B3` |
| [ADR-0019](ADR-0019-implementation-stack-revision.md) | Implementation stack revision: key algorithm, solver bounding, Prolog deferral | Proposed (supersedes 0009) | Round 2 `R2-D5`, `R2-D16`, `R2-D17` |
