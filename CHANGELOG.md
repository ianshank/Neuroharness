# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning will follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) from the first release.

Two conventions this project adds, both because its subject is enforcement:

- **Wire-schema versions are tracked separately from the package version.** The
  action envelope, the decision record and the action-class registry each carry
  their own version, declared in `src/neuroharness/version.py`. Widening the set
  a build can read is backwards-compatible; narrowing it is not and gets a
  migration note here.
- **A gate that starts blocking is a breaking change for contributors**, even
  when it breaks no API, so it is listed rather than filed under "internal".

This project has not made a release. Everything below is unreleased.

## [Unreleased]

### Added

- **`.claude/skills/`** — four skills encoding the multi-site procedures this
  repository gets wrong when rushed: `add-reason-code`, `add-record-kind`,
  `add-mutation-fixture` and `run-gates`. Each carries a machine-readable block
  naming the sites it tells a contributor to change, and
  `tests/unit/test_skills_are_current.py` holds those names to the tree, so a
  skill that goes stale fails CI rather than sending the next contributor
  confidently into the wrong edit.
- **`Makefile`** — one target per CI job, wrapping the commands the workflow
  actually runs. `make gate` runs every blocking gate in CI's order.
  `tests/unit/test_gate_parity.py` fails if the Makefile and the workflow drift,
  because a local gate that runs less than the pipeline is worse than none.
- **`CHANGELOG.md`** — this file.
- **Governance section 3, stage 2's second clause is now enforced**: the
  resolver at 100% branches, as its own CI step. It was specified from the start,
  enforced nowhere, and recorded as "unenforceable as written" because
  `resolve/safety.py`'s import-time rank guard was uncovered. It is covered now —
  by `tests/unit/test_import_time_guards.py`, which makes each guard fire, rather
  than by a `# pragma: no cover` that would have moved the number without
  learning anything. `resolve/` is at 100% statements and 100% branches.
- **A sixth import-time guard**: `version.py` now refuses at import if the build
  would write a schema version it cannot read back. That invariant was stated in
  a comment for two increments while five of its siblings were executable, and
  its failure mode is the one the evidence layer may not have — records written
  and then found unreadable at replay (`FR-71`, Constitution Art. III).
- **`tests/unit/test_suite_conventions.py`** — every module under
  `tests/property/` must carry `pytestmark = pytest.mark.property`.
- **C4 level 3 for the deterministic core** in `docs/sdd/02-technical-plan.md`
  §2.2a, covering only what is built. Levels 1 and 2 describe the system as
  designed; this one exists so the two cannot be confused.
- **`docs/review/2026-09-19-round-5-repository-gap-analysis.md`** — the gap
  analysis, tech-debt register and the record of which artefacts are deliberately
  absent and what unblocks each.

### Changed

- **The coverage floor moved into `pyproject.toml`.** It lived only in `ci.yml`
  as `MIN_COVERAGE`, so `pytest --cov` run locally enforced nothing and the first
  place a contributor learned they were under it was a red pull request — the
  same argument `ci.yml` already makes for the ruff rule set, applied to the one
  gate it had not been applied to. The number is declared in three places and
  `test_gate_parity.py` asserts they agree.
- **`tests/property/test_grammar_agreement.py` now carries the property marker.**
  It did not, so its ten properties — the ones holding the resource-key grammar
  together across five consumers — were outside every `-m property` selection.
  `-m property` selects 33 tests where it selected 23.
- **Two checks moved into the jobs whose names make their failure legible**: the
  generated-schema drift check into `schema-conformance`, and the secret-scan
  allowlist guard into `security`. Both ran only inside the full unit-test job,
  so a drift between the Python grammar and the published contract was reported
  under "Unit tests".
- `docs/sdd/07-increment-1-plan.md` §4a said CI runs four of the twelve stages.
  It runs eight jobs covering six. Corrected in place.
- `ADR-0021` described a rule its implementation deliberately does not follow —
  it said floats "are permitted" while the guard has always tested the canonical
  *form*, not the Python type. The code was right; the ADR is amended, with the
  reasoning it lacked (the broker re-parses canonical bytes, so a type-keyed rule
  would admit at the gateway and refuse at the broker).
- `pyproject.toml`'s ruff comment pointed at a `ratchet` rule group that was
  never written. Corrected rather than implemented: a rule is blocking or absent.

### Fixed

- **The fifth resource-key grammar.** `08-increment-2-plan.md` §3.1 found four
  spellings diverging on five axes and unified them behind `grammar.py`. A fifth
  — `models/envelope.py`'s `ResourceKey` alias — was in neither the enumeration
  nor the property test written to catch exactly this. It disagreed with the
  signed registry in both directions, and `models/record.py` imports it, so the
  disagreement reached `ExecutionLease.resource_key` (`FR-25`'s lease identity)
  and the required `EffectVerification.resource_key`. A hyphenated resource kind
  — `cluster-prod:svc-a`, which is what any real deployment writes — was accepted
  by the registry and by the published schema, rendered fine in a reason code,
  and could not be recorded: lease contention reached an operator as
  `SCHEMA_INVALID` rather than as contention. Under Constitution Article III that
  is a decision that was not made. The alias now validates with
  `grammar.RESOURCE_KEY_PATTERN` itself, so the model and the registry share one
  compiled object. `ADR-0023` already decided this; nothing new was decided.

### Security

- No change to the trusted computing base. The runtime dependency set is
  unchanged (one package, `pydantic`), and `pip-audit --strict` and `gitleaks`
  remain clean. The secret-scan allowlist was not widened, and
  `tests/unit/test_secret_scan_allowlist.py` now runs in the `security` job where
  a reviewer will look for it.

## Earlier work

Before this file existed, the history is the increments and their reviews, each
of which carries its own record:

- **Increment 1 — the deterministic core.** `docs/sdd/07-increment-1-plan.md`
  scopes it; `docs/review/2026-09-18-increment-1-implementation-findings.md`
  records what the implementation found that the specification had not.
- **Increment 2 — repair the drift.** `docs/sdd/08-increment-2-plan.md`, whose
  Appendix C publishes the twelve of its own factual claims that an adversarial
  review falsified.
- **Peer review rounds 1–4.** Round 1 reviewed the research, round 2 the
  specification (71 findings, one critical), round 3 an external multi-model
  synthesis against the tree, and round 4 the implementation itself. All four are
  under `docs/review/`.
