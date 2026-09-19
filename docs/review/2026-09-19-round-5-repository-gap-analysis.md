# Round-5: repository gap analysis, tech-debt register, and what is deliberately absent

| | |
|---|---|
| **Subject** | The repository as an artefact — tooling, gates, hygiene, structure and documentation — rather than the specification (round 2), an external synthesis (round 3) or the implementation's behaviour (round 4). |
| **Method** | Three parallel surveys (tooling and automation; code structure and hygiene; test suite and gates), every claim measured against the tree rather than recalled. §6 lists the commands. |
| **Date** | 2026-09-19 |
| **Outcome** | 2 gates turned on, 6 defects fixed, 23 items registered, 7 artefacts recorded as deliberately absent with the component that unblocks each. |

---

## 1. The rule this round is written under

`.github/workflows/ci.yml` states it in its own header:

> That document specifies twelve blocking stages. This workflow runs the ones
> that have something real to run against the code that exists, and no others.
> **A stage added before the thing it checks exists would be a green check
> proving nothing**, which is the inversion the evaluation plan's fixture
> lifecycle (section 1a) was written to prevent: it retires the pressure to
> build the real gate.

That rule decided the shape of this change. A request for "Docker, C4, coverage
gates, integration, SBOM" is a reasonable request for most repositories and a
trap for this one: four of those would describe components that do not exist —
the gateway, the PDP client, the critic bank and the broker — and each would
convert a counted gap into a green tick.

So: everything that describes what exists was done. Everything that would
describe fiction is in §5, with the component that unblocks it. **That table is
the deliverable for those items**, not a placeholder for them.

---

## 2. The twelve stages, measured

CI runs **eight jobs covering six of the twelve**, plus three checks the workflow
declares outside the twelve. `07-increment-1-plan.md` §4a said four; it was
written when four ran and is corrected in this change.

| # | Governance §3 stage | State | What it waits on |
|---|---|---|---|
| 1 | Lint and type-check | **Running** — `ruff` and `mypy --strict`, clean at zero; plus repository conventions | `Regal` needs `policy/` (`P1-15`); `ruff format` is a 53-file diff and its own change |
| 2 | Unit tests + coverage; **resolver 100% branches** | **Running, both clauses.** The second was enforced nowhere and recorded as "unenforceable as written"; this change makes it enforceable and turns it on | — |
| 3 | Policy unit tests ≥ 95% of rules | Absent | `policy/` does not exist (`P1-15`) |
| 4 | Negative mutation fixtures, both halves | **Running**, no override | The 30 `reserved` fixtures activate with their gates |
| 5 | Behaviour scenarios (pytest-bdd) | Absent | 42 of 45 scenarios are not executable; each names the task that owes it |
| 6 | Integration (testcontainers) | Absent | OPA, PostgreSQL, broker (`P1-04`, `P1-06a`, `P1-08`) |
| 7 | Golden replay — 0 diffs | Absent | No corpus; `P1-16` |
| 8 | Code mutation score ≥ 80% on pure packages | Absent | Blocking from Phase 2 **by design**; `resolve`, `tokens`, `canonical` are pure and ready |
| 9 | Security: CodeQL, pip-audit, gitleaks, Trivy | **Partly running** — gitleaks, pip-audit, and the allowlist-shape guard | CodeQL and Trivy need repository-admin settings and a container (`P0-10`) |
| 10 | Schema conformance | **Partly running** — envelope and record, both directions, plus generated-pattern drift | Registry and PDP-input halves need `P1-04` |
| 11 | Docs: link check, ADR index, spec IDs | **Partly running** — ADR index, spec IDs, scenario register, skill currency | Link check is unbuilt and is the cheapest remaining item here |
| 12 | Build: container, SBOM, provenance, cosign | Absent | Nothing is packaged; see §5 |

Outside the twelve, and deliberately so: `no-magic-values`,
`hard-rules-have-fixtures`, and the repository-convention checks added here.

---

## 3. What this change fixed

