# ADR-0005: Ontology / knowledge-graph work is deferred out of the critical path

**Status:** Proposed (defer) · **Date:** 2026-09-18 · **Deciders:** Product owner, Tech lead · **Supersedes:** research ADR-005

## Context
Semantic layers (knowledge graphs with SHACL/OWL validation) can become platform assets where a durable domain ontology exists, but knowledge engineering tends to dominate cost and delay enforcement value. The immediate problem is tool-call policy compliance and workflow safety.

## Decision
No ontology or knowledge graph is a prerequisite for any v1 gate. Typed contracts and enumerations in the registry carry the domain vocabulary. A SHACL/graph critic may be proposed post-v1 by a new ADR when a workflow demonstrates reusable, stable concepts and multi-hop questions that stateless policy cannot express.

## Alternatives considered
- **Ontology-first design.** Rejected for v1: cost and schedule risk; no enforcement value until complete.

## Consequences
- Positive: fast path to enforcement value.
- Negative: cross-workflow vocabulary may diverge; mitigated by shared enumeration packs in the registry.
