# ADR-0015: Two digests, and approval as a fact that triggers re-evaluation

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** ADR-0008 · **Origin:** round-two findings `R2-S2`, `R2-C2`, `R2-B1` (found independently by four reviewers)

## Context
ADR-0008 bound both tokens and approvals to a single envelope digest. That digest covers the facts and their fetch timestamps. Approvals live for up to 24 hours; the deployment-state fact lives for 60 seconds. So the design had only two possible behaviours, both wrong: re-fetch facts at approval time, changing the digest and voiding every approval older than the shortest fact lifetime; or issue the token against the frozen digest and execute a production change on day-old evidence, which is precisely the time-of-check/time-of-use gap the ADR was written to close. The lifecycle also never showed how an approved action reaches the broker, since re-invoking the tool produces a new envelope that supersedes the approval.

## Decision
1. **Two digests.** The *proposal digest* covers the proposal plus the actor, the action class and the policy-bundle digest: the identity of what is being asked, by whom, under which rules. The *envelope digest* covers the whole envelope including facts and timestamps: the identity of one evaluation.
2. **Approvals bind to the proposal digest** and the bundle digest. Tokens bind to the envelope digest. A change to the proposal, the actor, the class or the bundle changes the proposal digest and supersedes the approval, so piggybacking stays blocked; a mere fact refresh does not.
3. **Approval resolution triggers a fresh evaluation.** The approval service records the decision and hands off to the gateway; it never calls the token service. The gateway re-fetches every required fact, rebuilds the envelope, and re-runs resolution with the approval visible as the registered fact `harness_approval{proposal_digest, bundle_digest, approver, decided_at, expires_at}`. A token is issued only if that fresh evaluation resolves to `ALLOW`.
4. If the fresh evaluation denies or abstains, the approval becomes `void` with the reason recorded and the approver is notified. Nothing executes on evidence that no longer holds.
5. Human oversight therefore enters policy as a **fact**, not as a side channel or a privileged code path, which keeps Article I intact: the approval says a human decided, and the rules still decide what that permits.

## Alternatives considered
- **Keep one digest and shorten approval time-to-live to the shortest fact age.** Rejected: a 60-second approval window is unusable for humans, and it merely relocates the problem.
- **Exclude facts from the digest entirely.** Rejected: then the token would not bind the evidence the decision rested on, and a token could be replayed against a different fact set.
- **Let the approval service mint tokens directly** (the v0.1 architecture edge). Rejected: it puts a human-facing service in the mint path and skips re-evaluation, which is how the stale-execution bug arises.
- **Re-evaluate but allow a grace window on fact age for approved actions.** Rejected: an explicit exception to freshness is an explicit hole; if a class genuinely needs it, it belongs in that class's `max_age_seconds`, visibly.

## Consequences
- Positive: approvals survive the passage of time without authorizing stale action; the token still binds exactly what executes; the audit trail links the original decision, the approval and the fresh decision.
- Negative: an approval may be granted and then not execute, which must be communicated well or approvers will lose trust; the approval service gains a fact-provider role and enters the trusted computing base in that capacity.
- Follow-ups: `FR-04`, `FR-20`, `FR-40`–`FR-47`, `P1-29`, fixtures `MUT-11`, `MUT-22`.