| # | Finding | Evidence | Disposition |
|---|---|---|---|
| `R5-01` | The **coverage floor lived only in `ci.yml`**, so `pytest --cov` locally enforced nothing. `ci.yml` makes exactly this argument for the ruff rule set — "a gate whose rule set is spelled in a workflow file is a gate nobody can reproduce locally" — and had not applied it here | `ci.yml:29`; `pyproject.toml` had no `fail_under` | **Fixed** — floor in `pyproject.toml`; three declarations, checked against each other by `test_gate_parity.py` |
| `R5-02` | **Stage 2's resolver clause was unenforced.** The only uncovered branches in `resolve/` were three import-time guards whose failure arms had never executed — which `CONTRIBUTING.md` names as "the failure mode this project is most exposed to" | `resolve/` at 97%; `safety.py:75`, `inputs.py:423,433` | **Fixed** — `test_import_time_guards.py` makes each fire; `resolve/` at 100%/100%; gate turned on |
| `R5-03` | **`version.py`'s central invariant was a comment.** "Always a member of the readable set" was asserted in prose while five sibling guards were executable. A build writing a version it cannot read produces evidence that fails its own replay | `version.py:50` | **Fixed** — sixth import-time guard, with tests that fire it |
| `R5-04` | **`test_grammar_agreement.py` carried no property marker**, so its ten properties were outside every `-m property` selection. Nothing could have caught it: the only symptom is a smaller number in a report nobody compares | all three sibling modules set `pytestmark` | **Fixed** — marker added; `test_suite_conventions.py` enforces it; selection 23 → 33 |
| `R5-05` | **Two gates ran only inside the full unit-test job**: the generated-schema drift check and the secret-scan allowlist guard. A drift between the Python grammar and the published contract was reported under "Unit tests" | neither named in any job | **Fixed** — moved into `schema-conformance` and `security` |
| `R5-06` | **`pyproject.toml`'s ruff comment named a `ratchet` group that was never written** — the documentation half of the defect this project keeps finding in its code | `pyproject.toml:64-65` | **Fixed** — corrected rather than implemented |

---

## 4. Tech-debt register

Each row: evidence, severity, and either an owner or the component that must
exist first. Nothing here is fixed in this change; nothing here is hidden.

### 4.1 Correctness and contract risks

| # | Finding | Evidence | Severity | Blocked on |
|---|---|---|---|---|
| `R5-07` | **No dispatch record.** Nothing is appended between token consumption and the receipt, so a broker crashing after dispatch leaves a chain indistinguishable from "never sent". `INV-05` requires a record durable before a *token*; the same discipline is not applied before an *effect* | round 4 `R3-S1`; `decision-record.schema.json` kinds | **High** | ADR + `FR-`, before `P1-08` |
| `R5-08` | **`RecordKind` and `RECORD_PAYLOAD_FIELDS` are not statically tied.** A kind without a dict entry raises a bare `KeyError` from inside a validator rather than a `ValidationError` | `models/record.py:917-926`, `_kind_matches_payload` | Low | Partly mitigated here: `test_skills_are_current.py` asserts the two agree |
| `R5-09` | **No schema↔model field-set parity test.** `test_schema_conformance.py` parametrizes over `build_records()`, not `RecordKind`; nothing asserts `Context.model_fields` matches the schema's properties. This is the general form of round 4's `R4-S1` | — | Medium | — |
| `R5-10` | **Tokens carry no schema version.** `SchemaKind` has no `TOKEN` member, and `DecisionToken`/`SignedToken` are the objects designed to cross a process boundary | `tokens/model.py`; `version.py:26` | Medium | `P1-08` (the broker is the other end) |
| `R5-11` | **Two exported `MAX_SIGNATURE_LENGTH` constants**, 512 and 4096, for two different subjects (checkpoint signature, token signature). Not a drift — they never meet — but `tokens/model.py:50-52` claims its bounds "mirror `decision-record.schema.json`", and for this one there is nothing in that schema to mirror | `models/record.py:269`; `tokens/model.py:56` | Low | — |
| `R5-12` | **`BatchPolicy` and `ConnectorKind` are declared twice**, identically, in `models/envelope.py` and `registry/models.py`. They are separate types, so a value crossing from registry to envelope is not type-compatible | both files, same members, same `FR-` citations | Low | — |

