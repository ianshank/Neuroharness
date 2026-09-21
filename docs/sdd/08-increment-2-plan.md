# Increment 2 — Repair the drift, close what can be closed, build what is honest

**Status:** Draft v2, revised after adversarial review · **Date:** 2026-09-18 · **Constrained by:** `00-constitution.md` · **Implements:** `P0-06`, `P0-11`, the `FR-02`/`FR-03` half of `P1-02`, parts of `P0-10`, plus six repairs to merged code that no WBS line owns

Every quantitative claim here was measured against the tree; Appendix A gives the commands and Appendix B lists the document claims the code contradicts. Version 1 of this file was reviewed adversarially and **twelve of its factual claims were falsified**; the corrections are folded in and Appendix C records what was wrong, because a plan whose central argument is that unenforced claims rot has to publish its own.

---

## 1. The case, in three facts

### Fact 1 — Increment 1 shipped across a gate that is 3 of 7, and nobody wrote that down

| Phase 0 exit clause | State |
|---|---|
| Schemas frozen | ✅ `docs/sdd/schemas/` plus a blocking CI job |
| Registry **and provider formats** signed | ❌ the registry carries a digest, not a signature (`NullVerifier` and `DigestVerifier` are the only verifiers); the provider format (`P0-06`) does not exist in any form |
| Threat model signed | ❌ `04-threat-model.md:3` — *"Draft v0.2 — needs security review"* |
| Mutation matrix complete with states | ✅ 37 declarations, schema-enforced, cross-checked in CI |
| CI skeleton green with all stages | ❌ **3 of 12.** `ci.yml` runs four jobs, and its own comment at `:122` says the fourth, `no-magic-values`, is *"Not one of the twelve, and deliberately its own check"* |
| KMS and environments provisioned | ❌ no key material, no object storage, no cluster, no CI identities |
| Roles named | ❌ `06-delivery-and-governance.md:95` is titled *"RACI (to be confirmed in `P0-12`)"* and names no people |

`07-increment-1-plan.md` §6 declares two of these outstanding (`P0-13`, `P0-14`). It does not declare the other two. Shipping increment 1 over an unmet gate was defensible once: the deterministic core needed no infrastructure and the unmet clauses did not touch it. Shipping increment 2 the same way, silently, is the moment the gate becomes decorative.

**This increment does not close the gate either.** It closes `P0-06` and `P0-11` and part of `P0-10`. The exit is **4 of 7 at best**, and §5.7 says which clauses stay open and why. Saying so is the point; version 1 of this plan claimed to "put gate closure inside the increment" while leaving three `P0` items untriaged.

### Fact 2 — The obvious next increment is not reachable, and the reason is a missing document

`07-increment-1-plan.md:200` says increment 2 "attaches the policy decision point and fact providers to these seams (`P1-04`, `P1-18`)". Read as parallel work, that is wrong twice:

```
P0-06 ──▶ P1-18 ──▶ P1-04 ──▶ { P1-11, P1-12, P1-15, P1-16 }
  ▲                                        │
  │                                        └─▶ P1-15 ─▶ every WF-01..WF-05 fixture
  └── does not exist as an artifact
```

`P1-04`'s `Depends on` column is `P0-05, P1-18` — serial, not parallel. `P1-18` depends on `P0-06`, the fact-provider specification, which has no artifact anywhere in the repository. `FR-10`–`FR-14` and `SEC-11` exist as requirements and `02-technical-plan.md:150-152` sketches a one-line provider interface, but there is no provider registry format, no auth model, no per-provider TTL, and — the clause that matters — no *statement, per provider, of which tools can write to its backing system*. That is the `SEC-11`/`T-21` anti-laundering control: the thing that stops an agent writing a fact and then citing it as evidence. It cannot be derived by an engineer at implementation time, and a fact layer built without it would have `MUT-23`'s gate defined by whatever the code happened to do — the inversion `05-evaluation-plan.md` §1a exists to prevent.

Only `P1-02`, `P1-06a`, `P1-09` and `P1-27` in all of Phase 1 are unblocked by their declared dependencies.

### Fact 3 — Increment 1 left live defects, and increment 2 is what makes them bite

**3a. The resource-key grammar has four sources of truth, diverging on five axes, and has already drifted.** Measured:

| Axis | `registry/resource_keys.py` | `models/record.py` + both schemas |
|---|---|---|
| kind charset | `[a-z][a-z0-9-]{0,31}` — hyphen **yes**, underscore no | `[a-z][a-z0-9_]*` — hyphen **no**, underscore yes |
| id charset | `[A-Za-z0-9][A-Za-z0-9.-]{0,63}` | `[A-Za-z0-9._-]+` |
| id first character | must be alphanumeric | `-` and `.` accepted |
| id length | ≤ 64 | unbounded |
| whole-key length | `MAX_RESOURCE_KEY_LENGTH = 158` | schema `maxLength: 256` |

```
k8s-namespace:default  registry True   record False   <- registry-valid, unrecordable
cluster-prod:svc-a     registry True   record False   <- registry-valid, unrecordable
s3:bucket_name         registry False  record True    <- recordable, no lookup matches
repo:-foo              registry False  record True
```

A hyphenated resource kind — `cluster-prod`, `s3-bucket`, `k8s-namespace`, which is what any real deployment writes — is accepted by the signed registry, becomes the broker's lease identity under `FR-25`, and then cannot be recorded. Per the `C-01` resolution that is fail-closed: `SCHEMA_INVALID` → `ABSTAIN`. **So lease contention on a hyphen-named resource is reported to an operator as a harness malfunction rather than as contention, and the whole class abstains until someone renames the resource.**

The fourth source is `reason.py:143`'s `_SUBJECT_PATTERN`, which admits `-` but not `_` anywhere — so `ReasonCode(RESOURCE_BUSY, "cluster-prod:svc-a")` constructs and then fails `models/record.py`'s check, while `ReasonCode(RESOURCE_BUSY, "my_kind:foo")` passes both and names a key the registry rejects. And `registry/resource_keys.py:13-21` documents the grammar as *"a subset of the reason-code subject charset"* which *"excludes `_`"* — **both halves are false.** `tests/unit/test_anchored_patterns.py` checks anchoring, not cross-module agreement; nothing sits between the four.

**3b. Fact escalation is dead under any valid registry, and live for any new producer.** Measured:

```
required=False escalatable=True   blocks=False -> ALLOW             (escalation never reached)
required=True  escalatable=True   blocks=True  -> REQUIRES_APPROVAL (the loader refuses this shape)
required=True  escalatable=False  blocks=True  -> ABSTAIN           (correct)
```

`FactState.blocks` is `required and not status.is_usable`, so only required facts reach the escalation arm — and `FactRequirement._check_escalation` refuses `required AND escalatable`. The safety property is held entirely by the loader being the only producer of `FactState`; the resolver never checks `fact.required`, and `FactState` is a plain frozen dataclass with no validation. **Increment 2 adds fact providers; the first `FactState(required=True, escalatable=True)` any of them constructs turns a hard gate into a human-waveable formality, and no fixture would catch it.** §4.2 shows this is a specification defect rather than a code defect, which is not where version 1 of this plan filed it.

**3c. `EvaluationOutcome` lets `ALLOW` coexist with `token=None` and `evidence_failed=False`.** Verified at `pipeline/decision.py:440` and `:461`: both return `None` leaving `verdict=ALLOW, evidence_failed=False`. The docstring's defence — every consumer must read `permits_execution` — is correct, and the consumer is written in increment 2. Related: the real `EvidenceWriter.write` stages the failed record in the WAL before re-raising (`evidence/store.py:477-488`), so on recovery the chain permanently asserts a `token_issued` for a token that was never returned and can never be spent — and `verify_chain` will call that chain valid.

**What these force.** The valuable work is not "attach the next component". It is: repair what will otherwise be load-bearing under the next component, write the one document that unblocks a third of Phase 1, and build only the parts that can be built honestly.

### What a user can do afterwards: nothing new

Stated once, plainly. After this increment the harness still cannot evaluate a real tool call — no gateway, no PDP, no fact layer, no broker, no identity. Fixture coverage moves from 5 active to 7, against a Phase 1a exit gate that names 21. The deliverable is a repaired foundation, a specification that unblocks a third of Phase 1, and three enforcement mechanisms that make the remaining gap countable. That is worth four weeks and it is not a demo.

