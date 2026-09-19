# Round-3 Peer Review: the multi-model synthesis, checked against the tree

| | |
|---|---|
| **Artifact reviewed** | A multi-model research synthesis (agreements, disagreements, unique discoveries, comprehensive analysis and recommendations), supplied as input rather than checked in. Its three contributing perspectives are referred to below by the labels the artifact itself uses. |
| **Reviewed against** | The repository at `944e077`: the SDD package (constitution v0.2, specification v0.2, technical plan, WBS, threat model, evaluation plan, governance, increment plans 1 and 2, the fact-provider specification, ADR-0001..0026, both JSON Schemas) and `src/neuroharness/` as built. |
| **Method** | Every claim the synthesis makes *about this repository* was checked against the file it concerns. Measurements were taken, not recalled; §8 lists the commands. |
| **Review date** | 2026-09-19 |
| **Findings** | 15 against the synthesis (2 false, 4 already-decided-differently, 3 substantive and surviving, 6 confirmed-and-sharpened), plus 4 incidental findings against the tree (§6a) |
| **Verdict** | **Accept the sequencing; reject the currency.** The synthesis's central conclusion — finish the symbolic enforcement plane before adding anything neural — is right, is independently reached three times, and matches the dependency graph in `03-work-breakdown.md`. But the artifact was written against a v0.1-era snapshot. It re-derives five decisions already recorded as ADRs, cites one invariant ID this project does not have, and its single highest-priority recommendation is correct in its diagnosis and unimplementable in its proposed form. Three findings survive contact with the tree and are worth work; two of the three are narrower and more actionable than the synthesis states. |

Finding IDs are namespaced by lens, continuing the round-2 convention: `R3-F` false against the tree, `R3-D` already decided, `R3-S` substantive and surviving, `R3-C` confirmed.

---

## 1. How to read this

Round 1 reviewed the research. Round 2 reviewed the specification that answered it, at a point where no code existed. This round reviews a *second* research synthesis, and for the first time there is an implementation to check it against: 2709 passing tests over `src/neuroharness/`, 41 mutation-fixture declarations, and a specification that has already absorbed 71 adversarial findings.

That changes what a review of research is for. The question is no longer "is this good advice for a project like this" but "which of these recommendations does this repository, in this state, not already hold — and of those, which are right?"

The answer is: three.

---

## 2. False against the tree

### R3-F1 (High) — `INV-16` does not exist in this project, and the argument resting on it does not stand as written

The synthesis adjudicates its sharpest three-way disagreement — whether confidence scores may appear in a control path — by ruling that *"Your own `INV-16` (no `CognitiveSignal` field, including confidence, reaches a control path) already decides this."*

It does not, because there is no `INV-16` here. The identifier appears in this repository exactly three times, always as an **unresolved upstream reference**:

- `01-specification.md:121` adopts its evident content as `INV-01` and marks the adoption provisional: *"(Adopted from upstream `INV-16`; `OQ-01`.)"*
- `01-specification.md:662` carries `OQ-01` as open: *"Confirm mapping of upstream `INV-16`/`DEC-008` to `INV-01`/`INV-02`, or link the upstream registry"* — owner: product owner, needed by `P0-01`.
- `2026-09-18-peer-review-research-synthesis.md:109` is where round 1 first raised it: *"`INV-16` and `DEC-008` are referenced but never defined; they appear to belong to an upstream registry."*

`CognitiveSignal` appears nowhere in `docs/` or `src/`. So the synthesis resolves a disagreement by citing a registry entry this project flagged as missing a year of review ago and still lists as an open question.

The **substance** of the ruling is nonetheless correct, and better grounded than the synthesis knows — which is why this is filed as a citation defect rather than a reasoning defect. See `R3-F2`.

**Disposition:** cite `INV-01` and Article I. Separately, `OQ-01` has now been cited wrongly by an external reader; that is evidence it should be closed rather than carried.

### R3-F2 (High) — The confidence-score reconciliation is *weaker* than the rule this project already enforces; adopting it as written would be a loosening

The synthesis's reconciliation: *"Kimi's semantic critics are legitimate as long as their output is bucketed into a closed enum before crossing into the resolver, and can only move a verdict up the safety order."*

This repository does not permit a neural critic to move the verdict at all, in either direction:

