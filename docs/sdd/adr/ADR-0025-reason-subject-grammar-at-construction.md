# ADR-0025: One per-name subject grammar, checked at construction

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Relates to:** ADR-0023 · **Origin:** increment-2 `F-06`; reproduced while building the agent-facing response type

## Context

A reason code is `NAME` or `NAME:subject`, and §5.6 fixes a *different* subject shape for each parameterised name: a rule id after `RULE_FAILED`, a snake_case fact name after `FACT_MISSING`, a critic id after `CRITIC_ERROR`, a resource key after `RESOURCE_BUSY`, and so on.

That per-name grammar lived in exactly one place — `models/record.py`, applied when a record was written. `ReasonCode` itself checked only the *generic* subject pattern: is this an identifier rather than prose. So a reason code whose subject was identifier-shaped but wrong for its name constructed happily, travelled through the resolver, and failed hundreds of lines later at the record writer. By then the failure is a `RecordNotConstructibleError`, which the pipeline correctly turns into `ABSTAIN(SCHEMA_INVALID)` with **nothing recorded**.

The round-two debt audit filed this as `F-06` and deferred it, on the grounds that the resolver's test data used subjects a tightened rule would reject. That rationale expired when the resolver merged; and the defect turned out not to be latent.

**The reproduction.** `resolve/inputs.py` maps `VerifierResult.FAIL` to `ReasonName.RULE_FAILED` and subjects it with the *critic id* — that is the fallback for a hard critic that fails and does not supply a reason of its own. Run against a critic named `smt.deploy_contract`:

```
resolver verdict : DENY
resolver reasons : ['RULE_FAILED:smt.deploy_contract']
recordable?      : False
```

The record catalogue required `[A-Z]{2,6}-[0-9]{2,4}[a-z]?` after `RULE_FAILED`. So **a correct hard `DENY` — the system working exactly as designed — became an unrecorded harness fault.** An unrecorded decision was not made (Constitution Art. III), and the operator sees `SCHEMA_INVALID` rather than the rule that fired.

The suite could not see it. `test_pipeline.py` always passes an explicit `reason=ReasonCode(RULE_FAILED, "WF-01")`, and the resolver truth table never builds a record, so the two halves of the contract were each tested and never tested against each other.

## Decision

1. **One map, `reason.SUBJECT_GRAMMAR`**, from reason name to subject source, built from `neuroharness.grammar`. `models/record.py` derives its catalogue from it rather than restating it, so the reason-code regex, the published JSON Schema and the construction-time check all come from the same source.

2. **Checked at construction.** `ReasonCode.__post_init__` validates the subject against its name's grammar. A reason code that cannot be recorded cannot be built — which moves the failure from the record writer, where it costs a decision, to the line that made the mistake, where a test sees it.

3. **`RULE_FAILED` admits a rule id *or* a critic id.** Either genuinely names the rule that failed: its requirement id when the critic declares one, its critic id when it does not. The two grammars are disjoint — one starts uppercase, the other lowercase — so a reader can always tell which they are looking at, and neither admits prose.

4. **Totality is asserted at import.** Every member of `PARAMETERISED_REASONS` must have an entry, and no non-parameterised name may have one. A name with no entry would silently fall back to the generic identifier pattern, which is the gap that caused this.

## Alternatives considered

- **Keep the narrow `RULE_FAILED` and change the resolver's fallback.** The alternatives are all worse. `CRITIC_ERROR:<critic_id>` misreports a genuine rule failure as a critic malfunction, and `CRITIC_ERROR` is an *indeterminate* outcome that abstains, so it would change the verdict. Dropping the fallback entirely means a hard critic that supplies no reason produces a `DENY` with no reason code at all, which §5.6 forbids.
- **Have the resolver map critic id → `source_requirement`.** The registry knows each critic's requirement, so `RULE_FAILED:WF-01` would be both valid and more informative. Rejected on layering: the resolver deliberately does not import the registry — `resolve/inputs.py`'s whole design is the dependency inversion that keeps the verdict function pure and replayable. Doing this in the *gateway*, which does hold the registry, remains open and would be a strict improvement on top of this decision rather than instead of it.
- **Leave the check at the record boundary and add a test.** Rejected: a test catches the shapes somebody thought of. This defect existed because both halves were tested and never tested against each other, and a new test would have the same blind spot the day a ninth reason name is added.
- **Widen the record catalogue to the generic subject pattern.** Rejected outright — it would make every per-name shape unenforced, which is most of `SEC-07`'s structural half.

## Consequences

- **Positive.** A hard `DENY` from a critic that supplies no reason is now recordable, which was the whole point. The per-name grammar has one source, so the construction check, the record catalogue and the published schema cannot come apart again. `TokenInvalidReason` no longer has three sources, so the catalogue can gain a member in one edit rather than the coordinated three-file change that had effectively frozen it.
- **Negative, and it surfaced immediately.** Tightening construction rejected four pieces of *existing test data* that named identifiers no registry produces and the record catalogue had always refused — `deploy_state.in_flight` as a fact name, `fact.a`/`fact.b`, `prop.window` as a property id, `rule.change_window` as a rule id. Each was a latent instance of this same defect, sitting in a fixture. They now name real identifiers from the reference registry, which is better test data, but it is a change to tests that had passed for two increments and a reviewer should see it as such.
- **Negative, a contract change.** `RULE_FAILED:lowercase_rule` was refused and is now accepted. One row of `test_free_text_may_not_enter_a_reason_code` encoded that, and is replaced by five rows that demonstrate the same property — a subject of the wrong shape for its name — under the new contract. The published `decision-record.schema.json` changes its `ReasonCode` patterns accordingly, rendered by `tools/render_schema_patterns.py`.
- **Follow-up.** The gateway mapping a critic id to its `source_requirement` would make these reason codes cite the requirement rather than the implementation, which is what an operator wants. That belongs with `P1-01a`.