---

## 2. Scope — three tracks

| Track | What | Why now | People |
|---|---|---|---|
| **1. Repair** | Six defects in merged code (§4) | Each becomes load-bearing under increment 2 or 3. 3b opens the moment fact providers exist | 2 backend |
| **2. Close what can be closed** | `P0-06`, `P0-11`, the `P0-10` remainder that needs no infrastructure (§5) | `P0-06` unblocks a third of Phase 1. Three items are the sponsor's, not engineering's | Tech lead + security (50%) |
| **3. Build** | The `FR-02`/`FR-03` half of `P1-02`, the pinned envelope serialization, the typed response, three CI stages, two enforcement registries (§6) | Everything here is unblocked and honest to claim | 2 backend + evaluation (50%) + SRE (25%) |

Tracks 1 and 3 draw on the same backend engineers, which §10 sizes and which forced a cut-list.

---

## 3. Track 1 — repair what is already wrong

### 3.1 One resource-key grammar (`FR-25`, `FR-34`, `SEC-07`)

Four sources become one. `registry/resource_keys.py` owns the grammar; `models/record.py` imports it; the two published JSON Schemas are **generated** from it, with a CI check that the checked-in schema matches the generator's output. The length bound is expressed once. A property test asserts both directions — every string `is_resource_key` accepts is recordable, and every string the record model accepts resolves under `split_resource_key` — and **that test fails on the tree as merged**, which is how we know it tests something. `registry/resource_keys.py:13-21`'s docstring is corrected in the same change.

**The direction is the decision, and version 1 of this plan stated it backwards.** Making `cluster-prod:svc-a` recordable requires the record model to **widen** its kind charset to admit `-`, not narrow. And `models/record.py:166` embeds the resource-key pattern inside the reason-code regex as `(RESOURCE_BUSY|EFFECT_MISMATCH):{_RESOURCE_KEY_PATTERN}`, so that widening enlarges the charset admissible in a reason code delivered to the governed model — a `SEC-07` surface. Whichever way `D-1` goes, one side widens a published contract and the other breaks existing resource keys. It cannot be decided from a two-way framing, which is why `D-1` (§11) now carries all five axes.

### 3.2 Fact escalation — a specification defect, and this plan's first answer was wrong

The hole is real (Fact 3b). The obvious repair is not, and it is worth recording why.

Version 1 proposed "move the rule to the type: `FactState.__post_init__` refuses `required=True, escalatable=True`". Checked against the suite, that shape is constructed in **two** places: `test_resolver_truth_table.py:80`'s `fact()` helper defaults `required=True` and four rows pass `escalatable=True` (`:314`, `:338`, `:418`, `:436`) — the only truth-table rows exercising the resolver's fact-escalation arm — **and** `test_resolver_properties.py:99-112`, where `fact_states` and `constraining_facts` build it from `st.booleans()` strategies. Making the shape unconstructible would delete four truth-table rows and edit two Hypothesis strategies that §8.7 specifically protects.

Pulling the thread reaches the defect one layer up. `01-specification.md:180` says escalation is *"never for **evidence** facts marked `required: true`"* — note the qualifier, which is load-bearing below. `FactRequirement._check_escalation` enforces it; a non-required fact never blocks; so **the specification defines a feature whose enabling conditions it also makes mutually exclusive.** And `ActionClass._check_escalatable_facts_exist` (`registry/models.py:274-289`) *requires* a class declaring fact escalation to carry an escalatable fact — the registry mandates a provably inert configuration. Two registry knobs are read by the resolver and can never change its output, which by §8.2 makes them decoration.

So this was not a repair to schedule; it was a question for product and security (`D-7`, §11): **does a missing or stale required fact ever warrant human escalation, and is "evidence fact" a narrower class than "required fact"?**

**Decision (2026-09-20): Yes.** Recorded in [ADR-0027](adr/ADR-0027-d7-required-fact-escalation.md) under the P0-12 interim (Ian Cruickshank sole owner). §5.5 and the loader rule were the bug. **Selected branch:** loosen the loader, keep the resolver arm, keep all four truth-table rows, and the `FactState` check is "escalatable implies the class permits fact escalation", which a provider can actually violate. "Evidence fact" is not treated as a separate narrower class that permanently forbids escalating all required facts.

<details>
<summary>Historical alternative (not selected): If no</summary>

The resolver's fact-escalation arm, the per-fact `escalatable` flag, `escalate_on ∩ {FACT_MISSING, FACT_STALE, FACT_PROVIDER_ERROR}` and `_check_escalatable_facts_exist` would have come out together, §5.5 amended, and the four truth-table rows removed *because the behaviour they test no longer exists* — which would not have been weakening a test. The two property strategies would have narrowed with them.

</details>

**This answered `D-7` is a hard precondition on any fact-provider work, alongside `P0-06`.**

### 3.3 `EvaluationOutcome` makes the bad state unrepresentable

`ALLOW` with `token=None` is legal only when a flag says a record did not land. Either the flag is set on both failure paths, or the verdict is demoted on both, or the two fields become one discriminated shape. The last is the only one a careless consumer cannot misread, nothing outside `tests/` consumes `EvaluationOutcome` today, and the consumer arrives next increment. Plus: decide what the WAL should do with a `token_issued` record for a token that was never returned.

### 3.4 The issuance ledger stops wedging — against a documented invariant, not around it

`tokens/service.py:275` claims before `pipeline/decision.py:443` writes the record. If that write fails the token is correctly withheld, the claim stands, and **that `decision_id` is un-mintable forever, reported as `HARNESS_UNHEALTHY`, which is terminal and non-escalatable, so no human can unstick it.** One transient outage in a one-call window poisons a decision permanently, and there is no test of retry-after-outage.

**The obvious fix is refused in writing by the code it would change.** `tokens/nonce.py:393-399`: *"An issuance has no such backstop: forgetting it is indistinguishable from never having minted, so a purge would re-open the second-mint path the moment the first token expired — and a second mint is a fresh token with a fresh lifetime, which is the whole defect."* And "provably never returned" is unprovable in the failure mode that matters: on a crash between claim and write the compensating release never runs, and on a store timeout the record may in fact have landed.

So `D-5` (§11) must answer this against that argument, not past it, and its options are: a compensating release confined to the cases where the record is *provably absent* (which requires `has_record`, on the protocol and called by nothing); the documented status quo plus an operator-visible recovery path and a retention decision; or making the claim itself part of the record write so there is one atomic step rather than two. The third is the only one that removes the window rather than compensating for it.

### 3.5 One source of truth for reason-code subject shapes

`reason.py:143` has one generic subject regex; `models/record.py:136-139` has eleven per-name shapes; `TokenInvalidReason` has three sources (enum, regex, published schema). They agree today. The drift direction that matters is `record.py` *loosening* relative to `reason.py`: a record can carry a resource key the registry would reject and a reason code whose subject no lookup resolves — the `C-02` shape, reached through a grammar mismatch instead of a trailing newline. That is the same defect as §3.1 and the two should be fixed together.

**Version 1 offered a motive for urgency that the code refutes.** It claimed `DuplicateIssuanceError` reports `HARNESS_UNHEALTHY` "because adding a ninth member is a coordinated three-file edit". `tokens/nonce.py:316-322` gives a substantive reason instead: *"nothing is wrong with any token, and the closed `TokenInvalidReason` catalogue in the decision-record schema describes the broker's refusals, not the mint's."* That is correct and the plan should not have invented an editing-cost story for it. The structural duplication is still worth removing, and the real reason is that the PDP (`P1-04`) is the first component to mint subjects from outside this codebase.

### 3.6 Two data tables become one asserted invariant

`resolver.py:175` and `:180` are unreachable because `_CRITIC_REASON_BY_RESULT`'s keys `{FAIL, UNKNOWN, TIMEOUT, ERROR}` happen to be a superset of `is_indeterminate`'s `{UNKNOWN, TIMEOUT, ERROR}`, and `_FACT_REASON_BY_STATUS`'s keys `{MISSING, STALE, PROVIDER_ERROR}` happen to be exactly `FactStatus` minus `FRESH`. Nothing asserts either nesting. Add one `VerifierResult` member and the branch goes live — and its behaviour is to **silently drop an abstention reason from the record**, the quietest possible failure in a system whose constitution says an unrecorded decision was not made.

