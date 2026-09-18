# ADR-0018: Verify effects after execution where an authoritative state fact exists

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Product owner · **Origin:** round-two finding `R2-B3`

## Context
The project's premise is that agent systems govern inputs but not effects. v0.1 nonetheless had no post-execution stage: after the broker executed, only evidence was written. The critic bank listed "postconditions" that no component could evaluate, and the specification quietly disclaimed that it establishes nothing about what a tool actually did. So the product claim and the design disagreed: what was governed was *dispatch*, not effect. A deployment returning success while shipping the wrong artifact was invisible to the harness.

## Decision
Add an **effect critic** that runs on receipt (synchronous) or completion (asynchronous) for every action class that has a registered state fact. It compares the typed result and a freshly fetched state fact against the proposal: did the authorized thing happen, and only that? Results are appended as `effect_verification` records; a mismatch raises `EFFECT_MISMATCH`, alerts, feeds the monitor as an event, and per policy blocks subsequent actions on that resource key. Where no authoritative state fact exists, the specification says so plainly rather than implying verification.

## Alternatives considered
- **Leave effect checking to a periodic post-hoc audit** (the v0.1 position, deferred to the bypass exercise). Rejected: an audit that runs weekly does not stop the second wrong deployment, and it leaves the product claim unsupported between audits.
- **Verify effects synchronously before returning to the agent.** Rejected: for asynchronous actions the effect does not exist yet, and blocking the agent on eventual consistency would make the harness a latency problem.
- **Treat the tool's own success response as verification.** Rejected: that is the assertion under test.

## Consequences
- Positive: makes the "output-side gap" claim true rather than aspirational; catches specification gaming where arguments pass every check but resolve differently; gives the monitor a real completion signal.
- Negative: adds a second state-fact fetch per action and a new failure mode (the effect critic itself abstaining); mismatches will initially be dominated by connector and modelling errors rather than attacks, so the alert needs a tuning period before it is treated as security-relevant.
- Follow-ups: `FR-26`, `FR-57`, `P2-11`, fixture `MUT-32`, `A-38`, threat `T-26`.
