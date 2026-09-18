# Neuroharness Specification

**Status:** Draft v0.1 — ready for review · **Date:** 2026-09-18 · **Derived from:** `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` as revised by `docs/review/2026-09-18-peer-review-research-synthesis.md`

## 1. Purpose and audience

This specification defines *what* Neuroharness v1 does and how its behaviour is verified. It is written for the engineers and agents implementing it, the policy owners who will author rules, the security reviewers, and the auditors who will consume its evidence. Design (*how*) lives in `02-technical-plan.md`.

Requirement language follows RFC 2119 (`MUST`, `SHOULD`, `MAY`). Every requirement has a stable ID and a verification method (`Test`, `Fixture`, `Inspection`, `Measurement`).

## 2. Glossary

| Term | Definition |
|---|---|
| **Governed runtime** | The LLM agent process (and its framework) whose tool calls the harness controls. |
| **Harness** | Neuroharness as a whole: gateway, policy decision point, critic bank, broker, evidence store. |
| **Action envelope** | The canonical, typed description of one proposed action. Composed of an agent-authored **proposal** and a harness-authored **context**. |
| **Proposal** | The part of the envelope the model generates: intent, tool, arguments, and *claims*. |
| **Claim** | A statement made by the agent (e.g. "CI passed"). Advisory. Never a policy input. |
| **Fact** | A statement obtained by the harness from a registered **fact provider**, carrying source, time, TTL and digest. The only kind of statement policy may read. |
| **Context** | Harness-authored part of the envelope: actor identity, delegation chain, environment, facts, policy bundle version, trace IDs. |
| **Envelope digest** | SHA-256 over the canonical (RFC 8785 JCS) serialization of the full envelope. |
| **Policy decision point (PDP)** | Deterministic policy engine (OPA/Rego) evaluating authorization, environment, evidence and approval rules. |
| **Critic** | A narrow, versioned verifier that checks one class of invariant and returns a typed result. |
| **Critic bank** | The set of critics registered for an action class. |
| **Hard critic** | A critic whose `FAIL` produces `DENY` or `REPAIR`. **Soft critic:** a critic whose result is advisory (warn/rank/log). |
| **Verifier result** | Output of one critic or the PDP: `PASS`, `FAIL`, `UNKNOWN`, `TIMEOUT`, `ERROR`, `NOT_APPLICABLE`. |
| **Verdict** | The harness's decision on an envelope: `ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN` (terminal) or `REPAIR` (non-terminal). |
| **Decision record** | Append-only, hash-chained record of an evaluation, its inputs, results, verdict, approval and execution receipt. |
| **Decision token** | Single-use, short-lived credential bound to an envelope digest, issued only on `ALLOW` (or resolved approval), required by the broker to execute. |
| **Policy enforcement point (PEP) / Broker** | The component that executes a tool call and refuses to do so without a valid decision token. |
| **Action class** | A `(tool, intent)` pair registered with its critic set, rollout mode, approvability and formalizability. |
| **Rollout mode** | `shadow` (evaluate, record, never block), `advisory` (evaluate, record, surface, never block), `enforce` (block). |
| **Trajectory monitor** | A stateful critic (finite-state automaton compiled from temporal properties) over the sequence of actions in a session. |
| **Counterexample** | Typed, non-instructional explanation of a `FAIL`, sufficient for repair. |
| **Mutation fixture** | A deliberately invalid envelope or trajectory that a gate must reject. |

## 3. Scope

### 3.1 Problem statement
Agents are governed on the input side (retrieval, tool discovery, prompts) but rarely on the output side (effects, policy compliance, ordering). Prompt-resident rules lose influence over long trajectories and cannot be audited. Neuroharness closes the output-side gap with executable, external, evidence-producing gates around tool dispatch.

### 3.2 Scope boundaries

#### In scope for v1
- Interception of tool calls from an agent runtime, via an MCP gateway and/or in-process hooks.
- Deterministic policy evaluation (allowlists, identity and delegation, environment boundaries, required evidence, approval thresholds).
- Narrow symbolic critics: SMT contracts over typed arguments; a read-only Prolog rulebase (prototype option); a finite-state trajectory monitor (after certification).
- Typed repair loop with bounded budget.
- Human approval workflow bound to the exact action.
- Decision tokens and a broker that executes nothing without one.
- Hash-chained decision records, replay, and negative mutation fixtures as CI gates.
- Shadow/advisory/enforce rollout per action class.
- One reference workflow (§3.5) with six workflow invariants.

#### Out of scope for v1
- Reasoning-trace / chain-of-thought verification.
- Differentiable or gradient-coupled neurosymbolic components in the control path.
- Enterprise ontologies or knowledge graphs in the critical path.
- General factual-claim verification.
- LLM-as-judge presented as independent verification (an LLM MAY be a *soft* critic; it MUST NOT be a hard critic).
- Governing model *inputs* (prompt assembly, retrieval); only the tool-result *return path* is sanitized (§6, `FR-63`).