| Where | What it says |
|---|---|
| `01-specification.md:28` | "a soft critic's result is advisory (warn, rank, log) and **never changes a verdict**" |
| `01-specification.md:257` (`FR-55`) | "soft critics MAY return a `score`, hard critics MUST NOT" |
| `resolve/resolver.py:148` | implements it, in those words: `"soft critics never change the verdict"` |
| `ADR-0001` | "LLM-as-judge as the gate. Rejected as a hard gate: circular, manipulable, not deterministic. **Allowed only as a soft critic.**" |
| `ADR-0004` | no component that can produce an `ALLOW` may be trained jointly with, share parameters with, or receive gradients from the governed model; differentiable substrates "may appear only as soft critics if ever integrated" |
| `01-specification.md:351` (failure table) | "Soft critic any failure → Recorded; verdict unchanged" (`A-21`) |

A neural critic in Neuroharness is therefore permanently soft and permanently verdict-inert. "May raise severity" is a **promotion**, not a constraint. If the project wants the synthesis's rule it is an ADR superseding `ADR-0001` and `ADR-0004`, a glossary change, an `FR-55` change and a fixture — not a clarification, and not something to absorb by agreement.

### R3-F2b (Medium) — …and there is a live ambiguity the synthesis should have caught while it was there

Constitution Article VIII ends: *"any residual non-determinism may only move a verdict toward a safer outcome on the order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`."*

Read alone, that sentence **licenses exactly what the synthesis proposes**: a non-deterministic component moving a verdict toward safety. A neural critic is a non-deterministic component. Glossary line 28 and `FR-55` forbid it. Both are in force and they disagree, and the first person to propose a guard model will cite Article VIII, in good faith, and be half-right.

The fix is small and belongs in the same change as any neural-critic discussion: Article VIII's clause should say that residual non-determinism may only move a verdict toward safety *within components already authorised to change it*, or `FR-55` should state the prohibition positively rather than leaving it to the glossary. **Do this before anyone proposes a guard model, not after.**

### R3-F3 (Medium) — "Neuroharness implements four of the six symbolic-guardrail strategies; it is missing information flow and temporal logic"

The information-flow half is true and is the best finding in the set (`R3-S2`). The temporal half is false as a specification claim:

- `ADR-0013` decides a **bounded LTLf subset, built in-house**: precedence, bounded response, absence and their conjunctions over a finite registry-drawn event alphabet, compiled to explicit DFAs at bundle-build time, transition tables checked into the bundle, property-set digest versioning monitor state.
- `FR-52` requires the compilation; `FR-84` the state versioning; `FR-53`/`FR-54` the monitor; `05-evaluation-plan.md` §6 is a five-step monitor **certification protocol** with entropy-coverage thresholds and sample sizes.
- `03-work-breakdown.md:110` schedules it as `P3-01`.
- `MONITOR_STATE_LOST` and `MONITOR_VIOLATION:<property_id>` are in the closed reason catalogue (`01-specification.md:184`, `:189`) and `MONITOR_STATE_LOST` is one of the ten non-escalating infrastructure reasons.

It is unbuilt — as is everything past increment 1. But "missing" and "specified, ADR'd, scheduled, with a certification protocol and reserved reason codes" are different states, and the difference is load-bearing here: having declared the strategy missing, all three perspectives then proposed a **backend** (streaming STL monitors; "ordinary deterministic critics"; NuSMV/NuXmv model checking) for a build-vs-vendor decision `ADR-0013` already made, on stated grounds — determinism of per-step cost, dependency surface, and the fact that runtime interpretation makes the automaton unversionable and so breaks `FR-84`.

None of the three cites `ADR-0013`. The "temporal reasoning backend" disagreement row is therefore not a disagreement about this project; it is three independent re-proposals against an existing decision, none engaging its reasons. Any of them may be right — but the deliverable is a superseding ADR, which is cheap, and the synthesis as written would have an implementer simply pick a library.

---

## 3. Already decided here, and decided differently

### R3-D1 (High) — Shadow mode: the warning is a resolved Critical, and this project's resolution is the **opposite** of what the synthesis proposes

The synthesis flags as a unique discovery: *"Shadow mode must mean compare hypothetical decisions without authorizing effects, never 'allow when undecidable'."*

**The second half is settled law here**, and was round 2's single Critical finding. `R2-S1` found exactly the hole — shadow and advisory modes executing on infrastructure failure — and the package was changed:

- `ADR-0016` **supersedes** `ADR-0010` on precisely this point ("mode-independent fail-closed and the halt lever").
- `FR-80`: "infrastructure failures block in every mode."
- Constitution Article II: "Rollout modes change only whether the broker consults the *verdict*; they never disable the fail-closed path."
- `06-delivery-and-governance.md:55`: "**Modes never weaken fail-closed behaviour** … This is why shadow data is representative and why a shadow class is not an unguarded class."
- Failure table: "Infrastructure failure in a `shadow`/`advisory` class → No shadow token; no execution", fixture `MUT-21`, scenario `A-28`.

