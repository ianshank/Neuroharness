# Neuroharness Delivery and Governance Model

**Status:** Draft v0.2 · **Date:** 2026-09-18 · **Applies to:** every change to code, policy, critics, fixtures and the SDD package

## 1. Operating model
| Role | Accountable for |
|---|---|
| Product owner | Scope, reference workflow, acceptance, claims (Art. X). |
| Tech lead / architect | Technical plan, ADRs, TCB integrity, replay corpus. |
| Security lead | Threat model, `SEC-` requirements, policy change approval, bypass exercise. |
| Policy owner (per workflow) | Natural-language invariants, Rego, fixtures, labelling. |
| Evaluation lead | Metrics, gates, certification protocol, reports. |
| Delivery lead | Work breakdown, cadence, exit gates, risk register. |
| SRE | Deployment, rollout modes, alerts, key management. |

## 2. Branching, commits and releases
- **Trunk-based development.** `main` is always releasable. Feature branches live ≤ 3 days; squash-merge; no long-lived release branches.
- **Conventional Commits** (`feat`, `fix`, `docs`, `test`, `policy`, `chore`, `refactor`) with the requirement or task ID in the body (`Implements: FR-21, P1-08`).
- **Semantic versioning** for the runtime; policy bundles and registry carry date-based versions plus digests. Release notes are generated from commits; every release publishes SBOM, provenance and signatures.
- **Feature flags** are used only for rollout mode transitions (registry-driven), never to hide untested code paths in the TCB.
- **Vertical-slice rule for large tasks.** A 10-day task lands as several pull requests, each of which must pass every gate and must not create an `ALLOW` path that reaches the broker. Unwired modules are permitted; a half-wired enforcement path is not. The registry `mode` (including `halted`) is the only runtime switch.
- **Agent-authored commits.** Signing identity for commits authored by AI agents is decided in `P0-10`; the reviewing human is the accountable signer.
- **Branch protection:** required reviews (CODEOWNERS for `policy/`, `fixtures/`, `src/neuroharness/{pdp,resolve,tokens,broker,evidence}`), required status checks (all CI gates), signed commits, linear history.

## 3. CI quality gates (all blocking unless stated)
1. Lint and type-check (ruff, mypy strict, Regal for Rego).
2. Unit tests with coverage thresholds (TCB ≥ 90% lines; resolver 100% branches).
3. Policy unit tests with coverage ≥ 95% of rules.
4. **Negative mutation fixtures — 100% of `active` fixtures killed, plus a check that every hard registry rule has an active fixture. No manual override exists for this stage.** (Fixture lifecycle: `05-evaluation-plan.md` §1a.)
5. Behaviour scenarios (pytest-bdd).
6. Integration (testcontainers: OPA, PostgreSQL, broker, provider stubs).
7. Golden replay — 0 diffs.
8. Code mutation score ≥ 80% on pure packages (resolver, tokens, canonicalization) per pull request, unit-only test selection (blocking from Phase 2); full trusted-computing-base mutation score runs nightly on `main` with a trend alert, because container-backed suites cannot finish per pull request.
9. Security: CodeQL, pip-audit, gitleaks, Trivy on images.
10. Schema conformance: envelope, decision record, registry, PDP input (asserts no `claims`).
11. Docs: link check; ADR index consistency; spec IDs referenced by tests exist.
12. Build: reproducible container, SBOM (CycloneDX) and provenance attestation on every pull request; **cosign signing of images and bundles runs on `main` and tags only**, so signing keys are never exposed to pull-request CI.

Deleting or skipping a fixture or test requires an ADR reference in the commit body; CI checks for it.

## 4. Supply-chain security
- Dependencies pinned with `uv.lock`; Renovate/Dependabot with grouped weekly updates; pip-audit blocking on known vulnerabilities without an accepted exception.
- Build provenance (SLSA-style attestations from CI); images distroless, non-root, signed with cosign; verification enforced at deploy.
- Policy bundles, registry and critic packs are built in CI from reviewed sources and signed; the PDP and registry loader verify signatures (`SEC-05`).
- OpenSSF Scorecard tracked; target ≥ 8 by v1.0.
- Secrets never in the repo (gitleaks); token/bundle keys only in KMS (`NFR-17`).