### 3.3 Scope disclaimers (what the harness does not guarantee)
1. It guarantees only the invariants that are encoded, for the facts that are trusted. Unencoded risks are unmitigated.
2. It does not establish that a tool *did* what the receipt says; it records the receipt the broker observed.
3. It does not make an agent *succeed*; sound checking increases the share of proposals that are repaired, abstained or denied.
4. Trajectory-monitor coverage is bounded by the governed model's behaviour distribution and must be re-certified per model.
5. Schema validity is not semantic validity.

### 3.4 Personas
| Persona | Needs |
|---|---|
| Policy owner (platform/security engineer) | Author, test, version and roll out rules without touching agent code. |
| Agent developer | Integrate in hours; receive typed counterexamples; debug via decision records. |
| Approver (release manager, on-call) | See exactly what is proposed, why it needs approval, all abstentions and counterexamples; approve within a bounded window. |
| Auditor / compliance | Reconstruct any decision from records; prove gates were tested. |
| SRE / operator | Latency, availability, fail-closed behaviour, rollout modes, alerting. |

### 3.5 Reference workflow for v1 (decision `P0-02`)
Default candidate: **service deployment**. An agent proposes `deployment.apply(service, version, target)`. Alternatives (data-migration apply; payment refund) are evaluated in Phase 0. Workflow invariants for the default:

| ID | Class | Statement |
|---|---|---|
| `WF-01` | Authorization | Only tools registered in the allowlist for the actor's environment may be dispatched. |
| `WF-02` | Environment | `target = production` requires resolved approval; an unregistered target is denied. |
| `WF-03` | Authorization | The delegation chain MUST include a human principal holding the deploy right for `(service, target)`. |
| `WF-04` | Evidence | A CI-success fact for the exact `(service, version)` MUST exist, from the registered CI provider, fresher than its TTL. |
| `WF-05` | Precondition | A change-approval fact bound to `(service, version, target)` MUST exist for production targets. |
| `WF-06` | Temporal | The approval event precedes dispatch; dispatch occurs inside the change window; at most one deployment of a service is in flight per target. |

Typed contract (SMT critic, `FR-51`): `version` is a valid SemVer and greater than the currently deployed version unless `intent = rollback` (which is a distinct action class requiring approval); `replicas ≤ quota(target)`.

## 4. Harness invariant registry

| ID | Invariant | Source | Constitution |
|---|---|---|---|
| `INV-01` | Model output, confidence and reasoning are advisory; never an allow signal. (Adopted from upstream `INV-16`; mapping to be confirmed, `OQ-01`.) | Research §Exec. Conclusion; review m1, m9 | Art. I |
| `INV-02` | The control plane runs outside the governed runtime and is not modifiable by it. (Adopted from upstream `DEC-008`; `OQ-01`.) | Research §Exec. Conclusion | Art. I, VI |
| `INV-03` | Fail closed: no evaluation, no execution. | Review M4 | Art. II |
| `INV-04` | No execution without a valid, unexpired, unused decision token bound to the executed envelope digest. | Review M2 | Art. II, III |
| `INV-05` | No decision token without a durably persisted decision record. | Review M4 | Art. III |
| `INV-06` | Verifiers and the PDP are read-only, typed, non-instructional and credential-free. | Research §Security | Art. VI |
| `INV-07` | Every hard gate has at least one killing mutation fixture in CI. | Research §Evaluation | Art. IV |
| `INV-08` | Policy and hard critics read facts and harness context only; never agent claims. | Review M3 | Art. I |
| `INV-09` | Same canonical envelope + same facts + same bundle/critic versions ⇒ same verdict. | Review M4 | Art. VIII |
| `INV-10` | Approvals are bound to an envelope digest, expire, and satisfy separation of duties. | Review M9 | Art. IX |

## 5. Verdict semantics

### 5.1 Verifier results
`PASS`, `FAIL` (with typed counterexample and `repairable: bool`), `UNKNOWN` (solver could not decide within theory/bounds), `TIMEOUT`, `ERROR` (critic crashed or misconfigured), `NOT_APPLICABLE` (critic not registered for this action class; recorded, not evaluated).

### 5.2 Verdicts
| Verdict | Terminal | Meaning |
|---|---|---|
| `ALLOW` | yes | All enforced checks passed; a decision token is issued. |
| `DENY` | yes | A hard rule failed non-repairably, or the repair budget is exhausted, or the action class is marked non-approvable and would otherwise require approval. |
| `REQUIRES_APPROVAL` | yes (for the automated path) | Allowed only after a resolved human approval bound to this digest; on approval a token is issued. |
| `ABSTAIN` | yes | The harness could not evaluate: `UNKNOWN`/`TIMEOUT`/`ERROR` from a hard critic, stale or missing fact, engine or bundle failure, unregistered action class in strict mode. |
| `REPAIR` | no | At least one hard critic failed repairably and budget remains; typed counterexamples are returned to the proposer. |

