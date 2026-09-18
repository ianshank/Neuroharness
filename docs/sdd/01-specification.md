# Neuroharness Specification

**Status:** Draft v0.2 — revised after round-two review · **Date:** 2026-09-18 · **Derived from:** `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` as revised by `docs/review/2026-09-18-peer-review-research-synthesis.md` and `docs/review/2026-09-18-round-2-deep-dive-review.md`

## 1. Purpose and audience

This specification defines *what* Neuroharness v1 does and how its behaviour is verified. It is written for the engineers and agents implementing it, the policy owners who will author rules, the security reviewers, and the auditors who will consume its evidence. Design (*how*) lives in `02-technical-plan.md`.

Requirement language follows RFC 2119 (`MUST`, `SHOULD`, `MAY`). Every requirement has a stable ID and a verification method (`Test`, `Fixture`, `Inspection`, `Measurement`). Rationale that comes from the research synthesis without a checked-in primary source is treated as engineering judgement (`OQ-07`, `OQ-08`).

## 2. Glossary

| Term | Definition |
|---|---|
| **Governed runtime** | The LLM agent process (and its framework) whose tool calls the harness controls. |
| **Harness** | Neuroharness as a whole: gateway, policy decision point, critic bank, broker, evidence store, approval and override services. |
| **Action envelope** | The canonical, typed description of one proposed action: an agent-authored **proposal** and a harness-authored **context**. |
| **Proposal** | The part of the envelope the model generates: intent, tool, arguments, and *claims*. |
| **Claim** | A statement made by the agent (e.g. "CI passed"). Advisory. Never a policy input. |
| **Fact** | A statement obtained by the harness from a registered **fact provider**, carrying source, asserting principal, time, TTL and digest. The only kind of statement policy may read. |
| **Fact provider** | A registered, authenticated component that supplies facts. Part of the trusted computing base. Includes the harness's own approval service (`harness_approval`) and execution-completion source. |
| **Context** | Harness-authored part of the envelope: actor identity, delegation chain, session tree, environment, action class, facts, bundle and critic versions, batch position, trace IDs. |
| **Proposal digest** | SHA-256 over the RFC 8785 (JCS) canonical form of a fixed projection: `proposal.tool`, `proposal.intent`, `proposal.arguments`, `context.actor.agent_id`, `context.actor.principal`, `context.actor.delegation_chain`, `context.actor.environment`, `context.policy_bundle.digest`. The identity of *what is being asked, by whom, in which environment, under which rules*. Approvals bind to it. Deliberately excludes agent version, model identity, mutable action-class knobs, facts and timestamps, so that unrelated churn cannot supersede a pending human approval (`ADR-0020`). |
| **Envelope digest** | SHA-256 over the JCS canonical form of the whole envelope (including facts and timestamps). The identity of *one evaluation*. Tokens bind to it. |
| **Policy decision point (PDP)** | Deterministic policy engine (OPA/Rego) evaluating authorization, environment, evidence and approval rules. |
| **Critic** | A narrow, versioned verifier that checks one class of invariant and returns a typed result. |
| **Verifier** | Collective term for the PDP and the critics. |
| **Hard critic / soft critic** | A hard critic's `FAIL` can produce `DENY` or `REPAIR`; a soft critic's result is advisory (warn, rank, log) and never changes a verdict. |
| **Gate / hard gate** | A rule, critic or broker check whose failure prevents execution. Every hard gate has an `active` negative mutation fixture (`INV-07`). |
| **Verifier result** | Output of one critic or PDP rule: `PASS`, `FAIL`, `UNKNOWN`, `TIMEOUT`, `ERROR`, `NOT_APPLICABLE`. |
| **Verdict** | The harness's decision on an envelope: `ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN` (terminal) or `REPAIR` (non-terminal). |
| **Safety order** | `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`. Adding any failure never moves a verdict toward `ALLOW`. |
| **Decision record** | Append-only, hash-chained record of an evaluation, token issuance, approval, execution receipt, effect verification, override or checkpoint. |
| **Decision token** | Single-use, short-lived credential bound to an envelope digest, carrying the verdict and the rollout mode. Required by the broker to execute. A **shadow token** is a decision token issued in `shadow`/`advisory` mode; it is refused for a class whose current mode is `enforce`. |
| **Broker / policy enforcement point (PEP)** | The component that executes a tool call; holds the only tool credentials; refuses to execute without a valid token and, where declared, a resource lease. |
| **Action class** | A `(tool, intent)` pair registered with its argument schema, effect class, critic set, rollout mode, approvability, escalation rules, repair budget, resource key and batch policy. |
| **Effect class** | `none`, `read`, `write`, `destructive`. Determines the evaluation path and approvability. |
| **Resource key** | Canonical identifier (from the resource-key registry) of the thing an action changes, e.g. `service:example-api/target:production`. Leases and mutual-exclusion properties are keyed on it. |
| **Rollout mode** | `shadow` (evaluate, record, issue a shadow token, never block *on the verdict*), `advisory` (as shadow, plus operator visibility), `enforce` (block), `halted` (deny everything). Infrastructure failures block in every mode. |
| **Deployment strictness** | Deployment setting `unregistered_class_policy: strict | permissive` governing unregistered action classes. |
| **Harness approval** | A human decision recorded by the approval service, bound to a proposal digest, surfaced to policy as the fact `harness_approval`. |
| **Change-approval fact** | External change-management evidence (e.g. a change ticket) supplied by a fact provider. Distinct from harness approval. |
| **Approval event** | The trajectory-monitor event `approval_granted(proposal_digest)` emitted when a harness approval is recorded. |
| **Resolved approval** | A harness approval in state `approved`, unexpired, whose proposal digest and policy-bundle digest match the current evaluation. |
| **Operational override** | An authenticated operator action (`halt_class`, `demote_mode`, `revoke_token`, `void_approval`, `rotate_key`) that never grants an `ALLOW`. Distinct from approval. |
| **Trajectory monitor** | A stateful critic (finite-state automaton compiled from temporal properties) over the sequence of proposals, approvals, dispatches and completions, keyed by session tree and by resource key. |
| **Counterexample** | Typed, non-instructional explanation of a `FAIL`, sufficient for repair. |
| **Mutation fixture** | A deliberately invalid envelope or trajectory that a gate must reject. Lifecycle: `reserved` → `active` → `retired`. |
| **Session tree** | A root session and the sub-agent sessions spawned from it through the harness; `session_root_id` identifies the tree. |

## 3. Scope

### 3.1 Problem statement
Agents are governed on the input side (retrieval, tool discovery, prompts) but rarely on the output side: which effects they cause, under whose authority, with what evidence, in what order. Prompt-resident rules lose influence over long trajectories and cannot be audited. Neuroharness makes **dispatch-time policy compliance** of agent actions deterministic, external, fail-closed and evidence-producing, and adds **effect verification** where an authoritative state fact exists. It does not make an agent more capable or more consistent; it makes its actions governable.

### 3.2 Scope boundaries

#### In scope for v1
- Interception of tool calls from an agent runtime via an MCP gateway (required) and an in-process hook adapter (optional, `OQ-04`).
- Deterministic policy evaluation (allowlists, identity and delegation, environment boundaries, required evidence, approval thresholds).
- Narrow symbolic critics: SMT contracts over typed arguments; a finite-state trajectory monitor (advisory in v1.0, enforce after certification); a read-only Prolog rulebase only if an owner is named (`P2-04`).
- Typed repair loop with bounded budget and rate limit.
- Human approval bound to the exact proposal, with post-approval re-evaluation on fresh facts.
- Decision tokens, resource leases and a broker that executes nothing without them.
- Execution model for synchronous and asynchronous tools, retries and batches.
- Post-execution effect verification where a state fact exists.
- Hash-chained decision records, replay, and negative mutation fixtures as CI gates.
- Shadow / advisory / enforce / halted rollout per action class and per critic.
- Operational overrides with authority rules.
- One reference workflow (§3.5) with its workflow invariants.

#### Out of scope for v1
- Reasoning-trace / chain-of-thought verification.
- Differentiable or gradient-coupled neurosymbolic components in the control path.
- Enterprise ontologies or knowledge graphs in the critical path.
- General factual-claim verification.
- LLM-as-judge as a hard critic (an LLM MAY be a *soft* critic).
- Governing model *inputs* (prompt assembly, retrieval); only the tool-result *return path* is typed and bounded (`FR-63`).
- Per-decision human override of a `DENY` (does not exist in v1; `FR-48`).

### 3.3 Scope disclaimers (what the harness does not guarantee)
1. It guarantees only the invariants that are encoded, for the facts that are trusted. Unencoded risks are unmitigated.
2. It verifies effects only where a registered state fact exists (`FR-57`); otherwise it records the receipt the broker observed.
3. It does not make an agent *succeed*; sound checking increases the share of proposals that are repaired, abstained or denied. `pass^k` is a regression guard, not a target (`05-evaluation-plan.md` §3).
4. Trajectory-monitor coverage is bounded by the governed model's behaviour distribution and must be re-certified per model.
5. Schema validity is not semantic validity.
6. Fail-closed means harness outages are denials of service for governed actions (`NFR-10`, `NFR-21`).