Replace both narrowings with an import-time totality check of the same shape as `safety.py:70-78`, raising `ConfigurationError`: every `VerifierResult` for which `is_indeterminate` holds, and every `FactStatus` for which `is_usable` does not, has a table entry. No import cycle — `errors.py` is a leaf and `inputs.py` already imports `models.common`.

**One friction version 1 missed.** `CriticOutcome.reason_code` and `FactState.reason_code` return `ReasonCode | None` and must keep doing so (`PASS` → `None`). Dropping the guards passes `ReasonCode | None` where `ReasonCode` is required, which `mypy --strict` — made blocking by this same increment — rejects. An `assert` satisfies mypy and is stripped under `-O`, i.e. a fail-open in an optimized build. The repair needs a non-`Optional` accessor on the blocking path, which is real design work and is sized accordingly.

Also close the four untested paths: `pipeline/decision.py:506-515` (the `token_issued` validation handler — the `C-01` defect class repeating on the second record, fixed and never executed), `models/record.py:850` (`FR-48` override window floor, on the one *loosening* override), `tokens/nonce.py:291-293` (the revocation reason an operator reads during a key-compromise incident), and `models/record.py:193`.

---

## 4. Track 2 — close what can be closed

### 4.1 `P0-06` — the fact-provider specification (M)

Delivered as `docs/sdd/09-fact-provider-specification.md` — **not** `06-`, which would collide with `06-delivery-and-governance.md` in a directory the README presents as a numbered chain.

A specification, not code: provider registry format and its JSON Schema; auth model; per-provider TTL; value schema and trust level; error taxonomy mapped to `FACT_MISSING` / `FACT_STALE` / `FACT_PROVIDER_ERROR`; the stub contract (`FR-11`: anything not fresh carries no value); and the anti-laundering statement.

**The anti-laundering clause needs a design decision, not prose** (`D-2`):

- *Enumerated.* Each provider entry lists the tools that can write to its backing system. Direct and auditable, and a hand-maintained list that goes stale silently — the failure being that a new tool writes to the CI system, nobody updates the list, and laundering becomes possible without any document changing.
- *Derived.* Each provider declares its backing system; each action class declares which backing systems it writes to; the harness derives the relation and refuses a fact whose backing system the proposing chain can write to. Self-maintaining, catches the new tool automatically, and costs a new required field on every action class — a registry schema change.

The reference workflow's facts (`ci_result`, `change_approval`, `deploy_state`, `harness_approval`) are already declared in `tests/fixtures/registry/reference_deploy_registry.json` with keys and `max_age_seconds`, so the specification can be written concretely for the reference workflow with a named, explicit gap for real deployments. That unblocks `P1-18`; it does not close `P0-07`.

### 4.2 `P0-11` — licence and contribution model (S)

`pyproject.toml` says `license = { text = "UNLICENSED" }`; there is no `LICENSE` and no `CONTRIBUTING.md`. A live legal blocker on any contribution, and `OQ-09` — whether a GPL-licensed LTLf toolchain is acceptable — blocks `P3-01`. `ADR-0012` is an index row with no file.

### 4.3 The `P0-10` remainder that needs no infrastructure

| Item | Status | Verdict |
|---|---|---|
| `uv.lock` | Governance §4 commits to it; there is no lockfile, dependencies are floating ranges | **Do it.** A project whose central claim is replay determinism (`NFR-07`, `R-13`) cannot have an unpinned dependency set. One runtime dependency is the easiest this will ever be |
| `[tool.ruff]`, `[tool.mypy]` in `pyproject.toml` | Neither exists; `mypy` is not in the `[dev]` extra | **Do it.** The rule set and strictness the gate enforces are undeclared anywhere in the repo |
| `gitleaks` | Single action, no admin rights | **Do it.** The repo guards a signer abstraction and key-id handling by constitution. It scans history rather than the tree, and its two findings were both a `key_id` — public by construction, and the thing the `generic-api-key` rule cannot distinguish from a key. `.gitleaks.toml` exempts those two values as anchored literals; `tests/unit/test_secret_scan_allowlist.py` holds that shape, because widening an allowlist is the cheapest answer to the next false positive and leaves a green check behind |
| `pip-audit` | Runs against `pyproject.toml` | **Do it.** A one-dependency TCB is the easiest moment |
| `.pre-commit-config.yaml` | Absent | Do it; it is where the ratchets live locally |
| CodeQL default setup, Trivy, branch protection, Scorecard, SBOM/provenance/cosign | Need repository-admin settings or a container | **Correctly deferred.** Named here so the deferral is a decision |

### 4.4 Escalations to the sponsor — three items no engineering work unblocks

**`P0-12` — name the humans.** No workflow owner, no second security reviewer, no compliance contact, no agent-developer contact, no override group. Not administrative: `P1-15` requires two-person review of hard-gate policy, `05-evaluation-plan.md` §3's labelling protocol requires "the policy owner and the named workflow owner", `OQ-05` is owned by "Compliance", `P0-01`/`P0-07` need signatures — **and every one of `D-1` through `D-7` in §11 needs an approver who does not exist.** That last point is the binding one: this increment schedules seven ADRs and a published-schema change into a repository with no named reviewer.

> **Interim (2026-09-20):** Ian Cruickshank is sole owner for accepting increment-2 design decisions (`D-1`–`D-8` / related ADRs) until a fuller RACI lands. `D-7` is Accepted under that interim as [ADR-0027](adr/ADR-0027-d7-required-fact-escalation.md).

**`P0-13` — provision KMS, object storage, a staging cluster and CI service identities.** Highest fan-out missing item: gates `P1-01a`, `P1-07`-for-real, therefore `P1-08`, therefore `P1-13`, `P1-25`, `P1-26`, `P1-28` and transitively `P1-14`, `P1-19`, `P1-23`. Owns three of the six `partial` fixtures. Cannot be produced from inside this repository. The current state is correct rather than broken: `Settings.signing_algorithm` defaults to ECDSA P-256, `default_signer_registry` registers HMAC only (`tokens/signer.py:318`), so a default deployment refuses to start. Right failure direction, hard precondition.
>
> **Dated deferral note (2026-09-20 / 2026-09-21):** `P0-13` KMS, object storage, staging cluster, and CI service identities are explicitly deferred by sponsor/repository owner (Ian Cruickshank). Increment 3 may proceed `P1-06a`-first only per risk `R-D`. Resolved KMS/staging owners are not invented, and engineering work must not block on `P0-13`.

**The branching model.** `CLAUDE.md` says *"Trunk-based: short-lived branches off `main`, squash-merge"*. Measured: **the repository has exactly one branch and it is the default branch.** `main` was created for PR #1, merged, then deleted. There is nothing for a pull request to target, so every commit lands on trunk with no review gate. Governance requires two-person review for hard-gate policy changes; a repository where no pull request can be opened cannot perform it. This increment narrows a published JSON Schema (§3.1) and changes a public return shape (§3.3) — exactly the changes that should not land unreviewed. The decision determines what the branch protection in `P0-10` protects, so it must precede configuring it.

### 4.5 Governance hygiene

- **19 ADR files exist; the index lists 21.** `ADR-0011` and `ADR-0012` are rows with no files. Of the 19, **15 are `Proposed` and 4 are `Superseded`** (`ADR-0007`→`0014`, `0008`→`0015`, `0009`→`0019`, `0010`→`0016`). Zero are `Accepted`, so the index's rule — accepted ADRs are immutable, supersede rather than edit — has never engaged, while `ADR-0020` and `ADR-0021` are in force in shipping code and `ADR-0014` was amended in place. `D-6` decides whether the 15 are accepted.
- **An ADR-index consistency check** (stage 11) catches the dangling rows in about twenty lines and must tolerate the `Planned` state.
- **`02-technical-plan.md:399` and `06-delivery-and-governance.md:27` assert the same unenforceable 100%-resolver-branch gate.** Restating one leaves the other wrong. Note that `resolve/safety.py:74` is also uncovered and *is* inside `resolve/`, so §3.6's repairs do not by themselves make the gate pass.