### 5.3 Resolution order (`FR-05`)
Given the PDP result and all critic results for one evaluation:
1. If any hard result is `FAIL` with `repairable = false` → `DENY`.
2. Else if any hard result is `FAIL` with `repairable = true` and `repair_iteration < repair_budget` → `REPAIR` (all counterexamples returned together).
3. Else if any hard result is `FAIL` (budget exhausted) → `DENY` with reason `REPAIR_BUDGET_EXHAUSTED`.
4. Else if any hard result is `UNKNOWN`, `TIMEOUT` or `ERROR`, or any required fact is missing/stale → `ABSTAIN` (unless escalation applies, §5.5).
5. Else if the PDP requires approval → `REQUIRES_APPROVAL`.
6. Else → `ALLOW`.

Soft critics never change the verdict; their results are recorded and may raise the approval requirement only where policy explicitly says so.

### 5.4 Repair budget
Default 3 iterations per `action_id`; configurable per action class, maximum 10 (per the verified LLM-Modulo evaluation). Each iteration produces a new envelope digest and a new decision record linked by `action_id`.

### 5.5 `ABSTAIN` escalation
By default `ABSTAIN` is terminal. An action class MAY declare `abstain_escalates_to_approval: true`; then step 4 yields `REQUIRES_APPROVAL` and the approver MUST be shown every abstention reason. Action classes with reason `POLICY_ENGINE_UNAVAILABLE` or `BUNDLE_INTEGRITY_FAILED` never escalate.

### 5.6 Reason codes
Every non-`ALLOW` verdict carries one or more machine-readable reason codes (`RULE_FAILED:<rule_id>`, `FACT_MISSING:<name>`, `FACT_STALE:<name>`, `SOLVER_UNKNOWN:<critic_id>`, `SOLVER_TIMEOUT:<critic_id>`, `CRITIC_ERROR:<critic_id>`, `POLICY_ENGINE_UNAVAILABLE`, `BUNDLE_INTEGRITY_FAILED`, `SCHEMA_INVALID`, `ACTION_CLASS_UNREGISTERED`, `REPAIR_BUDGET_EXHAUSTED`, `APPROVAL_REQUIRED:<rule_id>`, `MONITOR_VIOLATION:<property_id>`). Reason payloads are typed and never contain free text generated by a model.

## 6. Requirements

### 6.1 Interception and envelope
| ID | Requirement | Verify |
|---|---|---|
| `FR-01` | The harness MUST intercept every tool call in registered action classes before dispatch, via (a) an MCP gateway between agent host and tool servers and (b) an in-process hook adapter for agent frameworks. Calls that bypass the gateway MUST be impossible by deployment (broker holds the only credentials). | Test, Inspection |
| `FR-02` | Every intercepted call MUST be normalized into an action envelope conforming to `schemas/action-envelope.schema.json`; schema failure MUST produce reason `SCHEMA_INVALID` and no dispatch. | Test, Fixture `MUT-07` |
| `FR-03` | The envelope MUST separate `proposal` (agent-authored) from `context` (harness-authored). The harness MUST overwrite any context-shaped fields present in the proposal. | Test |
| `FR-04` | The harness MUST compute the envelope digest over the canonical JSON (RFC 8785) of the full envelope and stamp `action_id` (UUIDv7), `policy_bundle_version`, `policy_bundle_digest`, and critic versions before evaluation. | Test |
| `FR-05` | Verdicts MUST be resolved exactly as §5.3. | Test (table-driven over all result combinations) |

### 6.2 Facts and provenance
| ID | Requirement | Verify |
|---|---|---|
| `FR-10` | Facts MUST be obtained by the harness from registered fact providers; each fact carries `name`, `value`, `source`, `fetched_at`, `ttl_seconds`, `provider_version`, `digest`. | Test |
| `FR-11` | A rule that requires a fact MUST declare the fact's maximum age; a fact older than that MUST be treated as missing (`FACT_STALE`). | Test, Fixture `MUT-14` |
| `FR-12` | Agent claims MUST be recorded in the decision record and MUST NOT be readable by PDP rules or hard critics (enforced by input-document construction, not convention). | Test, Fixture `MUT-17` |
| `FR-13` | Fact-provider failure or timeout MUST yield `ABSTAIN` for rules that need the fact; providers are part of the TCB inventory (`04-threat-model.md` §3). | Test |

