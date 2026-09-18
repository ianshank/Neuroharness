# Peer Review: *Neurosymbolic Wrapper for LLM and Agent Workflows — Consolidated Research Findings*

| | |
|---|---|
| **Artifact reviewed** | `docs/research/2026-09-18-neurosymbolic-wrapper-research-synthesis.md` (689 lines, prepared 2026-09-18) |
| **Review type** | Design-basis review: *is this document fit to serve as the basis for an implementation specification?* |
| **Reviewer** | AI reviewer (Claude), working in a sandboxed environment; see [Reviewer limitations](#reviewer-limitations) |
| **Review date** | 2026-09-18 |
| **Follow-up** | Round-two review of the resulting specification: `2026-09-18-round-2-deep-dive-review.md`, which also audits this document and corrects four of its findings (see §4 there). |
| **Verdict** | **Accept with major revisions.** The thesis and the v1 scoping are sound and well supported. The document is not yet adequate as a design basis: verdict semantics, decision-to-execution binding, fact trust, fail-closed behaviour, the threat model, non-functional requirements and phase exit criteria are missing or inconsistent. Each gap is closed in the SDD package under `docs/sdd/`, and the traceability table at the end of this review says where. |

## 1. Summary of the artifact

The synthesis argues that neurosymbolic reasoning belongs in an enterprise agent stack only as a **thin, deterministic, external enforcement layer**: the model proposes; a harness-owned policy decision point and a bank of narrow symbolic critics decide; decisions are `ALLOW`/`DENY`/`REQUIRES_APPROVAL`/`REPAIR`/`ABSTAIN`; evidence is emitted as a byproduct. It rejects differentiable coupling, ontology-first delivery and general factual formalization for v1, and proposes a five-phase build plan with negative mutation fixtures as the proof that gates work.

## 2. Scorecard

| Criterion | Score (1–5) | Comment |
|---|---|---|
| Clarity of thesis | 5 | "The cognitive plane proposes; the harness disposes" is crisp and consistently applied. |
| Evidence quality | 3 | Core architectural claims are well supported by primary literature (verified below). Several headline numbers rest on secondary or unverifiable sources, and two quoted figures do not match the sources as retrieved. |
| Internal consistency | 3 | Outcome set differs between sections; `UNKNOWN` is treated as both a verifier result and a verdict; the contract shape contradicts the prose on who supplies which fields. |
| Completeness as a design basis | 2 | No binding between decision and execution, no fact trust model, no failure-mode table, no NFR targets, no approval state machine, no action-class registry. |
| Risk treatment | 3 | Good risk list; not a threat model (no adversaries, no assets, no trust boundaries) and no likelihood/impact/owner. |
| Actionability | 3 | Phases are the right shape but have no exit criteria, owners or measurable acceptance. |

## 3. Strengths

- **S1 — The central architectural claim is correct and independently supported.** Runtime interception with deterministic enforcement (AgentSpec, ICSE '26), generate-and-check with sound external verifiers (LLM-Modulo), and policy-as-code outside the model (OPA practice) are each verified primary sources, and the synthesis represents them accurately.
- **S2 — The autoformalization caution is accurately sourced.** *Grammars of Formal Uncertainty* (verified) does report that SMT autoformalization helps on logical tasks and hurts on knowledge-heavy tasks, and that token-entropy uncertainty does not detect formalization errors. Domain-gating is the right response.
- **S3 — Explicit reject/defer decisions.** Rejecting differentiable coupling and deferring ontology-first work and chain-of-thought verification protects the trusted computing base and the schedule. This is the most valuable editorial discipline in the document.
- **S4 — Harness-level evaluation.** Insisting on negative mutation fixtures per gate, `pass^k` rather than `pass@1`, and mutation-kill rate as a metric is exactly right, and rarer than it should be.
- **S5 — MCP treated as a security boundary,** with read-only verifiers, typed non-instructional outputs and no credential reach.
- **S6 — Evidence-producing enforcement (ADR-006)** turns compliance into a byproduct rather than a separate program.
- **S7 — Honest evidence note** at the top, inviting exactly this kind of audit.
- **S8 — The "bank of narrow critics, not a truth engine" framing** matches how LLM-Modulo achieves its soundness guarantee in practice.

## 4. Major issues (must be resolved before the document is used as a design basis)

Each issue lists where it occurs, what is wrong, why it matters, the requested change, and where the SDD package resolves it.

### M1 — Verdict semantics are inconsistent and incomplete
- **Where:** *Executive Conclusion* (five outcomes), *Deterministic outcomes* (six, adding `UNKNOWN`), *Phase 2* ("prohibit implicit allow on solver timeout or `unknown`").
- **Problem:** `UNKNOWN` is a *verifier result* (sat/unsat/unknown/timeout), not a *decision*; listing it beside `DENY` conflates the two. `REPAIR` is not terminal, but no iteration bound is given and no terminal outcome is defined when the budget is exhausted. Nothing says how verdicts from the policy decision point and several critics compose (what wins when one critic says `REPAIR` and another `REQUIRES_APPROVAL`?). Nothing says whether `ABSTAIN` can be escalated to a human.
- **Why it matters:** Every gate, test and audit record depends on these semantics. Ambiguity here becomes an implicit-allow bug later.
- **Requested change:** Define terminal verdicts (`ALLOW`, `DENY`, `REQUIRES_APPROVAL`, `ABSTAIN`) and the non-terminal `REPAIR`; define a total resolution order over heterogeneous results; bound repairs; map verifier `unknown`/timeout to `ABSTAIN`; state whether and how `ABSTAIN` escalates.
- **Resolved by:** `01-specification.md` §5 (verdict semantics), ADR-0007.

### M2 — There is no binding between the evaluated envelope and the executed call
- **Where:** *Recommended Reference Architecture*, *Contract shape*, audit record (`execution_receipt`).
- **Problem:** The harness evaluates an envelope, then "brokered tool/service execution" happens. Nothing binds the executed arguments to the evaluated ones, nothing prevents a decision being reused for a different call, and facts (CI status, approval, change window) can change between decision and dispatch (time-of-check/time-of-use). An approval likewise is not bound to the exact action approved.
- **Why it matters:** Without binding, the entire gate can be bypassed by proposing one envelope and executing another, or by replaying an old `ALLOW`. This is the most serious gap in the document.
- **Requested change:** Canonicalize and hash the envelope; issue a single-use, short-lived decision token bound to that digest; have the tool broker refuse execution without a matching token; bind approvals to the same digest; give facts a freshness window.
- **Resolved by:** ADR-0008, `FR-20`–`FR-24`, `SEC-03`, `schemas/decision-record.schema.json`.

### M3 — The trust boundary around "authoritative facts" is undefined, and the contract shape contradicts the prose
- **Where:** *Contract shape* and the paragraph after it.
- **Problem:** The prose says the harness must independently obtain CI status, identity, approval state and change window. The JSON immediately above puts `declared_preconditions`, `delegation_chain`, `environment` and `policy_version` inside the LLM-generated envelope. There is no separation between *agent claims* and *harness facts*, no provenance or freshness on facts, and no statement that fact providers are part of the trusted computing base.
- **Why it matters:** A policy that reads `ci_passed` from a field the model wrote is prompt-governance with extra steps.
- **Requested change:** Split the envelope into an agent-authored `proposal` and a harness-authored `context`; give each fact `source`, `fetched_at`, `ttl` and a digest; register fact providers with their trust level; forbid policies from reading agent claims as facts.
- **Resolved by:** `schemas/action-envelope.schema.json`, `FR-10`–`FR-13`, `04-threat-model.md` (TCB inventory).

### M4 — Fail-closed behaviour is specified for one failure mode only
- **Where:** *Formalization is valuable but dangerous*, *Phase 2*, *Risks and Mitigations* (solver latency row).
- **Problem:** Only solver `unknown`/timeout is addressed. Unaddressed: policy engine unreachable; policy bundle fails integrity check; audit store write fails; trusted clock unavailable for change-window rules; fact provider times out; one critic crashes while others pass; two concurrent `ALLOW`s on a shared resource; monitor state lost on restart.
- **Requested change:** A failure-mode table with the verdict for each, and the rule that the audit record is written *before* any token is issued.
- **Resolved by:** `01-specification.md` §7 (failure-mode table), `NFR-10`–`NFR-14`.

### M5 — The security section is a control list, not a threat model
- **Where:** *Security and Governance Requirements*.
- **Problem:** No assets, adversaries or trust boundaries are named. Missing adversaries include: (a) **the governed agent itself**, which can learn to craft envelopes that pass checks while the executed effect differs (specification gaming); (b) **the policy authoring path**: the document cites AgentSpec's LLM-generated rules as "meaningfully effective", but the verified paper reports 70.96% recall for LLM-generated embodied-agent rules and 5-of-8 scenarios zero-shot for driving, so assisted authoring must be gated by the mutation suite, not by review alone; (c) **policy bundle supply chain** (who can change Rego, how bundles are signed); (d) **the approval channel** (approval fatigue, replayed approvals, approvals whose scope silently widens); (e) **the tool-result return path**: results re-enter the model context, and the document governs actions but says nothing about typed/sanitized results; (f) **fact-provider compromise or staleness**.
- **Requested change:** A STRIDE-style threat model with assets, trust boundaries, adversaries, controls and residual risk.
- **Resolved by:** `04-threat-model.md`.

### M6 — Several load-bearing numbers rest on secondary or unverifiable sources, and two quoted figures do not match the sources as retrieved
See the [citation audit](#6-citation-audit) for details. In summary:
- The 89% / 14% enterprise-pilot figures and the "~50% document-fidelity loss in a Microsoft-related study" come from news sites (one of them a cryptocurrency news outlet). Neither could be reached from this environment, and the Princeton *Towards a Science of AI Agent Reliability* paper cited nearby does not contain the fidelity figure.
- AgentSpec's ">90% of unsafe code executions prevented" is accurate but specific to the RedCode-Exec code-agent benchmark; the synthesis omits the measured false-block cost (safe-task success fell from 58.62% to 54.26% on the embodied benchmark), which is precisely the metric this project must manage.
- The Lobster figure "5.3× average speedup over Scallop across eight applications" does not match the version retrieved (1.2×–16×, 3.9× average, ten benchmark tasks). The number may come from a different version; verify before quoting.
- **Requested change:** Remove or re-source the news-derived figures; scope the AgentSpec claim; add the false-block cost; reconcile the Lobster figure to a specific version.
- **Resolved by:** The specification cites no news-derived figures; targets in `05-evaluation-plan.md` are set from primary sources or marked as hypotheses to be measured.

### M7 — Non-functional requirements are absent
- **Where:** *Metrics* lists p95/p99 latency but sets no budget; nothing on availability, throughput, tenancy, retention, or privacy.
- **Problem:** "Append-only audit log" collides with data-protection erasure rights if records contain personal data; token and signing keys need management; latency budgets determine whether SMT critics can sit in the synchronous path at all.
- **Requested change:** An NFR section with initial targets and an explicit note on which are hypotheses.
- **Resolved by:** `01-specification.md` §6 (`NFR-01`–`NFR-20`).

### M8 — Domain gating is asserted, not designed
- **Where:** *Formalization is valuable but dangerous* ("Use it where the input, rules, and action semantics are sufficiently bounded").
- **Problem:** No mechanism decides, at runtime, whether an action is inside a formalizable domain, which critics apply, and what happens when none do.
- **Requested change:** An action-class registry: each `(tool, intent)` maps to a critic set and a rollout mode; unregistered actions are policy-only or `ABSTAIN` by configuration.
- **Resolved by:** `FR-30`–`FR-33`, `02-technical-plan.md` §4.4.

### M9 — Human approval is a state machine, not a verdict
- **Where:** *Deterministic outcomes* (`REQUIRES_APPROVAL`), audit record (`human_decision`).
- **Problem:** No states (requested, pending, approved, rejected, expired, superseded), no TTL, no separation of duties (the proposing principal and the agent must not be able to approve), no statement of what the approver must be shown (all abstentions and counterexamples), and no binding of the approval to the exact envelope (see M2).
- **Resolved by:** `FR-40`–`FR-46`, `02-technical-plan.md` §4.6 (approval state machine).

### M10 — The phase plan has no exit criteria, and the trajectory-monitor phase understates what the cited paper requires
- **Where:** *Phased Build Plan*, *Phase 3*.
- **Problem:** No phase names an owner, an exit gate or a measurable acceptance. Phase 3 says "50–100 adversarial trajectories"; the verified paper's sample-complexity result gives ≈51 trajectories for low-entropy backends but ≈226 for high-entropy ones at ±0.3 bit. More importantly, the paper shows monitor coverage is a property of the *governed model's* attack distribution, so **the monitor must be re-certified whenever the governed model changes**. The synthesis does not say this, and it is an operational requirement with cost.
- **Resolved by:** `03-work-breakdown.md` (exit gates per phase), `FR-52`–`FR-54` (monitor certification and re-certification), `05-evaluation-plan.md` §6.

## 5. Minor issues

| ID | Where | Issue | Requested change |
|---|---|---|---|
| m1 | Executive Conclusion | `INV-16` and `DEC-008` are referenced but never defined; they appear to belong to an upstream registry. | Define them or link the registry. The SDD adopts their evident content as `INV-01`/`INV-02` and flags the mapping for confirmation. |
| m2 | Throughout | Terminology drifts: wrapper, harness, gate, critic, verifier, policy decision point, enforcement layer. | Add a glossary. (`01-specification.md` §2.) |
| m3 | *Model Perspectives* | Convergence among five LLMs is presented as corroboration. The models share training corpora; agreement is correlated, not independent evidence. | Keep the section as provenance of the analysis, label it as such, and do not count agreement as votes. |
| m4 | *Reference Architecture* diagram | No feedback edge from execution outcome to the temporal monitor and the evidence plane; only critics are shown emitting `REPAIR`, but policy denials can also carry structured reasons. | Add the edges; allow structured reason codes on any verdict. |
| m5 | *Contract shape* | `policy_version` and `action_id` appear in the model-generated envelope. | Both are harness-stamped (`action_id` as UUIDv7 to prevent replay/collision). |
| m6 | *Compliance evidence* record | Missing: envelope digest, per-rule outcomes (not only IDs evaluated), critic versions, latency, mode (shadow/advisory/enforce), trace ID, fact provenance, decision-token ID, previous-record hash for tamper evidence. | Adopt `schemas/decision-record.schema.json`. |
| m7 | *Metrics* | "False-block rate" and "policy-violation recall" need a labelling protocol and a violation corpus to be computable. | Define both (`05-evaluation-plan.md` §3). |
| m8 | *Benchmarks* | τ²-bench measures agent policy adherence in customer-service domains. Evaluating a *harness* on it needs a harness-on vs harness-off protocol and a translation of τ-bench policies into Rego; that is real effort. | State the protocol and budget it (`P4-03`). |
| m9 | *Constrained Decoding Position* | The two-stage approach should state that free-form reasoning text is never an input to the policy decision point. | Add: reasoning is advisory, may be logged with redaction, never evaluated as policy input (`INV-01`). |
| m10 | *Appendix ADRs* | ADRs lack Context, Alternatives Considered and Consequences. ADR-002 is "Adopt" without the gating mechanism (M8). | Re-issue in MADR format (`docs/sdd/adr/`). |
| m11 | *Risks and Mitigations* | No likelihood, impact, owner or trigger. | Convert to a risk register (`06-delivery-and-governance.md` §9). |
| m12 | *Defensible message* | Good. Should be paired with an explicit "not covered" list so the claim cannot be over-read. | Add scope disclaimers (`01-specification.md` §3.3). |
| m13 | *Findings Where Models Converge*, substrates row | Scallop, DeepProbLog and PyReason are differentiable/probabilistic substrates that ADR-004 excludes from the control path. Listing them as "sufficient for an experiment" invites scope creep. | Mark them as R&D-track only. |
| m14 | *Phase 2* | "Read-only Prolog MCP service" is recommended, but the cited PrologMCP reference server exposes `consult_text` and `replace_predicate`, i.e. the agent loads and hot-patches the program. | State that the rulebase is harness-owned and signed, the agent may only query, and those tools are disabled or absent (`FR-62`). |

## 6. Citation audit

Seven arXiv sources were retrieved through the alphaXiv service and checked against the claims made about them. Two news sources and all vendor/market pages could not be reached from this environment.

| Source (as cited) | Status | Finding |
|---|---|---|
| AgentSpec, arXiv 2503.18666 | **Verified** (Wang, Poskitt, Sun; SMU; ICSE '26) | Claims accurate but under-scoped. >90% prevention is on RedCode-Exec (24/25 categories); 100% on SafeAgentBench with safe-task success dropping 58.62%→54.26%; 100% on 8 driving scenarios. Overhead: 1.42 ms parse, 1.11–2.83 ms predicate evaluation. LLM-generated rules: 87.26% enforcement (code), 95.56% precision / **70.96% recall** (embodied), 5/8 zero-shot (driving). The recall figure should temper "retained meaningful effectiveness". |
| Grammars of Formal Uncertainty, arXiv 2505.20047 | **Verified** (CWRU + Microsoft) | Accurately represented: SMT autoformalization improves logical tasks (ProofWriter) and harms knowledge-heavy tasks (FOLIO); token-entropy UQ inadequate; grammar-based metrics with selective verification cut errors 14–100% with small abstention. |
| Attack Distribution Entropy as a Coverage Bound, arXiv 2608.01388 | **Verified** (single author, Ryonix Labs / Flock.io; IEEE IS 2026) | The bound and the pre-deployment test exist as described. Caveats the synthesis omits: n = 8 backends, one domain (AgentDojo banking), industry preprint; best observed recall 68–75%; 2 of 8 invariants were suppressed for false-positive rate >5%; sample size ≈51 for low-entropy but ≈226 for high-entropy backends; adaptive adversaries can inject entropy; coverage is a property of the governed model, so model changes require re-certification. |
| PrologMCP, arXiv 2606.14935 | **Verified** (Royal Holloway) | Exists and is MCP-based, but it is a **stateful, agent-writable** formalizer tool (`consult_text`, `replace_predicate`, nine tools), evaluated on PARARULE-Plus synthetic deduction, not policy compliance. Using it as a read-only verifier requires removing the write tools and harness-owning the rulebase. |
| Towards a Science of AI Agent Reliability, arXiv 2602.16666 | **Verified** (Princeton) | Supports "reliability lags capability" (15 models, 24 months, GAIA + τ-bench; four dimensions, 12 metrics). Does **not** contain the "~50% document-fidelity loss" figure. |
| Robust Planning with Compound LLM Architectures (LLM-Modulo), arXiv 2411.14484 | **Verified** (ASU) | Supports generate-test-critique with sound critics and a 10-iteration budget. Two design facts the synthesis should carry: detailed typed feedback beats binary feedback; the guarantee is *soundness of accepted outputs*, not completeness (Travel Planner accuracy rose only to 24–25%), so expect a high share of repair-exhausted / abstained proposals. |
| Lobster, arXiv 2503.21937 | **Verified with discrepancy** (UPenn) | Version retrieved reports 1.2×–16× speedups, **3.9× average across ten tasks**, not "5.3× across eight applications". Verify against the specific version before quoting. Not on the v1 control path in any case. |
| *Why most enterprise agent pilots never reach deployment* (artificialintelligence-news.com) | **Unverifiable here** | Secondary source for 89% / 14%. Do not use in product or investor material without the primary survey. |
| *Microsoft long-agent reliability issues* (cryptobriefing.com) | **Unverifiable here** | Secondary source, cryptocurrency news outlet, for the ~50% fidelity figure. Find the primary Microsoft publication or drop the number. |
| Remaining arXiv/ACL/NeurIPS/W3C sources | Not individually retrieved | Titles and venues are consistent with the reviewer's knowledge; none is load-bearing for a design decision beyond those above. |

## 7. Consistency and completeness checks

| Check | Result |
|---|---|
| Outcome set identical across sections | **Fail** — five vs six (M1). |
| Verifier results distinguished from verdicts | **Fail** (M1). |
| Envelope fields attributed to the party that can vouch for them | **Fail** (M3, m5). |
| Every hard gate has a stated negative fixture | **Pass** for the eight gates in the mutation table; **not stated** for token binding, approval binding, engine unavailability or audit write failure, because those controls are absent (M2, M4). |
| Every ADR has context, alternatives, consequences | **Fail** (m10). |
| Every phase has an exit criterion | **Fail** (M10). |
| Research claims traceable to a primary source | **Partial** (M6). |
| "Reject/defer" list consistent with the rest of the document | **Mostly pass**; m13 is the exception. |
| Security controls cover both directions of the tool boundary (call and result) | **Fail** (M5e). |

## 8. Requested changes (checklist for the authors)

1. [ ] Rewrite *Deterministic outcomes* to separate verifier results from verdicts, add the resolution order, the repair budget and `ABSTAIN` escalation rules. (M1)
2. [ ] Add decision-token binding, approval binding and fact freshness to the architecture and the audit record. (M2)
3. [ ] Split the contract shape into agent proposal and harness context; add fact provenance. (M3)
4. [ ] Add a failure-mode table. (M4)
5. [ ] Replace the security control list with a threat model, including the agent as adversary and the result return path. (M5)
6. [ ] Remove or re-source news-derived figures; scope the AgentSpec claim; reconcile the Lobster figure; add the PrologMCP writability caveat. (M6, m14)
7. [ ] Add NFRs with initial targets. (M7)
8. [ ] Add the action-class registry. (M8)
9. [ ] Add the approval state machine. (M9)
10. [ ] Add exit gates per phase and the monitor re-certification requirement. (M10)
11. [ ] Glossary, MADR-format ADRs, risk register, scope disclaimers. (m2, m10, m11, m12)

## 9. How the SDD package responds

| Review item | Resolved in |
|---|---|
| M1 | **Partially resolved in v0.1**, closed in v0.2: `01-specification.md` §5.3–5.6, ADR-0014 |
| M2 | **Partially resolved in v0.1** (approval binding was unworkable), closed in v0.2: ADR-0015, `FR-20`–`FR-27`, `SEC-03` |
| M3 | **Partially resolved in v0.1** (no provider registry, no evidence provenance), closed in v0.2: `FR-10`–`FR-14`, `SEC-11`, `T-21` |
| M4 | `01-specification.md` §7; `NFR-10`–`NFR-14` |
| M5 | `04-threat-model.md` |
| M6 | `05-evaluation-plan.md` (targets marked as measured vs hypothesis); no news-derived figures in the spec |
| M7 | `01-specification.md` §6 |
| M8 | `FR-30`–`FR-33`; `02-technical-plan.md` §4.4 |
| M9 | `FR-40`–`FR-46`; `02-technical-plan.md` §4.6 |
| M10 | `03-work-breakdown.md`; `FR-52`–`FR-54`; `05-evaluation-plan.md` §6 |
| m1–m14 | `01-specification.md` §2 (glossary), §3.3 (scope disclaimers); `docs/sdd/adr/`; `06-delivery-and-governance.md` §9 (risk register); `FR-62` |

## Reviewer limitations

- The review was performed in a sandbox with restricted network egress. Seven arXiv papers were retrieved through the alphaXiv service and read in summary or full-text form; news, vendor, market-research and W3C pages were not reachable and are marked unverifiable rather than wrong.
- No code from any cited repository was executed.
- The upstream registry that defines `INV-16` and `DEC-008` was not available; their content was inferred from the synthesis.
- A second review round found 71 further issues, including one critical defect introduced by this review's own recommendation on progressive rollout. Treat this document as a first pass, not a clearance.
- The reviewer is an AI system. Findings about evidence quality were checked against retrieved sources; findings about design gaps are engineering judgement and should be challenged by the human architecture reviewers named in `06-delivery-and-governance.md`.