## 5. Progressive rollout of enforcement (`ADR-0010`)
Per action class and per critic:
1. **Shadow** — evaluate and record; broker executes with a shadow token. Minimum one week and ≥ 500 decisions (H) before promotion; labelled false-block rate reported.
2. **Advisory** — as shadow, plus verdicts surfaced to operators (and optionally typed warnings to the agent, `OQ-06`). Used to validate alerting and approver workflows.
3. **Enforce** — broker consults the verdict; blocking verdicts block.
Promotion criteria: 100% mutation kill for the class's active hard-gate fixtures; labelled false-block rate ≤ threshold; no open blocking risk; monitor certified if the class enforces one.

**Modes never weaken fail-closed behaviour.** In `shadow` and `advisory` the harness still evaluates, still records, and still requires a (shadow) token; infrastructure failures block in every mode (`FR-80`, `INV-03`). This is why shadow data is representative and why a shadow class is not an unguarded class.

**Demotion versus halt.** These are different levers for different incidents:

| Incident | Lever | Authority |
|---|---|---|
| False-block spike, gate too aggressive | `demote_mode` to advisory | Two override-group principals, ≤ 1 h, ticket, then a signed registry change (`FR-48`) |
| Bundle or registry integrity failure, discovered bypass, token-key compromise, effect mismatches | `halt_class` (deny everything) | One override-group principal (`FR-49`) |
| Certification trigger on a monitor | Automatic demotion of that critic to advisory | Automatic (`FR-54`) |

Demoting on an integrity failure would turn a tampered bundle into unguarded execution, so it is prohibited: integrity failures halt. All transitions are signed registry changes or recorded overrides.

## 6. Definition of Ready and Definition of Done

**Definition of Ready (a task may start when):**
- Requirement IDs exist and are accepted; acceptance scenario(s) written.
- For a hard gate: mutation fixture ID reserved; source NL invariant text present.
- Fact dependencies and providers identified; threat-model delta considered.
- Size and owner assigned; dependencies met.

**Definition of Done (a task is Done when):**
- Code/policy merged to `main` with all CI gates green.
- Tests reference requirement IDs; new hard gates have killing fixtures in CI.
- Golden corpus updated if verdicts intentionally changed, with a note.
- Observability added (spans, metrics) for new stages.
- Docs updated: spec status, ADR if design changed, runbook if operational behaviour changed.
- Threat-model delta reviewed for any change touching a trust boundary.
- Evidence artifact produced where the task says so (report, certification record).

## 7. Compliance mapping (indicative; requires legal review)
| Obligation / framework | What it asks | How Neuroharness supports it |
|---|---|---|
| EU AI Act — record-keeping and traceability (Art. 12) | Automatic logging enabling traceability of the system's functioning | Decision records (`FR-70`–`FR-73`), hash chain and checkpoints (`SEC-10`), replay (`FR-71`). |
| EU AI Act — human oversight (Art. 14) | Ability for humans to intervene, override, or halt | Approval state machine with full disclosure and re-evaluation (`FR-40`–`FR-47`); non-approvable classes; `halt_class` as a real stop control (`FR-49`); recorded overrides (Art. IX). |
| EU AI Act — risk management (Art. 9) | Iterative risk identification and mitigation | Threat model, risk register, mutation and bypass suites. |
| NIST AI RMF | Govern / Map / Measure / Manage | Constitution and roles (Govern); spec scope and threat model (Map); evaluation plan (Measure); rollout modes and risk register (Manage). |
| ISO/IEC 42001 | AI management system controls incl. logging, change control | Policy change management (§10), CI gates, evidence export. |
| SOC 2 (change management, logical access) | Reviewed changes; least privilege | Two-person rule; broker-only credentials; signed bundles. |
The harness produces evidence; it does not by itself make a deployment compliant. This table is an **indicative mapping, not compliance-as-code**, and has not been reviewed by counsel (`R-22`).

## 8. RACI (interim mapping under `P0-12`)

