# Increment 2 — pull-request description (ready to open)

> **Why this is a file and not a pull request.** The repository has exactly one
> branch, `claude/sdd-plan-peer-review-i2v012`, and it is also the default
> branch, so there is nothing for a pull request to target. `main` was created
> for PR #1, merged, and deleted. This body is checked in so it is ready the
> moment a base branch exists; see "Blocked on you" below, and
> `08-increment-2-plan.md` §4.4.
>
> Copy everything below the rule into the PR body.

---

## Increment 2: repair the drift, close what can be closed, build what is honest

Implements `docs/sdd/08-increment-2-plan.md`. **143 files, +11,666 / −372.**

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
  sentence, and it digested that way.
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
| Tests + coverage floor | **2598 passed**, 98.96% (floor 90%) |
| Mutation kill run + fixture registry | pass |
| Schema conformance, render drift | pass, in sync |
| No-magic-values | pass |
| Hard-rule register | pass (17 known gaps, counted) |
| Docs: spec IDs, ADR index, scenarios | pass |
| `pip-audit` | no known vulnerabilities |
| `gitleaks` | **not run** — CI pins a release binary, not installable locally |

That QA run found a real defect: `python -m ruff` (0.16.8, what CI installs)
reported `RUF036` where the `ruff` on PATH (0.15.8) did not. `ruff>=0.6` and
`mypy>=1.11` were unbounded floors, so the gate's rule set drifted underneath it
and the build could go red with no change to the tree. Both are now
upper-bounded.

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
| **Branching model** | Why this is a file. One branch, which is the default | Repository owner |
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