### 4.6 Three `P0` items this increment does not close, stated so the deferral is a decision

| Item | Disposition |
|---|---|
| `P0-05` — registry signature and generated schema | **Deferred, and it is why the gate clause stays ❌.** This increment writes the *provider* half of "registry and provider formats signed"; the registry half needs a real signature verifier, which is the same capability `P1-04` needs for signed bundles, which needs `P0-13`. Escalated with `P0-13` rather than scheduled |
| `P0-07` — threat-model sign-off | **Blocked on `P0-12`.** It depends on `P0-04`–`P0-06`; writing `P0-06` is what makes it reachable, so this increment unblocks it and cannot perform it, because sign-off needs a named security reviewer |
| `P0-14` — `nh fixtures gen` | **Deliberately deferred.** M, unblocked, and *"used by every later fixture task"* — including §6.6's new declarations and §6.7's scenario registry, both of which are small enough to write by hand. It is the first thing to schedule if the cut-list in §10 frees capacity, and otherwise increment 3 |

**Honest gate exit: 4 of 7.** Version 1 of this plan claimed to put gate closure inside the increment and left these three untriaged.

---

## 5. Track 3 — build what is unblocked

### 5.1 The `FR-02`/`FR-03` half of `P1-02` — and a WBS correction it needs

What exists: `Proposal(extra="forbid")`, a `Context.stripped_proposal_keys` field nothing fills, and a registry that refuses an open `argument_schema`. What does not: **nothing strips, and nothing validates arguments against a schema** — `jsonschema` is dev-only and imported nowhere in `src/`.

Build: strip context-shaped keys *before* validation (the `extra="forbid"` model turns an unstripped proposal into an exception rather than a cleaned one, so ordering is load-bearing) and record them; validate `proposal.arguments` against the class's `argument_schema`; split proposal from context; assemble; canonicalise; compute both digests. Closes `FR-02` and `FR-03`. Promotes `MUT-07` `partial → active` against its declared `still_missing`.

**This does not close `P1-02`, and the WBS is why.** `03-work-breakdown.md:47` gives `P1-02` the acceptance criterion *"`MUT-17`, `MUT-30` active and killed"*. `MUT-30` is already active. `MUT-17`'s declaration reads `gate: "Claims isolation"`, `expected: "PDP input has no claims; ABSTAIN FACT_MISSING"` — that is the **PDP input builder** (`P1-04`), not the envelope builder, and it is excluded from this increment. So `P1-02` as written cannot be closed by the component it names.

Version 1 of this plan claimed `P1-02` anyway, substituting `MUT-07` for the criterion — the third commission of the error §6 prosecutes. The fix is a proposed WBS amendment, delivered as part of this increment: move `MUT-17` to `P1-04`'s acceptance where its gate actually lives, and give `P1-02` `MUT-07` and `MUT-30`. Until that amendment is accepted by someone (see `P0-12`), this increment claims **the `FR-02`/`FR-03` half of `P1-02`** and nothing more.

Forces `D-3`: the TCB gains a runtime dependency, or a generated model, or a bounded hand-rolled validator over the subset the registry admits.

### 5.2 The envelope serialization convention — the highest value per line here

`proposal_digest` and `envelope_digest` take `Mapping[str, Any]` by design; the package never fixes how an `ActionEnvelope` becomes that mapping. `test_envelope_model.py:316` uses `model_dump(mode="json")`; `test_schema_conformance.py:99` uses `model_dump(mode="json", exclude_none=True)`. **Those produce different bytes and therefore different digests for the same envelope**, and `Fact` has a custom serializer making `value` presence dump-dependent, so this is not academic. Under `FR-21` step 1 the broker recomputes the envelope digest independently; a convention mismatch is a fail-closed refusal of an untampered envelope, reproducible only in production.

Pin it in one function, assert it in a property test over generated envelopes, and publish the digest test vectors `P0-04` still owes — noting that the current vectors are self-derived and prove non-drift, not RFC 8785 conformance.

### 5.3 The agent-facing response type (`SEC-07`, `FR-56`)

`02-technical-plan.md:141` fixes the response as `{verdict, reason_codes[], counterexamples[], approval_ref?, result?}` with *"free text is never emitted"*. No type in the package has that shape. The nearest, `EvaluationOutcome`, carries `SignedToken` and `AppendResult` — artifacts an agent must never see — and has no `counterexamples` or `approval_ref`. Build it so free text is structurally unrepresentable and the token cannot leak by returning the wrong object.

### 5.4 CI stages that can run today

| Stage | What lands | Measured cost |
|---|---|---|
| **1. Lint and type-check** | Declare and configure ruff and mypy; run both | **ruff defaults: 1 finding.** Broad set: **173**, ~150 cosmetic. **`mypy --strict`: 48 errors in 9 files** — 19 in `registry/models.py`, 12 in `models/record.py`. Blocking against ruff defaults lands week 1; **clearing the 48 mypy errors is its own scheduled work**, because §12 makes it blocking |
| **11. Docs — spec-ID consistency** | Every `FR-/NFR-/SEC-/INV-/MUT-/ADR-/OQ-/WF-/A-/T-/P#-` id in `src/` and `tests/` exists in `docs/sdd/`, and vice versa | **Passes today with zero orphans.** The cheapest green check available; guards a test citing a renamed requirement |
| **11. Docs — ADR index** | Index rows and files agree, `Planned` tolerated | Catches `ADR-0011`/`ADR-0012` |
| **9. Security (partial)** | gitleaks, pip-audit | §4.3 |
| **4 (second half)** | §5.5 | §5.5 |

Three broad-set findings are defects rather than style and get read individually: four `RUF100` unused-`noqa` (suppressions that outlived their findings), `B905` at `test_write_ahead_log.py:258` (zips `staged` against `restored` without `strict=`, so a replay that drops a record passes silently), two `RUF043` (unescaped regex metacharacters in `pytest.raises(match=…)`). `UP042` (31 findings, `class X(str, Enum)` → `StrEnum`) must **not** be auto-fixed: `str, Enum` members compare equal to their string values and the serialization path depends on it.

### 5.5 The hard-rule fixture register — named for what it enforces

Governance §3 stage 4 specifies the kill run *"plus a check that every hard registry rule has an active fixture"*. Only the kill half is implemented. Measured against `reference_deploy_registry.json`: **19 hard critics, of which 17 are `mode: enforce`; zero have an active fixture.** The two advisory ones (`fsa.deploy-order`, `fsa.rollback-order`, both `WF-06a`) cannot block — `resolve/inputs.py:132` makes `counts_as_hard` require `ENFORCE` — so they are not gates, and `WF-06a` is catalogued anyway (`MUT-06`, `MUT-24`). **The register holds 17.** Of their source requirements, `WF-05`, `WF-06b`, `WF-06c` and the SMT typed contract have no catalogue entry at all.

Constitution Article IV (`00-constitution.md:20-21`): *"Every hard gate ships with at least one negative mutation fixture… and every hard rule in the registry must have one. **Test:** Point to the active fixture ID for each gate the change adds or modifies."* The registry declares those rules `hard: true, mode: enforce`. By the constitution they do not exist.

`CLAUDE.md` says fix the root cause or surface it. Surface it as a **frozen, shrink-only register**: a checked-in file naming each hard enforcing rule that lacks an active fixture and the task that owes it. The check is blocking and fails if a rule appears that is not on the list, or if a rule on the list gains a fixture without leaving it. The list may only shrink. That mechanizes the constitution's own change-scoped test exactly.

**Three things version 1 got wrong about this, and the adversarial review was right:**

1. **The name.** Version 1 landed it as *"CI stage 4's Article IV half runs and blocks"* — a green check whose name asserts a property false for 17 of 17 rules. That is "a green stage proving a gate nobody wrote", which is what `05-evaluation-plan.md` §1a invented the `partial` state to prevent, applied to the gate instead of the fixture. The check is named **`hard-rules-have-fixtures: 17 known gaps`** and reports the count, so nobody can read it as Article IV being satisfied.
2. **The exemption discipline.** Stage 4 is the one stage whose specification says in bold *"No manual override exists"* (`06-delivery-and-governance.md:29`), and §1a routes durable exemptions through an ADR. The register takes one (`D-8`), for the same reason `retired` fixtures do.
3. **The scope.** Version 1 keyed it on `tests/fixtures/registry/reference_deploy_registry.json` — a test fixture — so a hard critic added to a real deployment registry would be invisible. It is keyed on **every registry the build loads**, which is what makes the "fails the day a rule is added" property true rather than decorative.

