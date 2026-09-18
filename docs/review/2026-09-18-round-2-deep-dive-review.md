# Round-2 Deep-Dive Peer Review: Neuroharness SDD package

| | |
|---|---|
| **Artifacts reviewed** | `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md`, `docs/review/2026-09-18-peer-review-research-synthesis.md` (round 1), and the SDD package at commit `5f2d479` (constitution, specification v0.1, technical plan, work breakdown, threat model, evaluation plan, governance, ADR-0001..0010, both JSON Schemas) |
| **Method** | Four independent reviews with different lenses, run in parallel against the committed package, plus a reviewer pass on shadow mode, approval binding and the resolution order. Reviewers were instructed to break the design, not to praise it. |
| **Review date** | 2026-09-18 |
| **Findings** | 71 (1 critical, 14 high, 33 medium, 23 low), consolidated below |
| **Verdict** | **Major revision required, and applied.** The architecture survives: the propose/dispose split, external deterministic policy, digest-bound tokens, write-ahead evidence and mutation-proved gates all held up under attack. Five defects were serious enough to invalidate parts of the design as written: shadow mode executing on infrastructure failure, approval binding that cannot work with fact freshness, fact laundering through agent-writable evidence systems, free-string target aliasing, and race protection resting on an uncertified monitor. The delivery plan was over capacity by 35 to 40 percent and its mutation gate would have blocked every merge. Specification v0.2 and the accompanying document updates resolve or explicitly defer every finding. |

## 1. How to read this document

Round 1 reviewed the *research*. Round 2 reviews the *specification that answered it*, and re-audits round 1 itself. Finding IDs are namespaced by lens:

| Prefix | Lens | Count |
|---|---|---|
| `R2-S` | Adversarial security: break the design | 15 |
| `R2-C` | Specification consistency and completeness | 20 |
| `R2-D` | Delivery and SDLC realism | 20 |
| `R2-B` | Second read of the research and of round 1 | 15 (+ audit of 24 round-1 findings) |
| `R2-X` | Reviewer's own pass | 6 (merged into the above where they coincide) |

**Disposition** is one of: **Fixed** (v0.2 changes the specification), **Fixed elsewhere** (another document changed), **Deferred** (accepted, tracked as an open question or a post-v1 item), **Rejected** (with reason).

## 2. Critical and high findings

### R2-S1 (Critical) — Shadow and advisory modes executed on infrastructure failure; the rollout ADR made integrity failures a trigger for it
Round 1 required progressive rollout (`ADR-0010`), and v0.1 defined shadow as "evaluate, record, never block". Three consequences, none intended:
1. With the policy decision point down, a shadow-mode class returned `ABSTAIN` and the broker executed anyway. Constitution Article II and `INV-03` were false for every class not yet promoted to enforce, which per the rollout plan is *every* class at the start.
2. With the evidence store down, `FR-23` forbade issuing a token and `FR-80` forbade blocking. The specification was simply contradictory; an implementer would have resolved it by minting a token with no record, hollowing out `INV-05`.
3. `ADR-0010` listed "integrity failures" among the triggers for demoting a class to advisory, so a tampered or unsigned policy bundle, which `§7` says must `ABSTAIN` and refuse to load, would have moved the class into a mode where everything executes.
Shadow tokens also had no requirement of their own, so they sat outside the record-before-token and single-use rules.
**Disposition: Fixed.** `FR-80` and the glossary now say shadow and advisory never block *on the verdict*; a durable record and a token are required in every mode; the ten infrastructure reasons in `§5.5` block in every mode. `FR-20` defines shadow tokens explicitly. `ADR-0010` loses "integrity failures" as a demotion trigger and gains `halted` as the correct lever (`FR-49`). New fixture `MUT-21`, scenario `A-28`.

