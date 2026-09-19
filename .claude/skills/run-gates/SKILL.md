---
name: run-gates
description: Run the repository's quality gates the way CI runs them, and repair a failure without weakening it. Use before pushing, when a check is red, or when asked to validate a change. Covers the make targets that mirror each CI job and the fail-first discipline a repair must follow.
---

# Running the gates

Eight jobs block a pull request. `make gate` runs every blocking one in CI's
order; `tests/unit/test_gate_parity.py` fails if the Makefile and the workflow
ever stop agreeing, so the local run cannot quietly become the kinder one.

```
make install   # the package and its dev extra, as every CI job does
make gate      # everything CI blocks on
make security  # separate: needs gitleaks and pip-audit installed
```

Individual targets — `make help` lists them — map one-to-one onto the jobs:
`lint`, `conventions`, `coverage`, `resolver-coverage`, `mutation`, `schema`,
`schemas-current`, `magic-values`, `hard-rules`, `docs`.

## Repairing a failure

**The rule that overrides every convenience:** never skip, disable, weaken or
quarantine a test or a mutation fixture to get a gate green. Fix the root cause
or surface it. This is the rule whose violation is hardest to see in review.

The discipline for a repair, in order:

1. **Reproduce it locally first.** A fix pushed against a failure you have not
   seen is a guess with a CI round-trip attached.
2. **Write the failing check before the fix, and watch it fail.** A test that
   passes before the change tests nothing. When the round-four grammar repair
   landed, the property test was added first and failed on nine tests; that
   failure is the evidence the test is real.
3. **Fix the cause, not the symptom.** Widening a pattern, an allowlist or an
   `ignore` list to quiet a finding is the cheapest response and almost always
   the wrong one.
4. **Re-run the whole gate**, not the target that was red. A narrow fix that
   breaks something else is the common outcome.

## Specific failures and what they mean

- **`test_the_checked_in_schemas_match_python`** — run
  `python tools/render_schema_patterns.py`. Hand-editing generated nodes is a
  test failure by design.
- **`test_hard_rule_fixtures.py`** — the gap register may only shrink. A new
  hard enforcing rule needs an `active` fixture or a register entry naming the
  task that owes it.
- **A coverage floor** — `pyproject.toml` holds it so a local run enforces it
  too. Lowering the number to pass is the change this file exists to prevent.
- **`test_no_magic_values.py`** — a governing value belongs in `defaults.py` or
  the signed registry, named, with the requirement it comes from.
- **`test_gate_parity.py`** — you changed one of `ci.yml` or `Makefile` and not
  the other.

## Deliberate absences

`make` has no `format`, `docker`, `integration` or `replay` target, because the
workflow has no such stage: the gateway, PDP client, critic bank and broker do
not exist, and a gate added before the thing it checks is a green check proving
nothing. The gap analysis records each with the component that unblocks it.

```json coupling
{
  "sites": [
    {"path": "Makefile", "contains": "gate:", "why": "the aggregate target"},
    {"path": "Makefile", "contains": "resolver-coverage:", "why": "governance stage 2's second clause"},
    {"path": ".github/workflows/ci.yml", "contains": "mutation-fixtures:", "why": "the job the mutation target mirrors"},
    {"path": "tests/unit/test_gate_parity.py", "contains": "def test_the_coverage_floor_is_one_number", "why": "the floor is declared three times and must agree"},
    {"path": "CONTRIBUTING.md", "contains": "never", "why": "the rules a repair may not break"},
    {"path": "tools/render_schema_patterns.py", "contains": "--check", "why": "the regenerate-and-compare gate"}
  ],
  "verify": ["gate"]
}
```