Re-raising it is the single clearest indicator that the synthesis read a v0.1-era snapshot.

**The first half is a real, unflagged design disagreement**, and it is the part worth the reader's attention. In Neuroharness, shadow mode *does* authorise effects, deliberately:

- `ADR-0010` Decision: "`shadow` (evaluate, record, never block; **the broker executes with a shadow token** so the code path is identical)".
- `01-specification.md:34`: a shadow token is "a decision token issued in `shadow`/`advisory` mode".
- Scenario `A-14` (`:471-474`) states it without euphemism: class in shadow mode, "the decision record shows verdict **DENY** and mode shadow", "a shadow token is issued after the record is durable **and the broker executes the call**."

That is the design, and the reason is good: identical code path, so shadow data is representative, so `06-delivery-and-governance.md:50`'s promotion gate (≥ 500 decisions, labelled false-block rate) measures something real. The synthesis's non-executing shadow would yield hypothetical verdicts and no false-block or latency data from real traffic, which is the evidence promotion requires.

**But the residual it implies is not written down anywhere, and should be.** For any class in shadow, *a `DENY` verdict still executes*. The mitigations are real — mode is a signed registry change (`FR-80`), `MUT-16` kills the fixture — but the exposure is the entire rollout, and in `R2-S1`'s own words that is "*every* class at the start." `T-23` covers an operator abusing an override *to* advisory; nothing in `04-threat-model.md` covers an attacker simply knowing a class is legitimately in shadow.

**Recommendation.** Add it as a residual risk row with a detection signal. The signal is nearly free: `shadow_verdict` — "the verdict that would have applied had every hard critic been enforcing" — is already computed and carried (`resolve/inputs.py:350-360`), so alerting on `shadow_verdict = DENY` rate per class is a threshold on a field that already exists.

### R3-D2 (Medium) — REPAIR is already a bounded counterexample loop, specified more completely than the recommendation

All three perspectives converged on: counterexample-driven, bounded retries, a fresh digest per round, not LLM self-reflection. All of it is specified, and two controls the synthesis omits are specified too:

| Recommendation | Where it already lives |
|---|---|
| Counterexample-driven, not self-reflection | `FR-90`: all counterexamples returned together, typed, through the gateway response; "the harness never injects text into the model context" |
| Bounded retries | `§5.4`: default 3 per `action_id`, configurable per class, max 10; `FR-91` + `MUT-15` link resubmissions and count them **even if unchanged** |
| Fresh digest per round | `FR-04`'s two digests; each resubmission is a new envelope and a new evaluation |
| "Monotonicity rule so it cannot become a policy-boundary probing oracle" | `T-18` names the probing threat and answers it with minimal counterexamples (property ID, fields, expected domain only) and a repair budget; `FR-93` + `MUT-35` add a per-`(session_root, action_class)` rate limit, default 10/h |
| *(not in the synthesis)* | `01-specification.md:182`: escalation is refused while any hard critic has failed — so a `REPAIR` can never be laundered into a `REQUIRES_APPROVAL`, which is *less* safe on the order |
| *(not in the synthesis)* | `resolve/safety.py`: the order is a total join (`safer_of` is commutative, associative, idempotent), so the monotonicity property is structural rather than asserted |

Nothing in the cited CEGIS work adds a mechanism this specification lacks. What it would add is **evidence that repair converges**, which is `FR-92` (repair success rate, iterations-to-allow, repair cost, per action class) and `NFR-22` (≤ 2 added agent turns p95) — both unmeasured, because nothing runs. That is the honest version of this recommendation, and it is downstream of the gateway.

### R3-D3 (Medium) — OPA-to-Wasm is considered and deferred, and its stated benefit does not hold

The synthesis offers as a concrete implementation win: compiling Rego to Wasm and evaluating in-process *"removes the 'PDP unreachable' ambiguity class entirely and enforces your own constitution by construction, since Wasm-compiled bundles can't reach out with `http.send`."*

Considered and triaged, twice: `02-technical-plan.md:204` accepts an in-process WASM evaluator "for library deployments"; `:373` records "sidecar (WASM in-process is post-v1 and unowned until then)"; `:467` explains why there is no dual-evaluator divergence suite, against `R2-D17`.