### 6.3 Decision binding and tokens
| ID | Requirement | Verify |
|---|---|---|
| `FR-20` | On `ALLOW` (or on resolved approval) the harness MUST issue a decision token containing `decision_id`, `envelope_digest`, `policy_bundle_digest`, `issued_at`, `expires_at` (default 60 s; configurable per action class), signed or MAC'd with a harness-held key. | Test |
| `FR-21` | The broker MUST recompute the digest of the envelope it is about to execute and MUST refuse execution unless a token with that exact digest is presented, unexpired and unused. | Test, Fixtures `MUT-09`, `MUT-10` |
| `FR-22` | Tokens MUST be single-use; the broker MUST record consumption atomically before dispatch. | Test |
| `FR-23` | A decision token MUST NOT be issued until the decision record has been durably persisted (`INV-05`). | Test, Fixture `MUT-13` |
| `FR-24` | The execution receipt (`started_at`, `finished_at`, `status`, `result_digest`, broker identity) MUST be appended to the decision record; a missing receipt after `expires_at` MUST raise an operational alert. | Test |

### 6.4 Action-class registry and domain gating
| ID | Requirement | Verify |
|---|---|---|
| `FR-30` | The harness MUST maintain a versioned action-class registry mapping `(tool, intent)` to: critic set (hard/soft), rollout mode, approvability (`approvable`, `never_approvable`), `abstain_escalates_to_approval`, repair budget, token TTL, required facts. | Inspection, Test |
| `FR-31` | Unregistered action classes MUST receive policy-only evaluation in `permissive` deployments and `ABSTAIN` (`ACTION_CLASS_UNREGISTERED`) in `strict` deployments; the mode is a deployment setting defaulting to `strict`. | Test |
| `FR-32` | Each registered critic entry MUST reference the natural-language source requirement (`WF-` ID or policy doc) it encodes (Art. V). | Inspection |
| `FR-33` | Registry changes MUST be versioned and signed like policy bundles (`SEC-05`). | Inspection |

### 6.5 Approvals
| ID | Requirement | Verify |
|---|---|---|
| `FR-40` | `REQUIRES_APPROVAL` MUST create an approval request bound to the envelope digest with states `requested → pending → approved | rejected | expired | superseded`. | Test |
| `FR-41` | Approval requests MUST expire (default 24 h; per action class) and MUST be superseded if a new envelope for the same `action_id` is evaluated. | Test, Fixture `MUT-11` |
| `FR-42` | The approver MUST be a human principal from a registered approver group for the action class; the agent, the proposing principal and any principal in the delegation chain MUST be rejected as approver. | Test, Fixture `MUT-18` |
| `FR-43` | The approval UI/API MUST present: the full proposal, all facts with freshness, all reason codes, all counterexamples, all abstentions, the rollout mode, and the exact digest being approved. | Inspection |
| `FR-44` | An approval MUST be recorded with approver identity, method, time, and digest; approval of a digest that no longer matches the envelope MUST be void. | Test, Fixture `MUT-11` |
| `FR-45` | Action classes marked `never_approvable` MUST resolve to `DENY` where approval would otherwise be required. | Test |
| `FR-46` | Approvals MUST NOT be reusable across envelopes, sessions or action IDs. | Test |

### 6.6 Critics
| ID | Requirement | Verify |
|---|---|---|
| `FR-50` | The PDP MUST evaluate Rego policy bundles that are versioned, signed, unit-tested and linted; the PDP input document MUST be constructed by the harness from context only (`INV-08`). | Test, Inspection |
| `FR-51` | SMT critics MUST use bounded, decidable theories (v1: quantifier-free linear integer arithmetic, bit-vectors, enumerations), with a per-call timeout (default 200 ms) and a declared contract per action class; `unknown`/timeout MUST map to `UNKNOWN`. | Test, Fixture `MUT-08` |
| `FR-52` | The trajectory monitor MUST be a finite-state automaton compiled from declared temporal properties over the session's action sequence and execution outcomes; its state MUST be persisted per session and restored on restart; loss of state MUST yield `ABSTAIN` for monitored classes. | Test |
| `FR-53` | The monitor MUST NOT become a blocking (`enforce`) gate for an action class until it has passed the certification protocol in `05-evaluation-plan.md` §6 (entropy preflight, coverage and false-block measurement). | Inspection, Measurement |
| `FR-54` | Monitor certification MUST be repeated whenever the governed model, its version, or its system prompt family changes; the registry MUST record the certified model identity, and a mismatch MUST demote the monitor to `advisory`. | Test |
| `FR-55` | Every critic MUST declare `version`, `source_requirement`, `hard|soft`, `timeout_ms`, and MUST return a result conforming to the decision-record schema. | Test |
| `FR-56` | A `FAIL` from a hard critic MUST include a typed counterexample: failing property ID, offending fields, expected domain or relation, and `repairable`. | Test |

### 6.7 Verifier isolation and MCP
| ID | Requirement | Verify |
|---|---|---|
| `FR-60` | Critics and the PDP MUST run without credentials for any production system and without network egress except to registered fact providers. | Inspection, Test |
| `FR-61` | When a verifier is exposed via MCP it MUST be brokered, allowlisted, read-only, with typed input and output schemas; outputs MUST be structured data, never natural-language instructions. | Inspection, Test |
| `FR-62` | The Prolog verifier (prototype option) MUST load a harness-owned, signed rulebase at startup; the agent MUST only be able to submit typed queries; any load/consult/replace/assert capability MUST be absent from the exposed interface. Results are non-authoritative (soft) until promoted by the registry after mutation testing. | Inspection, Test |
| `FR-63` | Tool results returned to the governed runtime MUST pass through the broker, be typed per the tool's output schema, be size-bounded, and be tagged as untrusted data in the record. | Test |

