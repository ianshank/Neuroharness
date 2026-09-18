# ADR-0013: Temporal property compiler: bounded LTLf subset, built in-house

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead · **Origin:** round-two findings `R2-D17`, `R2-B8`

## Context
Trajectory properties must compile to deterministic finite automata at bundle-build time (`FR-52`). Mature LTLf-to-automaton tooling (MONA, Spot, and the libraries built on them) is GPL-licensed, which collides with a project licence that is still undecided (`P0-11`, `OQ-09`). The v1 property set is small and shallow: precedence, bounded response, absence. Vendoring a general LTL engine would import a large dependency, a licence constraint and a semantics surface far wider than the properties in use.

## Decision
Implement a compiler for a **bounded LTLf subset** in-house: precedence (`P` before `Q`), bounded response (`P` implies `Q` within k events), absence (`never P`), and their conjunctions over a finite event alphabet drawn from the registry. Properties compile to explicit DFAs at bundle-build time, are checked into the bundle with their transition tables, and carry a property-set digest that versions monitor state (`FR-84`). No general LTL parser, no epsilon-transitions, no unbounded until. If a future property genuinely needs full LTLf, that is a new ADR and a licence decision, not an incremental import.

## Alternatives considered
- **Vendor MONA/Spot.** Rejected for v1: GPL exposure before the licence is chosen, and far more semantics than the property set needs.
- **Interpret properties at runtime instead of compiling.** Rejected: runtime interpretation makes the per-step cost data-dependent and the automaton unversionable, which breaks `FR-84` state versioning.
- **Express ordering in Rego over a session log.** Rejected: the policy engine is stateless by design; putting trajectory state in it would move state into the component that must stay replayable from a single input document.

## Consequences
- Positive: no licence constraint, small dependency surface, explicit transition tables that a reviewer can read, deterministic per-step cost.
- Negative: a hand-written compiler is a correctness risk of its own; it needs property-based tests against a reference semantics and its own fixtures. The subset will eventually be too small for someone.
- Follow-ups: `P3-01`, `OQ-09`, `R-16`.