Adding declarations for `WF-05`, `WF-06b`, `WF-06c` and the SMT contract is part of this increment; activating them is `P1-15`.

### 5.6 The `A-xx` scenario registry — §1a applied to the specification

The specification carries **45 acceptance scenarios**. **Three** are named by any test: `A-16`, `A-19`, `A-35`.

Version 1 of this plan said four, adding `A-25` — which came from `SHA-256` matching a `grep "A-[0-9][0-9]"` with no word boundary, in `test_canonical_properties.py:190`. The plan published the command, the command was wrong, and the section it was wrong in is the one arguing that unenforced claims rot. It is the strongest available evidence for building the registry.

`07-increment-1-plan.md:120` claims `A-19`, `A-20`, `A-21` and `A-22` are *"executable here and are claimed"*. Only `A-19` is: `A-20`'s "and alerts" half has no alerting, `A-21` appears nowhere in `tests/` or `src/`, and no test drives `CLOCK_UNAVAILABLE` through the resolver for `A-22`.

So: one declaration per scenario, each either referenced by a test or marked not-yet-executable with the task that owes it, and a checker in the shape of `test_mutation_fixtures.py`, matching on a word boundary.

---

## 6. Fixtures — what moves, and the rule for claiming

**The counter-rule.** A fixture moves to `active` only when this increment builds the whole gate its `expected` outcome names. A module "carries" a requirement only when it closes that requirement's acceptance criterion in the WBS — and if the criterion names the wrong fixture, the fix is to amend the WBS (§5.1), not to substitute a different fixture and claim the task. That error was made in increment 1, in this plan's first draft, and again in version 1 of this file.

| Fixture | Move | Basis |
|---|---|---|
| `MUT-07` | `partial → active` | Its `still_missing` is verbatim *"The envelope builder validating an envelope's arguments against that schema (P1-02)"*, which §5.1 builds |
| `MUT-09`, `MUT-10`, `MUT-21` | stay `partial` | All owe the broker half (`P1-08` ← `P0-13`) |
| `MUT-13` | stays `partial` | Owes durability (`P1-06a`), which §7 defers with its reason |
| `MUT-17` | stays `reserved`, **and moves to `P1-04` in the WBS** | Its gate is the PDP input builder, not the envelope builder |
| `MUT-26` | stays `partial` | Owes delegation-chain eligibility (`P1-10a`) |
| `MUT-01`, `MUT-02`, `MUT-03`, `MUT-05` | stay `reserved` | Policy-rule fixtures; they need the signed Rego bundle and its two-person review (`P1-15`) |
| **New declarations** | `reserved` | `WF-05`, `WF-06b`, `WF-06c`, SMT typed contract |
| **New fixture, `D-7` permitting** | `active` | `D-7` = Yes ([ADR-0027](adr/ADR-0027-d7-required-fact-escalation.md)): prove loader accepts `required∧escalatable` under class opt-in, resolver escalates, non-opt-in still abstains. Version 1 promised a fixture for a repair it then retracted; this one is now unconditional on the Yes decision |

Census today: **5 active, 6 partial, 26 reserved**. The Phase 1a exit gate names 21 that must be active; 4 of those 21 are. This increment moves that to **7** (`D-7` Yes: the §6 permitting fixture is in scope). That is a small number and the plan says so.

---

## 7. Deliberately excluded

| Excluded | Owner | Why |
|---|---|---|
| MCP transport, HTTP API, `FR-01` | `P1-01a` | `P0-13` — an authenticated host and mTLS identities. There is no honest in-process version of "direct tool access is impossible in the stack" |
| OPA sidecar, signed bundles, policy pack | `P1-04`, `P1-15` | `P1-18` ← `P0-06`; also CI stage 6. A Python stand-in is excluded on Article V grounds: verifiers live outside the governed runtime, and an evaluator with no bundle would have to fabricate the `policy_bundle_digest` that `TokenService.verify` demands |
| Broker, leases, connectors, receipts | `P1-08` | `P0-13`. An out-of-process broker on today's HMAC signer puts the minting key in the component that must be unable to mint |
| Approval service | `P1-10a` | `P1-18`. `harness_approval` is specified as a *fact* |
| **`P1-06a`'s durable half** | `P1-06a` | **Not blocked by anything external, and the strongest rejected alternative — see below** |
| `P1-09` hot reload (`FR-83`), `P1-27` migrations | `P1-09`, `P1-27` | Cut on capacity (§10). Both are unblocked; neither promotes a fixture; `P1-27`'s only consumer is the durable store, which is also deferred |
| Identity and session tree | `P1-01a` | `P0-13`. Nothing can legitimately produce `CredentialStatus.VERIFIED` without an identity provider |
| Repair loop resubmission | `FR-90`–`FR-92` | The response type carries counterexamples; the loop needs an orchestrator |

**On `P1-06a`, stated properly, because version 1 rebutted the wrong scope.** `03-work-breakdown.md:51` scopes `P1-06a` as *"append-only, hash chain, write-ahead, **local WAL**"* — PostgreSQL is `P1-22` and object storage is `P1-06b`. So the durable half is a file- or SQLite-backed store, needing neither testcontainers nor `P0-13`; version 1's claim that it was blocked on object storage rebutted `P1-06b` under `P1-06a`'s name. It has **eight direct dependents** (`P1-06b`, `P1-07`, `P1-11`, `P1-16`, `P1-10a`, `P1-19`, `P1-22`, `P3-02`) against `P0-06`'s one, and `MUT-13` is **the only `partial` fixture whose missing half this team can supply without the sponsor**.

It is deferred on capacity alone (§10 shows the increment is already over on backend engineers) and on one sequencing fact: `D-5` must settle the ledger's retention and compensation *before* the ledger is durable, or §3.4's wedge is written into a database. `D-5` lands in week 2. **`P1-06a`'s durable half is therefore increment 3's first item, ahead of `P1-18`**, because it needs no sponsor decision and `P1-18` needs two.

---

## 8. Engineering constraints

1. **Every external dependency enters through a seam** — a `typing.Protocol` with an in-memory reference implementation; production is a later, separate change. A component that cannot be driven by a test double cannot be replayed (`FR-71`).
2. **No governing value is a literal.** Timeouts, TTLs, budgets and enumerations come from the signed registry or `defaults.py`; the AST scanner is blocking and traverses computed expressions. Corollary, learned in §3.2: *a knob nobody reads is a hardcoded knob* — a registry field that cannot change the resolver's output is decoration, and the provenance test should say so.
3. **Backwards compatible by construction.** Additive subpackages with their own `__all__`; new parameters as keywords whose defaults preserve behaviour. **Two deliberate exceptions**, both needing an ADR: the resource-key grammar (§3.1) changes a published schema and, through `models/record.py:166`, the charset admissible in a reason code delivered to the model; `EvaluationOutcome` (§3.3) changes a return shape. Both are here because there is no external consumer *yet* — and confirming that is a precondition owned by the tech lead in week 1, not a trip-wire (see `R-A`, §9).
4. **A gate without a killing fixture does not exist** — and a gate whose absence is not *counted* does not get fixed (§5.5).
5. **Logging is structured, redacted and bound.** Dotted event codes, free text replaced at the sink, `trace_id`/`action_id` bound through `bind_context`. One repair belongs here: `_SAFE_KEYS ∩ _SENSITIVE_KEYS = ∅`, so the set is provably dead, which is evidence substring matching was intended — and `provider_api_key` and `bundle_signature_blob`, exactly the vocabulary fact providers and the PDP introduce, are **not** redacted today.
6. **Debugging is a deliverable.** Every fail-closed refusal names what failed and which control refused, and the `NFR-20` explain-path is tested.
7. **Never weaken a test to get green.** Including: do not lower `_THOROUGH_EXAMPLES` on the resolver properties — those properties are what caught `F-02`. If CI time becomes a problem the answer is a marker-based fast lane, and `pytest-xdist` only after `DeterministicUuidGenerator`'s unguarded counter is fixed.