### 6.8 Evidence and audit
| ID | Requirement | Verify |
|---|---|---|
| `FR-70` | Every evaluation MUST produce a decision record conforming to `schemas/decision-record.schema.json`, including `prev_record_hash` forming a per-tenant hash chain. | Test |
| `FR-71` | Records MUST be replayable: given record + bundle version + critic versions, the harness reproduces the verdict (`INV-09`). | Test (golden corpus) |
| `FR-72` | Records MUST include per-rule outcomes, critic results and durations, verdict, reason codes, mode, trace ID, token ID, approval, receipt and human override. | Test |
| `FR-73` | The evidence store MUST support export in a documented format for audit and for the reference evidence pack. | Test |
| `FR-74` | Personal data in records MUST be limited to identifiers required for attribution; free-text model output MUST be stored only as a digest plus an optional redacted excerpt under a retention policy (`NFR-16`). | Inspection |

### 6.9 Rollout modes and operations
| ID | Requirement | Verify |
|---|---|---|
| `FR-80` | Each action class MUST have a rollout mode; `shadow` and `advisory` never block and always record; `enforce` blocks. Mode changes are versioned registry changes. | Test, Fixture `MUT-16` |
| `FR-81` | The harness MUST expose metrics and traces per `02-technical-plan.md` §7 (verdict counts by class and reason, latency percentiles per stage, token issuance/consumption, approval latency, monitor state). | Measurement |
| `FR-82` | A health endpoint MUST report bundle version/digest, registry version, critic versions and clock source status; unhealthy state MUST cause `ABSTAIN`, not `ALLOW`. | Test |
| `FR-83` | Policy bundles, registry and critic packs MUST be hot-reloadable with atomic switchover and recorded version transitions. | Test |

