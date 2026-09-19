# Increment 2 — pull-request description

> **What this file is.** The body of
> [PR #3](https://github.com/ianshank/Neuroharness/pull/3), kept in the tree so
> that it is reviewable under the same rules as everything else it describes and
> so that a correction to it leaves a commit behind.
>
> It was written before the pull request could be opened: the repository had
> exactly one branch, which was also the default, so there was nothing to
> target. `main` was recreated at the increment-1 end state (`b16f3ba`) and is
> now the base. The repository *default* branch is still this topic branch —
> that half remains open, and is listed under "Blocked on you" below.
>
> Everything below the rule is the PR body. It carries no diffstat: the file is
> itself part of the diff, so any count written here is falsified by the commit
> that writes it. GitHub computes the live one on the pull request.

---

## Increment 2: repair the drift, close what can be closed, build what is honest

Implements `docs/sdd/08-increment-2-plan.md`.

Increment 1 delivered a correct decision procedure. This increment did not extend
it — it repaired six defects inside it, three of which were live rather than
latent, and built only the parts of the boundary that could be built honestly.

### Why this shape, and not the obvious one

The increment-1 plan said increment 2 should attach the policy decision point and
fact providers (`P1-04`, `P1-18`). That is not reachable: `P1-04` depends on
`P1-18`, `P1-18` depends on `P0-06`, and `P0-06` — the fact-provider
specification — had no artifact. Only `P1-02`, `P1-06a`, `P1-09` and `P1-27` in
all of Phase 1 were unblocked.

So the plan was rewritten, then reviewed adversarially. **Twelve of its own
factual claims were falsified**, including one in the section arguing that
unenforced claims rot: it counted four acceptance scenarios as test-referenced,
and the fourth was `SHA-256` matching an unanchored `grep "A-[0-9][0-9]"` that
the plan itself published. `Appendix C` of the plan lists all fifteen
corrections.

### The three live defects

**A correct hard `DENY` could not be recorded.** The resolver's fallback for a
hard critic that fails without its own reason subjects `RULE_FAILED` with the
*critic id*; the record catalogue required a *rule id*. Reproduced:

```
resolver verdict : DENY
resolver reasons : ['RULE_FAILED:smt.deploy_contract']
recordable?      : False
```

The pipeline turns that into `ABSTAIN(SCHEMA_INVALID)` with nothing written down
— a hard denial becoming an unrecorded harness fault, which is the one outcome
Article III says cannot happen. Neither half of the suite could see it: the
pipeline tests always passed a well-shaped reason, and the truth table never
builds a record. Fixed structurally (`ADR-0025`): one `SUBJECT_GRAMMAR`, checked
**at construction**, so a reason code that cannot be recorded cannot be built.

**The resource-key grammar had four sources and had drifted.**
`cluster-prod:svc-a`, `s3-bucket:data` and `k8s-namespace:default` were accepted
by the signed registry, became the broker's lease identity under `FR-25`, and
could not be recorded. Lease contention on a hyphenated resource would have
reached an operator as a harness malfunction rather than as contention.
`ADR-0023`: one grammar, asserted at import, with the published schemas
**generated** from it.

**The issuance ledger wedged permanently.** A claim was taken before the
`token_issued` record was written; if that write failed the token was correctly
withheld and the claim stood, making the `decision_id` un-mintable **forever**
and reporting it as `HARNESS_UNHEALTHY` — terminal and non-escalatable, so no
human could unstick it. `ADR-0026` answers the never-purge argument rather than
working around it: the record's identity is derived from the decision, so the
store's existing duplicate refusal *is* the at-most-once insert.

### What else landed

- **`EvaluationOutcome` could say `ALLOW` while authorising nothing** — now a
  construction-time biconditional (`D-4`).
- **`model_copy(update=…)` bypassed every validator on every model.** `frozen=True`
  reads as "cannot be changed"; it wasn't. `proposal.model_copy(update={"tool":
  "ignore previous instructions"})` produced a `Proposal` whose tool was a
  sentence, and it digested that way. The first fix put the override on
  `WireModel` and this entry claimed it covered the package — **it covered two
  of five trees.** Eight frozen models inherited `BaseModel` directly and kept
  the hole, including both token models and the whole signed registry. It is now
  one base, `RevalidatingModel`, with a test that walks the package and fails on
  any frozen model that does not inherit it.
- **Two live conventions for serializing an envelope**, producing different
  envelope digests. `mode="json"` fails the published schema on a stale fact's
  nulls. A gateway digesting a raw dump would hand the broker a digest it cannot
  reproduce — fail-closed, and undiagnosable from either end.
- **`FR-02` and `FR-03` enforced for the first time** (`ADR-0024`), by an
  evaluator that **refuses a schema it cannot fully evaluate**.
- **`ResourceKeyRegistry.validate` shadowed `BaseModel.validate`** — same name,
  opposite meanings, no error at either end. Surfaced the moment `mypy --strict`
  ran with the pydantic plugin.
- **`P0-06`** written (`09-fact-provider-specification.md`, `ADR-0022`).

### Three enforcement mechanisms

| Mechanism | What it found |
|---|---|
| **Hard-rule register** | Governance stage 4's unwritten half. **19 hard critics, 17 enforcing, zero with an active fixture.** Blocking, shrink-only, named for what it enforces — a check reading "hard rules have fixtures" would be the false claim itself |
| **Scenario register** | 45 acceptance scenarios, **3 referenced by a test**. `07-increment-1-plan.md` claimed four; `A-21` is in no test or source file |
| **Spec-ID / ADR-index checks** | Caught three of my own regressions within minutes of existing — two ADRs cited before they were written, and two tests naming a fixture without its marker |

### QA

Every CI job run with its own command, not an approximation:

| Gate | Result |
|---|---|
| Lint (`ruff`, `mypy --strict`) | **0 / 0** — was 173 and 48 |
| Tests + coverage floor | **2709 passed**, 99.01% (floor 90%) |
| Mutation kill run + fixture registry | pass |
| Schema conformance, render drift | pass, in sync |
| No-magic-values | pass |
| Hard-rule register | pass (17 known gaps, counted) |
| Docs: spec IDs, ADR index, scenarios | pass |
| `pip-audit` | no known vulnerabilities |
| `gitleaks` (history) | pass, after the two findings below |

That QA run found two real defects, and missed a third until CI ran.

**The linter's rule set was drifting under its own gate.** `python -m ruff`
(0.16.8, what CI installs) reported `RUF036` where the `ruff` on PATH (0.15.8)
did not. `ruff>=0.6` and `mypy>=1.11` were unbounded floors, so the build could
go red with nothing in the tree having changed. Both are now upper-bounded — and
`.pre-commit-config.yaml`, whose own comment says the hooks "mirror CI stage 1
exactly", was pinned at `v0.14.0` and had been missed. It now matches. Its
`ruff-format --check` hook is removed for the reason CI already records for
excluding that stage: it fails on 53 of 134 files, so it was a blocking hook
that was red on every commit, which is a hook people learn to `--no-verify`
past.

**The secret scan was reported as not run, and it was runnable.** The claim in
the first version of this table — *"CI pins a release binary, not installable
locally"* — was false: the same pinned binary downloads and runs here, and doing
so reproduces the CI failure exactly. It reported two findings, both **key
identifiers rather than keys**: `key_id` in the fact-provider specification's
signature block, whose `value` is the literal text `<base64>`, and `key_id` in
the checkpoint test fixture, whose `signature` is a self-describing example
string. A key id is public by construction — it rides in the signature block so
a verifier knows which public key to fetch (`NFR-17`, `SEC-10`).

`.gitleaks.toml` exempts those two values and nothing else: anchored literals,
not fingerprints (which name a line number and go stale) and not the `key_id`
field (which would stop the scanner reporting a real key mislabelled as an
identifier). Verified narrow rather than asserted — a *different* key id of the
same shape, `fpr-signing-2027q1`, is still caught.

The allowlist is held to that shape by `tests/unit/test_secret_scan_allowlist.py`
rather than by convention, because widening it is the cheapest response to the
next false positive and leaves a green check behind. Five mutations of the
config were confirmed to fail it: `useDefault = false`, an unanchored entry, an
anchored wildcard, a `paths` exclusion, and an entry gone stale.

This is the second claim in this document falsified by checking it, after the
twelve in the plan. Both were claims that something could not be verified.

### What this does *not* do

Stated plainly, because the number the phase is judged by did not move.

**The Phase 1a exit gate names 21 fixtures that must be `active`. There were 4 of
those 21 before this increment and there are 4 now.** `MUT-07` was expected to
promote; it stays `partial`, because §5.1 built the *detection* and the
consequence — `SCHEMA_INVALID`, no evaluation — needs the orchestrator, which is
excluded. Promoting it would have produced a green, override-free check for a
gate whose second half nobody wrote. The plan's own DoD item is corrected in
place rather than quietly satisfied.

Nothing user-visible changes. There is still no gateway, no PDP, no fact layer,
no broker and no identity layer.

### Blocked on you

| | Why it matters | Who |
|---|---|---|
| **`P0-12` — name the humans** | **Nine `Proposed` ADRs and nobody who can accept one.** Also blocks two-person review for `P1-15`, `OQ-05`, the `P0-01`/`P0-07` signatures, and `D-7` | Sponsor |
| **`P0-13` — provision** | Gates `P1-01a`, `P1-07`-for-real, `P1-08`, and everything behind them | Sponsor, with procurement |
| **`D-7`** — does a missing *required* fact ever warrant escalation? | Hard precondition on fact providers. The reference registry already contains the provably inert shape | Product + security |
| **Default branch** | `main` now exists at the increment-1 end state and is this PR's base, but the repository default is still this topic branch. Settings → Branches | Repository owner |
| **The licence** | `ADR-0012` recommends Apache-2.0 and deliberately does not decide it: a grant cannot be withdrawn once given | Repository owner |

**Recommendation: do not schedule increment 3 until `P0-12` closes.** It is the
cheapest of the five — a meeting, not money — and it unblocks the others.
`08-increment-2-plan.md` §13 carries the dated re-baseline: **24 weeks becomes at
least 28**, and the span beyond that is sponsor latency rather than engineering.

### Review guide

Start with `docs/sdd/08-increment-2-plan.md` (`Appendix C` for what version 1 got
wrong), then the six ADRs: `ADR-0023` grammar, `ADR-0024` argument evaluation,
`ADR-0025` reason subjects, `ADR-0026` issuance and outcome, `ADR-0011`
reference workflow, `ADR-0012` licence.

No test was skipped, weakened or quarantined.
