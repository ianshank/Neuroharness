---
name: add-reason-code
description: Add a member to the closed reason-code catalogue. Use when a new refusal, abstention or verdict reason needs a code - anything that would otherwise be reported as free text. Covers the coupled sites in reason.py, grammar.py, the generated schema and the specification, and the import-time guards that refuse a half-finished addition.
---

# Adding a reason code

The catalogue is **closed** (`SEC-07`, specification §5.6). A reason code is the
only thing the harness ever says to a governed model about why it refused, so it
is typed data with a validated subject, never prose. Adding a member touches
seven places, and three import-time guards will refuse the package if you stop
half way — which is the design working, not an obstacle.

## Before you start

The specification changes first (Article VII). A code with no `FR-`/`SEC-`
requirement behind it is a decision nobody made.

## The coupled sites

1. **`ReasonName`** — the member itself. Value equals the name.
2. **`PARAMETERISED_REASONS`** — add it here *iff* the code carries a subject
   (`RULE_FAILED:<rule_id>`). Membership is what `__post_init__` checks: a
   parameterised code without a subject is refused, and so is a bare code with
   one.
3. **`SUBJECT_GRAMMAR`** — required for every parameterised member, and
   forbidden for every bare one. `_assert_subject_grammar_is_total` raises at
   import in both directions, so there is no way to land one without the other.
4. **`grammar.py`** — only if the subject is a shape that does not already
   exist. Reuse `RULE_ID_SOURCE`, `CRITIC_ID_SOURCE`, `SNAKE_SOURCE`,
   `PROPERTY_ID_SOURCE`, `UUID_SOURCE`, `RESOURCE_KEY_SOURCE` or
   `BATCH_POSITION_SOURCE` before writing a seventh.
5. **`INFRASTRUCTURE_REASONS` / `ESCALATABLE_REASONS`** — the two sets are
   disjoint and `test_reason_codes.py` enforces it. An infrastructure reason can
   never escalate to a human: nobody can vouch for an engine that is down.
6. **`python tools/render_schema_patterns.py`** — regenerates the `ReasonCode`
   node in `decision-record.schema.json`. Hand-editing what it writes is a test
   failure by design.
7. **Specification §5.6** — the prose catalogue. `test_spec_id_consistency.py`
   requires every identifier the code cites to exist under `docs/sdd/`.

Add an `errors.py` subclass **only** if the condition should be raisable as a
typed `FailClosedError`; `test_errors.py` walks the hierarchy and requires each
one to declare its `reason_name`.

## What will tell you it is wrong

- `ValueError` at import → `SUBJECT_GRAMMAR` and `PARAMETERISED_REASONS` disagree.
- `test_the_checked_in_schemas_match_python` red → you did not run the renderer.
- `test_reason_codes.py` red → the code is in both reason sets, or in neither
  when it should be in one.

```json coupling
{
  "sites": [
    {"path": "src/neuroharness/reason.py", "contains": "class ReasonName", "why": "the catalogue member"},
    {"path": "src/neuroharness/reason.py", "contains": "PARAMETERISED_REASONS", "why": "subject-bearing membership"},
    {"path": "src/neuroharness/reason.py", "contains": "SUBJECT_GRAMMAR", "why": "the subject's shape"},
    {"path": "src/neuroharness/reason.py", "contains": "INFRASTRUCTURE_REASONS", "why": "never escalatable"},
    {"path": "src/neuroharness/reason.py", "contains": "ESCALATABLE_REASONS", "why": "disjoint from the above"},
    {"path": "src/neuroharness/grammar.py", "contains": "RULE_ID_SOURCE", "why": "reuse a subject shape before adding one"},
    {"path": "tools/render_schema_patterns.py", "contains": "def drift", "why": "regenerates the published ReasonCode node"},
    {"path": "docs/sdd/01-specification.md", "contains": "RULE_FAILED", "why": "the prose catalogue, section 5.6"},
    {"path": "src/neuroharness/errors.py", "contains": "reason_name", "why": "only if the condition is raisable"}
  ],
  "verify": ["schemas-current", "gate"]
}
```
