# ADR-0002: Symbolic verification is bounded to registered action classes

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** research ADR-002

## Context
Autoformalization quality is uneven: SMT-based formalization improves logical tasks and harms knowledge-heavy tasks (verified). Sound checking guarantees the precision of accepted outputs, not completeness. Applying solvers to open-world or ambiguous actions would produce both false blocks and false confidence.

## Decision
Symbolic critics (SMT contracts, the Prolog rulebase, the trajectory monitor) run only for action classes that are registered in a signed action-class registry with: the critic set, each critic's natural-language source requirement, timeouts, hard/soft designation, rollout mode, and repair budget. Unregistered action classes receive policy-only evaluation or `ABSTAIN` (strict mode, default). Theories are bounded (quantifier-free linear integer arithmetic, bit-vectors, enumerations). Reasoning-trace verification and open-world factual checks are out of scope.

## Alternatives considered
- **Apply critics to every action.** Rejected: unbounded false-block rate and latency; contradicts the evidence.
- **Let the model decide when to invoke a verifier.** Rejected: violates Article I (the model would control whether it is checked).
- **Unbounded theories / quantifiers.** Rejected: `unknown` rate and latency unbounded; harder to test.

## Consequences
- Positive: predictable latency; every critic traceable to a requirement; scope creep requires a registry change under review.
- Negative: coverage limited to what is registered; new workflows require authoring effort.
- Follow-ups: `FR-30`–`FR-33`, `FR-51`, `FR-62`, Constitution Art. V.