And the benefit is overstated. In-process evaluation removes `POLICY_ENGINE_UNAVAILABLE` *as a network failure* and replaces it with bundle-load and evaluation failures, which map to `BUNDLE_INTEGRITY_FAILED` and `CRITIC_ERROR` — both already in the fixed non-escalating infrastructure set at `01-specification.md:184`, and both still `ABSTAIN`. One failure mode is swapped for two; the fail-closed surface does not shrink. The `http.send` argument is true of the Wasm target but redundant here: `FR-60` already forbids the PDP and critics any network egress, and `P1-12`'s acceptance criterion is "egress test proves isolation."

Worth doing eventually, for latency and for library-shaped deployments. Not worth doing as an ambiguity-elimination play, and not worth reopening before `P1-04` exists.

### R3-D4 (Medium) — The three-checkpoint OPA topology contradicts the synthesis's own consensus row, and weakens a structural guarantee

The synthesis lists as a unique discovery a "three-checkpoint OPA topology: coarse tool-class at gateway, delegation chains at a dedicated PDP, endpoint policy at the API layer," presented as validated defense-in-depth.

Two objections, the first internal to the synthesis:

1. Its own consensus row states that the PDP "must not become an alternate decision authority" — one typed input to one resolver. Three checkpoints create three places a verdict is formed, two outside the resolver.
2. It weakens `FR-01`/`SEC-02`. Bypass here is prevented *structurally*: the broker holds the only tool credentials, so "calls that bypass the gateway MUST be impossible by deployment." Adding enforcement at an API layer the broker does not gate does not add a layer to that guarantee; it adds a surface that is outside it.

The defense-in-depth instinct is right and this architecture already satisfies it in a stronger form: infrastructure status, PDP outcomes, critic results, fact status, approval state and rate-limit state all enter one monotone resolver (`§5.3`, `resolve/safety.py`), so **adding a check can only move the verdict toward safety**. Independent checkpoints give that property up — under three authorities, adding a check can change which authority answers first.

---

## 4. Substantive, surviving, and worth work

### R3-S1 (High) — The effect state machine: the direction is right, the diagnosis is wrong, and the real hole is one record

The synthesis's finding: the broker needs an explicit effect state machine — `NOT_STARTED / STARTED / EFFECT_UNKNOWN / EFFECT_OBSERVED / VERIFIED / COMPENSATION_REQUIRED` — because "fail-closed cannot undo an external side effect; ambiguous-effect recovery is unspecified today."

**The premise checks out.** `grep -r "EFFECT_UNKNOWN\|EFFECT_OBSERVED\|COMPENSATION\|NOT_STARTED\|EffectState" docs/sdd/ src/` returns nothing.

**The diagnosis does not.** Most of that machine already exists, distributed across three requirements as verdict rules rather than as states:

| Proposed state | Where it already is |
|---|---|
| `EFFECT_OBSERVED` / `VERIFIED` | `FR-57` + `ADR-0018`: an effect critic compares the typed result and a **freshly fetched** state fact against the proposal; `effect_verification` is a record type in `decision-record.schema.json`; mismatch is `EFFECT_MISMATCH:<resource_key>`, `T-26`, `MUT-32` |
| `EFFECT_UNKNOWN` | `FR-27`: "while the prior outcome is unresolved (`timeout` and no completion fact) the verdict MUST be `ABSTAIN` (`RETRY_UNRESOLVED`) until a fresh state fact resolves it", `T-25`, `MUT-27` |
| `STARTED` (async) | `FR-26`: connectors declare `sync`/`async`; async returns a `job_handle`; completion observed through the registered `execution_completion` fact and appended as a `completion` record; the lease is held until completion or a class timeout |
| `COMPENSATION_REQUIRED` | **Nothing.** `FR-26` requires a `rollback` class to exist with an approval TTL ≤ 15 minutes and a standing approval bound to the original proposal digest — but nothing in the specification ever *triggers* it |

**The actual hole is narrower, sharper and cheaper than the proposal.** `FR-27`'s unresolved branch is conditioned on a **`timeout` receipt** existing. `FR-24` requires a receipt to be appended and adds "a missing receipt after `expires_at` MUST raise an operational alert." The record schema's event types are `evaluation`, `token_issued`, `approval`, `execution_receipt`, `completion`, `effect_verification`, `override`, `checkpoint`.

There is **no record written between token consumption and the receipt**. So consider the broker crashing after dispatch and before the receipt append:

