# ADR-0010: Progressive enforcement rollout: shadow → advisory → enforce

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead, Product owner · **Origin:** peer review M10; 2026 progressive-delivery practice

## Context
A new gate can be wrong in two directions: it can miss violations (recall) or block legitimate work (false blocks). Both are only measurable on real traffic. The monitor's coverage additionally depends on the governed model. Turning gates on blind risks either harm or a loss of trust that leads to gates being disabled.

## Decision
Every action class and every critic has a rollout mode: `shadow` (evaluate, record, never block; the broker executes with a shadow token so the code path is identical), `advisory` (as shadow plus operator visibility and, optionally, typed warnings), `enforce` (block). Promotion requires: 100% mutation kill for the class's hard gates, labelled false-block rate within threshold over a minimum sample, no open blocking risk, and, for monitors, a certification record. Demotion is immediate on false-block spikes, integrity failures or certification triggers. Mode transitions are signed registry changes and are recorded. Demoting to advisory is the incident-response lever; loosening a gate is not.

## Alternatives considered
- **Enforce from day one.** Rejected: unmeasured false-block rate; erodes trust.
- **Feature flags in code.** Rejected: mode is policy data and must be signed and recorded like policy.

## Consequences
- Positive: evidence-based promotion; identical code path in all modes makes shadow data representative; a safe lever during incidents.
- Negative: shadow periods lengthen time-to-enforcement; requires labelling effort (`05-evaluation-plan.md` §3).