### 3.4 Personas
| Persona | Needs |
|---|---|
| Policy owner (platform/security engineer) | Author, test, version and roll out rules without touching agent code. |
| Workflow owner (product owner for the reference workflow) | Label decisions, own the natural-language invariants. |
| Agent developer | Integrate in hours; receive typed counterexamples; debug via decision records. |
| Approver (release manager, on-call) | See exactly what is proposed and why it needs approval; approve within a bounded window. |
| Operator (SRE) | Latency, availability, fail-closed behaviour, rollout modes, halt/demote levers, alerting. |
| Auditor / compliance | Reconstruct any decision from records; prove gates were tested. |

### 3.5 Reference workflow for v1 (decision `P0-02`)
Default candidate: **service deployment**. An agent proposes `deployment.apply(service, version, target, replicas)`. All four arguments are typed by the class's argument schema; `service` and `target` are enumerations from the resource-key registry (`FR-34`); `version` is pre-parsed by the harness into an integer tuple; `replicas` is a bounded integer. Alternatives (data-migration apply; payment refund) are evaluated in Phase 0.

| ID | Class | Statement | Needs state? |
|---|---|---|---|
| `WF-01` | Authorization | Only tools registered in the allowlist for the actor's environment may be dispatched. | No |
| `WF-02` | Environment / oversight | `target = production` requires a **resolved harness approval** for this proposal digest; an unregistered target is rejected at schema validation. | No (approval is a fact) |
| `WF-03` | Authorization | The delegation chain MUST include a human principal holding the deploy right for `(service, target)`, backed by a verifiable delegation credential (`SEC-13`). | No |
| `WF-04` | Evidence | A CI-success fact for the exact `(service, version)` MUST exist, from the registered CI provider, fresh, and not asserted by any principal in the delegation chain (`SEC-11`). | No |
| `WF-05` | Evidence | A **change-approval fact** (external change record) bound to `(service, version, target)` MUST exist for production targets. | No |
| `WF-06a` | Temporal | The approval event precedes dispatch: a token for a production proposal is issued only after `harness_approval` exists, and the monitor rejects an `executed` event without a preceding `approval_granted` for the same proposal digest. | Yes (monitor) |
| `WF-06b` | Temporal | Dispatch occurs inside the declared change window, evaluated at token issue against the trusted clock. | No (clock fact) |
| `WF-06c` | Mutual exclusion | At most one deployment of a service is in flight per target: enforced by the broker's resource lease (`FR-25`) and, when fresh, the `deploy_state.in_flight` fact. | Yes (lease) |

Typed contract (SMT critic, `FR-51`): `version` is greater than the currently deployed version unless `intent = rollback` (a distinct action class requiring approval with a short TTL, `FR-26`); `replicas ≤ quota(target)`.

`WF-02` and `WF-05` are different things: `WF-05` is evidence that the change was planned; `WF-02` is oversight of this specific action. In Phase 1 (`P1-15`) `WF-01`–`WF-05`, `WF-06b` and `WF-06c` are enforced; `WF-06a` precedence is enforced at token issue from Phase 1 and by the monitor from Phase 3. The Phase 1 exit gate records this residual.

## 4. Harness invariant registry

| ID | Invariant | Source | Constitution |
|---|---|---|---|
| `INV-01` | Model output, confidence and reasoning are advisory; never an allow signal. Model-authored fields may *select* which rules apply (tool, intent, typed arguments); they never *satisfy* an authorization or evidence rule. (Adopted from upstream `INV-16`; `OQ-01`.) | Research; review m1, m9 | Art. I |
| `INV-02` | The control plane runs outside the governed runtime and is not modifiable by it. (Adopted from upstream `DEC-008`; `OQ-01`.) | Research | Art. I, VI |
| `INV-03` | Fail closed: no evaluation, no execution, in every rollout mode. | Review M4; round 2 | Art. II |
| `INV-04` | No execution without a valid, unexpired, unused decision token whose envelope digest, verdict and mode match the executed envelope and the class's current mode. | Review M2; round 2 | Art. II, III |
| `INV-05` | No decision token without a durably persisted evaluation record. | Review M4 | Art. III |
| `INV-06` | Verifiers are read-only toward governed and production systems, typed, non-instructional and credential-free. | Research | Art. VI |
| `INV-07` | Every hard gate has at least one `active` killing mutation fixture in CI. | Research | Art. IV |
| `INV-08` | Policy and hard critics read facts and harness context only; never agent claims. | Review M3 | Art. I |
| `INV-09` | Same canonical envelope + same facts + same bundle/critic/solver versions + same evaluation time ⇒ same verdict. | Review M4; round 2 | Art. VIII |
| `INV-10` | Approvals bind to a proposal digest and a policy-bundle digest, expire, satisfy separation of duties across the session tree, and are followed by re-evaluation on fresh facts. | Review M9; round 2 | Art. IX |
| `INV-11` | Rollout modes change only whether the broker consults the verdict; evaluation, recording and token issuance are identical in every mode. Operational overrides never grant an `ALLOW`. | Round 2 | Art. II, IX |

## 5. Verdict semantics

### 5.1 Verifier results and PDP outcomes
Critic results: `PASS`, `FAIL` (typed counterexample, `repairable: bool`), `UNKNOWN` (solver could not decide within its resource limit), `TIMEOUT` (wall-clock backstop exceeded), `ERROR` (critic crashed or misconfigured), `NOT_APPLICABLE`.

PDP rule outcomes are recorded per rule in `pdp_outcomes` and mapped for resolution as follows:

| PDP outcome | Treated as | Notes |
|---|---|---|
| `deny` | hard `FAIL` with the rule's `repairable` flag and optional counterexample | |
| `abstain` | hard `UNKNOWN` with the rule's reason code | e.g. `FACT_MISSING:<name>` |
| `requires_approval` | input to step 6 | |
| `pass` / `not_applicable` | `PASS` / `NOT_APPLICABLE` | derived from the bundle's rule inventory |

The PDP is recorded in `pdp_outcomes` only, never duplicated in `critic_results`.

### 5.2 Verdicts
| Verdict | Terminal | Meaning |
|---|---|---|
| `ALLOW` | yes | All enforced checks passed; a decision token is issued. Optional informational reason codes MAY be attached. |
| `DENY` | yes | A hard rule failed non-repairably; or the repair budget or rate limit is exhausted; or approval would be required for a class that is not approvable; or the class is halted; or the envelope failed schema validation (`SCHEMA_INVALID`, recorded against the digest of the raw request). |
| `REQUIRES_APPROVAL` | yes (for the automated path) | Allowed only after a resolved harness approval bound to this proposal digest **and** a fresh evaluation that resolves to `ALLOW` (`FR-47`). |
| `ABSTAIN` | yes | The harness could not evaluate: `UNKNOWN`/`TIMEOUT`/`ERROR` from a hard critic, missing/stale fact, provider error, or an infrastructure reason. |
| `REPAIR` | no | At least one hard critic failed repairably, no abstention is present, and budget remains; typed counterexamples and the `action_id` are returned to the proposer. |

