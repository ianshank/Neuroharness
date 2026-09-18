# ADR-0014: Verdict resolution order, safety order and escalation limits

**Status:** Proposed (amended 2026-09-18 after a property test disproved its monotonicity claim) · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** ADR-0007 · **Origin:** round-two findings `R2-S3`, `R2-C1`, `R2-C3`, `R2-C7`, `R2-C8`

## Context
ADR-0007 defined a resolution procedure but left four holes that round-two review found: (1) the "monotonicity" property everyone cited had no defined order, and the procedure actually violated it, because an abstention plus one more repairable failure became a repair, which can end in an allow; (2) infrastructure failures appeared in the ADR's prose but not in the specification's procedure nor in the resolver's declared inputs; (3) any abstention could escalate to human approval if a class set a flag, turning "CI never ran" or "monitor state lost" into a rubber-stamp; (4) the procedure never consulted approvability, so a class marked non-approvable still returned `REQUIRES_APPROVAL`, contradicting `FR-45`.

## Decision
1. Define the **safety order** `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`, and state monotonicity against it: adding any failure never moves a verdict toward `ALLOW`.
2. Resolve in this order: mode normalization (a hard critic whose *own* declared mode is not `enforce` is treated as soft and records `would_be_verdict`; a `halted` class denies) → **every `DENY`-deciding condition** (non-repairable failure, rate limit, repair budget exhausted, approval required on a non-approvable class) → **then** the abstention conditions (non-escalating infrastructure first and terminal, then indeterminate results and unusable required facts, which **block repair**) → repairable failure within budget → approval required → allow.

   *Amendment, 2026-09-18.* The first draft of this ADR placed the repair-budget and approvability denials after the abstention steps, and claimed plain monotonicity. A property test written against the implementation disproved the claim: adding an abstention to a settled `DENY` softened it to `ABSTAIN`. Because an abstention on an escalatable fact in an approvable class escalates to `REQUIRES_APPROVAL`, that softening was exploitable — an agent with an exhausted repair budget could let a required fact go stale and buy a human approval request it was not entitled to. Monotonicity is now unconditional because every denial is decided first.
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