- The chain shows `token_issued` and no `execution_receipt`.
- That is **indistinguishable** from "token consumed, call never sent."
- `FR-27`'s antecedent (`timeout` receipt) is not met, so there is no verdict rule. `FR-24` gives an alert, not a verdict.
- The failure table (`§7`) has **no row** for it — every other unresolved state has one.
- `FR-25`'s lease releases "on completion (or lease timeout)", so the lease frees while the effect state is unknown, and the next proposal on that resource key proceeds normally.

Article III's test — "Can this decision be replayed from its record?" — fails on the one thing replay cannot reconstruct: what happened outside the process.

**And this project already knows the rule that fixes it.** `INV-05`/`FR-23` require the evaluation record to be durable *before a token exists*. The same discipline is simply not applied one step later: **no record is required to be durable before an external effect exists.** The fix is a `dispatch` record type — appended after token consumption and before the connector call — plus a verdict rule and reason code for `token_issued` + `dispatch` + no receipt, plus a lease policy that does not release on timeout while the effect is unknown, plus the trigger `FR-26`'s rollback class has been waiting for.

Note this is the *second* crash-window defect in this family. `08-increment-2-plan.md:75` already found the first: `EvidenceWriter.write` stages the failed record in the WAL before re-raising (`evidence/store.py:477-488`), so on recovery the chain permanently asserts a `token_issued` for a token that was never returned and can never be spent — "and `verify_chain` will call that chain valid." Two instances is a pattern: in-process crash consistency is handled by the WAL; **crossing a process or a network boundary is not.** Worth one ADR covering both, rather than two point fixes.

**Recommendation.** ADR extending `ADR-0018`, one `FR-`, one reason code added to the closed catalogue, one failure-table row, one fixture. Do it **before `P1-08`**: after the broker ships, this is a schema change to a published contract and a migration.

### R3-S2 (High) — Information flow: the finding is correct, the fix is not implementable as proposed, and there is a version that is

**The finding first, and it holds.** `T-12` is this project's prompt-injection row. Its control column: "typed per output schema, size-bounded, tagged untrusted; **the harness still evaluates the *next* proposal from facts, so injection cannot authorize**." Its residual: "injection can still cause *proposals* that waste repair budget (`R-08`)."

The control is true and is the property that matters. The residual understates the risk. "Cannot authorise" is not "cannot harm": an injected instruction that induces a **within-policy** action is authorised — correctly, by every rule, against fresh harness-fetched facts, with a clean chain. Nothing in the envelope distinguishes *the operator asked for this deploy* from *a poisoned README asked for it*. `T-17` and `T-26` catch effects that diverge from the proposal; neither catches a proposal that diverges from the principal's intent. The residual is not wasted budget — it is **an attacker choosing which policy-compliant action happens, and when**. Under Article X that row currently licenses a claim the design does not support, and it is a one-line change to fix.

**The proposed fix does not work here, for a structural reason the synthesis does not reach.** CaMeL's capability tags are computed by a privileged interpreter that observes how the agent derived each variable. Neuroharness intercepts at the MCP gateway (`FR-01`); it sees tool calls, not the agent's dataflow. A label supplied by the *agent* is a model-authored field, and Article I forbids a model-authored field from satisfying a rule — it "may select which rules apply; it may never satisfy an authorization, evidence or precondition rule." So an agent-supplied provenance label could select rules and could never enforce one, which is exactly the enforcement the recommendation wants. Adopting CaMeL properly means owning the agent's interpreter, which contradicts `ADR-0001`'s "outside the governed runtime" and is a different product.

**The version that works here — and that none of the three perspectives proposed — is for the harness to derive the label itself, from records it already holds.**

- `FR-63` requires *every* tool result returned to the governed runtime to pass through the broker, typed, size-bounded, tagged untrusted — **and recorded**.
- `FR-06` gives a server-derived session tree: `session_id` derived from the authenticated agent-host connection (`SEC-12`), plus `parent_session_id` and `session_root_id`, with session-scoped limits and monitor properties applying across the tree.

So at evaluation time the harness already knows — without trusting the agent for any of it — which untrusted-tagged results entered this session tree before this proposal, and from which registered sources. That is a **harness-computed, Article I-clean, session-scoped taint set**. It is not CaMeL's per-variable precision, and it should not claim to be. It is enough to express the rules that matter:

> no `repo.change` in a session tree that has ingested from an unattested external source since the last human turn
>
> escalate a sensitive class to `REQUIRES_APPROVAL` when it follows ingestion from a source outside the tenant

And it is not a new subsystem: it is the trajectory monitor's machinery — session-scoped state, versioned by a property-set digest (`FR-53`, `FR-84`) — which means it inherits the monitor certification protocol in `05-evaluation-plan.md` §6 rather than needing a new one, and it inherits `MONITOR_STATE_LOST` as its fail-closed reason.