### 5.3 Resolution order (`FR-05`)
Inputs: infrastructure status, PDP outcomes, critic results with effective modes, fact status, registry entry (`approvable`, `escalate_on`, `mode`, `repair_budget`), `repair_iteration`, approval state, and **rate-limit state** computed by the pipeline (`FR-93`; passed in as a declared input so the resolver stays a pure function). The procedure is monotone with respect to the safety order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`.

0. **Mode normalization.** A hard critic whose **own declared mode** is `shadow` or `advisory` is treated as soft for resolution; its would-be effect is recorded as `would_be_verdict`. The **class** mode does *not* demote its critics. Evaluation is identical in every mode (`INV-11`): the class mode governs only whether the broker consults the verdict and whether the token is marked `shadow`. If the class mode is `halted` → `DENY` (`CLASS_HALTED`).
1. If any hard result is `FAIL` with `repairable = false` → `DENY`.
2. If the action is rate limited → `DENY` (`REPAIR_RATE_LIMITED`).
3. If any hard result is `FAIL` with `repairable = true` and `repair_iteration ≥ repair_budget` → `DENY` (`REPAIR_BUDGET_EXHAUSTED`).
4. If approval is required and the class is not `approvable` → `DENY` (`APPROVAL_NOT_PERMITTED:<rule_id>`).
5. If any **non-escalating infrastructure reason** is present (§5.5) → `ABSTAIN`, terminal, no repair, no escalation.
6. If any hard result is `UNKNOWN`, `TIMEOUT` or `ERROR`, or any required fact is missing, stale or from a failed provider → `ABSTAIN`. Abstentions block repair (do not spend agent turns while evaluation is incomplete). Escalation to `REQUIRES_APPROVAL` only per §5.5.
7. If any hard result is `FAIL` with `repairable = true` and budget remains → `REPAIR` (all counterexamples returned together).
8. If approval is required: continue when a resolved approval exists for this proposal digest and bundle digest (`harness_approval` fact), else → `REQUIRES_APPROVAL` (`APPROVAL_REQUIRED:<rule_id>`).
9. Otherwise → `ALLOW`.

Every `DENY`-deciding condition is evaluated before every `ABSTAIN`-deciding one, and that ordering is load-bearing. An earlier draft placed the repair-budget and approvability denials *after* the abstention steps, which meant adding an abstention to a settled `DENY` softened it to `ABSTAIN`. That is not merely untidy: an abstention on an escalatable fact in an approvable class escalates to `REQUIRES_APPROVAL`, so an agent whose repair budget was exhausted could let a required fact go stale and convert a definitive denial into a human approval request. Adding a problem would have bought a path to `ALLOW` that did not exist before. A `DENY` that also has abstention reasons carries them as contributing reason codes, so the record still shows everything that was wrong.

Post-resolution: if the evaluation record cannot be durably written, the response is overridden to `ABSTAIN` (`EVIDENCE_UNAVAILABLE`), no token is issued, and the gateway's local write-ahead log replays the record on recovery (`FR-23`). Soft critics never change the verdict.

### 5.4 Repair budget, linkage and rate limit
Default 3 iterations per `action_id`; configurable per action class, maximum 10. A `REPAIR` response carries the `action_id`; a resubmission in the same session for the same `(tool, intent)` while the action is non-terminal is linked to it and counts against the budget even if unchanged. A resubmission after a terminal verdict is a new action and counts against a per-`(session_root, action_class)` cap (default 10 new actions per hour); excess → `DENY` (`REPAIR_RATE_LIMITED`) (`FR-93`).

### 5.5 `ABSTAIN` escalation
`ABSTAIN` is terminal by default. An action class MAY declare `escalate_on: [<reason codes>]`; escalation is allowed only for `SOLVER_UNKNOWN`, `SOLVER_TIMEOUT`, and `FACT_MISSING`/`FACT_STALE` for facts the class marks `escalatable: true` (never for evidence facts marked `required: true`). Escalation applies only if **every** abstention reason present is escalatable and the class is `approvable`; the registry loader rejects `escalate_on` on a non-approvable class. The approver is shown every abstention reason.

**Escalation is additionally refused while any hard critic has failed.** Without this rule the same softening the step order fixes reappears by another route: adding an abstention to a request that would have been `REPAIR` yields `REQUIRES_APPROVAL`, which is *less* safe on the safety order, and substantively it puts a human in front of an action a hard gate has already rejected. A human may be asked to decide what the harness could not evaluate; they may not be asked to overrule what it evaluated and refused.

**Non-escalating infrastructure reasons (fixed set):** `POLICY_ENGINE_UNAVAILABLE`, `BUNDLE_INTEGRITY_FAILED`, `REGISTRY_INTEGRITY_FAILED`, `EVIDENCE_UNAVAILABLE`, `CLOCK_UNAVAILABLE`, `MONITOR_STATE_LOST`, `HARNESS_UNHEALTHY`, `CRITIC_ERROR`, `SCHEMA_INVALID`, `ACTION_CLASS_UNREGISTERED`.

### 5.6 Reason-code catalogue (closed)
Every non-`ALLOW` verdict carries at least one code; `ALLOW` MAY carry informational codes. Suffixes are validated by the gateway against registry rule, critic, fact and property IDs (`SEC-07`); payloads never contain model-generated free text.

`RULE_FAILED:<rule_id>` · `FACT_MISSING:<fact>` · `FACT_STALE:<fact>` · `FACT_PROVIDER_ERROR:<fact>` · `SOLVER_UNKNOWN:<critic_id>` · `SOLVER_TIMEOUT:<critic_id>` · `CRITIC_ERROR:<critic_id>` · `POLICY_ENGINE_UNAVAILABLE` · `BUNDLE_INTEGRITY_FAILED` · `REGISTRY_INTEGRITY_FAILED` · `EVIDENCE_UNAVAILABLE` · `CLOCK_UNAVAILABLE` · `MONITOR_STATE_LOST` · `HARNESS_UNHEALTHY` · `SCHEMA_INVALID` · `ACTION_CLASS_UNREGISTERED` · `CLASS_HALTED` · `REPAIR_BUDGET_EXHAUSTED` · `REPAIR_RATE_LIMITED` · `APPROVAL_REQUIRED:<rule_id>` · `APPROVAL_NOT_PERMITTED:<rule_id>` · `APPROVAL_VOID:<approval_request_id>` · `APPROVER_NOT_ELIGIBLE` · `MONITOR_VIOLATION:<property_id>` · `RESOURCE_BUSY:<resource_key>` · `RETRY_UNRESOLVED:<decision_id>` · `TOKEN_INVALID:<reason>` (reasons: `expired`, `consumed`, `digest_mismatch`, `verdict_mismatch`, `mode_mismatch`, `bundle_stale`, `revoked`, `signature`) · `EFFECT_MISMATCH:<resource_key>` · `BATCH_DEPENDENCY_DENIED:<batch_index>`.

## 6. Requirements

### 6.1 Interception, envelope and identity
| ID | Requirement | Verify |
|---|---|---|
| `FR-01` | The harness MUST intercept every tool call in registered action classes before dispatch via (a) an MCP gateway between agent host and tool servers (MUST) and (b) an in-process hook adapter (SHOULD; `OQ-04`). Calls that bypass the gateway MUST be impossible by deployment: the broker holds the only tool credentials (`SEC-02`). | Test, Inspection |
| `FR-02` | Every intercepted call MUST be validated against the action class's `argument_schema` (JSON Schema, `additionalProperties: false`; every policy-compared argument an enumeration) and the envelope schema before digesting. Failure MUST yield verdict `DENY` with reason `SCHEMA_INVALID`, recorded against the SHA-256 of the raw request bytes, and no dispatch. | Test, Fixture `MUT-07`, `MUT-30` |
| `FR-03` | Context-shaped keys present in the raw tool-call payload MUST be removed before validation and listed in `context.stripped_proposal_keys`; unknown keys inside `arguments` are `SCHEMA_INVALID`. | Test |
| `FR-04` | The harness MUST compute the **proposal digest** over the fixed projection in §2 and the **envelope digest** over the whole envelope, both on JCS-canonical JSON, and stamp `action_id` (UUIDv7), `policy_bundle`, `registry` and critic versions (including solver versions) before evaluation. The projection MUST be declared as data, not as procedure, so that a change to it is reviewable (`ADR-0020`). | Test (published test vectors) |
| `FR-05` | Verdicts MUST be resolved exactly as §5.3; the resolver is a pure function with a table-driven test over all input combinations and a property test for monotonicity under the safety order. | Test |
| `FR-06` | `session_id` MUST be derived server-side from the authenticated agent-host connection (`SEC-12`); the delegation chain MUST be extended only by the identity layer on a harness-mediated sub-agent spawn; sub-agent sessions carry `parent_session_id` and `session_root_id`. Session-scoped limits and monitor properties apply to the session tree. | Test, Fixture `MUT-25` |
| `FR-07` | Batched tool calls MUST carry `batch_id`, `batch_index`, `batch_size` in context and be evaluated per the class `batch_policy` (`independent` or `all_or_nothing`). A member declared dependent on a denied or abstained member MUST be denied (`BATCH_DEPENDENCY_DENIED`). Monitor transitions commit in execution order at the broker, not proposal order. | Test, Fixture `MUT-28` |

### 6.2 Facts and provenance
| ID | Requirement | Verify |
|---|---|---|
| `FR-10` | Facts MUST be obtained by the harness from registered fact providers; each fact carries `name`, `key`, `value` (conforming to the provider's `value_schema`), `source`, `asserted_by` (principal that produced the underlying evidence, when the backing system exposes it), `observed_at`, `fetched_at`, `ttl_seconds`, `provider_version`, `digest`. | Test |
| `FR-11` | A fact is **stale** when `age > min(fact.ttl_seconds, class.required_facts[].max_age_seconds)`, with age measured by the trusted clock at evaluation time. Stale or missing required facts MUST be passed to the PDP and critics as status-only stubs (no `value`) so that no rule can read a stale value. | Test, Fixture `MUT-14` |
| `FR-12` | Agent claims MUST be recorded in the decision record and MUST NOT be readable by PDP rules or hard critics (enforced by input-document construction and a schema test, not by convention). | Test, Fixture `MUT-17` |
| `FR-13` | Fact-provider failure or timeout MUST yield `ABSTAIN` (`FACT_PROVIDER_ERROR`) for rules that need the fact. | Test |
| `FR-14` | The harness MUST maintain a signed **fact-provider registry**: provider name, backing system, trust level, authentication, version, `value_schema`, and whether facts carry `asserted_by`. Only registered providers may supply facts; the approval service (`harness_approval`) and the execution-completion source are registered providers. | Inspection, Test |

### 6.3 Decision binding, tokens, leases and execution
| ID | Requirement | Verify |
|---|---|---|
| `FR-20` | The decision token is `{token_id (nonce), decision_id, envelope_digest, proposal_digest, policy_bundle_digest, tenant_id, mode, verdict, record_hash, issued_at, expires_at, key_id, key_alg, shadow}` signed with a harness-held key (`NFR-17`). In `enforce` mode a token is issued only on `ALLOW` (including an approval-satisfied fresh evaluation, `FR-47`); in `shadow`/`advisory` a token with `shadow: true` is issued on every verdict. Default TTL 60 s, per action class. This list is authoritative; plan and ADRs reference it. | Test |
| `FR-21` | The broker MUST recompute the envelope digest of what it will execute and MUST refuse execution unless a token is presented that is unexpired and unconsumed, whose signature verifies, whose `envelope_digest`, `tenant_id` match, whose `verdict = ALLOW` if the class's **current** registry mode is `enforce`, whose `mode` equals the class's current mode, whose `policy_bundle_digest` is current (or within the configured grace window, `FR-84`), whose `token_id`/`key_id` are not on the revocation list, and only while the broker's clock source is healthy. Refusals are recorded with `TOKEN_INVALID:<reason>`. | Test, Fixtures `MUT-09`, `MUT-10`, `MUT-20`, `MUT-36` |
| `FR-22` | Tokens MUST be single-use; consumption is an atomic conditional insert into the nonce store before dispatch. Duplicate delivery (same token, same broker, within TTL, identical envelope) MUST be distinguished from replay in records and alerting. | Test (concurrency) |
| `FR-23` | No token MAY be issued before the evaluation record has been durably persisted (`INV-05`). Token issuance MUST be appended as a `token_issued` record linked by `decision_id`; the evaluation record contains no token. If the evidence store is unavailable, no token is issued, the response is `ABSTAIN` (`EVIDENCE_UNAVAILABLE`), and the gateway's local write-ahead log is replayed on recovery. | Test, Fixture `MUT-13` |
| `FR-24` | The execution receipt (`started_at`, `finished_at`, `status`, `result_digest`, `result_size_bytes`, `job_handle` if asynchronous, broker identity, connector) MUST be appended; a missing receipt after `expires_at` MUST raise an operational alert. | Test |
| `FR-25` | For classes declaring a `resource_key`, the broker MUST acquire a per-`(tenant, resource_key)` lease atomically with token consumption and release it on completion (or lease timeout); a second consumer MUST be refused with `RESOURCE_BUSY` and recorded. This is a broker gate and does not depend on monitor certification. | Test, Fixture `MUT-31` |
| `FR-26` | Connectors MUST declare `sync` or `async`. Async execution returns a `job_handle` in the receipt; completion is observed through the registered execution-completion fact (`execution_completion`) and appended as a completion record; temporal properties declare whether they bind to dispatch or completion; the lease is held until completion or a class-declared timeout. A `rollback`/abort action class MUST exist with an approval TTL ≤ 15 minutes and MAY carry a standing approval bound to the original proposal digest. | Test, Inspection |
| `FR-27` | `action_id` is the idempotency key. A retry after a `failed`, `timeout` or `refused` receipt is a new evaluation referencing the prior `decision_id`; while the prior outcome is unresolved (`timeout` and no completion fact) the verdict MUST be `ABSTAIN` (`RETRY_UNRESOLVED`) until a fresh state fact resolves it. | Test, Fixture `MUT-27` |

### 6.4 Action-class registry, effect classes and domain gating
| ID | Requirement | Verify |
|---|---|---|
| `FR-30` | The harness MUST maintain a signed, versioned action-class registry mapping `(tool, intent)` to: `argument_schema`, `effect_class`, `resource_key` template, critic set (each with `hard`, `mode`, `timeout_ms`, `source_requirement`), class `mode`, `approvable`, `escalate_on`, `required_facts[]` (`name`, `key`, `max_age_seconds`, `required`, `escalatable`), `repair_budget`, `token_ttl_seconds`, `approval_ttl_seconds`, `approver_groups`, `batch_policy`, `connector_kind` (`sync`/`async`), lease timeout. | Inspection, Test |
| `FR-31` | Unregistered action classes MUST be `ABSTAIN` (`ACTION_CLASS_UNREGISTERED`) when `unregistered_class_policy = strict` (default) and policy-only when `permissive`. | Test, `A-19` |
| `FR-32` | Each registered critic entry MUST reference the natural-language source requirement (`WF-` ID or policy document) it encodes (Art. V). | Inspection |
| `FR-33` | Registry changes MUST be versioned and signed like policy bundles (`SEC-05`); the loader MUST reject inconsistent entries (e.g. `escalate_on` on a non-approvable class; per-critic mode looser than the class mode). | Test |
| `FR-34` | The harness MUST maintain a signed **resource-key registry** (canonical resource identifiers and enumerations, e.g. services, targets) shared by all action classes; argument schemas and temporal properties reference it rather than free strings. | Inspection, Test |
| `FR-35` | Action classes with `effect_class` `none` or `read` receive policy-only evaluation with result size and egress bounds; they are never approvable and never repairable. Reading a tool result is not an action. `destructive` classes MUST be approvable or halted. | Test |

### 6.5 Approvals and operational overrides
| ID | Requirement | Verify |
|---|---|---|
| `FR-40` | `REQUIRES_APPROVAL` MUST create an approval request bound to the **proposal digest** and the policy-bundle digest, with states `requested → pending → approved | rejected | expired | superseded`, `approved → consumed | expired | void`. | Test |
| `FR-41` | Approval requests MUST expire (default 24 h; per class) and MUST be superseded when a new envelope with a **different** proposal digest is evaluated for the same `action_id`. | Test, Fixture `MUT-11` |
| `FR-42` | The approver MUST be a human principal from a registered approver group; the agent, every principal in the delegation chain of any session in the session tree, and the proposing principal MUST be rejected. | Test, Fixtures `MUT-18`, `MUT-33` |
| `FR-43` | The approval disclosure MUST present: the full proposal, the proposal digest, all facts with freshness, all reason codes, counterexamples, abstentions, the rollout mode; the approve request MUST carry the proposal digest and the disclosure digest, and both MUST be recorded. | Inspection, Test |
| `FR-44` | An approval MUST be recorded with approver identity, method, time, proposal digest, bundle digest and disclosure digest; an approval whose proposal digest or bundle digest no longer matches MUST be `void`. The approval service MUST honour `void_approval` overrides. | Test, Fixture `MUT-11` |
| `FR-45` | Classes with `approvable: false` MUST resolve to `DENY` (`APPROVAL_NOT_PERMITTED`) where approval would otherwise be required. | Test, Fixture `MUT-34` |
| `FR-46` | Approvals MUST NOT be reusable across proposal digests, action IDs, sessions or tenants. | Test |
| `FR-47` | A recorded approval MUST trigger a **fresh evaluation** (new facts, current bundle) by the gateway; the approval service is a fact provider supplying `harness_approval{proposal_digest, bundle_digest, approver, decided_at, expires_at}`. A token is issued only if that evaluation resolves to `ALLOW`; if it resolves to `DENY` or `ABSTAIN` the approval becomes `void` with the reason and the approver is notified. The approval service never calls the token service. | Test, Fixture `MUT-22` |
| `FR-48` | Operational overrides (`halt_class`, `demote_mode`, `revoke_token`, `void_approval`, `rotate_key`) MUST be authenticated, recorded, and never grant an `ALLOW`. `halt_class`, `revoke_token` and `void_approval` (tighten-only) MAY be issued by one member of the override group. `demote_mode` MUST be authorized by two distinct override-group principals, neither in any affected delegation chain, MUST carry a reason and ticket reference, and MUST auto-expire within 1 h unless replaced by a signed registry change with two-person review. Per-decision override of a `DENY` does not exist. | Test, Fixture `MUT-26` |
| `FR-49` | A class in mode `halted` MUST resolve every proposal to `DENY` (`CLASS_HALTED`), issue no token of any kind, and cancel pending approval requests. Halt is the incident lever for integrity failures, discovered bypasses and key compromise; demotion is only for false-block spikes (`06-delivery-and-governance.md` §5). | Test, Fixture `MUT-19` |

### 6.6 Critics and effect verification
| ID | Requirement | Verify |
|---|---|---|
| `FR-50` | The PDP MUST evaluate Rego policy bundles that are versioned, signed, unit-tested and linted; the input document MUST be constructed by the harness from context and facts only (`INV-08`); every bundle MUST ship a rule inventory so `pass` outcomes can be recorded; policy-compared arguments MUST be matched against registry enumerations with a default-deny rule. | Test, Inspection |
| `FR-51` | SMT critics MUST use bounded, decidable theories (quantifier-free linear integer arithmetic, bit-vectors, enumerations), a deterministic solver resource limit as the primary bound (Z3 `rlimit`) with a wall-clock backstop; solver `unknown` maps to `UNKNOWN`, backstop exceeded maps to `TIMEOUT`; the solver version is part of the critic version. | Test, Fixture `MUT-08` |
| `FR-52` | The trajectory monitor MUST be a finite-state automaton compiled from declared temporal properties over `proposed`, `approval_granted`, `allowed`, `executed`, `completed`, `denied` events, keyed by resource key (spanning sessions) and by session tree (session properties); state MUST be persisted transactionally and versioned by the property-set digest; loss or version mismatch MUST yield `ABSTAIN` (`MONITOR_STATE_LOST`) for monitored classes. | Test |
| `FR-53` | The monitor MUST NOT be `enforce` for an action class until it has passed the certification protocol (`05-evaluation-plan.md` §6). | Inspection, Measurement |
| `FR-54` | Certification MUST be repeated on governed-model, version or prompt-family change; a mismatch demotes the monitor to `advisory`. | Test |
| `FR-55` | Every critic MUST declare `version`, `source_requirement`, `hard|soft`, `timeout_ms`, and return a result conforming to the decision-record schema; soft critics MAY return a `score`, hard critics MUST NOT. | Test |
| `FR-56` | A hard `FAIL` MUST include a typed counterexample: property ID (registry-validated), offending JSON-pointer paths, scalar values or digests, expected domain or relation, and `repairable`. | Test |
| `FR-57` | For classes with a registered state fact, an **effect critic** MUST run on receipt/completion, comparing the typed result and a fresh state fact against the proposal; a mismatch MUST be recorded (`EFFECT_MISMATCH`), alerted, fed to the monitor, and MAY block subsequent actions on that resource key per policy. | Test, Fixture `MUT-32` |

### 6.7 Verifier isolation and MCP
| ID | Requirement | Verify |
|---|---|---|
| `FR-60` | The PDP and critics MUST run without credentials for any governed or production system and without network egress; facts are supplied by the gateway. A critic's own state store (monitor state) is the only permitted write target. | Inspection, Test |
| `FR-61` | A verifier exposed via MCP MUST be brokered, allowlisted, read-only, with typed input and output schemas; outputs are structured data, never natural-language instructions. | Inspection, Test |
| `FR-62` | The Prolog critic (optional) MUST load a harness-owned, signed rulebase at startup and MUST be invoked by the gateway only; no agent-facing query interface exists in v1; any load/consult/assert/replace capability MUST be absent. It is soft until promoted after mutation testing. | Inspection, Test |
| `FR-63` | Tool results returned to the governed runtime MUST pass through the broker, be typed per the tool's output schema, size-bounded, and tagged untrusted in the record. | Test |

### 6.8 Evidence and audit
| ID | Requirement | Verify |
|---|---|---|
| `FR-70` | Every evaluation MUST produce a decision record conforming to `schemas/decision-record.schema.json` including `proposal_digest`, `envelope_digest`, `action_class`, `session_id`, `session_root_id`, model identity, per-rule outcomes, critic results, fact references, verdict, reason codes, mode, per-stage and end-to-end latency, and `prev_record_hash` forming a per-tenant chain; the canonical envelope MUST be retained content-addressed by `envelope_digest` for the retention period. | Test |
| `FR-71` | Records MUST be replayable: given the record, the retained envelope, the referenced bundle and critic/solver versions, and the record's `timestamp` used as "now", the harness reproduces the verdict (`INV-09`). Historical bundles and critic packs are retained for the retention period. | Test (golden corpus) |
| `FR-72` | Approval, token issuance, execution receipt, completion, effect verification, override and checkpoint events MUST be appended as linked records. | Test |
| `FR-73` | The evidence store MUST support export in a documented format for audit and for the reference evidence pack. | Test |
| `FR-74` | Personal data in records MUST be limited to pseudonymous identifiers required for attribution; model free text is stored only as a digest plus an optional redacted excerpt under retention policy (`NFR-16`). Human labels live in a separate label store outside the hash chain. | Inspection |

### 6.9 Rollout modes and operations
| ID | Requirement | Verify |
|---|---|---|
| `FR-80` | Each action class MUST have a rollout mode (`shadow`, `advisory`, `enforce`, `halted`); each critic MUST have one of `shadow`, `advisory`, `enforce`. The class mode MUST NOT constrain its critics' modes: §5.3 step 0 demotes a hard critic on the critic's **own** declared mode, so an `enforce` critic inside a `shadow` class is the configuration a shadow rollout requires, not one the registry may refuse (`INV-11`, scenario `A-16`). `halted` is the class-level incident lever (`FR-49`) and MUST be rejected on a critic, where it would demote that critic to soft and disarm the gate it names. `shadow` and `advisory` never block **on the verdict** but still require a persisted record and a shadow token; infrastructure failures block in every mode. In `shadow` no approval requests are created and no counterexamples are returned to the agent; `advisory` MAY surface typed warnings per `OQ-06`. Mode changes are signed registry changes. | Test, Fixtures `MUT-16`, `MUT-21` |
| `FR-81` | The harness MUST expose metrics and traces per `02-technical-plan.md` §7, including verdicts by class, mode and reason, `ABSTAIN` rate by reason, per-stage latency, token issuance and refusals, approval latency, lease contention and monitor state. | Measurement |
| `FR-82` | A health endpoint MUST report bundle, registry and critic versions and clock-source status; unhealthy state MUST cause `ABSTAIN` (`HARNESS_UNHEALTHY`), never `ALLOW`. | Test |
| `FR-83` | Policy bundles, registries and critic packs MUST be hot-reloadable with atomic switchover and recorded version transitions. | Test |
| `FR-84` | On a bundle or registry transition: pending approvals bound to the previous bundle digest MUST be re-evaluated under the new bundle (re-requesting approval if still required); monitor state whose property-set digest changed MUST be reset to the declared initial state with a bounded `ABSTAIN` window for affected classes; tokens carrying a superseded bundle digest MUST be refused after a configurable grace window (default 0 s for loosening changes). | Test, Fixture `MUT-29` |

### 6.10 Repair
| ID | Requirement | Verify |
|---|---|---|
| `FR-90` | `REPAIR` MUST return all counterexamples from the iteration together, in typed form, with the `action_id`, through the gateway response; the harness never injects text into the model context. | Test |
| `FR-91` | Repair iterations MUST be linked by `action_id` per §5.4 and count toward the budget even if unchanged. | Test, Fixture `MUT-15` |
| `FR-92` | Repair success rate, iterations-to-allow and repair cost MUST be measured per action class. | Measurement |
| `FR-93` | New actions per `(session_root, action_class)` MUST be rate-limited (default 10/h) with `DENY` (`REPAIR_RATE_LIMITED`) on excess. | Test, Fixture `MUT-35` |

### 6.11 Non-functional requirements
| ID | Requirement | Target (H = hypothesis until measured) | Verify |
|---|---|---|---|
| `NFR-01` | Added latency, policy-only path, p95 | ≤ 50 ms (H); Phase 1 gate: ≤ 100 ms measured on the performance environment at 1× load | Measurement |
| `NFR-02` | Added latency with SMT critic, p95 / p99 | ≤ 300 ms / ≤ 600 ms (H) | Measurement |
| `NFR-03` | Added latency, monitor step, p95 | ≤ 10 ms (H) | Measurement |
| `NFR-04` | Throughput per gateway instance | ≥ 200 evaluations/s sustained (H); per-tenant chain write contention measured (`P1-25`) | Measurement |
| `NFR-05` | Record durability before token issue | 100% (fsync or replicated ack) | Test |
| `NFR-06` | Horizontal scalability | Stateless gateway/PDP; monitor state, leases and nonces in a shared store | Inspection |
| `NFR-07` | Deterministic replay of golden corpus | 0 diffs per release; solver upgrades follow `P2-08` | Test |
| `NFR-08` | Policy bundle reload | ≤ 5 s, atomic | Test |
| `NFR-09` | Integration effort for a new agent framework | ≤ 1 engineer-day (H) | Measurement |
| `NFR-10` | Availability posture | Fail closed; decision API SLO 99.9% requires HA PostgreSQL (`P1-22`); v1 support is business-hours (no on-call) | Test, Measurement |
| `NFR-11` | PDP unavailable / bundle or registry integrity failure | `ABSTAIN`; alert within 60 s | Test, Fixture `MUT-12` |
| `NFR-12` | Evidence store unavailable | No tokens; `ABSTAIN`; local WAL; alert | Test, Fixture `MUT-13` |
| `NFR-13` | Trusted clock unavailable | `ABSTAIN` for every evaluation with required facts, windows, token or approval expiry; broker refuses tokens | Test, `A-22` |
| `NFR-14` | Concurrency | Token consumption, leases and monitor transitions linearizable per key | Test (property-based) |
| `NFR-15` | Multi-tenancy | Records, keys, bundles, registries and leases tenant-scoped | Inspection |
| `NFR-16` | Retention and privacy | Default 400 days; pseudonymous principals; crypto-shredding preserves the chain | Inspection, Test |
| `NFR-17` | Key management | Token, bundle and checkpoint keys in a KMS/HSM; multi-active key IDs; rotation without downtime; algorithm per `ADR-0009` | Inspection, Test |
| `NFR-18` | Observability | OpenTelemetry traces and metrics; structured logs; no secrets, tokens or model free text | Inspection |
| `NFR-19` | Portability | Sidecar/container; no cloud-specific dependency in the core | Inspection |
| `NFR-20` | Supportability | Every verdict explainable from its record without the model | Test |
| `NFR-21` | `ABSTAIN` rate by reason | Alert thresholds per reason; correlated-failure alert when > 50% of evaluations in 5 min abstain for one infrastructure reason | Measurement |
| `NFR-22` | Repair cost | ≤ 2 added agent turns per completed task attributable to `REPAIR`, p95 (H) | Measurement |

### 6.12 Security requirements
| ID | Requirement | Threats (`04-threat-model.md`) |
|---|---|---|
| `SEC-01` | Policy inputs are constructed exclusively from harness context and facts (`INV-08`). | T-01, T-02 |
| `SEC-02` | The broker holds the only tool credentials; the governed runtime holds none. | T-03 |
| `SEC-03` | Decision tokens are digest-, verdict- and mode-bound, single-use, short-lived, keyed and revocable (`FR-20`–`FR-23`). | T-04, T-05, T-24 |
| `SEC-04` | Approvals bind to proposal and bundle digests, satisfy separation of duties across the session tree, and are followed by re-evaluation (`FR-42`, `FR-44`, `FR-47`). | T-06, T-07 |
| `SEC-05` | Policy bundles, registries and critic packs are signed; loaders refuse unsigned or mismatched artifacts. | T-08, T-09 |
| `SEC-06` | Hard-gate policy changes and `demote_mode` require two-person review and an active killing fixture (`INV-07`). | T-09, T-10, T-23 |
| `SEC-07` | Verifier outputs are typed data with registry-validated identifiers; the gateway rejects any string field not enumerated or pattern-bound in the output schema. | T-11 |
| `SEC-08` | Tool results are typed, size-bounded and tagged untrusted (`FR-63`). | T-12 |
| `SEC-09` | Fact providers are registered, authenticated, versioned and rate-limited; facts are digested (`FR-14`). | T-13, T-14 |
| `SEC-10` | Decision records are hash-chained and periodically anchored. | T-15 |
| `SEC-11` | **Fact laundering:** the backing system of an evidence fact provider MUST NOT be writable by any tool registered for the same tenant; where that cannot be guaranteed, facts MUST carry `asserted_by` and rules MUST require `asserted_by ∉ delegation_chain ∪ {agent_id}`. | T-21 |
| `SEC-12` | The gateway authenticates the agent host (mTLS/SPIFFE or OAuth client credentials) and derives `session_id` server-side (`FR-06`). | T-22 |
| `SEC-13` | Every hop in the delegation chain is backed by a verifiable delegation credential (e.g. token exchange with actor claims) validated by the identity resolver; credential digests are recorded. | T-22 |
| `SEC-14` | Operational overrides follow the authority rules in `FR-48`; `demote_mode` is never a single-principal action. | T-23 |

## 7. Failure-mode table (`INV-03`)

| Condition | Verdict / behaviour (all modes unless stated) | Reason code | Test |
|---|---|---|---|
| Envelope or arguments fail schema | `DENY`; recorded against raw-request digest; no dispatch | `SCHEMA_INVALID` | `MUT-07`, `MUT-30` |
| Action class unregistered (strict) | `ABSTAIN` | `ACTION_CLASS_UNREGISTERED` | `A-19` |
| Class halted | `DENY`; no token; pending approvals cancelled | `CLASS_HALTED` | `MUT-19` |
| PDP unreachable | `ABSTAIN`; alert | `POLICY_ENGINE_UNAVAILABLE` | `MUT-12` |
| Bundle or registry signature/digest mismatch | `ABSTAIN`; alert; refuse reload | `BUNDLE_INTEGRITY_FAILED` / `REGISTRY_INTEGRITY_FAILED` | `MUT-12b` |
| Required fact missing / stale / provider error | `ABSTAIN` | `FACT_MISSING` / `FACT_STALE` / `FACT_PROVIDER_ERROR` | `MUT-04`, `MUT-14`, `A-14` |
| Hard critic `UNKNOWN` / `TIMEOUT` | `ABSTAIN` | `SOLVER_UNKNOWN` / `SOLVER_TIMEOUT` | `MUT-08` |
| Hard critic `ERROR` | `ABSTAIN`; alert | `CRITIC_ERROR` | `A-20` |
| Soft critic any failure | Recorded; verdict unchanged | — | `A-21` |
| Evidence store write fails | No token; response `ABSTAIN`; WAL; alert | `EVIDENCE_UNAVAILABLE` | `MUT-13` |
| Infrastructure failure in a `shadow`/`advisory` class | No shadow token; no execution | as above | `MUT-21` |
| Trusted clock unavailable | `ABSTAIN` for evaluations needing age, window or expiry; broker refuses tokens | `CLOCK_UNAVAILABLE` | `A-22` |
| Monitor state lost or property-set version mismatch | `ABSTAIN` for monitored classes | `MONITOR_STATE_LOST` | `A-23` |
| Health endpoint unhealthy | `ABSTAIN` | `HARNESS_UNHEALTHY` | `A-12b` |
| Token expired / consumed / digest, verdict, mode or bundle mismatch / revoked | Broker refuses; recorded; alert on replay | `TOKEN_INVALID:<reason>` | `MUT-09`, `MUT-10`, `MUT-20`, `MUT-36` |
| Approval expired, superseded or void | No token; new evaluation required | `APPROVAL_VOID` | `MUT-11`, `MUT-22` |
| Approval required, class not approvable | `DENY` | `APPROVAL_NOT_PERMITTED` | `MUT-34` |
| Repair budget exhausted / rate limit | `DENY` | `REPAIR_BUDGET_EXHAUSTED` / `REPAIR_RATE_LIMITED` | `MUT-15`, `MUT-35` |
| Concurrent proposals on one resource key | Second consumer refused at the broker | `RESOURCE_BUSY` | `MUT-31` |
| Retry while prior outcome unresolved | `ABSTAIN` | `RETRY_UNRESOLVED` | `MUT-27` |
| Effect mismatch after execution | Recorded; alert; monitor transition; next action on resource key per policy | `EFFECT_MISMATCH` | `MUT-32` |
| Batch member depends on denied member | `DENY` | `BATCH_DEPENDENCY_DENIED` | `MUT-28` |

## 8. Acceptance scenarios

Executable as behaviour tests. Each maps to a mutation fixture (`MUT-`) or a positive scenario. Critic IDs and reason codes are exactly as recorded.

```gherkin
Feature: Deterministic gates on the deployment reference workflow

  Background:
    Given policy bundle "deploy-v1" is loaded and its signature verifies
    And action class ("deployment.apply", "deploy_service") is registered in enforce mode with approvable true
    And the CI provider reports success for ("example-api", "1.4.2") 3 minutes ago with TTL 15 minutes, asserted by ci:pipeline
    And the deploy_state fact reports version "1.4.1" deployed and nothing in flight for ("example-api", "staging")
    And the trusted clock and the evidence store are healthy

  Scenario: A-01 Undeclared tool is denied (WF-01, MUT-01)
    When the agent proposes tool "shell.exec" with intent "deploy_service"
    Then the verdict is DENY with reason RULE_FAILED:WF-01
    And no decision token is issued

  Scenario: A-02 Production target requires harness approval (WF-02, MUT-02)
    Given a change-approval fact exists for ("example-api","1.4.2","production")
    And no harness_approval fact exists for the proposal digest
    When the agent proposes deployment.apply(service="example-api", version="1.4.2", target="production", replicas=3)
    Then the verdict is REQUIRES_APPROVAL with reason APPROVAL_REQUIRED:WF-02
    And an approval request bound to the proposal digest and bundle digest is created

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

  Scenario: A-06 Approval piggybacking is refused (WF-06a, MUT-06)
    Given a harness_approval fact exists for proposal digest Q of a staging deployment
    When the agent proposes production deployment P and presents the approval for Q
    Then the verdict is REQUIRES_APPROVAL with reason APPROVAL_REQUIRED:WF-02
    And the record shows the mismatched approval and no token is issued

  Scenario: A-07 Unknown argument fails serialization before dispatch (MUT-07)
    When the agent proposes deployment.apply with an extra argument "force": true
    Then the verdict is DENY with reason SCHEMA_INVALID recorded against the raw-request digest
    And no PDP or critic evaluation occurs

  Scenario: A-08 Solver timeout never becomes allow (MUT-08)
    Given the critic smt.version-contract is configured with a 1 ms wall-clock backstop
    When the agent proposes a well-formed deployment.apply to "staging"
    Then the verdict is ABSTAIN with reason SOLVER_TIMEOUT:smt.version-contract

  Scenario: A-09 Token replay is refused (MUT-09)
    Given an ALLOW token T was consumed for envelope E
    When the broker receives E with token T again from a different connection
    Then execution is refused with reason TOKEN_INVALID:consumed
    And an alert "token replay" is raised

  Scenario: A-10 Envelope tampering after decision is refused (MUT-10)
    Given an ALLOW token T bound to the envelope digest of E
    When the broker receives envelope E' with target changed to "production" and token T
    Then execution is refused with reason TOKEN_INVALID:digest_mismatch

  Scenario: A-11 Approval is superseded if the proposal changes (MUT-11)
    Given approval request R is pending for proposal digest P
    When the agent submits a repaired envelope with proposal digest P' for the same action_id
    Then R is superseded
    And approving R afterwards issues no token and records APPROVAL_VOID:R

  Scenario: A-12 Policy engine unavailable never executes (MUT-12)
    Given the PDP is unreachable
    When the agent proposes any registered action
    Then the verdict is ABSTAIN with reason POLICY_ENGINE_UNAVAILABLE
    And no token is issued

  Scenario: A-12b Bundle integrity failure never executes (MUT-12b)
    Given the loaded bundle's signature does not verify
    When the agent proposes any registered action
    Then the verdict is ABSTAIN with reason BUNDLE_INTEGRITY_FAILED
    And the health endpoint reports unhealthy

  Scenario: A-13 Audit write failure blocks token issuance (MUT-13)
    Given the evidence store rejects writes
    When an envelope would otherwise be allowed
    Then no token is issued and the response is ABSTAIN with reason EVIDENCE_UNAVAILABLE
    And the record is replayed from the write-ahead log when the store recovers

  Scenario: A-14 Stale fact abstains (MUT-14)
    Given the CI fact for ("example-api","1.4.2") is 40 minutes old with TTL 15 minutes
    When the agent proposes deployment.apply(version="1.4.2", target="staging")
    Then the verdict is ABSTAIN with reason FACT_STALE:ci_result
    And the PDP input contains the fact as a status-only stub

  Scenario: A-15 Repair budget exhaustion denies (MUT-15)
    Given the repair budget for the action class is 2
    When the agent submits three consecutive envelopes for the same action_id failing the version contract
    Then the third verdict is DENY with reason REPAIR_BUDGET_EXHAUSTED

  Scenario: A-16 Shadow mode records but never blocks on the verdict (MUT-16)
    Given the action class is in shadow mode
    When the agent proposes an envelope that would be DENIED in enforce mode
    Then the decision record shows verdict DENY and mode shadow
    And a shadow token is issued after the record is durable and the broker executes the call

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
    Given unregistered_class_policy is strict
    When the agent proposes tool "dns.update" with intent "rotate_record" which is not in the registry
    Then the verdict is ABSTAIN with reason ACTION_CLASS_UNREGISTERED

  Scenario: A-20 Hard critic error abstains and alerts (FR-55, section 7)
    Given the critic smt.version-contract raises on evaluation
    When the agent proposes a well-formed deployment.apply to "staging"
    Then the verdict is ABSTAIN with reason CRITIC_ERROR:smt.version-contract
    And an alert "critic error" is raised

  Scenario: A-21 Soft critic failure never changes the verdict (section 5.3)
    Given the critic prolog.deploy-hints is registered as soft and times out
    When the agent proposes a well-formed deployment.apply to "staging" that passes all hard checks
    Then the verdict is ALLOW
    And the record shows result TIMEOUT for critic prolog.deploy-hints

  Scenario: A-22 Trusted clock unavailable abstains any evaluation with a required fact (NFR-13)
    Given the trusted clock provider is unreachable
    When the agent proposes deployment.apply to "staging"
    Then the verdict is ABSTAIN with reason CLOCK_UNAVAILABLE
    And the broker refuses all tokens while its clock source is unhealthy

  Scenario: A-23 Lost monitor state abstains monitored classes (FR-52)
    Given the monitor state row for resource key service:example-api/target:staging has been deleted
    When the agent proposes deployment.apply in any session
    Then the verdict is ABSTAIN with reason MONITOR_STATE_LOST

  Scenario: A-24 Concurrent deployments of one service are serialized by the lease (FR-25, WF-06c, MUT-31)
    Given session S1 holds the lease for service:example-api/target:staging
    When session S2 is allowed and its broker attempts to consume its token
    Then the broker refuses with reason RESOURCE_BUSY:service:example-api/target:staging
    And S2's receipt is recorded with status refused

  Scenario: A-25 Happy-path staging deployment (positive)
    When the agent proposes deployment.apply(service="example-api", version="1.4.2", target="staging", replicas=3)
    Then the verdict is ALLOW
    And a token_issued record follows the evaluation record in the chain
    And the broker executes and appends an execution receipt with status succeeded
    And the effect critic records a match against the fresh deploy_state fact

  Scenario: A-26 Repair succeeds within budget (positive)
    Given the agent first proposes version "1.4.0"
    When the verdict is REPAIR with counterexample property version-monotonic and the action_id
    And the agent resubmits version "1.4.2" for the same action_id
    Then the verdict is ALLOW and the record shows repair_iteration 1

  Scenario: A-27 Approved production deployment executes on fresh facts (positive, FR-47)
    Given approval request R for proposal digest P is approved by release-manager user:777 within its TTL
    When the gateway re-evaluates P with fresh facts and the harness_approval fact for P
    Then the verdict is ALLOW and a token is issued for the new envelope digest
    And the record links the approval, the original decision and the fresh decision

  Scenario: A-28 Infrastructure failure in a shadow class never executes (MUT-21)
    Given the action class is in shadow mode
    And the evidence store rejects writes
    When the agent proposes any envelope in the class
    Then no shadow token is issued and the broker does not execute
    And the response is ABSTAIN with reason EVIDENCE_UNAVAILABLE

  Scenario: A-29 Approval granted after facts went stale issues no token (MUT-22)
    Given approval request R for proposal digest P is approved 20 hours after the request
    And the fresh evaluation finds the CI fact missing
    When the gateway re-evaluates P
    Then the verdict is ABSTAIN with reason FACT_MISSING:ci_result
    And R is recorded as void with reason APPROVAL_VOID:R and the approver is notified

  Scenario: A-30 Halted class denies everything (MUT-19)
    Given an operator issued halt_class for the action class
    When the agent proposes a well-formed deployment.apply to "staging"
    Then the verdict is DENY with reason CLASS_HALTED
    And no token of any kind is issued

  Scenario: A-31 Token verdict or mode mismatch is refused (MUT-20)
    Given a shadow token with verdict DENY was issued while the class was in shadow mode
    And the class has since been promoted to enforce
    When the broker receives the envelope with that token
    Then execution is refused with reason TOKEN_INVALID:mode_mismatch

  Scenario: A-32 Retry while the prior outcome is unresolved abstains (MUT-27)
    Given the prior execution for action_id X ended with receipt status timeout and no completion fact
    When the agent retries deployment.apply for X
    Then the verdict is ABSTAIN with reason RETRY_UNRESOLVED:<prior decision_id>

  Scenario: A-33 Batch member depending on a denied member is denied (MUT-28)
    Given a batch of two calls where index 1 declares a dependency on index 0
    And index 0 resolves to DENY
    When index 1 is evaluated
    Then the verdict is DENY with reason BATCH_DEPENDENCY_DENIED:0

  Scenario: A-34 Evidence asserted by the delegation chain is not evidence (SEC-11, MUT-23)
    Given the CI fact for ("example-api","1.4.2") is asserted_by user:123
    And user:123 is in the delegation chain
    When the agent proposes deployment.apply(version="1.4.2", target="staging")
    Then the verdict is DENY with reason RULE_FAILED:WF-04

  Scenario: A-35 Target aliasing fails schema validation (FR-02, MUT-30)
    When the agent proposes deployment.apply with target "Production"
    Then the verdict is DENY with reason SCHEMA_INVALID

  Scenario: A-36 Forged delegation chain is rejected (SEC-13, MUT-25)
    Given the agent host presents a delegation chain hop without a verifiable delegation credential
    When the envelope is built
    Then the verdict is DENY with reason RULE_FAILED:WF-03
    And the record shows the unverifiable hop

  Scenario: A-37 Bundle transition re-evaluates pending approvals (FR-84, MUT-29)
    Given approval request R is pending under bundle digest B1
    When bundle B2 is loaded
    Then R is superseded and a new request is created only if B2 still requires approval
    And a token carrying bundle digest B1 is refused with reason TOKEN_INVALID:bundle_stale

  Scenario: A-38 Effect mismatch is recorded and blocks the next action on the resource (FR-57, MUT-32)
    Given a deployment of "1.4.2" was executed with receipt status succeeded
    And the fresh deploy_state fact reports version "1.3.9"
    When the effect critic runs on completion
    Then an effect_verification record with result mismatch and reason EFFECT_MISMATCH is appended
    And the next proposal on the resource key is DENY with reason EFFECT_MISMATCH:service:example-api/target:staging

  Scenario: A-39 Single-principal demotion is rejected (FR-48, MUT-26)
    When one operator issues demote_mode for the action class
    Then the override is rejected and recorded
    And the class mode is unchanged

  Scenario: A-40 Sub-agent session cannot approve the tree's action (FR-42, MUT-33)
    Given session S2 is a child of session S1 whose delegation chain includes user:123
    When user:123 attempts to approve a request raised in S2
    Then the approval is rejected with reason APPROVER_NOT_ELIGIBLE

  Scenario: A-41 Approval required on a non-approvable class denies (FR-45, MUT-34)
    Given the action class has approvable false
    When the agent proposes deployment.apply to "production"
    Then the verdict is DENY with reason APPROVAL_NOT_PERMITTED:WF-02

  Scenario: A-42 New-action rate limit (FR-93, MUT-35)
    Given the session root has created 10 new actions in the class within the hour
    When the agent proposes an eleventh
    Then the verdict is DENY with reason REPAIR_RATE_LIMITED

  Scenario: A-43 Revoked token is refused (FR-21, MUT-36)
    Given token T was revoked by an operator override
    When the broker receives its envelope with T
    Then execution is refused with reason TOKEN_INVALID:revoked

  Scenario: A-45 Monitor rejects execution without a preceding approval event (WF-06a, MUT-24, Phase 3)
    Given the monitor for resource key service:example-api/target:production is in enforce mode after certification
    When a replayed trajectory contains executed(P) without approval_granted(P)
    Then the monitor result is FAIL and the verdict is DENY with reason MONITOR_VIOLATION:WF-06a
