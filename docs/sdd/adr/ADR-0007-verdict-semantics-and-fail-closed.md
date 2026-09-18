# ADR-0007: Verdict semantics, resolution order and fail-closed mapping

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Origin:** peer review M1, M4

## Context
The research listed five outcomes in one place and six in another, treated the solver result `UNKNOWN` as a decision, left `REPAIR` unbounded, and did not say how results from the policy engine and several critics compose. Ambiguity here becomes an implicit-allow defect.

## Decision
1. **Verifier results** (`PASS`, `FAIL{repairable}`, `UNKNOWN`, `TIMEOUT`, `ERROR`, `NOT_APPLICABLE`) are distinct from **verdicts** (`ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN` terminal; `REPAIR` non-terminal).
2. Verdicts are resolved by a fixed procedure (spec §5.3): non-repairable hard failure → `DENY`; repairable hard failure with budget → `REPAIR`; budget exhausted → `DENY`; any hard `UNKNOWN`/`TIMEOUT`/`ERROR` or missing/stale fact or engine/bundle/evidence/clock failure → `ABSTAIN`; approval required → `REQUIRES_APPROVAL`; else `ALLOW`.
3. Repair budget defaults to 3, maximum 10, per action class.
4. `ABSTAIN` is terminal by default; an action class may declare escalation to `REQUIRES_APPROVAL`, except for infrastructure reasons, which never escalate.
5. Soft critics never change a verdict.
6. The resolver is a pure function with a table-driven test over all result combinations and a property test that adding a failure never moves the verdict toward `ALLOW`.

## Alternatives considered
- **A single lattice with a fixed order.** Rejected: `REPAIR` must take precedence over `REQUIRES_APPROVAL` while budget remains (do not ask a human to approve a known-defective action) but must not take precedence over `DENY`; a procedure expresses this more clearly than a lattice.
- **`UNKNOWN` as a verdict.** Rejected: conflates evaluation failure with decision; the mapping to `ABSTAIN` is the fail-closed rule.
- **Unbounded repair.** Rejected: livelock and probing risk.

## Consequences
- Positive: every failure path has a named verdict and a fixture; decisions are explainable by reason codes.
- Negative: higher `ABSTAIN` rates when facts or engines are flaky; mitigated by observability and SLOs, never by relaxing the mapping.
