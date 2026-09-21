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
| [ADR-0011](ADR-0011-reference-workflow-deployment.md) | Service deployment is the reference workflow | Proposed | `P0-02`, `OQ-02` |
| [ADR-0012](ADR-0012-licence-and-contribution-model.md) | Licence and contribution model (Apache-2.0 decided by owner) | Accepted | `P0-11`, `OQ-09` |
| [ADR-0013](ADR-0013-temporal-property-compiler.md) | Temporal property compiler: bounded LTLf subset, built in-house | Proposed | Round 2 `R2-D17`, `R2-B8` |
| [ADR-0014](ADR-0014-verdict-resolution-order.md) | Verdict resolution order, safety order and escalation limits | Proposed (supersedes 0007; **amended** after a property test disproved its monotonicity claim) | Round 2 `R2-S3`, `R2-C1`, `R2-C7`, `R2-C8` |
| [ADR-0015](ADR-0015-proposal-digest-and-approval-reevaluation.md) | Two digests, and approval as a fact that triggers re-evaluation | Proposed (supersedes 0008) | Round 2 `R2-S2`, `R2-C2`, `R2-B1` |
| [ADR-0016](ADR-0016-mode-independent-fail-closed.md) | Rollout modes never weaken fail-closed; halt is the incident lever | Proposed (supersedes 0010) | Round 2 `R2-S1` (critical), `R2-S7`, `R2-B2` |
| [ADR-0017](ADR-0017-broker-enforced-resource-leases.md) | Mutual exclusion is a broker lease, not a monitor property | Proposed | Round 2 `R2-S4` |
| [ADR-0018](ADR-0018-post-execution-effect-verification.md) | Verify effects after execution where an authoritative state fact exists | Proposed | Round 2 `R2-B3` |
| [ADR-0019](ADR-0019-implementation-stack-revision.md) | Implementation stack revision: key algorithm, solver bounding, Prolog deferral | Proposed (supersedes 0009) | Round 2 `R2-D5`, `R2-D16`, `R2-D17` |
| [ADR-0020](ADR-0020-proposal-digest-field-set.md) | The proposal digest covers a fixed, narrow projection | Proposed (amends 0015) | Increment-1 review |
| [ADR-0021](ADR-0021-canonical-number-encoding.md) | Exact integers in canonicalisation, with a rule that makes the deviation unreachable | Proposed | Raised by implementation |
| [ADR-0022](ADR-0022-fact-provider-anti-laundering.md) | The anti-laundering relation is derived from action-class writes, not enumerated per provider | Proposed | Increment-2 `D-2` (`P0-06`) |
| [ADR-0023](ADR-0023-one-resource-key-grammar.md) | One resource-key grammar, owned by the registry and rendered into everything else | Proposed | Increment-2 `D-1` |
| [ADR-0024](ADR-0024-bounded-argument-schema-evaluation.md) | A bounded, closed-vocabulary evaluator for `argument_schema`, not a JSON Schema dependency | Proposed | Increment-2 `D-3` |
| [ADR-0025](ADR-0025-reason-subject-grammar-at-construction.md) | One per-name reason-subject grammar, checked at construction | Proposed | Increment-2 `F-06` |
| [ADR-0026](ADR-0026-issuance-claim-is-the-record.md) | The issuance claim is the record, and every absent token names its reason | Proposed | Increment-2 `D-4`, `D-5` |
| [ADR-0027](ADR-0027-d7-required-fact-escalation.md) | D-7: required facts may escalate when class opts in | Accepted | Increment-2 `D-7`; product 2026-09-20 |
| [ADR-0028](ADR-0028-hard-rule-fixture-register.md) | Hard-rule fixture register as a shrink-only durable exemption | Accepted | Increment-2 `D-8` |
