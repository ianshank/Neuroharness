# Contributing to Neuroharness

This is a fail-closed policy-and-verification harness for LLM agent tool calls. Most of what follows is ordinary; the parts that are not are the parts that exist because a shortcut here produces an unenforced gate, and an unenforced gate is worse than no gate at all — it reports green.

> **Licence.** The repository is currently `UNLICENSED`, which means nobody outside the owner has permission to use, modify or distribute this code. Until a licence is chosen there is no legal basis for an outside contribution. See `docs/sdd/adr/ADR-0012-licence-and-contribution-model.md`; it is an open escalation, not an oversight.

## Read first

- `docs/sdd/00-constitution.md` — ten articles. They are not aspirational; several are enforced by CI.
- `docs/sdd/01-specification.md` — requirements with stable IDs (`FR-`, `NFR-`, `INV-`, `SEC-`).
- `docs/sdd/06-delivery-and-governance.md` §6 — the Definition of Done, which applies to every change.
- `CLAUDE.md` — the same conventions in short form.

## The workflow

**Spec before code** (Article VII). Requirements live in `docs/sdd/01-specification.md` with stable IDs. A change that alters behaviour changes the specification in the same pull request, and the commit, the tests and the PR description cite the ID. A decision about *how* goes in `docs/sdd/adr/` in MADR format; superseding a decision means a new ADR, never an edit to an accepted one.

**Trunk-based.** Short-lived branches, squash merge, [Conventional Commits](https://www.conventionalcommits.org/) (`feat(pdp): …`, `fix(evidence): …`, `docs(sdd): …`).

**A task is Done** when its acceptance criteria in `docs/sdd/03-work-breakdown.md` are met *and* the Definition of Done is satisfied. Not before, and "the code works" is not either of those.

## The rules that are not negotiable

**Never skip, disable, weaken or quarantine a test or a mutation fixture to get CI green.** Fix the root cause, or surface it. If a gate is genuinely wrong, change the gate deliberately and say so in the commit — do not route around it. This one is first because it is the rule whose violation is hardest to see in review.

**A gate without a killing fixture does not exist** (Article III). Every hard gate a change adds or modifies must have an `active` mutation fixture in `tests/fixtures/mutations/` that proves it blocks. `tests/unit/test_hard_rule_fixtures.py` counts what is missing; that register may only shrink.

**A fixture is `active` only when the whole gate exists.** If your change builds one layer of a multi-layer gate, the fixture stays `partial` and its `still_missing` names what is left and who owns it. Promoting on the strength of a half-built gate produces a green, override-free check for something nobody wrote — which is the specific failure `docs/sdd/05-evaluation-plan.md` §1a invented `partial` to prevent.

**Policy is production code.** Anything under `policy/**/*.rego` needs lint (Regal), unit tests (`opa test`), coverage and negative fixtures. **Hard-gate changes require two-person review.**

**No secrets, credentials or real customer data in fixtures.** Use synthetic values. `gitleaks` runs in CI and is the cheap half of this, not the whole of it.

**No free text where a type will do.** Reason codes are a closed catalogue and never carry prose (`SEC-07`): anything reaching the governed model is a structural attack surface. If you need to say something new, add a catalogue member deliberately — specification first.

**No hard-coded governing values.** Timeouts, TTLs, budgets and enumerations come from the signed registry or from `src/neuroharness/defaults.py`. `tests/unit/test_no_magic_values.py` is a blocking AST scan.

**Fail closed.** Any inability to evaluate ends in no execution. If you write a branch that cannot decide, it refuses — it does not pass through, and it does not guess.

## Running the checks

Eight jobs block a pull request. `make gate` runs every blocking one in the order
CI runs them, so the loop is local rather than a push and a wait:

```sh
make install   # the package and its dev extra, as every CI job does
make gate      # everything CI blocks on
make help      # one target per job: lint, coverage, mutation, schema, docs, ...
make security  # separate: needs gitleaks and pip-audit installed
```

`tests/unit/test_gate_parity.py` fails if the Makefile and the workflow ever stop
agreeing. A local gate that runs less than the pipeline is worse than none,
because it is trusted.

The underlying commands, if you would rather run them directly:

```sh
python -m pip install -e ".[dev]"

python -m pytest                 # the full suite
python -m ruff check src tests   # lint
python -m mypy                   # types; strict, with the pydantic plugin
```

`python -m mypy`, not `mypy`: the plugin has to load from the interpreter that has pydantic installed. Run any other way and every `BaseModel` subclass resolves to `Any`, so the models carrying the wire contract become the part of the tree `--strict` checks least.

`.pre-commit-config.yaml` mirrors CI stage 1 exactly. A hook stricter than CI trains people to skip hooks; a hook looser makes CI the first place a finding appears.

Some values are generated rather than written. After changing the reason-code catalogue or the resource-key grammar:

```sh
python tools/render_schema_patterns.py
```

Hand-editing what that tool writes is a test failure, deliberately.

## Skills

`.claude/skills/` holds the procedures that touch several files at once, where
missing one site is easy and the symptom is remote from the cause:

| Skill | When |
|---|---|
| `add-reason-code` | A new refusal, abstention or verdict reason needs a code |
| `add-record-kind` | Something new must be recorded and needs its own payload |
| `add-mutation-fixture` | You added or changed a hard gate, or are promoting a fixture |
| `run-gates` | Before pushing, or when a check is red |

Each one carries a machine-readable block naming the sites it tells you to
change, and `tests/unit/test_skills_are_current.py` holds those names to the
tree. A skill that goes stale fails CI rather than sending the next contributor
confidently into the wrong edit — instructions rot exactly the way the documents
in `docs/sdd/` rot, and the answer is the same: make the claim executable.

If you change a coupling a skill describes, update the skill in the same change.

## Writing tests

The suite is the argument that the harness works, so a test is expected to say *what defect it catches and why that defect matters*. Read `tests/unit/test_resolver_truth_table.py` or `tests/unit/test_mutation_fixtures.py` for the house style.

Specifics that come up in review:

- **Every negative case needs its control.** A check that refused everything would pass a table of refusals and be worthless.
- **Guard the guard.** A checker that passes by finding nothing is the failure mode this project is most exposed to. If a test walks a directory or a set, assert the walk found something.
- **Tables are data.** Parametrize over checked-in rows rather than writing the same assertion eight times.
- **Fixtures use real identifiers.** A fact name, rule id or resource key that no registry produces is a fixture describing a decision the harness cannot carry — and four such fixtures sat in this suite for two increments before a tightened check found them.
- **Property tests are deterministic** (`derandomize=True`). A project claiming replay determinism does not have a randomised suite.

## Reporting a security issue

Do not open a public issue. The threat model is `docs/sdd/04-threat-model.md`; contact the security lead named in `docs/sdd/06-delivery-and-governance.md` §8 — which, at time of writing, names nobody, and that is itself tracked as `P0-12`.
