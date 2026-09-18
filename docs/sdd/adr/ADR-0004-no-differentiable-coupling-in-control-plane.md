# ADR-0004: No differentiable or gradient-coupled components in the control plane

**Status:** Proposed (rejected for v1; R&D track only) · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** research ADR-004

## Context
Differentiable neurosymbolic substrates (Scallop, DeepProbLog, Lobster, logical neural networks) offer provenance and trainable rule integration, and GPU acceleration has improved their performance. They blur the boundary between generation and enforcement: a control path with gradients flowing from the model is not isolated, not deterministic within documented bounds, and not independently testable in the sense Article VIII requires.

## Decision
No component that can produce an `ALLOW` may be trained jointly with, share parameters with, or receive gradients from the governed model. Differentiable substrates may be explored on an R&D track for perception-heavy or edge domains and may appear only as soft critics if ever integrated.

## Alternatives considered
- **Differentiable Datalog as the critic bank.** Rejected for v1: isolation and determinism concerns; heavy dependency surface.
- **Probabilistic logic as a hard gate.** Rejected: probabilities are advisory by Article I.

## Consequences
- Positive: small, deterministic TCB.
- Negative: forgoes learned generalization in the critic bank; high-entropy behaviours may need probabilistic monitors later, which will be soft or subject to a new ADR.