```

## 9. Traceability matrix

| Research section | Review item | Requirement(s) | Task(s) | Verification |
|---|---|---|---|---|
| Executive Conclusion (propose/dispose) | — | `INV-01`, `INV-02`, `FR-03`, `SEC-01` | `P1-02`, `P1-04` | `A-17`, `MUT-17` |
| Deterministic outcomes | M1; R2-S3, R2-C1, R2-C7, R2-C8 | §5, `FR-05` | `P1-05` | table-driven + property test |
| Contract shape | M3, m5; R2-S5, R2-S6 | `FR-02`–`FR-04`, `FR-10`–`FR-14`, `SEC-11` | `P0-04`, `P1-03`, `P1-18` | `A-04`, `A-14`, `A-34`, `A-35` |
| Reference architecture (broker) | M2; R2-S2, R2-S4, R2-S8 | `FR-20`–`FR-27`, `SEC-03` | `P1-07`, `P1-08` | `A-09`–`A-11`, `A-24`, `A-31`, `A-32` |
| Formalization is dangerous | M8; R2-B9 | `FR-30`–`FR-35`, `FR-51` | `P0-05`, `P1-09`, `P2-01` | `A-08`, `A-19` |
| Deterministic outcomes (approval) | M9; R2-S2, R2-B1, R2-B2 | `FR-40`–`FR-49`, `SEC-04`, `SEC-14` | `P1-10a`, `P1-10b`, `P1-29` | `A-02`, `A-11`, `A-27`, `A-29`, `A-30`, `A-39`–`A-41` |
| Phase 2 (unknown never allows) | M4; R2-S1 | §7, `NFR-10`–`NFR-13`, `FR-80` | `P1-11`, `P1-14` | `MUT-12`, `MUT-13`, `MUT-21` |
| Phase 3 (monitor) | M10; R2-S4 | `FR-25`, `FR-52`–`FR-54` | `P3-01`–`P3-05` | `05-evaluation-plan.md` §6 |
| Security (MCP) | M5, m14; R2-S10, R2-S11 | `FR-60`–`FR-63`, `SEC-07`, `SEC-08`, `SEC-12`, `SEC-13` | `P1-12`, `P2-04` | inspection + tests |
| Compliance evidence | m6; R2-S13, R2-C9 | `FR-70`–`FR-74`, `SEC-10` | `P1-06a`, `P1-06b`, `P4-02` | schema tests, replay |
| Output-side gap | R2-B3, R2-B4, R2-B6, R2-B7 | `FR-07`, `FR-26`, `FR-27`, `FR-57` | `P2-10`, `P2-11`, `P2-12` | `A-32`, `A-33`, `A-38` |
| Evaluation plan | m7, m8; R2-D6, R2-D7 | `FR-92`, `NFR-01`–`NFR-04`, `NFR-21`, `NFR-22` | `P1-13`, `P1-19`, `P2-05`, `P4-03` | measurements |
| Product positioning | m12; R2-B11 | §3.1, §3.3, Art. X | `P4-04` | inspection |

Round-two finding IDs (`R2-S` security, `R2-C` consistency, `R2-D` delivery, `R2-B` second read) are defined in `docs/review/2026-09-18-round-2-deep-dive-review.md`.

## 10. Open questions

| ID | Question | Owner | Needed by |
|---|---|---|---|
| `OQ-01` | Confirm mapping of upstream `INV-16`/`DEC-008` to `INV-01`/`INV-02`, or link the upstream registry. | Product owner | `P0-01` |
| `OQ-02` | Reference workflow: deployment (default) vs data migration vs refund. | Product owner + Security | `P0-02` |
| `OQ-03` | Key custody and algorithm per target KMS (ECDSA P-256 default; Ed25519 where supported; HMAC single-process only). | SRE | `P0-13` |
| `OQ-04` | Is an in-process hook adapter required for the first integration, or is the MCP gateway sufficient? | Agent developer (named in `P0-12`) | `P1-01a` |
| `OQ-05` | Retention default (400 days proposed) and erasure obligations per jurisdiction. | Compliance (named in `P0-12`) | `P1-06b` |
| `OQ-06` | Whether `advisory` mode surfaces typed warnings to the agent or only to operators. Default: operators only. | Tech lead | `P1-14` |
| `OQ-07` | Primary evidence for the rationale of `ADR-0002`/`ADR-0003`/`ADR-0004` (reasoning-trace TCB expansion; format-instruction degradation; differentiable coupling) before any is cited in the claims document. | Tech lead | `P4-04` |
| `OQ-08` | Locate and check in (or link) the two source analyses and model-perspective transcripts behind the research synthesis. | Product owner | `P0-01` |
| `OQ-09` | Temporal-property compiler: build vs vendor; GPL constraints of MONA/Spot-based LTLf tooling vs the project licence. | Tech lead | `P0-11`, `ADR-0013` |
| `OQ-10` | Named workflow owner, second security reviewer, compliance contact and agent-developer contact. | Delivery lead | `P0-12` |

## 11. Change log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-09-18 | Initial draft derived from the research synthesis and the peer review. |
| 0.7 | 2026-09-18 | `FR-80` still carried the pre-`F-01` rule that a per-critic mode may only be *less* enforcing than the class mode, which the registry implemented. That rule and the corrected §5.3 step 0 cannot both hold: step 0 demotes on the critic's own mode, so forbidding an `enforce` critic inside a `shadow` class makes the `A-16`/`MUT-16` rollout unrepresentable. The class mode no longer constrains its critics; `halted` is refused on a critic, where it would silently disarm the gate instead of arming it. |
| 0.6 | 2026-09-18 | §5.5 now refuses escalation while any hard critic has failed. Implementing the v0.5 reordering exposed a second softening of the same shape by a different route: an abstention added to a `REPAIR` escalated to `REQUIRES_APPROVAL`, asking a human to overrule a gate that had already refused. |
| 0.5 | 2026-09-18 | A property test found §5.3 was not monotone: the repair-budget and approvability denials sat below the abstention steps, so adding an abstention softened a settled `DENY` into an `ABSTAIN`, which an approvable class could then escalate to human approval. Every `DENY`-deciding step now precedes every `ABSTAIN`-deciding one. |
| 0.4 | 2026-09-18 | Implementation found §5.3 step 0 contradicted `INV-11` and `A-16`: as written, a shadow *class* demoted every hard critic, so the verdict itself became mode-dependent and a shadow class could never record the denial it exists to measure. Step 0 now demotes only on a critic's own declared mode. |
| 0.3 | 2026-09-18 | Increment-1 review amendments: proposal-digest projection narrowed and declared as data (`ADR-0020`); rate-limit state added to the resolver's declared inputs so `REPAIR_RATE_LIMITED` has a home without breaking purity (`FR-05`, `FR-93`). |
| 0.2 | 2026-09-18 | Round-two revision: proposal vs envelope digests and post-approval re-evaluation (`FR-04`, `FR-40`–`FR-47`); rollout modes never block on infrastructure failure, `halted` mode and override authority (`FR-48`, `FR-49`, `FR-80`, `INV-11`); resolution order rewritten with safety order, infrastructure step, mode normalization and approvability (§5.3–5.5); closed reason-code catalogue (§5.6); token payload with verdict and mode, revocation, leases, execution model, retries, batches (`FR-20`–`FR-27`, `FR-07`); fact-provider and resource-key registries, `asserted_by`, effect classes (`FR-14`, `FR-34`, `FR-35`, `SEC-11`); session identity and delegation credentials (`FR-06`, `SEC-12`, `SEC-13`); effect verification (`FR-57`); bundle-transition semantics (`FR-84`); repair linkage and rate limit (§5.4, `FR-93`); replay time semantics (`FR-71`); `NFR-21`, `NFR-22`; failure-mode table and scenarios `A-25`–`A-45` completed; `OQ-07`–`OQ-10`. |