Roles referenced by other documents that must be mapped to real people in `P0-12` (`OQ-10`): **workflow owner** (labelling; product owner is the natural fit), **second security reviewer** (so separation of duties does not rest on one person), **compliance contact** (`OQ-05`), **agent-developer contact** (`OQ-04`), **override group** (`FR-48`). v1 is **business-hours support with no on-call rota**, which is why `NFR-10` states the SLO as a target rather than a commitment.

> **Interim P0-12 assignment (2026-09-21):**
> - **Ian Cruickshank** is the sole interim workflow owner, security reviewer, and override authority.
> - Other roles remain TBD; compliance contact remains TBD (`OQ-05`).
> - **Known gap:** The requirement for two-person hard-gate review remains an explicit known gap until a second person is named. Do **not** invent or placeholder a second reviewer.
> - ADR acceptances may proceed with Ian as sole named reviewer for now.
> - **P0-13 deferral (2026-09-21):** KMS, object storage, staging cluster, and CI service identities are explicitly deferred. Increment 3 may proceed `P1-06a`-first only per `08-increment-2-plan.md` risk `R-D`. KMS and staging owners are not resolved and work must not block on `P0-13`.
| Activity | Product | Tech lead | Security | Policy owner | Eval lead | Delivery | SRE |
|---|---|---|---|---|---|---|---|
| Spec changes | A | R | C | C | C | I | I |
| ADRs | C | A/R | C | I | I | I | C |
| Hard-gate policy change | I | C | A | R | C | I | I |
| Fixture/labelling | I | C | C | R | A | I | I |
| Rollout mode promotion | C | C | A | R | C | I | R |
| Release | C | R | C | I | C | A | R |
| Incident (harness) | I | R | A | C | I | I | R |
| Labelling adjudication | C | I | A | R | R | I | I |
| Monitor certification decision | C | C | A | C | R | I | I |
| Runbook ownership | I | C | C | I | I | A | R |
| Key rotation / tenant onboarding | I | C | A | I | I | I | R |
| Dependency updates | I | A/R | C | I | I | I | C |
| Claims document (Art. X) | A | C | R | I | C | I | I |
| Threat-model delta review | I | C | A/R | C | I | I | I |

