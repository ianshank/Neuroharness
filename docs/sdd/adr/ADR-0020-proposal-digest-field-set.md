# ADR-0020: The proposal digest covers a fixed, narrow projection

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Amends:** ADR-0015 · **Origin:** increment-1 peer review

## Context
ADR-0015 split the action's identity in two: a proposal digest that a human approval binds to, and an envelope digest that a decision token binds to. It defined the proposal digest loosely, as "proposal + actor + action class + policy bundle", and the specification glossary repeated that wording.

Reviewing the first implementation increment showed the field set is too wide to be safe. The envelope schema puts `agent_version` and `model_identity` inside `context.actor`, and puts `mode`, `repair_budget`, `approvable` and `escalate_on` inside `context.action_class`. An approval may be pending for up to a day (`FR-41`). Under the wide projection, all of the following silently supersede a pending approval for a reason that has nothing to do with what the human agreed to:

- the agent redeploys and its version string changes;
- the governed model is upgraded;
- an operator tunes a registry knob, or promotes the class from shadow to enforce.

The failure is quiet, which is the worst property it could have: the approver sees their decision evaporate, or worse, re-approves reflexively. The wide projection is also partly redundant, because `FR-40` already binds an approval to the policy-bundle digest as a separate field.

## Decision
The proposal digest covers exactly this projection, declared in code as data rather than as procedure so that changing it is a reviewable diff:

```
proposal.tool
proposal.intent
proposal.arguments
context.actor.agent_id
context.actor.principal
context.actor.delegation_chain
context.actor.environment
context.policy_bundle.digest
```

Everything else is excluded, each for a stated reason:

| Excluded | Why |
|---|---|
| `proposal.claims` | Agent-authored and never evaluated (`FR-12`). Including them would let the agent void its own pending approval. |
| `actor.agent_version`, `actor.model_identity` | Deployment churn unrelated to the request. |
| `context.action_class` (all of it) | Mutable policy knobs. The rule set is already pinned by the bundle digest. |
| `facts`, `proposed_at`, `trace_id`, `session_id`, `repair_iteration`, `batch` | Evaluation-scoped. Pinning them is exactly the job of the envelope digest. |

The envelope digest is unchanged: it covers the whole envelope, including facts and timestamps, and remains what a token binds to.

## Alternatives considered
- **Keep the wide projection and shorten the approval time-to-live.** Rejected: an approval window shorter than the churn it must survive is unusable for humans, and it relocates the problem rather than solving it.
- **Exclude the policy-bundle digest too, relying on the separate binding in `FR-40`.** Rejected: including it makes the digest self-describing, so a stored approval can be validated from the digest alone without joining another field. The cost is that a bundle change supersedes approvals, which is the intended behaviour under `FR-84`.
- **Include `action_class` but only its immutable parts** (`tool`, `intent`, `effect_class`). Rejected as redundant: `tool` and `intent` are already in the projection through `proposal`, and `effect_class` is a function of them.

## Consequences
- Positive: a pending approval survives agent redeploys, model upgrades and registry tuning, and is superseded only by a change to what was actually approved or to the rules it was approved under. Approval piggybacking stays blocked, because the arguments, the principal chain and the environment are all covered.
- Negative: a registry change that *tightens* a class no longer supersedes pending approvals through the digest. `FR-84` must therefore carry that responsibility explicitly, by re-evaluating pending approvals on a registry transition and not only on a bundle transition. This ADR makes that dependency load-bearing rather than incidental.
- Follow-ups: specification §2 and `FR-04` amended; `FR-84` to be checked for the registry-transition clause; digest test vectors published against this projection and not before.
