# ADR-0001: Policy is decided by an external decision point before tool dispatch

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Supersedes:** research ADR-001

## Context
Prompt-resident governance loses influence over long trajectories and cannot be audited. Agent frameworks typically govern inputs but not effects. Runtime interception with deterministic rule evaluation is the most proven wrapper shape in the literature (AgentSpec, verified), and policy-as-code engines are the mainstream enterprise mechanism for auditable authorization.

## Decision
Every tool call in a registered action class is intercepted before dispatch and evaluated by a policy decision point (OPA/Rego) that runs outside the governed runtime, reads only harness-built context and facts, and returns per-rule outcomes. The governed runtime cannot modify, bypass or influence the decision other than by proposing a different action. Interception is provided by an MCP gateway (primary) and an in-process hook adapter (secondary); the broker holds the only tool credentials so bypass is structurally impossible.

## Alternatives considered
- **Prompt instructions / system-prompt policy.** Rejected: not deterministic, not auditable, degrades with context length.
- **LLM-as-judge as the gate.** Rejected as a hard gate: circular, manipulable, not deterministic. Allowed only as a soft critic.
- **Framework-internal middleware only (no gateway).** Rejected as the sole mechanism: bypassable if the framework can reach tools directly.
- **Custom rule engine instead of OPA.** Rejected: reinvents testing, bundling, signing and lint tooling that OPA/Regal already provide.

## Consequences
- Positive: deterministic, testable, auditable gates; policy changes decoupled from agent code; mutation fixtures are straightforward.
- Negative: an extra hop and a sidecar to operate; rule authoring becomes a governed discipline (two-person review, fixtures).
- Follow-ups: `FR-01`, `FR-50`, `SEC-01`, `SEC-02`; `ADR-0009` for the concrete engine deployment.