**The schema half of the recommendation stands, and is urgent for the stated reason** — reserve the fields before external consumers exist, because retrofitting provenance into a hashed, canonicalized, versioned envelope afterward is miserable. With one correction that matters more than the timing: reserve them as **harness-stamped context**, never as proposal fields. The split already exists (`FR-02`/`FR-03`, and `FR-03` strips context-shaped keys out of the raw payload and lists them in `context.stripped_proposal_keys`). Putting a provenance label on the proposal side would be the single most consequential schema mistake available in this increment: it would make a model-authored field load-bearing for authorisation, which is the one thing the constitution's first article exists to prevent.

### R3-S3 (Medium) — Tool-manifest digest pinning: real, and narrower than described

The synthesis's framing — *"you already own the canonicalization and digest machinery; point it at tool manifests, and a changed digest becomes a DENY with a dedicated reason code"* — is sound, and the machinery is real and built (`canonical/jcs.py`, `canonical/digest.py`). Two repo facts change the shape:

1. **The reason-code catalogue is closed** (`01-specification.md:189`, enforced at `reason.py` and `models/record.py:166`). A "dedicated reason code" is a specification change plus a fixture, not a new constant. That is the catalogue working as designed.
2. **The threat is already partly mitigated, by something better than pinning.** Tool poisoning works by changing a description the *model* consumes. Here, the model's description of a tool authorises nothing: authorisation is by registered action class against harness-fetched facts (`FR-01`, `ADR-0001`), and `SEC-02` gives the broker the only credentials. A poisoned description changes what the model **proposes**; it cannot change what is **permitted**. The honest claim is therefore `T-12`'s claim — with `R3-S2`'s corrected residual, which is the part that bites.

**What pinning genuinely buys is evidence, not prevention**, and that is the version consistent with Article III: a manifest digest recorded per decision makes a rug-pull *visible in the chain and replayable* — you can prove which manifest was in force when a decision was made. That belongs in `FR-70`'s field list and in `T-12`'s controls. One `FR-`, one reason code, one fixture — and the claim phrased as detection, not defeat.

---

## 5. Confirmed, and sharpened

### R3-C1 — Sequencing. Correct, and the WBS already encodes it

Three perspectives, three methods, one answer, and it matches `03-work-breakdown.md` rather than contradicting it. Worth stating plainly because it settles the build-order disagreement: the graph is **serial**, and `08-increment-2-plan.md` §1 Fact 2 proves it —

```
P0-06 ──▶ P1-18 ──▶ P1-04 ──▶ { P1-11, P1-12, P1-15, P1-16 }
```

`P1-04`'s declared dependencies are `P0-05, P1-18`. So of the three proposed orders, **fact providers first is the one the graph forces**, and the synthesis's other two orders each invert a declared dependency by putting the PDP before the fact layer. The project reached that conclusion independently and wrote `09-fact-provider-specification.md` — 429 lines, with the epistemic-status question answered in a form the synthesis proposes and does not know exists: a closed `FactStatus`, a total error taxonomy (§5), a `FR-11` contract that makes a non-fresh fact carrying a value *unconstructible* (`models/envelope.py:464-494`), and §6's anti-laundering derivation (`ADR-0022`).

### R3-C2 — "The differentiator is the chain of custody, not OPA-gated tool calls." Correct, and this is where built code is ahead of the narrative

`FR-70`–`FR-74`, `SEC-10`, and `evidence/` as built: hash chain, write-ahead log, checkpoints, `verify_chain`. `FR-71` replay uses the record's own timestamp as "now". This is the one area where the implementation is ahead of the README.

### R3-C3 — Transparency-log anchoring. The best effort-to-differentiation ratio in the set, and closer to done than stated

The synthesis rates this ~a week of work for the strongest compliance claim. It is less than that, because the foundation is built:

- `SEC-10` already says records are "hash-chained and **periodically anchored**".
- `02-technical-plan.md:334` already specifies hourly signed checkpoints `(tenant, last_seq, last_hash, signature)` written to object storage, with a verification tool that recomputes the chain.
- `evidence/chain.py` already exports `create_checkpoint`, `verify_checkpoint`, `verify_against_checkpoint`, `checkpoint_signing_bytes`.
- `06-delivery-and-governance.md:87` already maps it to EU AI Act Art. 12.