---

## 9. Risks

| | Risk | Likelihood | Impact | Owner | Mitigation |
|---|---|---|---|---|---|
| **R-A** | The published schemas have an external consumer nobody inventoried, so §3.1's change breaks it | Low | High | Tech lead, week 1 | An explicit inventory, delivered as a written answer, before the grammar change starts. Not a trip-wire |
| **R-B** | The sponsor never answers `P0-12`, so none of `D-1`–`D-8` has an approver and no ADR can be accepted | **Medium** | **High** | Sponsor | The increment can *write* every ADR; it cannot accept one. Escalated week 1 with a dated deferral required by §12.18 |
| **R-C** | `D-7` is unanswerable without a named security owner, so the fact-escalation hole stays open into the increment that adds a second `FactState` producer | Medium | High | Sponsor | `P1-18` does not start until `D-7` closes — which is already true, since it is increment 3 |
| **R-D** | `P0-13` never lands, leaving increment 3 defined only by `P1-06a`'s durable half and `P1-18` | Medium | Medium | Sponsor | §13 already branches on it; the `P1-06a`-first ordering means increment 3 exists either way |
| **R-E** | Four weeks produce one fixture promotion and read as no progress | **High** | Medium | Tech lead | Stated up front in §1. The countable output is the 17-entry register and the scenario registry, not the fixture count |
| **R-F** | Clearing 48 `mypy --strict` errors uncovers a real typing defect that forces a signature change mid-increment | Low | Medium | Backend | The 48 are inventoried by file before week 1 commits to the schedule |

---

## 10. Sequencing, capacity and the cut-list

```
Week 1   T1: §3.1 grammar · §3.3 EvaluationOutcome        │ backend
         T2: P0-06 drafted · P0-11 · R-A inventory        │ tech lead
         T2: three escalations raised (§4.4)              │ tech lead
         T3: ruff+mypy config · stage 1 (ruff defaults)   │ SRE
Week 2   T1: §3.4 ledger · §3.5 subjects · §3.6 totality  │ backend
         T2: P0-06 reviewed · D-7 answered   ◀── gate     │ tech lead + security
         T3: §5.2 digest convention · §5.3 response type  │ backend
         T3: stage 11 id-checker + ADR index              │ evaluation
Week 3   T1: §3.2 implemented per D-7                     │ backend
         T3: §5.1 envelope builder + WBS amendment        │ backend
         T3: mypy 48 → 0                                  │ backend
         T3: §5.5 register · §5.6 scenario registry       │ evaluation
         T3: gitleaks · pip-audit · pre-commit            │ SRE
Week 4   T2: ADRs D-1..D-8 · ADR accepts · gate restated  │ tech lead
         T3: ruff broad-set ratchet                       │ evaluation
         All: review, DoD, increment-3 plan
```

**Capacity, per person rather than in aggregate.** `03-work-breakdown.md` §0 gives 1 tech lead, 2 backend, 1 security at 50%, 1 evaluation at 50%, 1 SRE at 25%, at 70–75% utilization. Twenty days at 72.5%:

| Pool | Available | Committed | |
|---|---|---|---|
| 2 backend + 0.5 evaluation (T1 + most of T3) | **36.25** | **33** | §3.1 (4) · §3.2 (3) · §3.3 (2) · §3.4 (3) · §3.5 (3) · §3.6 (3) · §5.1 (5) · §5.2 (2) · §5.3 (2) · mypy→0 (3) · §5.5 (2) · §5.6 (3) — of which 5 sit with evaluation |
| tech lead + 0.5 security (T2) | **21.75** | **17** | `P0-06` (5) · `P0-11` (2) · R-A inventory (1) · escalations (1) · eight ADRs (5) · WBS amendment and gate restatement (2) · review (1) |
| 0.25 SRE | **3.6** | **3** | tool config (1) · gitleaks + pip-audit (1) · pre-commit (1) |

**The aggregate fits and the distribution is what binds.** Version 1 of this plan committed 54 days against a 62-day pool and called it eight days of slack; per person, tracks 1 and 3 were **41 days against 36.25**, over by 13% before any stretch. Two changes fixed it: CI infrastructure moved to the SRE, who had no assignment at all, and `P1-09` hot reload and `P1-27` were cut.

**There is no stretch, and that is the honest answer.** Version 1 offered `P1-18` as one; it is an `L` — by the WBS's own *"L ≤ 10 days (one engineer)"* — landing on backend engineers with 3.25 days left in week 4, behind two sponsor decisions. It does not fit and never did.

**Cut-list, in order, if week 2 runs long:** §5.6 scenario registry (3) → §3.6's four untested paths, keeping the totality assertion (1.5) → §5.5 register (2) → §5.2's published digest vectors, keeping the pinned convention (1). Nothing in §3.1–§3.5 is cuttable; those are the repairs the increment exists for.

**No-go criteria — stop and escalate rather than proceed:**

1. `P0-06`'s anti-laundering clause or `D-7` cannot be settled because no named owner exists → this is `P0-12` and `R-B`, and the dependent work stops rather than being fudged.
2. `R-A`'s inventory finds an external consumer of the published schemas → `D-1` changes, and §3.1 waits for it.
3. Any repair in §3 cannot be made without weakening a test → surface it; do not proceed.
4. The §5.5 register grows during the increment → a hard rule was added without a fixture, which is the thing it exists to stop.
5. Clearing mypy forces a public signature change not in `D-1`–`D-8` → it becomes a ninth decision, not a quiet edit.

---

## 11. Decisions this increment forces

Each needs an ADR, and **each needs an approver who does not exist until `P0-12` closes** (`R-B`). `ADR-0011` and `ADR-0012` already owe files.

| | Decision | Options | Why it cannot wait |
|---|---|---|---|
| **D-1** | The resource-key grammar, across all five axes in Fact 3a | Registry wins (record model and both schemas **widen** to admit `-`, enlarging a `SEC-07` charset) · record wins (registry widens to admit `_`, breaking nothing but admitting keys the reason-code subject pattern rejects) · union (widen everything, including `reason.py`) | The published schemas are an external contract and the broker's lease identity is built on this next increment. One side widens whichever way it goes |
| **D-2** | The anti-laundering statement's form | Enumerated per provider · derived from action-class writes | Derived costs a new required field on every action class. `MUT-23`'s gate is whichever is chosen |
| **D-3** | Does the TCB gain a runtime dependency? | Promote `jsonschema` · generate a pydantic model per class · bounded hand-rolled validator | `FR-02` needs a schema evaluator and the runtime surface is exactly one package. Supply-chain and TCB-inventory decision |
| **D-4** | `EvaluationOutcome`'s shape | Flag on both paths · demote verdict on both · one discriminated shape | The consumer is written next increment |
| **D-5** | Issuance-ledger retention and compensation, **answering `tokens/nonce.py:393-399` rather than past it** | Compensating release confined to provable absence (needs `has_record`, currently uncalled) · the documented never-purge plus an operator recovery path and a retention decision · fold the claim into the record write so there is one atomic step | It becomes unrecoverable data the moment the ledger is durable, which is increment 3's first item |
| **D-6** | ADR status | Accept the 15 `Proposed` · keep them `Proposed` | Two are in force in shipping code while `Proposed`, and the immutability rule has never engaged. Four others are already `Superseded` and are not in scope |
| **D-7** | Does a missing or stale **required** fact ever warrant human escalation? | **Answered Yes (2026-09-20)** — see `docs/sdd/adr/ADR-0027-d7-required-fact-escalation.md`. Loosen loader; keep resolver arm + truth-table rows; §6 D-7 fixture | §3.2; hard precondition on fact providers |
| **D-8** | The hard-rule fixture register as a durable exemption to the one CI stage that forbids overrides | ADR under `06-delivery-and-governance.md:39` · red-with-baseline instead · neither, and stage 4's second half stays unwritten | §5.5. `05-evaluation-plan.md` §1a routes durable exemptions through an ADR; this is one |

---

## 12. Definition of done

Beyond the standing Definition of Done in `06-delivery-and-governance.md` §6:

