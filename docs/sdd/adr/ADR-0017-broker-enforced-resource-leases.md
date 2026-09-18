# ADR-0017: Mutual exclusion is a broker lease, not a monitor property

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Origin:** round-two finding `R2-S4`

## Context
The reference workflow requires that at most one deployment of a service is in flight per target. v0.1 expressed this as a temporal property of the trajectory monitor. But the monitor may not enforce before certification (`FR-53`), is automatically demoted whenever the governed model changes (`FR-54`), and shipped in shadow in the registry example. Two sessions proposing inside one 60-second deployment-state window would both pass every check and both execute. The claim that token consumption was "linearizable per resource key" was unimplementable because tokens carried no resource key.

## Decision
Mutual exclusion is enforced by the **broker**, as a lease over a canonical resource key from the resource-key registry (`FR-34`), acquired atomically with token consumption and released on completion or lease timeout. A second consumer is refused with `RESOURCE_BUSY` and the refusal is recorded. Temporal *ordering* properties (precedence, bounded response) remain monitor properties; *exclusion* does not.

## Alternatives considered
- **Wait for monitor certification.** Rejected: it leaves a race open for the whole of v1 and re-opens it on every model change.
- **Rely on the deployment-state fact.** Rejected: any fact has an age, and the race fits inside it.
- **Optimistic concurrency at the tool.** Rejected: it assumes every governed tool implements it, which is exactly the assumption the harness exists to remove.

## Consequences
- Positive: exclusion holds from Phase 1, independent of model behaviour and certification; it is testable with a simple concurrency fixture; the lease also bounds asynchronous actions, which the monitor could not.
- Negative: the lease store becomes part of the trusted computing base and a liveness dependency; a crashed broker holds a lease until it times out, which will occasionally deny a legitimate retry. Lease timeouts are per class and must be tuned against real execution durations.
- Follow-ups: `FR-25`, `FR-26`, `NFR-14`, fixture `MUT-31`, `A-24`.