What is missing is exactly the part the synthesis names and nothing more: **inclusion proofs, consistency proofs, and a third-party witness** so the log cannot present split views. That is an increment on a built component with a signing key already in the threat model (`AS-2`). **I would promote this above the synthesis's own ranking of it** — it is the only recommendation in the set that is both differentiating and not blocked on a component that does not exist.

### R3-C4 — The inverse-scaling point belongs in the README. Agreed, with a condition

"More capable models are easier to attack because they competently execute the injected task" is the cleanest one-line argument for why enforcement lives outside the model. Put it in the README — **paired with `T-12`'s corrected residual** (`R3-S2`). Alone it reads as a claim this harness defeats injection. It does not; it makes injection unable to *authorise*, which is a narrower and defensible claim, and Article X requires the narrower one.

### R3-C5 — The 2-D frontier metric. Right, and the protocol already exists

Attack success rate against benign task completion, harness-on vs harness-off, is the right headline. `05-evaluation-plan.md` §5.3 already has the protocol shape for τ²-bench: same agent harness-off and harness-on in enforce mode, k = 5 trials, policy-violation rate, `pass@1` **and `pass^k`**, labelled false-block rate, added latency, repair iterations, and a stated-limitations clause. Adding AgentDojo is a second instance of an existing protocol, not new methodology.

The benchmark disagreement therefore dissolves: `pass^k` at `:61` is already the "utility you kept" number, and its gate is already stated as relative (`harness-on ≥ harness-off − X`) rather than absolute. And "own mutation matrix first" is not in tension with it, because the matrix exists — which is `R3-C6`.

### R3-C6 — "Stop doing: 71 findings against a spec whose external components don't exist." The count is exact, and the sharper number is elsewhere

71 is right: `round-2` header, 1 critical, 14 high, 33 medium, 23 low.

But the number that makes the argument is the fixture count. Measured on this tree: **41 mutation-fixture declarations — 5 `active`, 6 `partial`, 30 `reserved`** — against a Phase 1a exit gate naming 21 active. Constitution Article IV: *"A gate without a killing fixture does not exist."* By the project's own rule, **36 of its 41 gates do not yet exist.** Set against 2709 passing tests and ~16,500 lines of source and specification, that is the ratio the synthesis is gesturing at, and it is more damning and more useful than the findings count.

To the project's credit, `08-increment-2-plan.md` already leads with the honest version — its section heading is *"What a user can do afterwards: nothing new"* — and Appendix C publishes the twelve factual claims its own first draft got wrong. That is the discipline working. The synthesis's "stop doing" is aimed at a habit the project has already started correcting.

### R3-C7 — Licence. Right answer, already the project's answer, and the blocker is misidentified

`ADR-0012` exists, recommends **Apache-2.0**, and gives the same patent-grant reasoning. It also explains at length why the engineering plan deliberately did *not* execute it: publishing a licence grant is irreversible, it belongs to the repository owner, and `P0-12` has named no approver for any ADR in this package. `CONTRIBUTING.md` already ships and already states the repo is `UNLICENSED` and why.

Measured: `pyproject.toml:11` is `license = { text = "UNLICENSED" }`; there is no `LICENSE` file.

So "resolve `P0-11` now" is correct but is **not engineering work**. It is one decision by the repository owner plus four mechanical follow-ups: add `LICENSE`, set the `pyproject.toml` field, add the SPDX-header convention to `CONTRIBUTING.md`, close `OQ-09`, mark `ADR-0012` Accepted. Filing it as a blocking work item hides that it is blocked on a person, and that person is the reader.

**Related drift, found while checking this.** `OQ-09` at `01-specification.md:670` still reads *"Temporal-property compiler: build vs vendor; GPL constraints of MONA/Spot-based LTLf tooling vs the project licence"* — a question `ADR-0013` and `ADR-0012` jointly closed, and which `ADR-0012`'s own "Alternatives considered" says should be closed with a pointer to both. Small, and exactly the class of rot this project's thesis is about.

---

## 6. What I would actually do, in order

1. **Today, and by the owner rather than engineering:** execute `ADR-0012` — Apache-2.0, `LICENSE`, `pyproject.toml:11`, SPDX convention, close `OQ-09`, mark the ADR Accepted. Every other recommendation in the synthesis is downstream of a repository someone is permitted to contribute to.
2. **Rewrite two threat-model rows.** `T-12`'s residual (`R3-S2`) and a new residual for shadow-mode execution (`R3-D1`), with `shadow_verdict = DENY` rate as its detection signal. Cheapest available correction of a claim the project would otherwise make wrongly under Article X.
3. **ADR + `FR-` + reason code + failure-table row + fixture for the dispatch record** (`R3-S1`), covering both crash-window defects. **Before `P1-08`.**
4. **Reserve harness-stamped provenance fields in envelope v2** (`R3-S2`) — context side only — with the session-taint derivation recorded as the intended computation, even though `P1-18` and `P1-08` land first.
5. **Inclusion proofs, consistency proofs and a witness** on the existing checkpoint code (`R3-C3`). Smallest diff, largest claim.
6. **Close the Article VIII / soft-critic ambiguity** (`R3-F2b`) before anyone proposes a guard model.

