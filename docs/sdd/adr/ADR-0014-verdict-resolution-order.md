# ADR-0014: Verdict resolution order, safety order and escalation limits

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** ADR-0007 · **Origin:** round-two findings `R2-S3`, `R2-C1`, `R2-C3`, `R2-C7`, `R2-C8`

## Context
ADR-0007 defined a resolution procedure but left four holes that round-two review found: (1) the "monotonicity" property everyone cited had no defined order, and the procedure actually violated it, because an abstention plus one more repairable failure became a repair, which can end in an allow; (2) infrastructure failures appeared in the ADR's prose but not in the specification's procedure nor in the resolver's declared inputs; (3) any abstention could escalate to human approval if a class set a flag, turning "CI never ran" or "monitor state lost" into a rubber-stamp; (4) the procedure never consulted approvability, so a class marked non-approvable still returned `REQUIRES_APPROVAL`, contradicting `FR-45`.

## Decision
1. Define the **safety order** `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`, and state monotonicity against it: adding any failure never moves a verdict toward `ALLOW`.
2. Resolve in this order: mode normalization (a hard critic not in `enforce` is treated as soft and records `would_be_verdict`; a `halted` class denies) → non-repairable hard failure → **non-escalating infrastructure reason (terminal abstention)** → other abstentions (which **block repair**) → repairable failure within budget → budget exhausted → approval required (denied outright when the class is not approvable, satisfied when a matching `harness_approval` fact exists) → allow.
3. Abstentions precede repairs. Spending agent turns repairing while evaluation is incomplete is both wasteful and a probing channel, and it is what broke monotonicity.
4. Escalation from abstention to approval is allowed only for solver uncertainty and for facts a class explicitly marks escalatable, only when *every* present abstention reason is escalatable, and never for the ten infrastructure reasons. The registry loader rejects escalation on a non-approvable class.
5. Evidence-store failure after resolution overrides the response to an abstention and is replayed from a local write-ahead log on recovery.

## Alternatives considered
- **A pure lattice join over per-check verdicts.** Rejected again, for the reason ADR-0007 gave and one more: repair must outrank approval while budget remains but must not outrank denial or abstention, and expressing that as a lattice obscures the infrastructure short-circuit.
- **Let repair precede abstention** (the v0.1 behaviour). Rejected: it breaks monotonicity and lets an agent burn budget against a harness that cannot currently decide anything.
- **Per-reason escalation configured freely per class.** Rejected: that is how "CI missing" becomes approvable.

## Consequences
- Positive: every input combination has exactly one verdict; the property test is now satisfiable; infrastructure failures cannot be escalated away by configuration.
- Negative: more abstentions reach humans as terminal outcomes rather than approval requests, which is the correct but less convenient behaviour; operators will feel this during provider flakiness (`NFR-21`).
- Follow-ups: `FR-05`, spec §5.3–5.6, `P0-08`, `P1-05`.