### R2-S2 / R2-C2 / R2-B1 / R2-X2 (High) — Approval bound to the full envelope digest cannot work
Four reviewers converged here independently. The envelope digest covers `facts[].fetched_at`, `proposed_at`, `trace_id` and `repair_iteration`. Approvals bound to it, with a 24-hour approval time-to-live against fact ages measured in minutes. So either the harness re-fetched facts at approval time, changing the digest and voiding every approval older than the shortest fact lifetime (60 seconds for deploy state), or it issued the token on the frozen digest and executed a production deployment on day-old evidence. The latter is exactly the time-of-check/time-of-use gap that round 1's `M2` claimed to close. The lifecycle diagram also never showed how an approved action reaches the broker: re-invoking the tool creates a new envelope, which supersedes the approval, so nothing ever executes.
**Disposition: Fixed.** Two digests now exist. The **proposal digest** covers the proposal, actor, action class and policy bundle: the thing a human actually approves. The **envelope digest** covers everything and binds the token to one evaluation. `FR-47` makes approval resolution trigger a fresh evaluation on current facts, with the approval service acting as a registered fact provider supplying `harness_approval`. A token is issued only if that fresh evaluation resolves to `ALLOW`. An approval that no longer holds becomes `void` and the approver is notified. Piggybacking stays blocked because any change to proposal, actor or class changes the proposal digest. Scenarios `A-27`, `A-29`; fixture `MUT-22`.

### R2-S5 (High) — Fact laundering: the agent can manufacture its own evidence
The threat model asserted that prompt injection "cannot authorize" because policy reads facts rather than claims. That holds only if the agent cannot influence the facts. Nothing in v0.1 stopped a registered tool from writing to a system that a fact provider reads. An injected agent calls a permitted low-risk tool to move a change ticket to approved, or triggers a trivially passing pipeline for the version it wants to ship; minutes later the provider returns a fresh, authenticated, digested fact and every control passes. Facts also carried no record of *who* produced the underlying evidence, so no rule could exclude the agent's own principals.
**Disposition: Fixed.** `SEC-11` forbids a tenant's registered tools from writing to the backing system of an evidence fact provider, or else requires facts to carry `asserted_by` with rules rejecting evidence asserted by anyone in the delegation chain. `asserted_by` and `observed_at` are added to the fact schema; `WF-04` carries the condition; threat `T-21`, fixture `MUT-23`, scenario `A-34`.

### R2-S6 / R2-X (High) — Target aliasing through a free-form string
`target` was a model-authored string with no enumeration anywhere, and the example policy compared it to the literal `"production"`. `target: "Production"` or `"prod-eu"` would miss the approval rule, miss the (absent) unregistered-target denial, and reach a connector that resolves it to a real environment. The threat model claimed targets were "resolved by the harness, not free arguments", which the example policy contradicted.
**Disposition: Fixed.** `FR-02` requires a per-class `argument_schema` with `additionalProperties: false` and enumerations for every policy-compared argument, validated before digesting. `FR-34` adds a signed resource-key registry that supplies those enumerations. The example policy gains a default-deny rule. `T-17`'s control text is corrected. Fixture `MUT-30`, scenario `A-35`.

### R2-S4 / R2-C3 (High) — Mutual exclusion depended on a monitor that v1 never enforces
`WF-06` (now `WF-06c`) promised at most one deployment in flight per service and target, and scenario `A-24` expected a monitor violation. But `FR-53` forbids the monitor from enforcing before certification, `FR-54` demotes it on any model change, and the registry example shipped it in shadow. Two sessions inside one 60-second deploy-state window would both be allowed and both execute. The claim that token consumption is "linearizable per resource key" was unimplementable because tokens carried no resource key.
**Disposition: Fixed.** `FR-25` moves mutual exclusion to a broker-held lease per tenant and resource key, acquired atomically with token consumption. It is a policy-enforcement-point gate, so monitor certification does not gate it. `WF-06` splits into `WF-06a` (precedence), `WF-06b` (change window) and `WF-06c` (mutual exclusion). `A-24` is rewritten against the lease; fixture `MUT-31`.

