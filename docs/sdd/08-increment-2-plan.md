# Increment 2 — Repair the drift, close the gate, build the boundary's first half

**Status:** Draft for review · **Date:** 2026-09-18 · **Supersedes:** the first draft of this file, in substance (see §2) · **Constrained by:** `00-constitution.md` · **Implements:** `P0-06`, `P0-11`, `P1-02`, `P1-09` (completion), `P1-27`, parts of `P0-10`, plus six repairs to merged code that no WBS line owns

Every quantitative claim in this document was measured against the tree at `b16f3ba` during planning. The commands and their output are in Appendix A. Where a claim comes from an existing document rather than from a measurement, it is attributed, and where the document turned out to be wrong the correction is in Appendix B.

---

## 1. The case, in three facts

### Fact 1 — Increment 1 shipped across a gate that is 3 of 7, and nobody wrote that down

The Phase 0 exit gate in `03-work-breakdown.md` has seven clauses. Four are unsatisfied:

| Clause | State |
|---|---|
| Schemas frozen | ✅ `docs/sdd/schemas/` + a blocking CI job |
| Registry **and provider formats** signed | ❌ registry carries a digest, not a signature (`NullVerifier`, `DigestVerifier` are the only verifiers); the provider format (`P0-06`) does not exist in any form |
| Threat model signed | ❌ `04-threat-model.md:3` — *"Draft v0.2 — needs security review"* |
| Mutation matrix complete with states | ✅ 37 declarations, schema-enforced, cross-checked in CI |
| CI skeleton green with all stages | ❌ 4 of 12 stages exist |
| KMS and environments provisioned | ❌ nothing: no key material, no object storage, no cluster, no CI identities |
| Roles named | ❌ `06-delivery-and-governance.md:95` is titled *"RACI (to be confirmed in `P0-12`)"* and names no people |

`07-increment-1-plan.md` §6 declares two of these outstanding (`P0-13`, `P0-14`). It does not declare the other two. Shipping increment 1 over an unmet gate was defensible once — the deterministic core needed no infrastructure and the gate's unmet clauses did not touch it. Shipping increment 2 the same way, without saying so, is the moment the gate becomes decorative. This plan therefore puts gate closure inside the increment rather than beside it.

### Fact 2 — The obvious next increment is not reachable, and the reason is a missing document

`07-increment-1-plan.md:200` says increment 2 "attaches the policy decision point and fact providers to these seams (`P1-04`, `P1-18`)". Read as parallel work, that is wrong twice:

```
P0-06 ──▶ P1-18 ──▶ P1-04 ──▶ { P1-11, P1-12, P1-15, P1-16 }
  ▲                              │
  │                              └──▶ P1-15 ──▶ every WF-01..WF-05 fixture
  └── does not exist as an artifact
```

`P1-04`'s `Depends on` column is `P0-05, P1-18` — serial, not parallel. And `P1-18` depends on `P0-06`, the fact-provider specification, which has no artifact anywhere in the repository. `FR-10`–`FR-14` and `SEC-11` exist as requirements; `02-technical-plan.md:150-152` sketches a one-line provider interface; there is no provider registry format, no auth model, no per-provider TTL, and — the clause that matters — no *statement, per provider, of which tools can write to its backing system*. That statement is the `SEC-11`/`T-21` anti-laundering control. It is the thing that stops an agent writing a fact and then citing it as evidence. It cannot be derived by an engineer at implementation time, and a fact layer built without it would have `MUT-23`'s gate defined by whatever the code happened to do — the exact inversion `05-evaluation-plan.md` §1a exists to prevent.

Only `P1-02`, `P1-06a`, `P1-09` and `P1-27` in all of Phase 1 are unblocked by their declared dependencies.

### Fact 3 — Increment 1 left three live defects in merged code, and increment 2 is what makes them bite

These are not stylistic debt. Each is a wrong behaviour today or a hole that opens the moment the next component is written.

**3a. The resource-key grammar has three sources of truth and has already drifted.** Measured:

```
key                                  registry  record
repo:my-repo                         True      True
cluster-prod:svc-a                   True      False   <- registry-valid, unrecordable
s3-bucket:data                       True      False   <- registry-valid, unrecordable
s3:bucket_name                       False     True    <- recordable, no lookup matches
my_kind:foo                          False     True    <- recordable, no lookup matches
```

`registry/resource_keys.py` admits hyphens in a kind segment and refuses underscores. `models/record.py` and the two published JSON Schemas do the opposite. A hyphenated resource kind — `cluster-prod`, `s3-bucket`, `k8s-namespace`, which is what any real deployment writes — is accepted by the signed registry, becomes the broker's lease identity under `FR-25`, and then cannot be recorded. Per the `C-01` resolution that is fail-closed: `SCHEMA_INVALID` → `ABSTAIN`. So **lease contention on a hyphen-named resource is reported to an operator as a harness malfunction rather than as contention, and the whole class abstains until someone renames the resource.** `tests/unit/test_anchored_patterns.py` checks anchoring, not cross-module agreement; nothing sits between the three halves. The broker (`P1-08`) is built directly on this.

**3b. Fact escalation is dead under any valid registry, and live for any new producer.** Measured:

```
required=False escalatable=True   blocks=False -> ALLOW             (escalation never reached)
required=True  escalatable=True   blocks=True  -> REQUIRES_APPROVAL (loader refuses this shape)
required=True  escalatable=False  blocks=True  -> ABSTAIN           (correct)
```

`FactState.blocks` is `required and not status.is_usable`, so only required facts reach the escalation arm — and `FactRequirement._check_escalation` refuses `required AND escalatable`. The safety property in §5.5 of the specification, *"never for facts marked `required: true`"*, is therefore held entirely by the loader being the only producer of `FactState`. The resolver never checks `fact.required`. `FactState` is a plain frozen dataclass with no validation on that flag. **Increment 2 adds fact providers; the first `FactState(required=True, escalatable=True)` any of them constructs turns a hard gate into a human-waveable formality, and no fixture would catch it.** This is the single strongest argument for repairing before building, and it was found by auditing the thing this increment was about to extend.

**3c. `EvaluationOutcome` lets `ALLOW` coexist with `token=None` and `evidence_failed=False`.** On the token-record failure path the pipeline withholds the token and returns `verdict=ALLOW, evidence_failed=False`. The docstring's defence — every consumer must read `permits_execution` — is correct, and the consumer is written in increment 2. A gateway that branches on `outcome.verdict` reads `ALLOW` on a decision that did not fully land. Related: the real `EvidenceWriter.write` stages the failed record in the WAL before re-raising, so on recovery the chain permanently asserts a `token_issued` for a token that was never returned and can never be spent — and `verify_chain` will find that chain perfectly valid.