1. One resource-key grammar, generated into both published schemas, with a property test asserting both directions that **fails on the tree as merged**; `registry/resource_keys.py`'s docstring corrected.
2. `D-7` is answered and the specification says what the code does. Whichever answer: no producer can construct a `FactState` that waves a hard gate, proved by a killing fixture; and no truth-table row or property strategy was narrowed for a behaviour that still exists.
3. `EvaluationOutcome` cannot represent `ALLOW` with no token and no failure signal.
4. A `decision_id` whose `token_issued` record failed can be retried, with a test that drives the retry, under whichever option `D-5` chose.
5. Reason-code subject shapes have one source of truth, and `TokenInvalidReason` can gain a member in one edit.
6. The two resolver narrowings are replaced by an import-time totality assertion; the accessor on the blocking path is non-`Optional` so `mypy --strict` passes without an `assert`; the four named untested paths have tests.
7. `docs/sdd/09-fact-provider-specification.md` exists, decides `D-2`, and is reviewed.
8. `CONTRIBUTING.md` and `ADR-0012` exist; `ADR-0011`'s dangling index row is closed.

    > **Half open at close, deliberately.** `LICENSE` is not written and `pyproject.toml` still says `UNLICENSED`. Choosing a licence is a legally consequential and effectively irreversible act of the repository owner — a grant cannot be withdrawn from anyone who already received it — and `R-B` records that none of these ADRs has an approver. `ADR-0012` sets out the options and recommends Apache-2.0; `ADR-0013` already removed the GPL constraint that made `OQ-09` hard. The Phase 0 gate does not move on `P0-11`.
9. `uv.lock` is checked in; `[tool.ruff]` and `[tool.mypy]` are declared; `mypy` is in `[dev]`; `.pre-commit-config.yaml` exists.
10. CI stage 1 blocks on ruff defaults **and on `mypy --strict` at zero errors**, with the broad rule set on a declared, shrinking per-rule ratchet.
11. CI stage 11 blocks on spec-ID consistency and ADR-index consistency; CI stage 9 runs gitleaks and pip-audit.
12. The `hard-rules-have-fixtures` check runs and blocks over a shrink-only register of **17** entries, keyed on every registry the build loads, under the ADR `D-8` decided.
13. Every acceptance scenario has a declaration; the checker blocks and matches on a word boundary; `07-increment-1-plan.md:120`'s claims for `A-20`, **`A-21`** and `A-22` are corrected.
14. An `ActionEnvelope` is built from a tool call, with `stripped_proposal_keys` recording what was removed and arguments validated against the class's schema; both digests computed through **one** pinned serialization, asserted by a property test.
15. A typed agent-facing response exists in which free text is unrepresentable and no token can leak.
16. `WF-05`, `WF-06b`, `WF-06c` and the SMT contract have declarations; the WBS amendment moving `MUT-17` to `P1-04` is written and submitted; every fixture that stays `partial` or `reserved` names the task that owes it.

    > **Corrected at close.** This item originally read "`MUT-07` is `active` and killed". It is not, and should not be. Its expected outcome is `SCHEMA_INVALID, no evaluation`; §5.1 built the *detection* and nothing yet turns a violation into that reason code or declines to evaluate, which is the orchestrator (`P1-01a`, excluded by §7). Promoting it would have produced a green, override-free check for a gate whose second half nobody wrote — the inversion `05-evaluation-plan.md` §1a invented `partial` to prevent, committed by the plan that prosecutes it in §6. The declaration now records two layers owned and one owed, and a second killing test covers the layer that landed.
17. The full suite passes, coverage stays at or above the 90% floor, and **no test is skipped, weakened or quarantined.**
18. The three sponsor escalations in §4.4 each have a decision or an explicitly recorded deferral **with a date**. An escalation still simply open at the end of the increment was not escalated.
19. `ADR-0011` and `ADR-0012` exist as files; the **15** `Proposed` ADRs are resolved per `D-6`; the 100%-resolver-branch gate says the same thing in `06-delivery-and-governance.md:27` and `02-technical-plan.md:399`.
20. `R-A`'s consumer inventory is a written answer, delivered in week 1, before §3.1 starts.
21. §13's re-baseline is delivered: a dated statement of what this increment does to the 24-week schedule.
22. Appendix A's measurements are re-run at the closing commit and this document's numbers updated — because §5.6's whole argument is that unenforced claims rot, and both `07-increment-1-plan.md:139`'s stale "168" and version 1's `A-25` are the proof.

---

## 13. What comes after, and what it costs

**Increment 3, in this order:** `P1-06a`'s durable half first — eight dependents, no sponsor decision needed, `D-5` already settled, and it moves `MUT-13` — then `P1-27` and `P1-09`'s hot reload, cut from here on capacity. Then, whichever the sponsor unblocks: `P1-18` and `P1-04` if `P0-06` and `D-7` closed, or `P1-08` if `P0-13` landed. The §5.5 register is increment 3's backlog in priority order, with **17** entries.

**The re-baseline, delivered (§12.21).** Dated 2026-09-18, at the increment's
closing commit.

**What increment 2 actually closed.** No Phase 1a task outright. `P1-02` moved
from ~25% to its `FR-02`/`FR-03` half and cannot close, because its WBS
acceptance criterion names `MUT-17`, whose gate is the PDP input builder (§5.1
proposes the amendment). `P0-06` is written. `P0-11` is half written — the
contribution model is decided, the licence is escalated. `P0-10` gained stages 1,
9 and 11. Six defects in merged code were repaired, three of which were live
rather than latent.

**What it did not move: the number Phase 1a is counted by.** The exit gate names
21 fixtures that must be `active`. There were 4 of those 21 active before this
increment and there are 4 now. The one fixture this increment was expected to
promote, `MUT-07`, stays `partial`: the work built its second layer of three, and
promoting on a half-built gate is the inversion `05-evaluation-plan.md` §1a
exists to prevent. That is the honest headline and it should not be softened —
four weeks of work, zero movement on the metric the phase is judged by.

It is not zero *progress*: 17 hard enforcing rules are now counted rather than
uncounted, 45 scenarios are declared rather than assumed, and the deterministic
core no longer contains a path where a correct hard `DENY` cannot be recorded.
But a gate count is the thing the plan committed to, and it did not move.

**The schedule.** `03-work-breakdown.md:18` puts Phase 1a at weeks 4–9. On the
WBS's own sizing the remaining Phase 1a work is unchanged, so **v1.0 moves out by
at least this increment — four weeks — and that is the floor, not the estimate.**

The estimate cannot be given, and the reason is the deliverable:

| Blocker | What it gates | Who can clear it |
|---|---|---|
| `P0-12` | Nine `Proposed` ADRs with no one who can accept one; two-person review for `P1-15`; `OQ-05`; the `P0-01`/`P0-07` signatures; `D-7` | Sponsor |
| `P0-13` | `P1-01a`, `P1-07`-for-real, therefore `P1-08`, therefore `P1-13`/`P1-25`/`P1-26`/`P1-28` and transitively `P1-14`/`P1-19`/`P1-23` | Sponsor, with procurement |
| `D-7` | Fact providers, therefore `P1-18` → `P1-04` → `P1-11`/`P1-12`/`P1-15`/`P1-16` | Product and security |
| Branching model | Every commit in this increment; no pull request can target anything | Repository owner |

**So: 24 weeks becomes at least 28, and the span beyond that is a function of
sponsor latency rather than of engineering.** Each of the four above has been
open for the whole increment. If they close inside the next two weeks, increment
3 starts on `P1-06a`'s durable half and the slip stays near the floor. If they do
not, increment 3 has no code path that is both unblocked and worth taking, and
the honest re-baseline at that point is a pause, not a longer plan.

**The recommendation a re-baseline is for:** do not schedule increment 3 until
`P0-12` closes. It is the cheapest of the four — it costs a meeting, not money or
procurement — and it is the one that unblocks the other three, because `P0-13` is
a budget decision someone has to be accountable for, `D-7` needs a named security
owner, and the branching model needs someone with repository admin.

---

## Appendix A — verification log

Re-measured at the increment's closing commit, per §12.22. Where a number moved
because this increment moved it, both are shown: the point of re-running the log
is to show what the work changed, not to quietly overwrite the evidence that
motivated it.

