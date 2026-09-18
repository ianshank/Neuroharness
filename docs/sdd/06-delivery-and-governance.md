# Neuroharness Delivery and Governance Model

**Status:** Draft v0.1 · **Date:** 2026-09-18 · **Applies to:** every change to code, policy, critics, fixtures and the SDD package

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
- **Branch protection:** required reviews (CODEOWNERS for `policy/`, `fixtures/`, `src/neuroharness/{pdp,resolve,tokens,broker,evidence}`), required status checks (all CI gates), signed commits, linear history.

## 3. CI quality gates (all blocking unless stated)
1. Lint and type-check (ruff, mypy strict, Regal for Rego).
2. Unit tests with coverage thresholds (TCB ≥ 90% lines; resolver 100% branches).
3. Policy unit tests with coverage ≥ 95% of rules.
4. **Negative mutation fixtures — 100% killed; no manual override exists for this stage.**
5. Behaviour scenarios (pytest-bdd).
6. Integration (testcontainers: OPA, PostgreSQL, broker, provider stubs).
7. Golden replay — 0 diffs.
8. Code mutation score ≥ 80% on TCB (blocking from Phase 2).
9. Security: CodeQL, pip-audit, gitleaks, Trivy on images.
10. Schema conformance: envelope, decision record, registry, PDP input (asserts no `claims`).
11. Docs: link check; ADR index consistency; spec IDs referenced by tests exist.
12. Build: reproducible container, SBOM (CycloneDX), provenance attestation, cosign signing of images and policy bundles.

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
Promotion criteria: mutation kill 100% for the class's hard gates; false-block rate ≤ threshold; no open `blocking` risk; monitor certified (`05-evaluation-plan.md` §6) if the class uses one. Demotion is immediate on: false-block spike, integrity failure, certification trigger. All transitions are signed registry changes and are recorded.

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
| EU AI Act — human oversight (Art. 14) | Ability for humans to intervene, override, or halt | `REQUIRES_APPROVAL` state machine with full disclosure (`FR-40`–`FR-46`); `never_approvable` classes; recorded overrides (Art. IX). |
| EU AI Act — risk management (Art. 9) | Iterative risk identification and mitigation | Threat model, risk register, mutation and bypass suites. |
| NIST AI RMF | Govern / Map / Measure / Manage | Constitution and roles (Govern); spec scope and threat model (Map); evaluation plan (Measure); rollout modes and risk register (Manage). |
| ISO/IEC 42001 | AI management system controls incl. logging, change control | Policy change management (§10), CI gates, evidence export. |
| SOC 2 (change management, logical access) | Reviewed changes; least privilege | Two-person rule; broker-only credentials; signed bundles. |
The harness produces evidence; it does not by itself make a deployment compliant.

## 8. RACI (to be confirmed in `P0-12`)
| Activity | Product | Tech lead | Security | Policy owner | Eval lead | Delivery | SRE |
|---|---|---|---|---|---|---|---|
| Spec changes | A | R | C | C | C | I | I |
| ADRs | C | A/R | C | I | I | I | C |
| Hard-gate policy change | I | C | A | R | C | I | I |
| Fixture/labelling | I | C | C | R | A | I | I |
| Rollout mode promotion | C | C | A | R | C | I | R |
| Release | C | R | C | I | C | A | R |
| Incident (harness) | I | R | A | C | I | I | R |

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

## 10. Policy change management
- Rego, registry, critic packs and fixtures are production code: PR with linked requirement IDs, `opa test`, Regal, mutation gate, and **two-person review** including the security lead for hard gates (`SEC-06`).
- Every new rule stores its natural-language source beside it (`FR-32`) and is introduced in shadow mode.
- **Emergency changes** (e.g. blocking a newly discovered abuse) may be merged with one reviewer if they only *tighten* a gate; a post-hoc second review within 24 h and a fixture are mandatory. Loosening changes are never emergency changes.
- LLM-assisted authoring is allowed for drafts; acceptance is by the same tests and reviews, and the assisted origin is recorded.

## 11. Incident response (harness-specific)
Runbooks (written in Phase 1–3) cover: token reuse detected; bundle or registry integrity failure; evidence store unavailable; fact-provider outage; bypass discovered; monitor demotion; signing-key rotation. Each runbook states the fail-closed posture, who is paged, how to demote a class to advisory *without* loosening a gate, and what evidence to preserve.

## 12. Delivery metrics
- DORA four keys (deployment frequency, lead time, change-failure rate, time to restore) for the harness itself.
- Harness-specific: mutation kill rate trend, replay diff count, false-block rate, `ABSTAIN` rate by reason, approval latency, time-to-certify a monitor after a model change.
- Reviewed at each phase exit; targets re-baselined with measured data.