Deliberately not on this list: Z3 delegation critics, guard models, temporal backends, AgentDojo wiring, manifest pinning as prevention. Each is downstream of a component that does not exist, and `03-work-breakdown.md` already places them in Phases 2 through 4. The synthesis's own closing advice applies to its own recommendations: every remaining question gets cheaper to answer after the first vertical slice survives contact.

---

## 6a. Incidental findings

Not claims from the synthesis; found while checking its claims. Each is small, and each is the class of drift this project's own thesis is about.

| # | Finding | Evidence |
|---|---|---|
| i1 | The mutation-fixture CI job cites the wrong constitutional article. Its comment reads "Article III, a gate without a killing fixture does not exist"; that is **Article IV**. Article III is "Evidence is a byproduct of enforcement". The claim the job enforces is correct; the citation in the checks list is not. | `.github/workflows/ci.yml`, `mutation-fixtures` job comment |
| i2 | `OQ-09` is stale. It still reads "Temporal-property compiler: build vs vendor; GPL constraints of MONA/Spot-based LTLf tooling vs the project licence" — a question `ADR-0013` and `ADR-0012` jointly closed, and which `ADR-0012`'s "Alternatives considered" says should be closed with a pointer rather than carried. | `01-specification.md:670`; `ADR-0012` |
| i3 | `OQ-01` has now been miscited by an external reader (`R3-F1`), which is evidence for closing it rather than carrying it: an open mapping to an unavailable upstream registry invites exactly that error. | `01-specification.md:662`; this review §2 |
| i4 | The README is behind the tree in two ways. Its status section still reads "Phase: increment 1, the deterministic core," while `08-increment-2-plan.md` is checked in at Draft v2 and six of its repairs are merged; and its document map omits both `08-increment-2-plan.md` and `09-fact-provider-specification.md`. A reader — including the author of the synthesis under review — will take the README at its word, and the second omission is why that synthesis recommends building a fact-provider specification this repository already has. Left for the owner rather than edited here, because the phase claim is the project's to make. | `README.md` Status and Document map; `docs/sdd/08-increment-2-plan.md`; `docs/sdd/09-fact-provider-specification.md`; `git log --oneline -15` |

## 7. Reviewer limitations

- The synthesis was supplied as conversational input, not checked in. It is reviewed as received; its own sources were not re-verified, and its claims about the outside world — competing stacks' audit records, what a given SDK ships, published benchmark numbers — were **not** checked. Only its claims about this repository were.
- Claims about unbuilt components are claims about the specification. Where this review says something "exists", it means it is specified and, where stated, implemented; `§8` distinguishes the two.
- The dependency-graph reading in `R3-C1` is taken from `03-work-breakdown.md`'s declared `Depends on` columns and `08-increment-2-plan.md` §1, not re-derived from first principles.

## 8. What was measured, and how

Run at `944e077`:

```
PYTHONPATH=src python3 -m pytest -q            # 2709 passed in 22.37s
grep -h '"state"' tests/fixtures/mutations/MUT-*.json | sed 's/.*: "//;s/",//' | sort | uniq -c
                                               # 5 active, 6 partial, 30 reserved
grep -rn "INV-16" docs/ src/ tests/            # 5 hits, all upstream-unresolved
grep -rn "CognitiveSignal\|cognitive_signal" docs/ src/   # 0 hits
grep -rn "EFFECT_UNKNOWN\|EFFECT_OBSERVED\|COMPENSATION\|EffectState" docs/sdd/ src/   # 0 hits
grep -rn "wasm" docs/sdd/                      # 02-technical-plan.md:204, :373, :467
ls LICENSE                                     # absent; pyproject.toml:11 UNLICENSED
grep -o "R2-[A-Z]\+[0-9]\+" docs/review/2026-09-18-round-2-deep-dive-review.md | sort -u | wc -l
```

## 9. Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-09-19 | Initial round-3 review: the multi-model synthesis checked against the tree at `944e077`. |