**The conclusion these three facts force.** The valuable work is not "attach the next component". It is: repair what will otherwise be load-bearing under the next component, write the one document that unblocks a third of Phase 1, and build only the parts of the boundary that can be built honestly.

---

## 2. What the peer review overturned in this plan's own first draft

This section exists because the first draft of this file was wrong in ways that are worth recording, and because the same errors are easy to make again.

| First draft said | Evidence | Now |
|---|---|---|
| `facts/` (`P1-18`) ships this increment, promoting `MUT-04`, `MUT-14`, `MUT-23` `reserved → active` | `P1-18` depends on `P0-06`, absent. `MUT-23`'s gate *is* the missing writability statement | `P0-06` is written this increment. `P1-18` is a stretch, only if `P0-06` lands by the midpoint, and it claims no fixture the specification has not defined first |
| `policy/client.py` ships an in-process reference policy evaluator | Constitution Article V: verifiers are *outside* the governed runtime. `FR-50`/`SEC-05`/`SEC-06` specify signed Rego bundles; a Python evaluator has no bundle, so `policy_bundle_digest` — which `TokenService.verify` demands — would have to be fabricated. Fabricating a bundle digest so the pipeline runs is a code path where a non-authoritative artifact authorizes an action | **Cut entirely.** The `PolicyDecisionPoint` protocol is still defined so `P1-04` has a seam, but no evaluator is written |
| `gateway/` closes `FR-01` | `FR-01` is *"direct tool access impossible in the stack"*, whose acceptance is `P1-01a` and whose precondition is `P0-13`'s authenticated host | The orchestrator closes no `FR-01`. It is named `orchestrator`, not `gateway`, so nobody reads the name as the control |
| `identity/` closes `FR-06`, `SEC-12`, `SEC-13` | `SEC-12` needs a server-derived session from an authenticated host (`P0-13`). Nothing can legitimately produce `CredentialStatus.VERIFIED` without an identity provider | Deferred. A reference implementation that emits `VERIFIED` is a forgery of the control, not an implementation of it |
| `MUT-30` moves `partial → active` | `MUT-30` is already `active` | Struck |
| "`ruff` reports 168 findings" | Measured: **173** with the broad rule set, **1** with ruff's defaults; `mypy --strict` reports **48** errors in 9 files and had never been run on 7 of the 12 packages | Restated with measured numbers, and the framing changes: stage 1 is cheap, not a cleanup project |
| Three provably-unreachable resolver branches | Five sites, three of them outside `resolve/`, and the two resolver ones are unreachable by a nesting relation between two data tables that nothing asserts | Repaired as an invariant, not a coverage exclusion (§4.6) |

The pattern in the first five rows is one error made five times: **naming a requirement a module "carries" because the module is on the same subject, rather than because the module closes the requirement's acceptance criterion.** That is how increment 1 came to claim `P1-02`. Section 7 states the counter-rule.

---

## 3. Scope — three tracks

Three tracks because they need different people and have different risk. They are ordered by what blocks what, not by size.

| Track | What | Why now | Owner profile |
|---|---|---|---|
| **1. Repair** | Six defects in merged code (§4) | Each becomes load-bearing under increment 2 or 3. 3b opens the moment fact providers exist | Backend |
| **2. Close the gate** | `P0-06`, `P0-11`, plus the `P0-10` remainder that needs no infrastructure (§5) | `P0-06` unblocks a third of Phase 1. `P0-12`/`P0-13` are escalations, not tasks | Architect + security; two items are the sponsor's |
| **3. Build** | `P1-02`, the response type, the digest convention, `P1-09` completion, `P1-27`, three CI stages, two enforcement registries (§6) | Everything here is unblocked by the WBS dependency column and honest to claim | Backend + evaluation |

Tracks 1 and 3 are the same engineers, sequenced: **repair lands before the code that would sit on it.** Track 2 runs in parallel and gates the stretch.

---

## 4. Track 1 — repair what is already wrong

Ordered by cost of deferring one more increment.

### 4.1 One resource-key grammar (`FR-25`, `FR-34`, `C-02` class)

`registry/resource_keys.py` becomes the single source. `models/record.py` imports the pattern instead of restating it, and the two published JSON Schemas are **generated from it** rather than hand-kept — a generator plus a CI check that the checked-in schema matches what the generator emits, in the shape of the existing `test_no_magic_values` job. The length bound (`MAX_RESOURCE_KEY_LENGTH = 158`, chosen to fit inside the reason-code subject limit) is expressed once and enforced on both sides.

A property test asserts the two directions that are currently broken: every string `is_resource_key` accepts is recordable, and every string the record model accepts resolves under `split_resource_key`. That test fails on the tree as merged, which is how we know it is a test of something.

**Which grammar wins is a decision, not a merge.** Hyphens in kind segments are what deployments actually write; underscores are what the record model permits. Picking hyphens means the registry is right and the record model narrows; picking both means widening two patterns and the published schemas. It needs an ADR because the published schema is an external contract (§11, `D-1`).

### 4.2 Fact escalation — a specification defect, and the plan's own first answer was wrong

The hole is real: the resolver never checks `fact.required`, `FactState` is a plain frozen dataclass with no validation, and the §5.5 safety property is held entirely by the loader being the only producer. Adding fact providers adds a second producer.

**The obvious repair does not work, and it is worth recording why.** This plan's first answer was "move the rule to the type: `FactState.__post_init__` refuses `required=True, escalatable=True`". Checked against the suite: `tests/unit/test_resolver_truth_table.py:80`'s `fact()` helper defaults `required=True`, and four rows pass `escalatable=True` — `escalate/escalatable-fact-missing` (:314), `abstain/provider-error-is-never-escalatable` (:338), `deny/exhausted-budget-cannot-be-softened-into-an-approval-request` (:418) and `deny/approval-not-permitted-beats-an-escalatable-abstention` (:436). **Those four are the only rows that exercise the resolver's fact-escalation arm at all.** Making the shape unconstructible would delete them, which is the line `CLAUDE.md` draws, and would leave a resolver branch with no coverage.