| Claim | Command | Result |
|---|---|---|
| Suite health | `PYTHONPATH=src pytest -q` | **2598 passed**, ~21s (was 1908) |
| ruff, defaults | `ruff check --select E4,E7,E9,F src tests` | **0** (was 1) |
| ruff, broad | `ruff check src tests` (config in `pyproject.toml`) | **0** (was 173). Three rules ignored with the reason at the ignore |
| mypy | `python3 -m mypy` (strict, pydantic plugin) | **0** in 43 source files (was 48 in 9). 44 of the 48 were the missing plugin line |
| Fixture census | all `tests/fixtures/mutations/MUT-*.json` | 5 active, 6 partial, 30 reserved (41; four declarations added for gates the catalogue never named). **Unchanged at 5 active** — see §6 |
| Hard-rule gap | `tests/fixtures/hard_rule_gaps.json`, keyed on every registry the build loads | **17**, unchanged, and now counted by a blocking shrink-only check rather than by nobody. The four missing catalogue entries were added as `reserved` declarations |
| Scenario coverage | `tests/fixtures/scenarios/` + `test_scenario_coverage.py` | 45 scenarios; **3** referenced (`A-16`, `A-19`, `A-35`), 42 declared not-executable with the task that owes them. Unchanged, and now enforced rather than asserted |
| CI stages | `ci.yml` jobs vs `06-delivery-and-governance.md` §3 | **8 jobs** covering stages 1, 2, 4 (both halves), 9 (partial), 10 and 11, plus `no-magic-values`, which `ci.yml` declares outside the twelve (was 3) |
| ADRs | `ls docs/sdd/adr/ADR-*.md`, status lines | **26 files**, no dangling index rows (was 19 files against an index of 21). 0 `Accepted`, because `P0-12` has named nobody who can accept one |
| Resource-key drift | `is_resource_key` vs `models/record.py:132`, plus `reason.py:143` | Five diverging axes, four sources; `cluster-prod:svc-a` and `k8s-namespace:default` registry-valid and unrecordable; `s3:bucket_name` and `repo:-foo` recordable and unresolvable |
| Fact escalation | `resolve()` over the three `FactState` shapes | `required=True, escalatable=True` → `REQUIRES_APPROVAL`; the loader refuses the shape; constructed in `test_resolver_truth_table.py` (4 rows) **and** `test_resolver_properties.py:99-112` (2 strategies) |
| `jsonschema` in TCB | `grep -rn jsonschema src/ pyproject.toml` | dev-only extra, zero imports in `src/` |
| Strip function | `grep -rn "stripped_proposal_keys\|def strip" src/` | a model field and its docstring; no implementation |
| Hot reload | `grep -rni reload src/ --include=*.py` | **one** docstring mention, `registry/models.py:562` |
| Redaction | `_SAFE_KEYS & _SENSITIVE_KEYS` | empty; `provider_api_key` and `bundle_signature_blob` are unredacted |
| Branch topology | `git fetch --prune && git remote show origin` | Unchanged: one branch, which is also the default. No pull request can target anything |

## Appendix B — corrections to existing documents

Each row is a document asserting something the code contradicts. Fixing them is in scope.

| Document | Says | Correct |
|---|---|---|
| `07-increment-1-plan.md:3` | implements `P1-02` | ~25% done, and `P1-02`'s WBS criterion names `MUT-17`, whose gate is the PDP — see §5.1 |
| `07-increment-1-plan.md:120` | `A-19`–`A-22` executable and claimed | `A-19` ✓. `A-20` partial (no alerting). **`A-21` appears in no test or source file.** `A-22` named by no test |
| `07-increment-1-plan.md:136` | CI stage 4 running and blocking | The kill half runs; the hard-rule half does not exist |
| `07-increment-1-plan.md:138` | stage 11 running as the constitutional-constants and no-magic-values scanners | Stage 11 is *"link check; ADR index consistency; spec IDs referenced by tests exist"* — none implemented; `ci.yml:122` says `no-magic-values` is not one of the twelve |
| `07-increment-1-plan.md:139` | `ruff` reports 168 findings | 173 broad, 1 against defaults; mypy never reported |
| `07-increment-1-plan.md:169` | `P0-05` closed inside increment 1 | No generated registry schema; the sample carries a digest, not a signature |
| `07-increment-1-plan.md:200` | increment 2 is `P1-04` + `P1-18` | Serial, and both behind `P0-06` |
| `03-work-breakdown.md:47` | `P1-02` acceptance is `MUT-17`, `MUT-30` | `MUT-17`'s gate is the PDP input builder; amendment proposed in §5.1 |
| `06-delivery-and-governance.md:27` | resolver 100% branches | Unenforceable as written; `resolve/safety.py:74` is uncovered and inside `resolve/`, so §3.6 does not by itself make it pass |
| `02-technical-plan.md:399` | the same 100% claim | Same correction, second document |
| `06-delivery-and-governance.md:42` | dependencies pinned with `uv.lock` | No lockfile exists |
| `registry/resource_keys.py:13-21` | the grammar is a subset of the reason-code subject charset and excludes `_` for the same reason | Both halves false: it admits `-`, which `models/record.py` rejects, and `record.py` admits `_` |
| `docs/review/2026-09-18-…-findings.md` §3 | six active, four partial | 5 active, 6 partial, 26 reserved — the same document's §5 records the corrections that produce it |
| `docs/review/2026-09-18-…-findings.md` §7 | no `main` branch exists; blocking for review | **Still open.** `main` was created, merged and deleted; the sole branch is the default branch — §4.4 |

## Appendix C — what version 1 of this plan got wrong

Published because §5.6 argues that unenforced claims rot, and a plan that argued it while carrying twelve falsified claims would be making the case against itself. Found by adversarial review and by re-measurement.

| | Version 1 said | Correct |
|---|---|---|
| 1 | Four acceptance scenarios are referenced by tests (`A-16`, `A-19`, `A-25`, `A-35`) | **Three.** `A-25` was `SHA-256` matching an unanchored grep — in the section arguing for the scenario registry, using a command the plan published |
| 2 | Appendix B: `A-21` ✓ verified | `A-21` is in no test or source file, and contradicted the same plan's own §6.8 list |
| 3 | All 21 ADRs are `Proposed`; accept the 21 | 19 files, 15 `Proposed`, 4 `Superseded`; `ADR-0011`/`0012` have no files — as the next bullet of the same section said |
| 4 | 4 of 12 CI stages exist | 3. `no-magic-values` is declared outside the twelve at `ci.yml:122`, and stage 11 is not running |
| 5 | Article IV, quoted from the constitution | Quoted `CLAUDE.md`. The constitution's own text is stronger and its change-scoped *Test* line is the best support for §5.5's register |
| 6 | 19 hard rules in the register | 17. Two are `mode: advisory`, cannot block, and their requirement `WF-06a` is catalogued |
| 7 | "`grep reload src/` finds two docstring mentions" | One |
| 8 | "7 of 12 packages had never been type-checked" | Backed by no measurement, and contradicted by the plan's own note that mypy was never run at all |
| 9 | `DuplicateIssuanceError` reports `HARNESS_UNHEALTHY` because the catalogue is frozen by editing cost | `tokens/nonce.py:316-322` gives a substantive reason. The plan invented a motive for a documented decision |
| 10 | Four truth-table rows are the only coverage of the fact-escalation arm | Two Hypothesis strategies build the shape as well. The blast radius was understated |
| 11 | "Registry wins means the record model narrows" | It **widens** — and through `models/record.py:166` that enlarges a `SEC-07` charset. The framing also missed three of five axes and a fourth source of truth |
| 12 | §4.4's compensating release, offered with no mention of the alternative | `tokens/nonce.py:393-399` refuses exactly that in writing. `D-5` now answers it rather than past it |
| 13 | "~8 days of slack"; `P1-18` as a stretch | Per person, tracks 1 and 3 were 41 days against 36.25. There is no stretch, and `P1-18` never fit |
| 14 | `P1-06a` is blocked on `P0-13` object storage | `P1-06a` is *"local WAL"*; PostgreSQL is `P1-22` and object storage is `P1-06b`. It was rebutted under the wrong scope, and it is increment 3's first item |
| 15 | Claimed `P1-02` | Its WBS criterion names a fixture whose gate is the PDP. The plan substituted a different fixture and claimed the task — the error it prosecutes in §6 |
