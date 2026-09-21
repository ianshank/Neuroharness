# ADR-0028: Hard-rule fixture register as a shrink-only durable exemption

**Status:** Accepted under interim P0-12 (Ian Cruickshank sole named owner/decider) · **Date:** 2026-09-21 · **Deciders:** Ian Cruickshank (interim sole decider/owner under P0-12) · **Relates to:** ADR-0016 · **Origin:** increment-2 `D-8`, `08-increment-2-plan.md` §5.5, `06-delivery-and-governance.md` §3

> **Governance note (interim P0-12):** Ian Cruickshank is sole owner for workflow / policy / security acceptance of increment-2 design decisions under interim `P0-12`. The requirement for two-person hard-gate review remains an explicit known gap until a second person is named — do not invent or placeholder a second reviewer.

## Context

Constitution Article IV (`00-constitution.md`) states:
> *Every hard gate has at least one negative mutation fixture that proves it blocks. A gate without a killing fixture does not exist.*

Governance `06-delivery-and-governance.md` §3 Stage 4 specifies:
> *Negative mutation fixtures — 100% of `active` fixtures killed, plus a check that every hard registry rule has an active fixture. No manual override exists for this stage.*

When measured against the reference action-class registry and every loaded registry, there are 19 hard critics, of which 17 are in `mode: "enforce"`. None of those 17 currently has an `active` mutation fixture (most policy critics owe implementation to `P1-15`, SMT critics to `P2-01`, and effect critics to `P2-11`).

This creates a structural paradox in CI:
1. Stage 4's second half ("every hard registry rule has an active fixture") would immediately fail on 17 rules.
2. If this check is omitted or made non-blocking, unenforced hard gates enter the registry quietly.
3. If the stage is given a generic manual override, the constitutional rule is undermined.
4. If a green check is added claiming Article IV is satisfied, it creates a green check proving a gate nobody wrote — the exact inversion `05-evaluation-plan.md` §1a created the `partial` state to prevent.

Per `05-evaluation-plan.md` §1a and `06-delivery-and-governance.md:39`, durable exemptions to gates require an ADR. Decision `D-8` in `08-increment-2-plan.md` §5.5 and §11 resolves how to reconcile Stage 4 with the 17 known gaps.

## Decision

1. **A checked-in, shrink-only register.** Maintain `tests/fixtures/hard_rule_gaps.json` conforming to `tests/fixtures/hard_rule_gaps.schema.json`. The file explicitly lists all hard enforcing critics lacking active fixtures, the candidate fixtures that would cover them, and the WBS task that owes them.
2. **Blocking CI gate named for what it checks.** The CI job is named `hard-rules-have-fixtures` (`test_hard_rule_fixtures.py`). It passes **only** when the register is completely consistent with every registry document the build loads:
   - Every hard enforcing critic in every loaded registry appears in either `gaps` or `covered`.
   - Any new hard enforcing critic added without an active fixture or unregistered immediately fails the build.
   - Any gap whose candidate fixture becomes `active` must move to `covered` and reduce `gap_count`, or the build fails.
   - The register may only shrink; `gap_count` is published as data (currently 17) and printed in CI so a green check reads as "register consistent" and never as "Article IV satisfied".
3. **Durable exemption via this ADR.** This ADR serves as the formal, durable exemption required by `hard_rule_gaps.schema.json`'s `exemption` field (referencing `ADR-0028`) under `06-delivery-and-governance.md:39` and `08-increment-2-plan.md` §5.5 (`D-8`).

## Alternatives considered

- **Leave Stage 4's second half unwritten until all fixtures exist.** Rejected: unenforced hard rules would enter registries without notice, and the pressure to write killing fixtures would disappear.
- **Fail CI red until all 17 fixtures exist.** Rejected: it would permanently block trunk-based integration across all unrelated components (such as canonicalization, token signing, and schema updates).
- **Allow ad-hoc manual overrides or skip flags.** Rejected: governance §3 explicitly specifies that no manual override exists for Stage 4.

## Consequences

- **Positive.** Every missing hard gate is counted, tracked, and assigned to a specific WBS task (`owed_by`). Newly added hard enforcing critics cannot slip in unnoticed. The ratchet only moves towards full coverage.
- **Negative.** The repository explicitly operates under a declared exemption with 17 known gaps until `P1-15`, `P2-01`, and `P2-11` land active fixtures.
- **Follow-up.** As policy bundles (`P1-15`) and critics land, candidate fixtures (`MUT-01`, `MUT-02`, `MUT-03`, `MUT-05`, `MUT-08`, `MUT-32`, etc.) move to `active`, and corresponding entries move from `gaps` to `covered` in `tests/fixtures/hard_rule_gaps.json`.