Pulling that thread reaches the actual defect, which is one layer up. §5.5 says escalation is *"never for facts marked `required: true`"*; `FactRequirement._check_escalation` enforces it; and a non-required fact never blocks, because `FactState.blocks` is `required and not status.is_usable`. So **the specification defines a feature whose enabling conditions it also makes mutually exclusive.** Worse, `ActionClass._check_escalatable_facts_exist` *requires* a class that declares fact escalation to carry at least one `escalatable: true` fact — the registry mandates a configuration that is provably inert. Two registry knobs are read by the resolver and can never change its output, which by this increment's own constraint 2 (§8) makes them decoration.

So this is not a repair to schedule; it is a question to answer, and it belongs to product and security rather than to whoever writes the code (`D-7`, §11): **does a missing or stale *required* fact ever warrant human escalation?**

- **If yes** — §5.5 is wrong and the loader rule is the bug. Loosen the loader, keep the resolver arm and all four truth-table rows, and the `FactState` check becomes "escalatable implies the class permits fact escalation", which is a real constraint a provider can violate.
- **If no** — the resolver's fact-escalation arm, the per-fact `escalatable` flag, `escalate_on ∩ {FACT_MISSING, FACT_STALE, FACT_PROVIDER_ERROR}` and `_check_escalatable_facts_exist` are all specification-level dead code and come out together, §5.5 is amended, and the four rows go away *because the behaviour they test no longer exists* — which is not weakening a test.

Either answer closes the hole; neither can be chosen by an engineer. What cannot happen is shipping fact providers while the question is open, because the first `FactState(required=True, escalatable=True)` a provider constructs waves a hard gate through and no fixture would catch it. **This is therefore a hard precondition on §6.9, alongside `P0-06`.**

### 4.3 `EvaluationOutcome` makes the bad state unrepresentable (`INV-05` class)

`ALLOW` with `token=None` is legal only when a flag says a record did not land. Either the flag is set on both failure paths, or `verdict` is demoted on both, or the two fields become one discriminated shape. The last is the only one a careless consumer cannot misread, and the consumer does not exist yet, which is the cheapest moment there will ever be. Plus: decide what the WAL should do with a `token_issued` record for a token that was never returned — today it replays and the chain asserts an issuance that did not happen.

### 4.4 The issuance ledger stops wedging (`FR-20`, `C-05` class)

