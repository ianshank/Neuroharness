# Neuroharness — conventions for contributors (human and AI)

This file is read by AI coding assistants. Keep it short and factual.

## What this repo is
A fail-closed policy-and-verification harness for LLM agent tool calls. See `README.md` for the document map. The specification package is at revision 2 and **increment 1, the deterministic core, is implemented** in `src/neuroharness/`: the closed reason-code catalogue, typed fail-closed errors, the injection seams, RFC 8785 canonicalization and the two digests, the action-class and resource-key registries, the verdict resolver, the token service, the hash-chained evidence store and its write-ahead log, and the pipeline that sequences them. Nothing that talks outside the process is built yet (gateway, PDP client, critics, broker). `docs/sdd/07-increment-1-plan.md` scopes the increment; its section 4a says which CI stages run.

Tests: `PYTHONPATH=src python3 -m pytest`.

## Non-negotiables (from `docs/sdd/00-constitution.md`)
1. The model proposes; the harness decides. No code path may let model output, model confidence, or model-generated text authorize an action.
2. Fail closed. Any inability to evaluate (unknown, timeout, stale fact, engine down, audit write failure) results in no execution.
3. Every hard gate has at least one negative mutation fixture that proves it blocks. A gate without a killing fixture does not exist.
4. Evidence is a byproduct of enforcement: a decision that is not recorded was not made.
5. Verifiers are read-only, typed, non-instructional, and outside the governed runtime.

## Spec-Driven Development workflow
- Change the spec before the code. Requirements live in `docs/sdd/01-specification.md` with stable IDs (`FR-`, `NFR-`, `INV-`, `SEC-`). Reference the ID in commits, tests, and PR descriptions.
- Architectural decisions go in `docs/sdd/adr/` (MADR format). Superseding a decision means a new ADR, not an edit.
- Tasks live in `docs/sdd/03-work-breakdown.md` with IDs (`P1-03`). A task is Done only when its listed acceptance criteria and the Definition of Done in `docs/sdd/06-delivery-and-governance.md` are met.

## Working rules
- Trunk-based: short-lived branches off `main`, squash-merge, Conventional Commits (`feat(pdp): ...`, `docs(sdd): ...`).
- Never skip, disable, weaken or quarantine a test or mutation fixture to get CI green. Fix the root cause or surface it.
- Policy (`policy/**/*.rego`, when it exists) is production code: lint (Regal), unit tests (`opa test`), coverage, and negative fixtures are all required; hard-gate changes require two-person review.
- Do not put secrets, credentials, or real customer data in fixtures. Use the synthetic fixture generators described in the evaluation plan.
- Do not include model identifiers or assistant attribution in code comments.

## Where things live
```
src/neuroharness/    built: reason, errors, defaults, seams, config,
                     observability, models, canonical, registry, resolve,
                     tokens, evidence, pipeline
                     not built: gateway, PDP client, critics, broker
tests/               unit, property, mutation, and the fixtures they read
docs/sdd/            the spec-driven development package (source of truth)
policy/              not built: Rego bundles + tests + mutation fixtures
critics/             not built: Z3 contract packs, Prolog rulebase, FSA specs
```
