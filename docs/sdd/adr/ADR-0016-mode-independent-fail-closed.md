# ADR-0016: Rollout modes never weaken fail-closed; halt is the incident lever

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead, Product owner · **Supersedes:** ADR-0010 · **Origin:** round-two findings `R2-S1` (critical), `R2-S7`, `R2-B2`, `R2-D9`

## Context
ADR-0010 introduced shadow, advisory and enforce, defining shadow as "evaluate, record, never block". Round-two review showed that this sentence, read literally by an implementer, breaks the constitution: with the policy engine down a shadow class executes anyway, and with the evidence store down the specification demanded both "no token" and "never block". Since every class starts in shadow, Article II was false for the entire system at the moment of rollout. The same ADR listed integrity failures among the triggers for demoting a class to advisory, which would have turned a tampered policy bundle into a licence to execute. Separately, demotion to advisory was the only incident lever and was available to a single principal, making it a cheaper total bypass than a policy change, which requires two-person review and a killing fixture.

## Decision
1. Shadow and advisory never block **on the verdict**. They always evaluate, always record, and always issue a token (marked `shadow`). Infrastructure failures block in every mode.
2. Tokens carry `verdict` and `mode`; the broker refuses a shadow token for a class whose current mode is enforce, so a promotion cannot be straddled.
3. In shadow no approval requests are created and no counterexamples are returned to the agent; advisory may surface typed warnings to operators and, per `OQ-06`, to the agent.
4. Add a fourth mode, **`halted`**: deny everything, issue no token, cancel pending approvals. This is the incident lever for integrity failures, discovered bypasses, key compromise and effect mismatches.
5. **Demotion is not an incident lever for integrity problems.** It applies only to false-block spikes, requires two override-group principals outside the affected delegation chains, a reason and a ticket, and auto-expires within one hour unless replaced by a signed registry change under two-person review.

## Alternatives considered
- **Keep "never block" absolute and accept unguarded execution in shadow.** Rejected: it makes the harness a no-op precisely while teams are deciding whether to trust it, and it contradicts Article II.
- **Block on the verdict in shadow too** (i.e. no shadow mode). Rejected: then false-block rate can never be measured before enforcement, which is the entire point of progressive rollout.
- **Make demotion single-principal but heavily alerted.** Rejected: alerting is not authority. A lever that disables enforcement must cost at least as much as changing the rules.

## Consequences
- Positive: shadow data stays representative because the code path is identical; a tampered bundle now halts rather than opening the gate; the incident playbook distinguishes "too strict" from "compromised".
- Negative: shadow classes can now fail closed, so an early-stage deployment can deny service during an infrastructure outage before anyone has come to rely on the harness. This is intended and must be communicated during onboarding.
- Follow-ups: `FR-20`, `FR-48`, `FR-49`, `FR-80`, `INV-11`, fixtures `MUT-19`, `MUT-20`, `MUT-21`, `MUT-26`.
