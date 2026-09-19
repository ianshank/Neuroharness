---
name: add-record-kind
description: Add an event type to the hash-chained decision record. Use when a new thing must be recorded - a dispatch, a completion, an override - and it needs its own payload. Covers the four schema edits, the six model edits and the conformance fixture, and explains which of them fails loudly and which fails as a bare KeyError.
---

# Adding a decision-record kind

The record is the artefact this project exists to produce: Article III says a
decision that has not been durably recorded has not been made. A kind is an
append-only addition to a published contract, so it moves in both the schema and
the model together or it is a contract that disagrees with itself.

The record is **not** a `oneOf` and **not** a pydantic discriminated union. The
"union" is four declarations that must agree, and only one of them fails loudly.

## Before you start

An ADR, then the specification, then this (Article VII). `FR-72` enumerates the
linked event types in prose and is part of the contract.

## The coupled sites

**In `docs/sdd/schemas/decision-record.schema.json` — four edits:**

1. the `kind` enum;
2. a sibling property `"<kind>": {"$ref": "#/$defs/<Payload>"}`;
3. an `allOf` `if/then` branch requiring the payload — and `decision_id` /
   `action_id` too, unless the kind is harness-scoped like `override` and
   `checkpoint`;
4. the `$defs/<Payload>` object itself, `additionalProperties: false`.

Also update the top-level `description`, which lists the kinds in prose.

**In `src/neuroharness/models/record.py` — six edits:**

5. the payload class, deriving `WireModel`;
6. the `RecordKind` member;
7. **`RECORD_PAYLOAD_FIELDS`** — kind → payload field name. *This is the one
   that bites.* `_kind_matches_payload` subscripts this dict directly, so a kind
   without an entry raises a bare `KeyError` from inside a validator rather than
   a `ValidationError`. Nothing static ties the enum and the dict together;
8. the `RecordPayload` alias;
9. `RECORD_KINDS_REQUIRING_DECISION_LINK`, if the kind is action-scoped;
10. the `DecisionRecord.<kind>: <Payload> | None = None` field, and both
    `__all__` lists — `models/record.py` and `models/__init__.py`.

**In the tests:**

11. a `build_<kind>()` in `tests/unit/test_schema_conformance.py` and an entry
    in `build_records()`. The two conformance tests parametrize over
    `build_records()`, **not** over `RecordKind` — so a kind you add to the enum
    and the schema and forget here is silently unexercised in both directions.

## What needs no change

`evidence/chain.py` hashes whatever keys the mapping carries, and `evidence/wal.py`
is kind-agnostic. `evidence/store.py` changes only if the kind belongs in
`RECORD_KINDS_REQUIRING_DECISION_LINK`, and that constant lives in `record.py`.

## Versioning

An additive optional property does not need a `schema_version` bump. If you bump
it, four sites move together: the schema's `const`, `_READABLE` and `_WRITTEN` in
`version.py`, and `EXPECTED_*` in `test_schema_versions.py`. `version.py` refuses
at import if the build would write a version it cannot read.

## Article IV

A new record kind that carries a gate needs a mutation fixture. See the
`add-mutation-fixture` skill; a gate without a killing fixture does not exist.

```json coupling
{
  "sites": [
    {"path": "docs/sdd/schemas/decision-record.schema.json", "contains": "\"kind\"", "why": "the kind enum"},
    {"path": "docs/sdd/schemas/decision-record.schema.json", "contains": "allOf", "why": "the per-kind required branch"},
    {"path": "src/neuroharness/models/record.py", "contains": "class RecordKind", "why": "the member"},
    {"path": "src/neuroharness/models/record.py", "contains": "RECORD_PAYLOAD_FIELDS", "why": "kind to payload field; a missing entry is a bare KeyError"},
    {"path": "src/neuroharness/models/record.py", "contains": "RecordPayload", "why": "the payload alias"},
    {"path": "src/neuroharness/models/record.py", "contains": "RECORD_KINDS_REQUIRING_DECISION_LINK", "why": "action-scoped kinds link to their action"},
    {"path": "src/neuroharness/models/record.py", "contains": "class DecisionRecord", "why": "the optional payload field"},
    {"path": "src/neuroharness/models/__init__.py", "contains": "RecordKind", "why": "the package re-export"},
    {"path": "tests/unit/test_schema_conformance.py", "contains": "def build_records", "why": "the fixture the two conformance tests parametrize over"},
    {"path": "src/neuroharness/version.py", "contains": "_WRITTEN", "why": "only if the schema version bumps"}
  ],
  "verify": ["schema", "gate"]
}
```