`IssuanceLedger.claim` succeeds and is permanent before the `token_issued` record is written. If that write fails, the token is correctly withheld — and the claim stands, so **that `decision_id` is un-mintable forever, reported as `HARNESS_UNHEALTHY`, which is terminal and non-escalatable, so no human can unstick it.** One transient evidence outage in a one-call window poisons a decision permanently. Needs a compensating release on the path where the token is provably never returned, a test of *retry after outage* (today's test asserts only that the token is withheld), and a retention decision before the ledger is a PostgreSQL table that never purges.

### 4.5 One source of truth for reason-code subject shapes (`F-06`, `NFR-20`)

`reason.py` has one generic subject regex; `models/record.py` has eleven per-name shapes; `TokenInvalidReason` has three sources (enum, regex, published schema). Today they are consistent *and already cost something*: `DuplicateIssuanceError` reports `HARNESS_UNHEALTHY` rather than a `TOKEN_INVALID:<why>` subject, because adding a ninth member is a coordinated three-file edit. The catalogue is de facto frozen by its own duplication. The deferral rationale — resolver test churn — expired when the resolver merged. The PDP (`P1-04`) is the first component to mint subjects from outside this codebase; fix it before it does.

### 4.6 Two data tables become one asserted invariant (resolver coverage)

`resolver.py:175` and `:180` are unreachable because `_CRITIC_REASON_BY_RESULT`'s keys happen to be a superset of `is_indeterminate`'s results, and `_FACT_REASON_BY_STATUS`'s keys happen to be a superset of the non-usable statuses. Nothing asserts either nesting. Add one `VerifierResult` member and the branch goes live — and its behaviour is to **silently drop an abstention reason from the record**, which is the quietest possible failure in a system whose constitution says an unrecorded decision was not made. Replace both narrowings with an import-time totality check of the same shape as `safety.py:74`, and the guards become unconditional. The check is writable today and the nesting was verified: `_CRITIC_REASON_BY_RESULT` covers `{FAIL, UNKNOWN, TIMEOUT, ERROR}` while `CriticOutcome.is_indeterminate` is `{UNKNOWN, TIMEOUT, ERROR}`, and `_FACT_REASON_BY_STATUS` covers `{MISSING, STALE, PROVIDER_ERROR}` which is exactly `FactStatus` minus `FRESH`. Both nestings hold by coincidence and nothing states them, which is the whole finding. The assertion is: every `VerifierResult` for which `is_indeterminate` is true, and every `FactStatus` for which `is_usable` is false, has a table entry — so adding a sixth `VerifierResult` or a fifth `FactStatus` fails at import rather than silently dropping an abstention reason from a record.

Also close the four genuinely untested paths the audit named: `pipeline/decision.py:506-515` (the `token_issued` validation handler — the `C-01` defect class repeating on the second record, fixed and never executed), `models/record.py:850` (`FR-48` override window floor, on the one *loosening* override), `tokens/nonce.py:291-293` (the revocation reason an operator reads during a key-compromise incident), `models/record.py:193`.

---

## 5. Track 2 — close the Phase 0 gate

### 5.1 `P0-06` — the fact-provider specification (M, the highest-fan-out missing document)

The deliverable is a specification, not code: provider registry format and its JSON Schema; auth model; per-provider TTL; value schema and trust level; error taxonomy mapped to `FACT_MISSING` / `FACT_STALE` / `FACT_PROVIDER_ERROR`; the stub contract (`FR-11`: anything not fresh carries no value); and the anti-laundering statement.

**The anti-laundering clause is the hard part and it needs a design decision, not prose.** Two options, and `P0-06` must pick one:

- *Enumerated.* Each provider entry lists the tools that can write to its backing system. Direct, auditable, and a hand-maintained list that goes stale silently — the failure mode being that a new tool writes to the CI system and nobody adds it, so laundering becomes possible without any document changing.
- *Derived.* Each provider declares its backing system; each action class declares which backing systems it writes to; the harness derives the relation and refuses a fact whose backing system the proposing chain can write to. Self-maintaining, catches the new tool automatically, and costs one new required field on every action class — a registry schema change, therefore an ADR (§11, `D-2`).

The reference deployment workflow's four facts (`ci_result`, `change_approval`, `deploy_state`, `harness_approval`) are already declared in `tests/fixtures/registry/reference_deploy_registry.json` with their keys and `max_age_seconds`, so the specification can be written concretely for the reference workflow and carry a named, explicit gap for real deployments. That is enough to unblock `P1-18`; it is not enough to close `P0-07`.

### 5.2 `P0-11` — licence and contribution model (S)

`pyproject.toml` says `license = { text = "UNLICENSED" }`; there is no `LICENSE` and no `CONTRIBUTING.md`. This is a live legal blocker on any contribution, and `OQ-09` — whether a GPL-licensed LTLf toolchain is acceptable — blocks `P3-01`. `ADR-0012` is an index row with no file.

### 5.3 The `P0-10` remainder that needs no infrastructure

Everything here was deferred as "`P0-10`", and each piece has been re-checked against whether the thing it checks actually exists yet:

| Item | Status | Verdict |
|---|---|---|
| `uv.lock` | Governance §4 commits to it; there is no lockfile, dependencies are floating ranges | **Do it.** A project whose central claim is replay determinism (`NFR-07`, `R-13`) cannot have an unpinned dependency set. One runtime dependency is the easiest this will ever be |
| `[tool.ruff]`, `[tool.mypy]` in `pyproject.toml` | Neither exists; `mypy` is not in the `[dev]` extra | **Do it.** The rule set and strictness the gate enforces are currently undeclared anywhere |
| `gitleaks` | Single action, no admin rights | **Do it.** The repo guards a signer abstraction and key-id handling by constitution |
| `pip-audit` | Runs against `pyproject.toml` | **Do it.** A one-dependency TCB is the easiest moment |
| `.pre-commit-config.yaml` | Absent | Do it; it is where the ratchets live locally |
| CodeQL default setup, Trivy, branch protection, Scorecard, SBOM/provenance/cosign | Need repository-admin settings or a container | **Correctly deferred.** Named here so the deferral is a decision |

### 5.4 Escalations to the sponsor — items no engineering work unblocks

Two here, and a third in §5.5.

**`P0-12` — name the humans.** No workflow owner, no second security reviewer, no compliance contact, no agent-developer contact, no override group. The consequence is not administrative: `P1-15` requires two-person review of hard-gate policy, `05-evaluation-plan.md` §3's labelling protocol requires "the policy owner and the named workflow owner", `OQ-05` (retention) is owned by "Compliance", and `P0-01`/`P0-07` require signatures. **Nothing in this project can be approved until people exist.** No engineering work unblocks this.

**`P0-13` — provision KMS, object storage, a staging cluster and CI service identities.** The single highest-fan-out missing item overall: it gates `P1-01a` (gateway, mTLS), `P1-07`-for-real, therefore `P1-08`, therefore `P1-13`, `P1-25`, `P1-26`, `P1-28` and transitively `P1-14`, `P1-19`, `P1-23`. It owns three of the six `partial` fixtures. And it cannot be produced from inside this repository. Note the current state is correct rather than broken: `Settings.signing_algorithm` defaults to ECDSA P-256, `default_signer_registry` registers HMAC only, so `signer_for_settings` refuses to start a default deployment. That is the right failure direction. It is also a hard precondition, and it should be stated as one rather than discovered.

### 5.5 The branching model does not exist, and the repository has no review gate

`CLAUDE.md` says *"Trunk-based: short-lived branches off `main`, squash-merge"*. Measured: **the repository has exactly one branch, and it is the default branch.** `main` was created for PR #1, merged, and then deleted; PR #2 merged it back into the working branch. There is now nothing for a pull request to target, so every commit lands on trunk with no review gate at all.

This is not cosmetic. `06-delivery-and-governance.md` requires two-person review for hard-gate policy changes (`P1-15`) and `CLAUDE.md` restates it. A repository where no pull request can be opened cannot satisfy a two-person review requirement, and increment 2 changes a published JSON Schema (§4.1) and a public return shape (§4.3) — exactly the changes that should not land unreviewed.

**This is the sponsor's decision, not an engineering task**, because it determines what the branch protection in `P0-10` protects: re-create `main` as the default and protected branch with the working branch reverting to a short-lived topic branch, or adopt a different model and amend `CLAUDE.md` and governance §3 to describe it. Either way, the documents and the repository must agree before branch protection is configured, or the protection will be configured against a model nobody uses.

### 5.6 Governance hygiene that the gate implies

- **All 21 ADRs are `Status: Proposed`.** The index's rule — accepted ADRs are immutable, supersede rather than edit — has therefore never once engaged, while `ADR-0020` and `ADR-0021` are in force in shipping code and `ADR-0014` was amended in place. Accept them, or the instrument is inert.
- **`ADR-0011` and `ADR-0012` are index rows with no files.** An ADR-index consistency check (stage 11) catches this in about twenty lines and must tolerate the `Planned` state.
- **`02-technical-plan.md:399` asserts the same unenforceable 100%-resolver-branch gate as governance §3.** Restating it in one document leaves the other wrong. Restate both, against the five-site inventory from §4.6 — or better, delete the exclusion list because §4.6 removes two of the five.

---

## 6. Track 3 — build what is unblocked

### 6.1 `P1-02` — the envelope builder (M, ~25% exists)

What exists: `Proposal(extra="forbid")`, a `Context.stripped_proposal_keys` field nothing fills, and a registry that refuses an open `argument_schema`. What does not: **nothing strips, and nothing validates arguments against a schema** — `jsonschema` is a dev-only dependency and is imported nowhere in `src/`.

Build: strip context-shaped keys *before* validation (the `extra="forbid"` model turns an unstripped proposal into an exception rather than a cleaned one, so ordering is load-bearing) and record them; validate `proposal.arguments` against the class's `argument_schema`; split proposal from context; assemble; canonicalise; compute both digests. Closes `FR-02` and `FR-03`. Promotes `MUT-07` `partial → active` against its declared `still_missing`. Makes `A-07` and `A-35` executable at the layer `FR-02` names rather than only at the resource-key layer.

Forces `D-3` (§11): the TCB gains a runtime dependency, or a generated model, or a bounded hand-rolled validator over the subset the registry admits.

### 6.2 The envelope serialization convention — the highest value per line in this increment

`proposal_digest` and `envelope_digest` take `Mapping[str, Any]` by design; the package never fixes how an `ActionEnvelope` becomes that mapping. `tests/unit/test_envelope_model.py:316` uses `model_dump(mode="json")`; `tests/unit/test_schema_conformance.py:99` uses `model_dump(mode="json", exclude_none=True)`. **Those produce different bytes and therefore different digests for the same envelope**, and `Fact` has a custom serializer that makes `value` presence dump-dependent, so this is not academic. Under `FR-21` step 1 the broker recomputes the envelope digest independently; a convention mismatch is a fail-closed refusal of an untampered envelope, reproducible only in production.

Pin it in one function, assert it in a property test over generated envelopes, and publish the digest test vectors that `P0-04` still owes — noting honestly that the current vectors are self-derived and prove non-drift, not RFC 8785 conformance.

### 6.3 The agent-facing response type (`SEC-07`, `FR-56`, plan §4.1)

`02-technical-plan.md:141` fixes the response as `{verdict, reason_codes[], counterexamples[], approval_ref?, result?}` with *"free text is never emitted"*. No type in the package has that shape. The nearest, `EvaluationOutcome`, carries `SignedToken` and `AppendResult` — artifacts an agent must never see — and has no `counterexamples` or `approval_ref` field. Build the response type so free text is structurally unrepresentable and the token cannot leak by returning the wrong object. No external dependency; closes a security control that is currently held by a document.

### 6.4 `P1-09` completion — hot reload (`FR-83`)

`FR-31`, `FR-33` and `FR-49` are met. Hot reload and reload atomicity are not, and `grep reload src/` finds two docstring mentions and no implementation. There is a real design constraint to settle first: `pipeline/decision.py::_assert_mode_agrees` requires that the *same* `ActionClass` reach the resolver and the context, so a registry that re-reads between the two raises. Settle that before a gateway exists, not after.

### 6.5 `P1-27` — migration tooling (S)

Unblocked, promotes no fixture, closes no `FR`, and is a precondition for the durable evidence store and the durable ledger from §4.4.

### 6.6 CI stages that can run today

| Stage | What lands | Measured cost |
|---|---|---|
| **1. Lint and type-check** | Declare and configure ruff and mypy; run both | **ruff defaults: 1 finding.** Broad set (`E,F,W,I,B,UP,SIM,C4,RUF`): **173**, of which ~150 are cosmetic or mechanical. **`mypy --strict`: 48 errors in 9 files**, and 7 of 12 packages had never been type-checked. Land blocking against defaults immediately; land the broad set as a per-rule ratchet |
| **11. Docs — spec-ID consistency** | Every `FR-/NFR-/SEC-/INV-/MUT-/ADR-/OQ-/WF-/A-/T-/P#-` id in `src/` and `tests/` exists in `docs/sdd/`, and vice versa | **Passes today with zero orphans.** The cheapest green check available, and it guards a real regression: a test citing a renamed requirement |
| **11. Docs — ADR index** | Index rows and files agree, `Planned` tolerated | Would have caught `ADR-0011`/`ADR-0012` |
| **9. Security (partial)** | gitleaks, pip-audit | See §5.3 |
| **4 (second half). Article IV** | See §6.7 | See §6.7 |

Three of ruff's broad findings are worth reading as defects rather than style, and should be triaged individually rather than auto-fixed: four `RUF100` unused-`noqa` (suppressions that outlived their findings), `B905` in `test_write_ahead_log.py:258` (zips `staged` against `restored` without `strict=`, so a replay that drops a record passes silently), and two `RUF043` (unescaped regex metacharacters in `pytest.raises(match=…)`, so the match is looser than intended). Conversely `UP042` (31 findings, `class X(str, Enum)` → `StrEnum`) must **not** be auto-fixed: `str, Enum` members compare equal to their string values and the serialization path depends on it. That is a per-rule ignore with a comment, not a change.

### 6.7 The Article IV check — landing a gate we know is red

Governance §3 stage 4 specifies the kill run *"plus a check that every hard registry rule has an active fixture"*. Only the kill half is implemented. Measured against `reference_deploy_registry.json`:

> **19 distinct hard critics. Zero have an active fixture. Three source requirements — `WF-05` (change-record), `WF-06b` (change-window), `WF-06c` (in-flight) — and the SMT typed contract have no fixture in the catalogue whose gate names them at all.**

Constitution Article IV: *"Every hard gate has at least one negative mutation fixture that proves it blocks. A gate without a killing fixture does not exist."* The registry declares those rules `hard: true, mode: enforce`. By the constitution they do not exist. This was invisible because the check that would have shown it was never written.

`CLAUDE.md` says: fix the root cause or surface it. Surface it, as a **ratchet, not an override**: a checked-in file listing the hard rules that currently lack an active fixture, with the task that owes each. The check is blocking, and it fails if a rule appears that is not on the list, or if a rule on the list gains a fixture without leaving it. The list may only shrink. Any new hard rule without a fixture fails the build the day it is added — which is the property Article IV actually wants — and the existing 19 are counted in one place instead of being absent. Adding `WF-05`, `WF-06b` and `WF-06c` fixture *declarations* to the catalogue is part of this increment; activating them is `P1-15`.

### 6.8 The `A-xx` scenario registry — §1a applied to the specification

The specification carries **45 acceptance scenarios**. Four are named by any test: `A-16`, `A-19`, `A-25`, `A-35`. `07-increment-1-plan.md:120` claims `A-19`, `A-20`, `A-21` and `A-22` are *"executable here and are claimed"*; `A-20` and `A-22` are named by no test — `A-20`'s "and alerts" half has no alerting, and no test drives `CLOCK_UNAVAILABLE` through the resolver at all.

This is the same defect the mutation-fixture registry was built to catch, in the half of the evaluation plan that has no registry. So: one declaration per scenario, each either referenced by a test or marked not-yet-executable with the task that owes it, and a checker in the shape of `test_mutation_fixtures.py`. A claim of coverage that nothing enforces is the same defect whichever document makes it — including this one.

### 6.9 Stretch — `P1-18`, only if `P0-06` lands by the midpoint

If and only if §5.1 is complete and reviewed: the `FactProvider` protocol, the provider registry loader, bounded parallel fetch, staleness on the trusted clock as `age > min(fact.ttl_seconds, class.required_facts[].max_age_seconds)`, value-less stubs, and the anti-laundering rule as `P0-06` decided it. `MUT-04`, `MUT-14` and `MUT-23` move `reserved → active` **only** where `P0-06` defines the gate first. If `P0-06` slips, `P1-18` slips with it, and that is the correct outcome rather than a failure of the increment.

---

## 7. Fixtures — what moves, and the rule for claiming

**The counter-rule, stated once.** A fixture moves to `active` only when this increment builds the whole gate its `expected` outcome names — not when it builds a component on the same subject. A module "carries" a requirement only when it closes that requirement's acceptance criterion in the WBS. Both errors were made in increment 1 and in this plan's first draft.

| Fixture | Move | Basis |
|---|---|---|
| `MUT-07` | `partial → active` | Its `still_missing` is verbatim *"The envelope builder validating an envelope's arguments against that schema (P1-02)"*, which §6.1 builds |
| `MUT-04`, `MUT-14`, `MUT-23` | `reserved → active` **only under §6.9** | Otherwise unchanged, with `still_missing` naming `P0-06` |
| `MUT-09`, `MUT-10`, `MUT-21` | stay `partial` | All three owe the broker half (`P1-08` ← `P0-13`) |
| `MUT-13` | stays `partial` | Owes real durability (`P1-06a`) |
| `MUT-26` | stays `partial` | Owes delegation-chain eligibility (`P1-10a`) |
| `MUT-01`, `MUT-02`, `MUT-03`, `MUT-05` | stay `reserved` | Policy-rule fixtures. They need the signed Rego bundle and its two-person review (`P1-15`) |
| **New declarations** | `reserved` | `WF-05`, `WF-06b`, `WF-06c` and the SMT typed contract have no catalogue entry (§6.7). Adding them makes the Article IV gap countable |
| **New fixture** | `active` | The `FactState` repair in §4.2: a fixture proving the type refuses `required=True, escalatable=True` |

Census today: **5 active, 6 partial, 26 reserved**. The Phase 1a exit gate names 21 that must be active; 4 of those 21 are. This increment moves that to 5 or 8, which is not a large number, and the plan says so rather than dressing it up.

---

## 8. Engineering constraints

Carried from increment 1 unchanged, because they are why increment 1 survived three review rounds.

1. **Every external dependency enters through a seam.** A `typing.Protocol` with an in-memory reference implementation; the production implementation is a later, separate change. A component that cannot be driven by a test double cannot be replayed (`FR-71`). This increment adds `FactProvider` and `PolicyDecisionPoint` as protocols whether or not it implements them, because that is what lets `P1-04` and `P1-18` land without touching callers.
2. **No governing value is a literal.** Timeouts, TTLs, budgets and enumerations come from the signed registry or from `defaults.py`. The AST scanner in `test_no_magic_values.py` is blocking and traverses computed expressions. Corollary, learned in §4.2: *a knob nobody reads is a hardcoded knob* — if a registry field cannot change the resolver's output, it is decoration, and the provenance test should say so.
3. **Backwards compatible by construction.** Additive subpackages with their own `__all__`; no existing public signature changes; new parameters arrive as keywords whose defaults preserve current behaviour. Two exceptions are deliberate and need the ADRs in §11: the resource-key grammar (§4.1) narrows a published schema, and `EvaluationOutcome` (§4.3) changes a return shape. Both are in this increment precisely because there is no external consumer yet.
4. **A gate without a killing fixture does not exist** — and, from §6.7, a gate whose absence is not *counted* does not get fixed.
5. **Logging is structured, redacted and bound.** Dotted event codes through `observability.logging`, free text replaced at the sink, `trace_id`/`action_id` bound via `bind_context` so one decision is followable across components. One repair belongs here: `_SAFE_KEYS` is provably dead (its intersection with `_SENSITIVE_KEYS` is empty), which is evidence that substring matching was intended — so `provider_api_key` and `bundle_signature_blob`, exactly the vocabulary fact providers and the PDP introduce, are **not** redacted today under the exact-match rule.
6. **Debugging is a deliverable.** Every fail-closed refusal names what failed and which control refused, and the `NFR-20` explain-path is tested (§4.6 closes `tokens/nonce.py:291-293`, the reason an operator reads during a key-compromise incident).
7. **Never weaken a test to get green.** Unchanged from `CLAUDE.md`. Including: do not lower `_THOROUGH_EXAMPLES` on the resolver properties to shorten CI — those properties are what caught `F-02`. If CI time becomes a problem, the answer is a marker-based fast lane, and `pytest-xdist` only after `DeterministicUuidGenerator`'s unguarded counter is fixed.

---

## 9. Deliberately excluded

| Excluded | Owner | Precondition |
|---|---|---|
| MCP transport, HTTP decision API, `FR-01` | `P1-01a` | `P0-13` — an authenticated host and mTLS identities. There is no honest in-process version of "direct tool access is impossible in the stack" |
| OPA sidecar, signed Rego bundles, policy pack | `P1-04`, `P1-15` | `P1-18` ← `P0-06`; also CI stage 6 (testcontainers). A Python stand-in is excluded on Article V grounds (§2) |
| Broker, leases, connectors, receipts, effect critic | `P1-08` | `P0-13`. An out-of-process broker on today's HMAC signer puts the minting key in the component that is supposed to be unable to mint — a design violation, not a shortcut |
| Approval service | `P1-10a` | `P1-18`. `harness_approval` is specified as a *fact*, so the provider interface is its seam |
| PostgreSQL evidence store | `P1-06a` durable half | **Not blocked by `P0-13`** — a container in CI is not provisioned infrastructure, and the WBS lists `P1-06a` as unblocked. Deferred for a reason this plan creates: §4.4 must decide `D-5` (ledger retention and compensation) *before* the ledger is a database, or the wedge is built into PostgreSQL and becomes unrecoverable. It is also an `L` against a four-week increment that already carries two tracks, and it needs CI stage 6 (testcontainers), which does not exist. `P1-27` lands here as its enabler, and it is increment 3's first item |
| Checkpoint anchoring, pseudonymization, crypto-shredding, retention | `P1-06b` | `P0-13` object storage; `OQ-05` is owned by Compliance, unnamed until `P0-12` |
| Identity and session tree | part of `P1-01a` | `P0-13`. Nothing can legitimately produce `CredentialStatus.VERIFIED` without an identity provider |
| Repair loop resubmission | `FR-90`–`FR-92` | The response type (§6.3) carries counterexamples; the loop is an orchestrator concern once there is an orchestrator |

---

## 10. Sequencing and no-go criteria

```
Week 1   T1: §4.1 grammar · §4.3 EvaluationOutcome · §4.2 raised as D-7
         T2: P0-06 drafted · P0-11 · uv.lock + tool config
         T3: CI stage 1 (defaults, blocking) · stage 11 id-checker + ADR index
Week 2   T1: §4.4 ledger · §4.5 subject shapes · §4.6 totality + 4 tests
         T2: P0-06 reviewed · D-7 answered  ◀── MIDPOINT GATE for §6.9
         T1: §4.2 implemented per D-7's answer
         T3: §6.2 digest convention · §6.3 response type · §6.7 Article IV ratchet
Week 3   T2: §5.6 ADR accepts · restate the 100%-branch gate in both documents
         T3: §6.1 envelope builder · §6.4 hot reload · §6.5 migrations
         T3: §6.8 A-xx registry · gitleaks · pip-audit · ruff ratchet
Week 4   T3: §6.9 P1-18 if the midpoint gate passed, else buffer
         All: review, DoD, increment-3 plan
```

**Does it fit?** `03-work-breakdown.md` §0 gives 1 tech lead, 2 backend, 1 policy/security at 50%, 1 evaluation at 50%, 1 SRE at 25%, at 70–75% utilization. Four weeks is **≈ 62 engineer-days** available (excluding the product owner). Sized at the WBS's own upper bounds — S ≤ 2 days, M ≤ 5, L ≤ 10:

| Track | Items | Days |
|---|---|---|
| 1 | §4.1 (4) · §4.2 (3) · §4.3 (2) · §4.4 (3) · §4.5 (3) · §4.6 (2) | **17** |
| 2 | `P0-06` M (5) · `P0-11` S (2) · lockfile and tool config (1) · ADR accepts (1) · seven ADRs for `D-1`–`D-7` (4) | **13** |
| 3 | `P1-02` M (5) · §6.2 (2) · §6.3 (2) · §6.4 hot reload (3) · `P1-27` S (2) · stage 1 (3) · stage 11 (1) · gitleaks + pip-audit (1) · §6.7 ratchet (2) · §6.8 registry (3) | **24** |
| | **Committed** | **54** |
| 3 stretch | §6.9 `P1-18` L | **10** |

54 of 62 leaves ~8 days of slack, which is thin but real. **With the stretch it is 64 against 62 — it does not fit**, and that is why §6.9 is gated on a midpoint that is two weeks in rather than scheduled from day one: if `P0-06` and `D-7` land early, the remaining ~10 days of Track 3 can absorb it; if they do not, the stretch is dropped rather than the slack. The two tracks that share people are 1 and 3 (the same backend engineers), which is why §3 sequences them rather than running them concurrently.

**No-go criteria — stop and escalate rather than proceed:**

1. `P0-06` cannot settle the anti-laundering clause, or `D-7` cannot be answered, because no named owner exists to settle either → this is `P0-12`, and §6.9 is cancelled, not fudged.
2. §4.1's grammar decision would break the published schema for a consumer we do not know about → confirm there is no such consumer before narrowing; if there is, the decision changes.
3. Any repair in §4 cannot be made without weakening a test → surface it; do not proceed.
4. The Article IV ratchet list grows during the increment → a hard rule was added without a fixture, which is the thing the ratchet exists to stop.

---

## 11. Decisions this increment forces

Named here rather than made silently by whoever writes the code first. Each needs an ADR, and `ADR-0011`/`ADR-0012` already owe files.

| | Decision | Options | Why it cannot wait |
|---|---|---|---|
| **D-1** | The resource-key grammar | Registry wins (hyphens, narrow the record model and both schemas) · record wins (underscores, widen the registry) · union (widen everything) | The published JSON Schemas are an external contract, and the broker's lease identity is built on it next increment |
| **D-2** | The anti-laundering statement's form | Enumerated per provider · derived from action-class writes | Derived costs a new required field on every action class, i.e. a registry schema change. `MUT-23`'s gate is whichever is chosen |
| **D-3** | Does the TCB gain a runtime dependency? | Promote `jsonschema` · generate a pydantic model per action class · bounded hand-rolled validator over the registry-admitted subset | `FR-02` needs a schema evaluator and the runtime dependency surface is currently exactly one package. Supply-chain and TCB-inventory decision, not a convenience one |
| **D-4** | `EvaluationOutcome`'s shape | Flag on both paths · demote verdict on both · one discriminated shape | The consumer is written in increment 2 |
| **D-5** | Issuance-ledger retention and compensation | Compensating release · never-purge with a documented operator path · bounded retention | It becomes unrecoverable data the moment the ledger is PostgreSQL |
| **D-6** | ADR status | Accept the 21 · keep them `Proposed` | Two are in force in shipping code while `Proposed`, and the immutability rule has never engaged |
| **D-7** | Does a missing or stale **required** fact ever warrant human escalation? | Yes — §5.5 and the loader rule are wrong · No — the resolver arm and two registry knobs are dead and come out | §4.2. Product and security own it. It is a hard precondition on §6.9: fact providers cannot ship while it is open |

---

## 12. Definition of done

Beyond the standing Definition of Done in `06-delivery-and-governance.md` §6:

1. Every string the resource-key registry accepts is recordable, and every string the record model accepts resolves — asserted by a property test that **fails on the tree as merged**.
2. `D-7` is answered and §5.5 says what the code does. Whichever answer: no producer can construct a `FactState` that waves a hard gate, proved by a killing fixture, and no truth-table row was deleted for a behaviour that still exists.
3. `EvaluationOutcome` cannot represent `ALLOW` with no token and no failure signal.
4. A `decision_id` whose `token_issued` record failed can be retried, with a test that drives the retry.
5. Reason-code subject shapes have one source of truth, and `TokenInvalidReason` can gain a member in one edit.
6. `resolver.py:175/180` are unconditional, replaced by an import-time totality assertion; the four named untested paths have tests.
7. `docs/sdd/06-fact-provider-specification.md` exists, decides `D-2`, and is reviewed.
8. `LICENSE` and `CONTRIBUTING.md` exist; `pyproject.toml` no longer says `UNLICENSED`; `ADR-0012` exists.
9. `uv.lock` is checked in; `[tool.ruff]` and `[tool.mypy]` are declared; `mypy` is in `[dev]`.
10. CI stage 1 blocks on ruff defaults and on `mypy --strict`, with the broad rule set on a declared, shrinking per-rule ratchet.
11. CI stage 11 blocks on spec-ID consistency and ADR-index consistency.
12. CI stage 4's Article IV half runs and blocks, over a list that may only shrink and that currently names 19 hard rules.
13. Every acceptance scenario has a declaration; the checker blocks; the `A-20`/`A-22` claims in `07-increment-1-plan.md` are corrected.
14. An `ActionEnvelope` is built from a tool call, with `stripped_proposal_keys` recording what was removed and arguments validated against the class's schema; both digests computed through **one** pinned serialization, asserted by a property test.
15. A typed agent-facing response exists in which free text is unrepresentable and no token can leak.
16. `MUT-07` is `active` and killed; `WF-05`, `WF-06b`, `WF-06c` and the SMT contract have declarations; every fixture that stays `partial` or `reserved` names the task that owes it.
17. The full suite passes, coverage stays at or above the 90% floor, and **no test is skipped, weakened or quarantined.**
18. The three sponsor escalations — `P0-12` (name the humans), `P0-13` (provision), §5.5 (branching model) — each have a decision or an explicitly recorded deferral with a date. An escalation that is simply still open at the end of the increment was not escalated.
19. `ADR-0011` and `ADR-0012` exist as files; the 21 `Proposed` ADRs are resolved per `D-6`; the 100%-resolver-branch gate says the same thing in `06-delivery-and-governance.md` §3 and `02-technical-plan.md:399`.
20. Appendix A's measurements are re-run and this document's numbers updated — because §6.8's whole argument is that unenforced claims rot, and `07-increment-1-plan.md:139`'s stale "168" is the proof.

---

## 13. What increment 3 is

Whichever of `P0-13` and `P0-12` the sponsor closes decides it.

- **If `P0-13` lands:** the execution side. Real signer, durable evidence store, then the broker (`P1-08`) with digest recompute, full token checks and resource leases — promoting `MUT-09`, `MUT-10`, `MUT-13` and `MUT-21` out of `partial` in one increment, on a resource-key grammar that §4.1 made single-valued.
- **If `P0-13` does not land:** `P1-18` and then `P1-04` with the OPA sidecar, on the specification §5.1 wrote, taking the `WF-01`–`WF-05` fixtures off the Article IV list — which is the larger number, and the one the Phase 1a exit gate is actually counting.

Either way the Article IV list from §6.7 is the increment-3 backlog, in priority order, with nineteen entries. That is the plan's real output: not a set of modules, but a countable, shrinking statement of which gates do not yet exist.

---

## Appendix A — verification log

Measured against `b16f3ba` on 2026-09-18. Every number in this document comes from here or is attributed to a document.

| Claim | Command | Result |
|---|---|---|
| Suite health | `PYTHONPATH=src pytest -q` | 1908 passed in 12.61s |
| ruff, defaults | `ruff check --target-version py311 --line-length 100 src tests` | 1 error (`F401`, `MappingProxyType` in `evidence/store.py`) |
| ruff, broad | `ruff check --select E,F,W,I,B,UP,SIM,C4,RUF …` | 173 errors, 112 auto-fixable |
| mypy | `mypy --strict --ignore-missing-imports src/neuroharness` | 48 errors in 9 files, 35 source files checked |
| Fixture census | read all `tests/fixtures/mutations/MUT-*.json` | 5 active, 6 partial, 26 reserved |
| Article IV gap | cross-product of `reference_deploy_registry.json` hard critics × fixture states | 19 distinct hard critics, 0 with an active fixture; `WF-05`, `WF-06b`, `WF-06c`, SMT typed contract have no catalogue entry |
| Scenario coverage | `grep -rohI "A-[0-9][0-9]" tests/` vs `docs/sdd/01-specification.md` | 45 scenarios; 4 referenced (`A-16`, `A-19`, `A-25`, `A-35`) |
| Resource-key drift | `is_resource_key(k)` vs `models/record.py::_RESOURCE_KEY_PATTERN` | `cluster-prod:svc-a`, `s3-bucket:data` registry-valid and unrecordable; `s3:bucket_name`, `my_kind:foo` recordable and unresolvable |
| Fact escalation | `resolve()` over the three `FactState` shapes | `required=True, escalatable=True` → `REQUIRES_APPROVAL`; the loader refuses that shape; so the path is dead today and live for any new producer |
| `jsonschema` in TCB | `grep -rn jsonschema src/ pyproject.toml` | dev-only extra, zero imports in `src/` |
| Strip function | `grep -rn "stripped_proposal_keys\|def strip" src/` | a model field and its docstring; no implementation |
| Lint tooling declared | `grep -n "ruff\|mypy\|Regal" pyproject.toml .github/workflows/ci.yml` | none; 4 CI jobs total |
| Branch topology | `git fetch --prune && git remote show origin` | **`main` does not exist.** The repository has exactly one branch, `claude/sdd-plan-peer-review-i2v012`, which is also the default branch. A local `remotes/origin/main` ref survived the deletion and was stale; `--prune` removed it |

## Appendix B — corrections to existing documents

Each of these is a document asserting something the code contradicts. Fixing them is in scope; each is one line.

| Document | Says | Correct |
|---|---|---|
| `07-increment-1-plan.md:3` | implements `P1-02` | `P1-02` is ~25% done; `MUT-07.json`'s own `still_missing` names the gap |
| `07-increment-1-plan.md:120` | `A-19`–`A-22` executable and claimed | `A-19` ✓, `A-21` ✓, `A-20` partial (no alerting), `A-22` named by no test |
| `07-increment-1-plan.md:136` | CI stage 4 running and blocking | The kill half runs; the Article IV half does not exist |
| `07-increment-1-plan.md:139` | `ruff` reports 168 findings | 173 with the broad set, 1 with defaults; mypy never reported |
| `07-increment-1-plan.md:169` | `P0-05` closed inside increment 1 | No generated registry schema; the sample carries a digest, not a signature |
| `07-increment-1-plan.md:200` | increment 2 is `P1-04` + `P1-18` | Serial, and both behind `P0-06` |
| `06-delivery-and-governance.md` §3 stage 2 | resolver 100% branches | Unenforceable as written; five sites, three outside `resolve/`. §4.6 removes two |
| `02-technical-plan.md:399` | the same 100% claim | Same correction, second document |
| `06-delivery-and-governance.md` §4 | dependencies pinned with `uv.lock` | No lockfile exists |
| `docs/review/2026-09-18-…-findings.md` §3 | six active, four partial | 5 active, 6 partial, 26 reserved — the same document's §5 records the corrections that produce it |
| `docs/review/2026-09-18-…-findings.md` §7 | no `main` branch exists; blocking for review | **Still open, and worse.** `main` was created for PR #1, merged, and then deleted. The sole remaining branch is the default branch, so no pull request can target anything — see §5.5 |
| `01-specification.md:663` | `OQ-03` open | `ADR-0019` chose ECDSA P-256; the provisioning (`P0-13`) is what remains open |
