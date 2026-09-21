# ADR-0027: D-7 — required facts may escalate when class opts in

**Status:** Accepted · **Date:** 2026-09-20 · **Deciders:** Ian Cruickshank (P0-12 interim sole owner) · **Relates to:** ADR-0014 · **Origin:** increment-2 `D-7`; product answer 2026-09-20

**Decides:** `D-7` in `docs/sdd/08-increment-2-plan.md` §3.2 / §11. **Amends:** `01-specification.md` §5.5 blanket ban on `required ∧ escalatable`. **Implements against:** `FactRequirement._check_escalation`, `FactState`, resolver fact-escalation arm, truth-table rows, §6 D-7 fixture. **Precondition for:** `P1-18` fact loader / providers.

## Context

Today the registry loader refuses `required=True` and `escalatable=True` on the same `FactRequirement`. Only required facts set `FactState.blocks`, so only they can reach the resolver’s fact-escalation arm — which means the loader makes that arm unreachable under any valid registry. The truth table still contains four rows that construct `required ∧ escalatable` and expect `REQUIRES_APPROVAL`. Increment 2 adds fact-provider producers of `FactState`; without a closed `D-7`, the first such construction would silently turn a hard gate into a waveable formality (or the loader would keep the feature inert forever).

`08-increment-2-plan.md` §3.2 framed the product question:

> Does a missing or stale **required** fact ever warrant human escalation, and is “evidence fact” narrower than “required fact”?

## Decision

**Yes — a missing or stale required fact may warrant human escalation** when the action class opts in.

Ian’s product answer (2026-09-20): missing/stale required facts may escalate. Therefore:

1. **Loosen the loader rule** that refused `required ∧ escalatable`.
2. **`FactState` invariant:** `escalatable` implies the class permits fact escalation (see below).
3. **Keep** the resolver fact-escalation arm and the existing truth-table / property rows that exercise it.
4. **Add (or verify) the conditional D-7 killing fixture** named in `08-increment-2-plan.md` §6 (“New fixture, D-7 permitting”).

“Evidence fact” is **not** treated as a separate narrower class that permanently forbids escalation of all required facts. Opt-in is per-requirement (`escalatable: true`) plus class-level permission via `escalate_on`.

## Ownership (P0-12 interim)

`08-increment-2-plan.md` §4.4 / R-B required a named approver before D-7 could be Accepted. **Interim P0-12:** Ian Cruickshank is sole owner for workflow / policy / security acceptance of increment-2 design decisions (`D-1`–`D-8` and related ADRs) until a fuller RACI lands.

This ADR’s **Accepted** status rests on that interim: product answer Yes (2026-09-20) under Ian as sole named owner. Two-person review for hard-gate policy (`P1-15`) remains a later P0-12 expansion; it does not reopen D-7.

## Normative rules for Implementer

### A. Registry loader (`FactRequirement`)

- **Remove** the hard reject in `FactRequirement._check_escalation` that raises when `required and escalatable`.
- A fact may be `{required: true, escalatable: true}` when the class also lists the corresponding fact reasons in `escalate_on` (see B).
- Keep rejecting escalation of infrastructure / non-fact reasons onto facts (existing `ESCALATABLE_REASONS` / §5.5 infrastructure set unchanged).
- Keep `_check_escalatable_facts_exist`: a class that lists `FACT_MISSING` / `FACT_STALE` (or equivalent) in `escalate_on` must still declare at least one `escalatable` fact.

### B. Class permission (escalatable ⇒ class permits fact escalation)

When constructing or validating `FactState` (fact loader / registry cross-check):

```
if fact.escalatable:
    class.escalate_on ∩ {FACT_MISSING, FACT_STALE}  must be non-empty
    # and the class must be approvable (existing escalate_on × approvable checks)
```