### 6.10 Repair
| ID | Requirement | Verify |
|---|---|---|
| `FR-90` | `REPAIR` MUST return all counterexamples from the iteration together, in typed form, to the governed runtime through the gateway response (never injected into the model's context as free text by the harness). | Test |
| `FR-91` | Repair iterations MUST be linked by `action_id` in records and MUST count toward the budget even if the proposal is unchanged. | Test, Fixture `MUT-15` |
| `FR-92` | Repair success rate and iterations-to-allow MUST be measured per action class (`05-evaluation-plan.md` §3). | Measurement |

### 6.11 Non-functional requirements
| ID | Requirement | Target (H = hypothesis to be measured in Phase 1–2) | Verify |
|---|---|---|---|
| `NFR-01` | Added latency, policy-only path (schema + PDP + record), p95 | ≤ 50 ms (H) | Measurement |
| `NFR-02` | Added latency with SMT critic, p95 / p99 | ≤ 300 ms / ≤ 600 ms (H) | Measurement |
| `NFR-03` | Added latency, trajectory monitor step, p95 | ≤ 10 ms (H) | Measurement |
| `NFR-04` | Throughput per gateway instance | ≥ 200 evaluations/s sustained (H) | Measurement |
| `NFR-05` | Decision-record write durability before token issue | 100% (fsync or replicated ack) | Test |
| `NFR-06` | Horizontal scalability | Stateless gateway/PDP; monitor state and token nonces in a shared store | Inspection |
| `NFR-07` | Deterministic replay of golden corpus | 0 diffs per release | Test |
| `NFR-08` | Policy bundle reload time | ≤ 5 s, atomic | Test |
| `NFR-09` | Integration effort for a new agent framework via hook adapter | ≤ 1 engineer-day (H) | Measurement |
| `NFR-10` | Availability posture | Fail closed: harness unavailability never results in unguarded execution; SLO target 99.9% for the decision API | Test, Measurement |
| `NFR-11` | PDP unavailable or bundle integrity failure | `ABSTAIN` with reason; alert within 60 s | Test, Fixture `MUT-12` |
| `NFR-12` | Audit store unavailable | No tokens issued; alert | Test, Fixture `MUT-13` |
| `NFR-13` | Trusted clock unavailable | Temporal/window rules `ABSTAIN` | Test |
| `NFR-14` | Concurrency | Token consumption and monitor transitions are linearizable per session/resource key | Test (property-based) |
| `NFR-15` | Multi-tenancy | Records, keys, bundles and registries are tenant-scoped | Inspection |
| `NFR-16` | Retention and privacy | Records retained per configured policy (default 400 days); personal data minimized (`FR-74`); erasure requests handled by crypto-shredding tenant/principal keys without breaking the hash chain | Inspection, Test |
| `NFR-17` | Key management | Token and bundle-signing keys in a KMS/HSM or equivalent; rotation without downtime | Inspection |
| `NFR-18` | Observability | OpenTelemetry traces and metrics; structured logs; no secrets or model free text in logs | Inspection |
| `NFR-19` | Portability | Runs as a sidecar/container; no cloud-specific dependency in the core | Inspection |
| `NFR-20` | Supportability | Every verdict is explainable from its record without access to the model | Test |

### 6.12 Security requirements
| ID | Requirement | Threats (`04-threat-model.md`) |
|---|---|---|
| `SEC-01` | Policy inputs are constructed exclusively from harness context (`INV-08`). | T-01, T-02 |
| `SEC-02` | Broker holds the only credentials to tools; the governed runtime holds none. | T-03 |
| `SEC-03` | Decision tokens are digest-bound, single-use, short-lived, and keyed (`FR-20`–`FR-23`). | T-04, T-05 |
| `SEC-04` | Approvals are digest-bound with separation of duties (`FR-42`, `FR-44`). | T-06, T-07 |
| `SEC-05` | Policy bundles, registry and critic packs are signed; the PDP refuses unsigned or mismatched bundles. | T-08, T-09 |
| `SEC-06` | Hard-gate policy changes require two-person review and a killing fixture (`INV-07`). | T-09, T-10 |
| `SEC-07` | Verifier outputs are typed data; the gateway strips or rejects any string-typed field that is not enumerated in the output schema. | T-11 |
| `SEC-08` | Tool results are typed, size-bounded and tagged untrusted (`FR-63`). | T-12 |
| `SEC-09` | Fact providers are authenticated, versioned and rate-limited; facts are digested. | T-13, T-14 |
| `SEC-10` | Decision records are hash-chained and periodically anchored (signed checkpoint). | T-15 |

## 7. Failure-mode table (`INV-03`)

| Condition | Verdict / behaviour | Reason code | Test |
|---|---|---|---|
| Envelope fails schema | No evaluation, no dispatch | `SCHEMA_INVALID` | `MUT-07` |
| Action class unregistered (strict) | `ABSTAIN` | `ACTION_CLASS_UNREGISTERED` | `A-19` |
| PDP unreachable | `ABSTAIN`; alert | `POLICY_ENGINE_UNAVAILABLE` | `MUT-12` |
| Bundle signature/digest mismatch | `ABSTAIN`; alert; refuse reload | `BUNDLE_INTEGRITY_FAILED` | `MUT-12` |
| Required fact missing | `ABSTAIN` | `FACT_MISSING` | `MUT-04` |
| Required fact stale | `ABSTAIN` | `FACT_STALE` | `MUT-14` |
| Fact provider timeout | `ABSTAIN` | `FACT_MISSING` | `A-14` |
| Hard critic `UNKNOWN`/`TIMEOUT` | `ABSTAIN` | `SOLVER_*` | `MUT-08` |
| Hard critic `ERROR` | `ABSTAIN`; alert | `CRITIC_ERROR` | `A-20` |
| Soft critic any failure | Recorded; verdict unchanged | — | `A-21` |
| Audit store write fails | No token; `ABSTAIN` returned; alert | `EVIDENCE_UNAVAILABLE` | `MUT-13` |
| Trusted clock unavailable | Temporal rules `ABSTAIN` | `CLOCK_UNAVAILABLE` | `A-22` |
| Monitor state lost | `ABSTAIN` for monitored classes | `MONITOR_STATE_LOST` | `A-23` |
| Token expired / reused / digest mismatch | Broker refuses; record appended; alert on reuse | `TOKEN_INVALID` | `MUT-09`, `MUT-10` |
| Approval expired or superseded | No token; new evaluation required | `APPROVAL_VOID` | `MUT-11` |
| Repair budget exhausted | `DENY` | `REPAIR_BUDGET_EXHAUSTED` | `MUT-15` |
| Concurrent proposals on same resource key | Monitor serializes; second gets `MONITOR_VIOLATION` or waits per policy | `MONITOR_VIOLATION` | `A-24` |

## 8. Acceptance scenarios

Written in Gherkin so they can be executed as behaviour tests. Each maps to a mutation fixture (`MUT-`) or a positive scenario.

```gherkin
Feature: Deterministic gates on the deployment reference workflow

  Background:
    Given policy bundle "deploy-v1" is loaded and its signature verifies
    And action class ("deployment.apply", "deploy_service") is registered in enforce mode
    And the CI provider reports success for ("example-api", "1.4.2") 3 minutes ago with TTL 15 minutes

  Scenario: A-01 Undeclared tool is denied (WF-01, MUT-01)
    When the agent proposes tool "shell.exec" with intent "deploy_service"
    Then the verdict is DENY with reason RULE_FAILED:WF-01
    And no decision token is issued

  Scenario: A-02 Production target requires approval (WF-02, MUT-02)
    When the agent proposes deployment.apply(service="example-api", version="1.4.2", target="production")
    And a change-approval fact exists for ("example-api","1.4.2","production")
    Then the verdict is REQUIRES_APPROVAL
    And an approval request bound to the envelope digest is created

  Scenario: A-03 Missing human principal in delegation chain is denied (WF-03, MUT-03)
    Given the delegation chain is ["workflow:release"] only
    When the agent proposes deployment.apply to "staging"
    Then the verdict is DENY with reason RULE_FAILED:WF-03

  Scenario: A-04 Missing CI evidence abstains (WF-04, MUT-04)
    Given no CI fact exists for ("example-api","1.4.3")
    When the agent proposes deployment.apply(version="1.4.3", target="staging")
    Then the verdict is ABSTAIN with reason FACT_MISSING:ci_result

  Scenario: A-05 Failed CI is denied (WF-04, MUT-05)
    Given the CI provider reports failure for ("example-api","1.4.2")
    When the agent proposes deployment.apply(version="1.4.2", target="staging")
    Then the verdict is DENY with reason RULE_FAILED:WF-04

  Scenario: A-06 Deployment before approval event is denied by the monitor (WF-06, MUT-06)
    Given the monitor for session S has not observed approval_granted(example-api,1.4.2,production)
    When the agent proposes deployment.apply(target="production") with a forged approval claim
    Then the verdict is DENY with reason MONITOR_VIOLATION:WF-06
    And the claim is recorded as an agent claim, not a fact

  Scenario: A-07 Malformed argument fails serialization before dispatch (MUT-07)
    When the agent proposes deployment.apply with an extra argument "force": true
    Then the envelope is rejected with reason SCHEMA_INVALID
    And no evaluation record beyond the schema failure exists

  Scenario: A-08 Solver unknown never becomes allow (MUT-08)
    Given the version contract critic is configured with a 1 ms timeout
    When the agent proposes a well-formed deployment.apply to "staging"
    Then the verdict is ABSTAIN with reason SOLVER_TIMEOUT:version-contract

  Scenario: A-09 Token replay is refused (MUT-09)
    Given an ALLOW token T was consumed for envelope E
    When the broker receives E with token T again
    Then execution is refused with reason TOKEN_INVALID
    And an alert "token reuse" is raised

  Scenario: A-10 Envelope tampering after decision is refused (MUT-10)
    Given an ALLOW token T bound to digest D(E)
    When the broker receives envelope E' with target changed to "production" and token T
    Then execution is refused with reason TOKEN_INVALID

  Scenario: A-11 Approval is void if the envelope changes (MUT-11)
    Given approval request R is pending for digest D(E)
    When the agent submits a repaired envelope E' for the same action_id
    Then R is superseded
    And approving R afterwards issues no token

  Scenario: A-12 Policy engine unavailable never executes (MUT-12)
    Given the PDP is unreachable
    When the agent proposes any registered action
    Then the verdict is ABSTAIN with reason POLICY_ENGINE_UNAVAILABLE
    And no token is issued

  Scenario: A-13 Audit write failure blocks token issuance (MUT-13)
    Given the evidence store rejects writes
    When an envelope would otherwise be allowed
    Then no token is issued and the response reason is EVIDENCE_UNAVAILABLE

  Scenario: A-14 Stale fact abstains (MUT-14)
    Given the CI fact for ("example-api","1.4.2") is 40 minutes old with TTL 15 minutes
    When the agent proposes deployment.apply(version="1.4.2", target="staging")
    Then the verdict is ABSTAIN with reason FACT_STALE:ci_result

  Scenario: A-15 Repair budget exhaustion denies (MUT-15)
    Given the repair budget for the action class is 2
    When the agent submits three consecutive envelopes failing the version contract
    Then the third verdict is DENY with reason REPAIR_BUDGET_EXHAUSTED

  Scenario: A-16 Shadow mode records but never blocks (MUT-16)
    Given the action class is in shadow mode
    When the agent proposes an envelope that would be DENIED in enforce mode
    Then the decision record shows verdict DENY and mode shadow
    And the broker executes the call

  Scenario: A-17 Agent claims never reach policy (MUT-17)
    Given the proposal contains claims {"ci_passed": true, "change_approved": true}
    And no corresponding facts exist
    When the envelope is evaluated
    Then the PDP input document contains no field derived from the claims
    And the verdict is ABSTAIN with reason FACT_MISSING:ci_result

  Scenario: A-18 Proposer cannot approve their own action (MUT-18)
    Given approval request R for an envelope whose delegation chain includes user:123
    When user:123 attempts to approve R
    Then the approval is rejected with reason APPROVER_NOT_ELIGIBLE
  Scenario: A-19 Unregistered action class abstains in strict mode (FR-31)
    Given the deployment is configured strict
    When the agent proposes tool "dns.update" with intent "rotate_record" which is not in the registry
    Then the verdict is ABSTAIN with reason ACTION_CLASS_UNREGISTERED

  Scenario: A-20 Hard critic error abstains and alerts (FR-13, section 7)
    Given the SMT critic worker is misconfigured and raises on evaluation
    When the agent proposes a well-formed deployment.apply to "staging"
    Then the verdict is ABSTAIN with reason CRITIC_ERROR:smt.version-contract
    And an alert "critic error" is raised

  Scenario: A-21 Soft critic failure never changes the verdict (section 5.3)
    Given the Prolog hints critic is registered as soft and times out
    When the agent proposes a well-formed deployment.apply to "staging" that passes all hard checks
    Then the verdict is ALLOW
    And the record shows result TIMEOUT for critic prolog.deploy-hints

  Scenario: A-22 Trusted clock unavailable abstains temporal rules (NFR-13)
    Given the trusted clock provider is unreachable
    When the agent proposes deployment.apply to "production" inside a declared change window
    Then the verdict is ABSTAIN with reason CLOCK_UNAVAILABLE

  Scenario: A-23 Lost monitor state abstains monitored classes (FR-52)
    Given the monitor state row for session S has been deleted
    When the agent proposes deployment.apply in session S
    Then the verdict is ABSTAIN with reason MONITOR_STATE_LOST

  Scenario: A-24 Concurrent deployments of one service are serialized (NFR-14, WF-06)
    Given session S1 holds an in-flight deployment of "example-api" to "staging"
    When session S2 proposes deployment.apply(service="example-api", target="staging")
    Then the verdict for S2 is DENY with reason MONITOR_VIOLATION:WF-06
    And the record for S2 references the in-flight resource key
```

Positive scenarios `A-25` (happy-path staging deploy → `ALLOW` → token → receipt), `A-26` (repair succeeds within budget), `A-27` (production deploy approved by eligible approver within TTL → token) are defined in the same feature file at implementation time and are part of the golden replay corpus.

## 9. Traceability matrix

| Research section | Review item | Requirement(s) | Task(s) | Verification |
|---|---|---|---|---|
| Executive Conclusion (propose/dispose) | — | `INV-01`, `INV-02`, `FR-03`, `SEC-01` | `P1-02`, `P1-04` | `A-17`, `MUT-17` |
| Deterministic outcomes | M1 | §5, `FR-05` | `P1-05` | table-driven test |
| Contract shape | M3, m5 | `FR-02`–`FR-04`, `FR-10`–`FR-13` | `P0-04`, `P1-03`, `P1-06` | `A-04`, `A-14`, `A-17` |
| Reference architecture (broker) | M2 | `FR-20`–`FR-24`, `SEC-03` | `P1-07`, `P1-08` | `A-09`–`A-11` |
| Formalization is dangerous | M8 | `FR-30`–`FR-33`, `FR-51` | `P0-05`, `P1-09`, `P2-01` | `A-08`, `A-19` |
| Deterministic outcomes (approval) | M9 | `FR-40`–`FR-46`, `SEC-04` | `P1-10` | `A-02`, `A-11`, `A-18` |
| Phase 2 (unknown never allows) | M4 | §7, `NFR-10`–`NFR-14` | `P1-11` | `MUT-12`, `MUT-13` |
| Phase 3 (monitor) | M10 | `FR-52`–`FR-54` | `P3-01`–`P3-05` | `05-evaluation-plan.md` §6 |
| Security (MCP) | M5, m14 | `FR-60`–`FR-63`, `SEC-07`, `SEC-08` | `P1-12`, `P2-04` | inspection + tests |
| Compliance evidence | m6 | `FR-70`–`FR-74`, `SEC-10` | `P1-06`, `P4-02` | schema tests, replay |
| Evaluation plan | m7, m8 | `FR-92`, `NFR-01`–`NFR-04` | `P1-13`, `P2-05`, `P4-03` | measurements |
| Product positioning | m12 | §3.3, Art. X | `P4-04` | inspection |

## 10. Open questions

| ID | Question | Owner | Needed by |
|---|---|---|---|
| `OQ-01` | Confirm mapping of upstream `INV-16`/`DEC-008` to `INV-01`/`INV-02`, or link the upstream registry. | Product owner | `P0-01` |
| `OQ-02` | Reference workflow: deployment (default) vs data migration vs refund. Choose by cost-of-failure and fact-provider availability. | Product owner + Security | `P0-02` |
| `OQ-03` | Token key custody: KMS available in target environments? | SRE | `P1-07` |
| `OQ-04` | Is an in-process hook adapter for a specific framework required for the first integration, or is the MCP gateway sufficient? | Agent developer persona | `P1-01` |
| `OQ-05` | Retention default (400 days proposed) and erasure obligations per jurisdiction. | Compliance | `P1-06` |
| `OQ-06` | Whether `advisory` mode surfaces verdicts to the agent (as typed warnings) or only to operators. Default: operators only, to avoid training the agent on gate internals. | Tech lead | `P1-14` |

## 11. Change log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-09-18 | Initial draft derived from the research synthesis and the peer review. |