## 9. Risk register
| ID | Risk | Likelihood | Impact | Owner | Trigger / indicator | Response |
|---|---|---|---|---|---|---|
| R-01 | Autoformalization error: contract or rule encodes the wrong thing | M | H | Policy owner | Labelled false-block or missed violation | NL provenance, review, fixtures, shadow before enforce, abstain on uncertainty. |
| R-02 | Latency budget exceeded with SMT on the synchronous path | M | M | Tech lead | `NFR-02` p95 breached in `P2-03` | Cache safe facts; move contract off path; tighten theory. |
| R-03 | Mutation suite gives false confidence (holes not covered by fixtures) | M | H | Eval lead | Bypass exercise finds untested path | Add fixture per finding; periodic bypass exercises; mutation score on code. |
| R-04 | Fact-provider compromise or drift | L | H | Security | Receipt/state mismatch audit | Provider authentication, digests, cross-checks, TCB review. |
| R-05 | Tool semantics drift (spec gaming succeeds) | M | H | Tech lead | Post-hoc effect audit mismatch | 1:1 connectors, enumerations, periodic audits, add invariants. |
| R-06 | Approval fatigue / rubber-stamping | M | M | Product | Approval latency ↓ with volume ↑ | Tune approvable classes; disclosure UI; sampling reviews. |
| R-07 | Monitor coverage low for the governed model (high-entropy) | M | M | Eval lead | Certification `H > 2.0` | Keep advisory; plan probabilistic monitor; add stateless rules where possible. |
| R-08 | Prompt-injected proposals exhaust repair budgets / degrade agent utility | M | L | Product | Repair-exhausted rate ↑ | Result typing, budget tuning, upstream input controls. |
| R-09 | Team capacity below assumption | M | M | Delivery | Velocity < plan two iterations running | Re-baseline; cut Phase 3 to advisory-only for v1. |
| R-10 | Over-claiming in product material | L | H | Product | Claim without measured value | Art. X review; claims doc gate. |
| R-11 | Key-management gaps in target environments | M | H | SRE | `OQ-03` unresolved by `P1-07` | Provision KMS; interim HMAC with strict rotation runbook. |
| R-12 | Scope creep toward ontology/CoT verification | M | M | Tech lead | Backlog items outside `ADR-0002`/`ADR-0005` | ADR gate; deferred backlog list. |
| R-13 | Solver version non-determinism breaks replay | M | M | Tech lead | Replay diff after a dependency bump | Deterministic `rlimit` as the primary bound; solver version in the critic version; Z3 pinned outside the weekly update group; `P2-08` upgrade procedure. |
| R-14 | Key-person concentration in the security/policy role | H | H | Delivery lead | One 0.5 FTE owns threat model, policy pack, labelling adjudication and bypass exercise | Name a workflow owner and a second security reviewer in `P0-12`; external red team for `P4-05`. |
| R-15 | Adoption risk: false blocks or latency push developers to wire tools directly | M | H | Product | `nh_verdicts_total` by mode trending toward advisory; integration requests to bypass | Broker holds credentials; track false-block rate as a headline metric; demotion requires two principals so it cannot be done quietly. |
| R-16 | Temporal-compiler licensing (MONA/Spot are GPL) conflicts with the project licence | M | M | Tech lead | `OQ-09` unresolved by `P0-11` | Decide build vs vendor in `ADR-0013`; a hand-written DFA compiler for the bounded subset is feasible. |
| R-17 | PostgreSQL as a fail-closed single point of failure against a 99.9% SLO | M | H | SRE | Any store outage abstains everything | HA and backup/restore in `P1-22`; correlated-failure alerting (`NFR-21`). |
| R-18 | Vendor and platform lock-in (OPA bundle signing, KMS algorithms, Kubernetes) | M | M | Tech lead | Portability requests | `NFR-19`; ECDSA P-256 default (`ADR-0009`); conformance suite if a second evaluator is ever adopted. |
| R-19 | Model-vendor churn forces repeated monitor re-certification | H | M | Evaluation lead | Certification trigger fires on each vendor update | Automate corpus generation and the entropy test; budget it as recurring; keep the monitor advisory where cost exceeds value. |
| R-20 | LLM API cost for corpora and benchmarks is unbudgeted | M | M | Delivery lead | `P3-03`, `P4-03` spend | Explicit cost lines in those tasks; τ²-bench scoped to one domain, k=3. |
| R-21 | Fact-provider integrity remains the soft underbelly after `SEC-11` | M | H | Security | Effect mismatches, state audit divergence | Effect verification (`FR-57`); provider trust levels; post-hoc audits. |
| R-22 | Compliance mapping never receives legal review and is read as a claim | M | M | Product | Mapping cited externally | Marked indicative; `P4-02` walkthrough; counsel review before any external use. |

## 10. Policy change management
- Rego, registry, critic packs and fixtures are production code: PR with linked requirement IDs, `opa test`, Regal, mutation gate, and **two-person review** including the security lead for hard gates (`SEC-06`).
- Every new rule stores its natural-language source beside it (`FR-32`) and is introduced in shadow mode.
- **Emergency changes** (e.g. blocking a newly discovered abuse) may be merged with one reviewer if they only *tighten* a gate; a post-hoc second review within 24 h and a fixture are mandatory. Loosening changes are never emergency changes.
- LLM-assisted authoring is allowed for drafts; acceptance is by the same tests and reviews, and the assisted origin is recorded.

## 11. Incident response (harness-specific)
Runbooks are deliverables, not promises: batch 1 in `P1-23` (token replay, bundle or registry integrity failure, evidence store unavailable, fact-provider outage) and batch 2 in `P3-07` (bypass discovered, demotion and halt procedure, signing-key rotation, effect-mismatch response). Each states the fail-closed posture, who is paged during business hours, which lever applies (halt for integrity and bypass; demote only for false-block spikes), the authority required, and what evidence to preserve.

## 12. Delivery metrics
- DORA four keys (deployment frequency, lead time, change-failure rate, time to restore) for the harness itself.
- Harness-specific: mutation kill rate trend, replay diff count, false-block rate, `ABSTAIN` rate by reason, approval latency, time-to-certify a monitor after a model change.
- Reviewed at each phase exit; targets re-baselined with measured data.
