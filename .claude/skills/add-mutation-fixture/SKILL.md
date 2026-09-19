---
name: add-mutation-fixture
description: Add or promote a negative mutation fixture proving a hard gate blocks. Use when adding or changing a hard gate, or moving a fixture from reserved to partial or active. Covers the catalogue row, the declaration JSON, the lifecycle states and the marked test, and what each state means mechanically.
---

# Adding a mutation fixture

Constitution Article IV: **a gate without a killing fixture does not exist.**
This is the one CI stage whose specification says no manual override exists, so
the machinery around it is deliberately unforgiving.

## The four states, and what each one means mechanically

| State | Meaning | Enforced by |
|---|---|---|
| `reserved` | The id and the intent exist; the gate does not. | No test may name it. The schema forbids `owned_here`/`still_missing`. |
| `partial` | Something blocks at one layer, but not the whole gate. **Not counted toward hard-gate coverage.** | Schema *requires* `owned_here` and `still_missing`, so it cannot read as full coverage. Must be killed by a marked test. |
| `active` | The gate exists and this fixture proves it blocks. | Must be killed by a marked test. The only state that lets a rule leave the hard-rule gap register. |
| `retired` | Superseded by a decision. | Requires `superseded_by` matching `^ADR-[0-9]{4}$`. Retiring is a decision, not a cleanup. |

**Do not promote to `active` because the test passes.** `active` means the whole
gate exists. A half-built gate stays `partial` with `still_missing` naming what
is left and who owns it — that state exists precisely to stop a green check
standing in for an unwritten gate.

## The coupled sites

1. **The catalogue row** in `docs/sdd/05-evaluation-plan.md` — the table
   `test_mutation_fixtures.py` parses. Format matters: `` | `MUT-NN` | gate |
   mutation | expected | phase | ``.
2. **The declaration** `tests/fixtures/mutations/MUT-NN.json`, validated against
   `_declaration.schema.json`. The filename must equal the `id`.
3. **`expected`** must name a real outcome — a `Verdict` value, a `ReasonName`
   value, or one of the small closed set. "Something fails" kills nothing, and
   the test checks this against the code's own vocabulary.
4. **The increment-plan row** in `docs/sdd/07-increment-1-plan.md` §4 — required
   for `active` and `partial`, forbidden for `reserved`. The declared states and
   the plan's tables must match exactly.
5. **A test carrying `@pytest.mark.mutation`** that names the id, either in its
   body or in its name (`test_mut_13_...`). Naming a fixture *without* the
   marker fails: it would be invisible to the CI mutation job.

## Writing the test

A negative case needs its control. A fixture that proves a gate blocks proves
nothing unless something also proves the gate passes what it should — otherwise
a gate that refuses everything satisfies it.

```json coupling
{
  "sites": [
    {"path": "docs/sdd/05-evaluation-plan.md", "contains": "MUT-13", "why": "the catalogue table"},
    {"path": "tests/fixtures/mutations/_declaration.schema.json", "contains": "still_missing", "why": "partial requires it"},
    {"path": "docs/sdd/07-increment-1-plan.md", "contains": "MUT-19", "why": "the increment plan's state tables"},
    {"path": "tests/unit/test_mutation_fixtures.py", "contains": "CLAIMING_STATES", "why": "active and partial must be killed"},
    {"path": "tests/mutation/test_killing_fixtures.py", "contains": "pytestmark", "why": "the marker the CI job selects on"}
  ],
  "verify": ["mutation", "hard-rules"]
}
```
