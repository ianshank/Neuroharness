# ADR-0011: Service deployment is the reference workflow

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Product owner, Security lead · **Relates to:** ADR-0017, ADR-0022 · **Origin:** `P0-02`, `OQ-02`; written late, recording a decision the tree already implements

## Context

`P0-02` asks for one reference workflow, and `OQ-02` names the candidates: **deployment**, **data migration**, **refund**. Everything downstream depends on the answer — `P0-03`'s invariants, `P0-06`'s fact providers, `P1-15`'s policy pack and every `WF-` fixture are written against one domain.

This ADR is late. The decision was taken in substance when `tests/fixtures/registry/reference_deploy_registry.json` was written and `WF-01`–`WF-06c` were defined against service deployment, and the index has carried `ADR-0011 | Reference workflow selection | Planned` ever since. Writing it now records what was decided rather than deciding it afresh — and the reason it is worth writing at all is that the ADR index check (CI stage 11) treats a dangling `Planned` row as exactly what it is: a decision nobody can point at.

## Decision

**Service deployment.** `deployment.apply`, `deployment.rollback` and `deployment.status`, with the six workflow invariants `WF-01`–`WF-06c`.

The three criteria `P0-02` asks for:

**Cost of failure.** A bad deployment is expensive, bounded and reversible. That combination is what a first reference workflow needs: expensive enough that the harness is worth having, bounded enough that a shadow-mode mistake is survivable, and reversible enough that the failure mode during `P1-14`'s shadow run is a rollback rather than an incident. A refund workflow fails *irreversibly* — money leaves — which is the wrong risk profile for the workflow you are using to debug the harness itself. A data migration fails irreversibly too, and more quietly.

**Fact-provider availability.** Deployment's facts are the ones a real organisation already has: CI results, a change-approval record, and the deploy control plane's own state. Each has an API, a natural TTL and a clear authority. A refund workflow's decisive facts — fraud signals, chargeback state, customer history — are the ones most likely to be a model's judgement rather than a system of record, which would make the reference workflow an argument about `SEC-01` rather than a demonstration of it.

**Approver availability.** Deployment already has an on-call rotation and an approval culture. `FR-42`'s separation of duties and `P1-10a`'s approval service can be exercised against people who already expect to be asked. Refunds have approvers too, but the approval is usually a customer-service judgement, which is harder to bind to a proposal digest.

**And one criterion `P0-02` does not name, which decided it.** Deployment is the workflow where an *effect* can be verified after the fact (`FR-57`, `ADR-0018`): the control plane will tell you what is actually running. `WF-06c`'s mutual exclusion and the broker's resource lease (`FR-25`, `ADR-0017`) are meaningful because a deployment target is a real, contended resource with observable state. That is what makes the reference workflow able to exercise the post-execution half of the harness rather than only the decision half.

## Alternatives considered

- **Refund.** Rejected on cost of failure and fact availability, above. It remains the best *second* workflow, because it exercises the approval path hardest.
- **Data migration.** Rejected: the effect is hard to verify (a migration's success is a property of data nobody wants to read back in full), and the natural invariants are schema-shaped rather than policy-shaped, so it would exercise less of the harness for the same cost.
- **Defer the choice and build workflow-agnostic.** Rejected. It is the tempting option and it is how a harness ends up with no killing fixtures: the invariants, the facts and the policy pack all need a domain to be concrete about, and `05-evaluation-plan.md`'s whole method rests on concrete mutations.

## Consequences

- **Positive.** Every downstream artefact already assumes this and now has a decision to cite. `SEC-11`'s anti-laundering clause has a concrete tier-2 case to reason about — see `ADR-0022` and `09-fact-provider-specification.md` §7.3, where `deploy_state` cannot be an isolated provider because the control plane that answers "is a deploy in flight" is the one `deployment.apply` writes to.
- **Negative.** The harness's generality is unproven until a second workflow lands. Everything in `policy/` and `critics/` will be written against deployment's shape first, and some of it will turn out to be deployment-specific in ways nobody notices until the second workflow arrives. That is the accepted cost of having killing fixtures at all.
- **Negative, procedural.** This ADR post-dates the work it records, which is the thing `00-constitution.md` Article VII exists to prevent. It is recorded rather than backdated, and the lesson is the one the ADR-index check now enforces mechanically: a `Planned` row is a decision that has probably already been made somewhere in the tree.
