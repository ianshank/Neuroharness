# ADR-0023: One resource-key grammar, owned by the registry and rendered into everything else

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Relates to:** ADR-0017 · **Origin:** increment-2 `D-1`; found by auditing the code increment 2 was about to build on

## Context

`FR-34` requires one shared, signed catalogue of canonical resource identifiers, because two spellings of one production target are two policies and only one of them was reviewed (`A-35`). The grammar for those identifiers was nonetheless spelled in four places — `registry/resource_keys.py`, `models/record.py`, `reason.py` and the two published JSON Schemas — and nothing sat between them. `tests/unit/test_anchored_patterns.py` checked that each pattern was *anchored*; no test checked that they were the *same* pattern.

They had drifted. Measured:

| key | registry | record model + schemas |
|---|---|---|
| `repo:my-repo` | accepted | accepted |
| `cluster-prod:svc-a` | accepted | **refused** |
| `s3-bucket:data` | accepted | **refused** |
| `k8s-namespace:default` | accepted | **refused** |
| `s3:bucket_name` | **refused** | accepted |

The registry admitted a hyphen in a *kind* segment and refused an underscore; the record model did the opposite. Two further axes disagreed silently: the identifier's first character, and the length bound, which was 158 in Python and 256 in both schemas.

**What that cost.** A hyphenated resource kind — `cluster-prod`, `s3-bucket`, `k8s-namespace`, which is what a real deployment writes — is accepted by the signed registry and becomes the broker's lease identity under `FR-25` and `ADR-0017`. When that resource is contended, the harness renders `RESOURCE_BUSY:<key>` into the decision record, the record model refuses it, and the fail-closed path turns that into `SCHEMA_INVALID` → `ABSTAIN`. So **lease contention on a hyphenated resource reaches an operator as a harness malfunction rather than as contention, and the whole action class abstains until somebody renames the resource.** The mirror direction is quieter and worse in kind: a record could assert a lease on `s3:bucket_name`, a key no registry lookup will ever match — the `C-02` failure shape, reached through a grammar mismatch instead of a trailing newline.

`registry/resource_keys.py` documented the invariant that should have prevented this: the grammar "is a subset of the reason-code subject charset [...] excludes `_` for the same reason". **Both halves were false.** It admitted `-`, which the record model's kind segment rejected; and the record model admitted `_`, which voided the stated reason for excluding it. The claim was written down and never checked, which is how four copies drift without anyone noticing.

## Decision

1. **One grammar, in one leaf module.** `neuroharness/grammar.py` owns the resource-key grammar, the reason-code subject grammar and the per-name subject shapes. It imports nothing from the package, which is what lets `reason`, `models` and `registry` all depend on it without a cycle.

2. **The registry's grammar wins.** `models/record.py` and both published JSON Schemas adopt it. Concretely this means the record model **widens** to admit `-` in a kind segment and **narrows** to refuse `_`.

   Widening is the part that needed a security decision, because `models/record.py` embeds the resource-key pattern inside the reason-code regex as `(RESOURCE_BUSY|EFFECT_MISMATCH):<key>`, so the change enlarges the charset admissible in a reason code delivered to the governed model — a `SEC-07` surface. It is safe because `reason.py`'s subject pattern already admitted `-` and never admitted `_`: the new grammar is a strict subset of what a reason-code subject could already carry, so nothing reaches the model that could not reach it before.

3. **The subset relation is asserted at import, not documented.** `grammar.py` probes both patterns character by character and refuses to load if the resource-key charset is not contained in the subject charset, if a maximum-length key does not fit in a subject, or if the grammar cannot express a key of its own declared maximum length. This is the same shape as `resolve/safety.py`'s verdict-rank guard: the place to discover that an identifier is unrecordable is process start, not the decision that needed it.

4. **The published schemas are generated.** `tools/render_schema_patterns.py` writes the derived patterns, the `anyOf` branches and the bounds into `docs/sdd/schemas/*.json`; `tests/unit/test_schema_patterns_are_generated.py` refuses hand edits. The `TokenInvalidReason` alternation is derived from the enum for the same reason — it had three sources of truth, which had effectively frozen the catalogue.

5. **The length bound travels with the pattern.** It is expressed as an ECMA-262 lookahead rather than as a separate `len()` check, so every consumer of the pattern gets it. This closed a sixth divergence that only the new property test found: bounding each segment is not bounding the key, and `models/record.py` and both schemas consume the pattern rather than the function.

## Alternatives considered

- **The record model's grammar wins** (registry widens to admit `_`). Rejected: `reason.py`'s subject pattern does not admit `_` anywhere, so a key containing one could never be rendered as `RESOURCE_BUSY:<key>`. That is precisely the failure the resource-key grammar was narrowed to avoid, and choosing this direction would have made the documented reason true by making the system broken.
- **Union — widen everything, including `reason.py`.** Rejected: it enlarges the `SEC-07` charset for no benefit. Nothing needs `_` in a resource kind; the registry has never permitted it, so no existing key uses one.
- **Leave the four copies and add a test asserting they agree.** Rejected: a test would catch the next drift, and the fix for each drift would still be four edits. The generator makes the fix one edit and the test makes hand-editing the generated output fail.
- **Put the grammar in `registry/resource_keys.py` and import it from `models/record.py`.** Rejected on layering: `registry` imports `models.common`, so the edge would run backwards through the package. A leaf costs one module and removes the question.

## Consequences

- **Positive.** One grammar, four consumers, checked in both directions by a property test that generates over an alphabet wider than any of them. `resolve/resolver.py` reaches 100% branch coverage as a side effect of the related totality work. The `TokenInvalidReason` catalogue can gain a member in one edit rather than three. A key of the declared maximum length is now reachable and accepted everywhere, so the bound means something.
- **Negative, and it is a real break.** A resource kind containing `_` is no longer recordable. No registry has ever accepted one, so no key in the repository or in any fixture changes — but an operator who had written one into an unsigned draft registry will see a load-time refusal rather than silent acceptance. The published JSON Schemas change their `pattern` and their `maxLength` (256 → 158), which is a wire-contract change; it is made now, in increment 2, because the schemas have no external consumer yet and confirming that is a named precondition (`R-A`) rather than an assumption.
- **Follow-ups.** `P1-08`'s broker builds its lease identity on this grammar; the property test is the thing that makes that safe. If a future deployment genuinely needs `_` in a kind, the change is to `grammar.py` and `reason.py` together, with this ADR superseded — not to one of the four copies, because there is now only one.