Optional stronger form (recommended): if `escalatable` and the observed status is `MISSING`, require `FACT_MISSING ∈ escalate_on`; if `STALE`, require `FACT_STALE ∈ escalate_on`. Do **not** allow `FACT_PROVIDER_ERROR` (or other infrastructure codes) to become escalatable via this path — provider outages stay non-escalating infrastructure (`09-fact-provider-specification.md` §5).

### C. Resolver

- **Keep** the fact-escalation arm that maps blocking + escalatable + permitted reason → `REQUIRES_APPROVAL`.
- **Keep** all truth-table rows that currently pass `escalatable=True` on required facts and expect `REQUIRES_APPROVAL`.
- **Keep** Hypothesis strategies that can generate `required ∧ escalatable`; do not narrow them “to match the old loader”.

### D. Blocking semantics (unchanged)

- `FactState.blocks = required and not status.is_usable` (missing/stale/unusable).
- Optional (`required=false`) facts never block and therefore never escalate via this arm, even if `escalatable=true` (inert for verdict; may remain for registry completeness).

### E. Killing fixture (08 §6, D-7 permitting)

Add an **active** fixture that fails if the YES decision is regressed:

| Case | Setup | Expected |
| --- | --- | --- |
| Loader accepts opt-in | Class approvable; `escalate_on` includes `FACT_STALE` (and/or `FACT_MISSING`); fact `required=true`, `escalatable=true` | Registry load succeeds |
| Stale required escalates | Same class; fact status `STALE` (or missing) | Verdict `REQUIRES_APPROVAL` with fact reason |
| Escalatable without class permission | Fact `escalatable=true` but class `escalate_on` has no fact reasons | Load or `FactState` construction fails closed (config error / reject) |
| Non-escalatable required still abstains | `required=true`, `escalatable=false`, unusable | Verdict `ABSTAIN` (not approval) |
| Provider error does not escalate | Required fact `PROVIDER_ERROR` | `ABSTAIN` / infrastructure path — never `REQUIRES_APPROVAL` via fact arm |

Wire the fixture into the hard-rules / mutation register when landed so a silent revert of the loader reject cannot go green.

## Spec text to amend (same change or follow-on)

1. `01-specification.md` §5.5 — **done on this PR:** required facts escalate only when marked `escalatable: true` **and** the class lists the matching fact reason in `escalate_on`; otherwise missing/stale required facts abstain. (Replaces blanket “never for evidence facts marked `required: true`”.)
2. `09-fact-provider-specification.md` §3.3 / §9 — mark `D-7` **decided** by this ADR; FactState mapping notes cite ADR-0027.
3. `08-increment-2-plan.md` §3.2 / §11 D-7 row — status **Answered: Yes**; point to this ADR and the §6 fixture.

## Consequences

- Positive: fact-escalation is a real, testable control path; truth-table rows stay meaningful; `P1-18` can construct `FactState` without choosing between “dead feature” and “untested hole”.
- Positive: human escalation of missing/stale gates is explicit opt-in per class/fact, not an accidental soften of every required fact.
- Negative: §5.5 and the loader docstring must change; reference registries that relied on inert `escalatable` on optional facts should be reviewed so opt-in is intentional.
- Negative: approval queues can now legally receive “required CI/change evidence missing” when a class opts in — operators must treat that as a deliberate policy choice.

## Out of scope

- Distilled_Agents / symbolic KD work.
- Changing infrastructure non-escalation set, anti-laundering (`ADR-0022`), or verdict safety order (`ADR-0014`).
- Implementing providers (`P1-18`) beyond the loader/`FactState`/fixture changes this decision unlocks.

## Implementer checklist

1. Land this ADR; update ADR index / README status row.
2. Loosen `FactRequirement._check_escalation`; add class-permission check (B).
3. Confirm resolver arm + four truth-table rows + property strategies still pass.
4. Add §6 D-7 permitting killing fixture; register it.
5. Amend §5.5 / `09` §9 open-question text to cite this ADR.
6. Do not start provider work until (2)–(4) are green.