### 4.2 Observability

| # | Finding | Evidence | Severity |
|---|---|---|---|
| `R5-13` | **`evidence/chain.py` is entirely silent.** Ten distinct `ChainBreak` outcomes — tamper, truncation, hash mismatch, missing genesis — are returned with no log line anywhere in the module. These are the detections `SEC-10` exists to produce | 10 return sites | **Medium-high** |
| `R5-14` | **The `PERMISSIVE` registry branch returns `None` silently**, degrading enforcement. `config.py:36` calls it "a deliberate, recorded reduction in coverage"; it is not recorded at runtime | `registry/models.py:657-660` | Medium |
| `R5-15` | **Four distinct `RecordNotConstructibleError` causes collapse into one log event** carrying the same reason code, so the four are indistinguishable in the log | `pipeline/decision.py:439,481,503,534` | Low |
| `R5-16` | **Event naming is inconsistent**: `tokens/service.py`, `resolver.py` and `registry/loader.py` use flat names (`token_issued`); every other package uses dotted namespaces (`pipeline.evaluated`). And `logging.py:228` claims "every event in this package is ... a module-level constant" — true of 13 of 29 | — | Low |
| `R5-17` | **The `HALTED` short-circuit emits no distinct event.** The incident lever returns `DENY` before any critic runs and reads in the log like any other `DENY` | `resolve/resolver.py:184-206` | Low |

### 4.3 Structure

Module sizes were measured and **no decomposition is proposed**. These files are
large because their docstrings carry the reasoning: the package is 46.8% code to
36.8% prose, and six modules are majority prose (`envelope/wire.py` is 27 lines
of code to 96 of explanation). Splitting a cohesive module to hit a line count
would trade a real property for a metric, and under Article VII a structural
change needs an ADR first. What is recorded instead is *where a second subject
is genuinely present*, so a future split has evidence behind it.

| # | Module | Second subject | Lines |
|---|---|---|---|
| `R5-18` | `pipeline/decision.py` | `_IssuanceRecord`, an `IssuanceEvidence` adapter implementing a `tokens.nonce` protocol against an `evidence` writer — 21% of the file | 812 |
| `R5-19` | `evidence/store.py` | Four subjects: store protocol, WAL-coordinating facade, record assembly, and the auditor export format (`FR-73`) | 675 |
| `R5-20` | `models/record.py` | A second reason-code string parser (`_coerce_reason_code`) beside `reason.py`'s `ReasonCode.parse` | 1056 |
| `R5-21` | `models/envelope.py` | Defines the package-wide `WireModel` base and the `Absent` sentinel, which `evidence/store.py` imports — a de-facto second `models/common.py` | 682 |
| `R5-22` | `tokens/nonce.py` | Its own docstring names three responsibilities; four independent protocols | 643 |

### 4.4 Wiring

| # | Finding | Evidence | Severity |
|---|---|---|---|
| `R5-23` | **`envelope/`, `registry/` and `canonical/` have no in-repo production caller.** `pipeline/decision.py` imports none of them, and there is no `__main__`, no CLI and no `[project.scripts]`. So `FR-02` argument validation, `FR-03` proposal stripping and `FR-04` digesting are implemented, tested, and assembled by nothing. `envelope/proposal.py`'s own docstring says `Context.stripped_proposal_keys` "has been a field that nobody populates since the model was written" | import graph | **Medium** — this is the envelope builder, and it needs `P0-13` (identity) and `P0-06` (facts) |

### 4.5 Housekeeping