### R2-S3 / R2-C1 / R2-C7 (High) — Escalation and approvability holes in the resolution order
Three defects in one procedure. Any abstention could escalate to human approval if a class set the flag, so "CI never ran" or "monitor state lost" became a rubber-stamp; the specification excluded only two reasons from escalation while the ADR said "infrastructure reasons" without defining them. Step 5 never consulted approvability, so a class marked never-approvable still returned `REQUIRES_APPROVAL`, contradicting `FR-45`. And infrastructure failures were in the ADR's procedure but not in the specification's, nor in the resolver's declared inputs.
**Disposition: Fixed.** `§5.3` is rewritten with a mode-normalization step, an explicit infrastructure step that is terminal and never escalates, approvability consulted at the approval step (`APPROVAL_NOT_PERMITTED`), and abstentions blocking repair. `§5.5` names the fixed non-escalating set and restricts escalation to solver uncertainty and facts a class marks escalatable. The registry loader rejects escalation on a non-approvable class. `ADR-0007` is superseded by `ADR-0014`.

### R2-C8 (High) — The monotonicity property had no defined order and was violated
Three documents required that "adding a failure never moves the verdict toward `ALLOW`" without ever defining the order. Under v0.1's procedure, a state that resolved to `ABSTAIN` plus one additional repairable failure became `REPAIR`, which can end in `ALLOW`. The property test would have failed on the specification's own rules.
**Disposition: Fixed.** The safety order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY` is stated in `§2` and `§5.3`, and abstentions now precede repair, so the procedure is monotone as written.

### R2-B3 (High) — The "output-side gap" was only half closed
The research's premise is that agent systems govern inputs but not effects, and the critic bank listed postconditions. But there was no post-execution stage anywhere in the architecture: after execution came evidence, and nothing else. A deployment that returns success while shipping the wrong artifact was invisible, and the specification quietly disclaimed it.
**Disposition: Fixed.** `FR-57` adds an effect critic that runs on receipt or completion, comparing the typed result and a fresh state fact against the proposal, recording `EFFECT_MISMATCH`, alerting, feeding the monitor and optionally blocking the next action on that resource key. `§3.1` is also narrowed honestly to dispatch-time compliance plus effect verification where a state fact exists.

### R2-B4 / R2-B6 / R2-B7 (High) — No execution model: asynchronous tools, retries and batches
The reference action is a deployment, which is asynchronous, yet the design assumed the effect completes during the call. Retries were undefined: a framework retrying a transport failure replays envelope and token, producing `TOKEN_INVALID` and a "token reuse" security alert, so benign duplicates would drown the one alert that matters. Batched tool calls, which every modern runtime emits, had no semantics at all.
**Disposition: Fixed.** `FR-26` defines synchronous and asynchronous connectors, job handles, a registered completion fact, lease hold until completion, and a rollback class with a short approval time-to-live. `FR-27` makes `action_id` the idempotency key, defines retry after a failed or timed-out receipt, and abstains with `RETRY_UNRESOLVED` while a prior outcome is unknown. `FR-22` distinguishes duplicate delivery from replay in alerting. `FR-07` defines batch identity, batch policy, dependency denial and execution-order monitor commits.

### R2-S7 / R2-B2 (High) — "Demote to advisory" was an unguarded blanket allow, and the only incident lever
The decision-record schema had an override kind that demotes a class to advisory, described as never granting an `ALLOW`. Since advisory executes regardless of verdict, it is exactly a blanket allow, available to a single principal with a reason string, while a policy change needs two-person review and a killing fixture. It was also the documented response to "bypass discovered" and "token reuse", so the incident procedure removed the control. There was no halt.
**Disposition: Fixed.** `FR-48` sets override authority: tighten-only overrides need one principal; demotion needs two principals outside the affected delegation chains, a reason and ticket, and auto-expires within an hour. `FR-49` adds `halted` mode, which denies everything and issues no token, as the lever for integrity failures, bypasses and key compromise; demotion is only for false-block spikes. Per-decision override of a denial is stated not to exist. Fixtures `MUT-19`, `MUT-26`; scenarios `A-30`, `A-39`.

### R2-D1 / R2-D2 (High) — The plan was 35 to 40 percent over capacity and Phase 3 was impossible by its own rules
Summing the work breakdown at upper-bound sizes gives about 251 engineer-days against roughly 230 available at realistic utilization, before counting missing work. The critical path alone is about 73 serial days plus three weeks of mandatory shadow waiting, which is 17.6 weeks with zero slack and zero rework against a 17-week plan. Phase 3 needs about 34 days of work in a 15-day phase, because the evaluation plan itself requires two weeks of benign shadow traffic before a monitor can be promoted.
**Disposition: Fixed elsewhere.** The work breakdown is re-baselined to 24 weeks, Phase 1 is split, monitor promotion moves to a post-v1 milestone with v1.0 shipping the monitor in advisory, and the reviewer's missing tasks are added (see `R2-D3`, `R2-D10`, `R2-D11`).

### R2-D3 (High) — No task built the fact providers
Every Phase 1 gate reads facts. The technical plan specifies five providers. The work breakdown had a specification task and a timeout-handling task, and nothing that builds them. The threat model calls providers the most overlooked part of the trusted computing base, and the plan overlooked them.
**Disposition: Fixed elsewhere.** New task `P1-18` builds the provider interface, the five v1 providers, caching, staleness and error mapping; four tasks now depend on it.

### R2-D4 (High) — The mutation gate would have blocked every merge from day one
All eighteen fixtures are authored in Phase 0; the gates that kill them land across Phases 1 to 3; the runner fails if any fixture is not killed; and there is no override. Main would have been red from the first Phase 1 merge until Phase 3, which is incompatible with trunk-based development.
**Disposition: Fixed elsewhere.** The evaluation plan defines a fixture lifecycle: `reserved` (identifier only, non-blocking), `active` (blocking, activated in the same change that lands the gate), `retired` (needs an architecture decision record). The gate is 100 percent of active fixtures, plus a check that every hard rule in the registry has an active fixture, so `reserved` cannot be abused.

## 3. Medium findings, consolidated by theme

**Identity and sessions.** Session identifiers were never specified as harness-derived, so an agent could mint a fresh session per proposal and defeat every per-session control (`R2-S11`). Delegation chains were trusted strings, so whoever launches an agent chooses which human "holds the deploy right" and which principals are excluded from approving (`R2-S11`, `R2-B5`). Sub-agents had no representation at all: no parent session, no tree, so monitor state and approver eligibility were undefined across a spawn. *Fixed:* `FR-06`, `SEC-12`, `SEC-13`, session tree fields in the envelope, `FR-42` eligibility across the tree, fixtures `MUT-25`, `MUT-33`, scenarios `A-36`, `A-40`.

**Token semantics.** Tokens carried neither verdict nor mode, so a shadow token was cryptographically identical to an allow token and a class promoted to enforce mid-flight could not be detected by the broker (`R2-S8`). Revocation existed in the schema with no requirement enforcing it. The token field list differed in four documents (`R2-C15`). *Fixed:* `FR-20` is the single authoritative payload including verdict, mode, proposal digest, record hash and key algorithm; `FR-21` checks verdict, mode, bundle currency and the revocation list; fixtures `MUT-20`, `MUT-36`.

**Free-text channels into the model.** Reason codes allowed a 150-character free suffix, property identifiers had no pattern, counterexample paths and values were unbounded, and fact values were untyped. A compromised critic pack or a string-valued fact could deliver instruction-shaped text to the model through the repair channel that the security requirement claimed was enumerated (`R2-S10`). *Fixed:* `§5.6` is a closed catalogue with registry-validated suffixes; the schema constrains property identifiers, JSON-pointer paths, scalar values and enumerations; `FR-14` adds per-provider value schemas.

**Evidence and replay.** Fact values were not required in the record and the envelope reference was optional, so records were not replayable as the constitution requires (`R2-C9`). Replay compared against wall-clock rather than the recorded time, making staleness non-deterministic (`R2-X4`). The evaluation record embedded the token although the record must be written first and the store forbids updates (`R2-S13`). Bundles and critic packs had no retention requirement. *Fixed:* `FR-70` requires the canonical envelope to be retained content-addressed; `FR-71` uses the record timestamp as "now" and retains historical bundles; token issuance becomes its own record kind.

**Policy bundle transitions.** Nothing said what a bundle change does to pending approvals, monitor automata compiled from the old property set, or tokens already issued (`R2-B8`). *Fixed:* `FR-84`, fixture `MUT-29`, scenario `A-37`.

**Read-only tools and strictness.** Strict mode plus a six-invariant scope meant the agent could not list a file until every tool was registered; teams would have flipped the whole deployment to permissive (`R2-B9`). *Fixed:* `FR-35` adds effect classes with a policy-only fast path for none and read, and states that reading a result is not an action.

**Clock scope.** Fail-closed on clock loss was limited to temporal rules, but fact age, token expiry and approval expiry all need a trusted clock (`R2-S12`, `R2-C7`). *Fixed:* `NFR-13` and `FR-21` extended.

**Repair linkage.** Nothing said how the gateway decides that a resubmission is iteration N+1 rather than a new action, so the natural implementation resets the budget on every call and the probing mitigation was not one (`R2-S9`). *Fixed:* `§5.4` defines linkage, and `FR-93` adds a per-session-root rate limit with fixture `MUT-35`.

**Schema rejection.** `SCHEMA_INVALID` had no verdict, no recordable shape and an uncomputable digest (`R2-C13`). *Fixed:* it is a `DENY` recorded against the digest of the raw request bytes.

**Metrics not computable.** Every metric is specified per action class, but the record carried no tool, intent, session or model identity, and soft-critic confidence had no field, so calibration was uncomputable and labels had no home (`R2-D6`, `R2-D7`). *Fixed:* schema additions plus a separate label store and a new task.

**Solver determinism.** A wall-clock timeout makes solver outcomes load-dependent, so replay diffs under continuous-integration contention, and a routine solver upgrade would fail the replay gate with no override (`R2-D5`). *Fixed:* `FR-51` makes a deterministic solver resource limit the primary bound with wall-clock as a backstop mapping to `TIMEOUT`; the solver version is part of the critic version; a solver-upgrade task and a risk entry are added.

**Terminology.** Three different things were called "approval", and gate, verifier, shadow token, resolved approval, strictness and operational override were used but never defined (`R2-C5`, `R2-C19`). *Fixed:* glossary expanded; the three approval concepts are separated and `WF-02` versus `WF-05` is stated explicitly.

## 4. Round-1 audit (`R2-B`, Part A)

The second-read reviewer audited all ten major and fourteen minor round-1 findings against the research text. Twenty-one hold as written. Corrections:

| Round-1 item | Correction |
|---|---|
| `M4` | Overstated: the research does address missing evidence and treats formalization failure as an observable event. The listed absent failure modes are genuinely absent, so the finding stands with narrower wording. |
| `M6` | Two misfires. The research never attributes the fidelity figure to the Princeton reliability paper, so that criticism was a non-finding and is withdrawn. The Lobster discrepancy is real but not load-bearing, since it appears only in a model perspective and not on the v1 control path. The research also hedges every figure at the top of the document, so "overclaiming" was too strong; the substantive addition is the false-block cost omitted from the AgentSpec summary. |
| `m4` | Half wrong: the research diagram does flow execution into the evidence plane. The missing monitor feedback edge stands. |
| `m6` | The research record already carries an inputs digest, so "missing envelope digest" was wrong; the other omissions stand. |
| `m9`, `m12` | Largely redundant with text already in the research's final recommendation and risk table; retained as precision, not new findings. |
| `m13` | Imprecise: PyReason and OpenSSA are not differentiable substrates. The scope-creep concern stands. |
| `m3` | Under-rated. The deeper problem is not that model agreement is presented as corroboration but that several adopted decisions rest *only* on model opinion (see below). |
| `m14` | Under-rated: a recommended component whose default interface is agent-writable is more than minor. |
| `§7` "every hard gate has a fixture: pass" | Too generous: two fixtures had disjunctive expectations, which cannot kill a mutant precisely. |

**New finding from the audit (`R2-B12`):** four adopted decisions cite only model opinion or vendor material as support: that reasoning-trace verification expands the trusted computing base; that format instructions degrade reasoning more than token masking; that differentiable coupling is architecturally undesirable; and the hard/soft critic split. Two round-1 "converged findings" rest on vendor marketing pages and a vendor blog. *Disposition: Fixed.* The affected records are marked as engineering judgement and `OQ-07` requires primary evidence before any of it appears in a published claim.

**`R2-B15`:** the research corpus itself has no provenance. The "two complementary analyses" are never identified or linked, and the model perspectives have no prompts, dates or authors. *Disposition: Deferred to `OQ-08`*, with the rule that research-derived rationale without a checked-in source is treated as judgement.

**`R2-B11`:** the motivating evidence does not support the intervention. Pilot-failure rates and document-fidelity loss are not policy-violation problems, and a dispatch gate does nothing for either. Round 1 attacked the sourcing but not the inference. Separately, the harness can only lower `pass^k`, which the specification itself concedes, so listing it as a success metric was incoherent. *Disposition: Fixed.* `§3.1` now claims policy compliance, auditability and effect verification rather than reliability, and `pass^k` becomes a regression guard.

**`R2-C20`:** several round-1 "resolved by" pointers resolve in form but not in substance. `M1`, `M2` and `M3` are downgraded to partially resolved in round 1's own mapping until the fixes in this round land, and a fact-provider registry is added because "enumerated in the trusted-computing-base inventory" was not a registration mechanism.

## 5. What survived

Worth recording, because it constrains what future changes may weaken:

- Claims are excluded from the policy input by construction rather than convention, with a schema test and a fixture.
- The broker holds the only credentials; verifiers have no egress; the hook adapter cannot execute.
- Token binding at the broker (digest recompute, signature, expiry, tenant, atomic nonce insert before dispatch) is sound; the round-two additions harden it rather than replace it.
- Bundle and registry signature verification with abstention and refused reload.
- Append-only evidence with per-tenant chaining, insert-only enforcement, signed checkpoints, and crypto-shredding that preserves the chain.
- Approver separation of duties was already correctly broad; round two extends it across the session tree.
- Monitor promotion gated on certification with per-model re-certification and automatic demotion.
- The emergency-change rule, tighten-only with one reviewer and a mandatory follow-up, with loosening never an emergency.
- Evidence classes (measured, hypothesis, literature) applied consistently to every target.
- The dependency graph is acyclic, every referenced identifier resolves, and enumerations agree across specification, plan and schemas.

## 6. Residual risk after this round

1. **Fact-provider integrity remains the soft underbelly.** `SEC-11` closes the tenant-tool write path, but a compromised provider still produces authentic facts. Detection is post-hoc through effect verification and state audits.
2. **The monitor ships advisory in v1.0.** Precedence at token issue and the broker lease cover the two highest-value temporal properties, but general ordering invariants are not enforced until certification completes post-v1.
3. **Delegation credentials depend on an identity layer the project does not own.** If the environment cannot issue verifiable delegation, `WF-03` degrades to a trusted string and should be marked as such per deployment.
4. **Capacity.** Even re-baselined, the plan has one half-time person carrying policy, security, labelling and the bypass exercise. Named alternates are required in `P0-12`.
5. **No primary evidence yet** for four adopted rationales (`OQ-07`) or for the research corpus itself (`OQ-08`).

## Reviewer limitations

Network egress was restricted to the same set as round 1; no new external sources were fetched, and no source claim from round 1 was re-verified against the internet. All four reviews and the reviewer pass are model-generated engineering judgement over the committed text. The findings about internal consistency, schema behaviour and dependency structure were checked mechanically where possible (schema validation, identifier cross-referencing, dependency-cycle detection). Findings about attack feasibility and delivery realism are arguments, not measurements, and should be challenged by the human architecture and security reviewers named in the governance document.