| # | Finding | Severity |
|---|---|---|
| `R5-24` | `freezegun` is a declared dev dependency and is imported nowhere; time is injected through the `Clock` seam instead. A dependency in the audited set that nothing uses | Low |
| `R5-25` | `uv.lock` is checked in and `uv` is never invoked — every CI job uses `pip install -e ".[dev]"` | Low |
| `R5-26` | Governance §4 commits to "Renovate/Dependabot with grouped weekly updates"; neither is configured | Low |
| `R5-27` | `ci.yml` and `.pre-commit-config.yaml` disagree on the `ruff format` diff size (50 of 85 files vs 53 of 134) | Low |
| `R5-28` | `test_canonical_properties.py:252` draws from a fixed `sampled_from` list with no control row, so a narrowed list would leave the property passing vacuously — the one residual vacuity risk in an otherwise carefully guarded property suite | Low |
| `R5-29` | The `Environment` enum declares exactly the strings `test_no_magic_values.py` forbids as literals, and nothing uses it | Low |

---

## 5. Deliberately absent, and what unblocks each

This is the answer to "why is there no Dockerfile", written once, where a
reviewer will look.

| Artefact | Why not now | Unblocked by |
|---|---|---|
| `Dockerfile`, `.dockerignore` | Nothing is packaged. There is no service, no entry point, no `[project.scripts]`. A container would build a library and prove it | `P1-01a` (gateway) and `P0-13` (environments) |
| **C4 level 2 as a diagram**, level 4 | Level 2 exists as a table naming containers that do not exist; drawing them would read as evidence they do. Level 3 for the built core was added in this change | The containers themselves: `P1-01a`, `P1-04`, `P1-08` |
| Integration stage (§3.6) | testcontainers for OPA, PostgreSQL, broker and provider stubs — none of which this repo talks to | `P1-04`, `P1-06a`, `P1-08` |
| Golden replay (§3.7) | No corpus. Replay needs recorded decisions from a running harness | `P1-16` |
| SBOM, provenance, cosign (§3.12) | Signing on `main` and tags only, over a container that does not exist | `P0-10` + a container |
| CodeQL, Trivy (§3.9) | Repository-admin settings and an image | `P0-10` |
| `ruff format` | Would rewrite 53 of 134 files. A diff that size landing beside substantive work buries it | Its own change, on a quiet tree |
| Review/QA **agents** under `.claude/` | Non-deterministic output cannot be gated the way the four skills are. Skills first; agents once the harness has proven itself | This change's skills surviving a few increments |

---

## 6. What was measured

```
PYTHONPATH=src python3 -m pytest                      # 2724 passed (was 2709)
PYTHONPATH=src python3 -m pytest --cov=neuroharness --cov-branch
                                                      # 99.20%, floor 90 from pyproject
make resolver-coverage                                # resolve/ 100% stmts, 100% branches
python3 -m ruff check src tests                       # clean
python3 -m mypy                                       # clean, 43 source files
make gate                                             # every blocking gate, CI's order
PYTHONPATH=src python3 -m pytest -m property --collect-only   # 33 (was 23)
```

Structural figures: 43 source files, 13,262 lines — 46.8% code, 29.5% docstring,
7.3% comment. Tests are 20,064 lines, 3.2× the production code. One runtime
dependency (`pydantic`); no array, numeric or ML dependency anywhere, consistent
with `ADR-0004`. Six import-time totality guards. One import cycle, in the type
graph only, broken by a `TYPE_CHECKING` guard. No star imports, no unused
imports, no symbol with zero references.

Counted gaps, unchanged by this round and published by the repo itself: **5
active / 6 partial / 30 reserved** mutation fixtures against a Phase 1a gate
naming 21; **17 hard enforcing rules with no active fixture**; **42 of 45
acceptance scenarios not executable**.

## 7. Reviewer limitations

- The three surveys were run in parallel and their findings cross-checked
  against the tree before being recorded here; where a survey's claim did not
  survive checking it was dropped rather than softened. One example is kept in
  `R5-11` because the correction is more useful than the finding.
- Module cohesion (§4.3) is a judgement about docstrings and imports, not a
  measurement. Each row names the evidence so the judgement can be disputed.
- No concurrency, crash-injection or performance testing was done.

## 8. Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-09-19 | Initial round-five gap analysis. Six defects fixed, 23 registered, 8 artefacts recorded as deliberately absent. |
