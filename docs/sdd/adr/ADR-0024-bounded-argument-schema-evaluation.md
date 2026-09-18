# ADR-0024: A bounded, closed-vocabulary evaluator for `argument_schema`, not a JSON Schema dependency

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Relates to:** ADR-0019, ADR-0021 · **Origin:** increment-2 `D-3`

## Context

`FR-02` requires that `proposal.arguments` is validated against the action class's `argument_schema`, and that an undeclared argument is `SCHEMA_INVALID`. Since increment 1 the registry has *stored* that schema and *defended* it — an action class whose schema is open is refused at load (`MUT-30`, `A-35`) — and nothing has ever *evaluated* one. `jsonschema` is a dev-only dependency, used by CI to validate the published schemas, and is imported nowhere in `src/`.

So the choice is what evaluates it. Three properties of this system constrain the answer more than they would constrain an ordinary application:

**The trusted computing base is inventoried, and it is one runtime package.** `pydantic` is the whole of it. Governance §4 makes adding a dependency a supply-chain decision with an SBOM and an audit trail, not a convenience. The argument-validation path sits inside the decision procedure, so anything added here is added to the part of the system whose correctness the entire harness rests on.

**Replay determinism is a hard requirement.** `NFR-07` requires that a recorded decision replays to the same verdict, and `R-13` names version non-determinism in a dependency as a live risk. A general JSON Schema implementation's behaviour depends on its draft resolution, its format-assertion mode, and its version. "The same verdict, modulo a minor release of a transitive dependency" is not replay determinism, and the failure would appear as a golden-corpus divergence a year after the change that caused it.

**The vocabulary is already small, because the registry pushed it there.** Across every registry in the tree, `argument_schema` uses eight keywords: `type`, `properties`, `required`, `enum`, `additionalProperties`, `pattern`, `minimum`, `maximum`. That is not luck — the registry's own rules require `additionalProperties: false` and push authors towards enumerations for anything policy compares, and `ADR-0021` forbids a float on a policy-compared argument.

## Decision

1. **A hand-written evaluator over a closed vocabulary**, `neuroharness.envelope.arguments.BoundedSchemaValidator`, with `SUPPORTED_KEYWORDS` declared as data.

2. **It refuses a schema it cannot fully evaluate.** This is the property that makes the decision safe, and it is the whole design. An unsupported keyword is not ignored and the evaluation is not best-effort: the keyword is reported as a violation against the *schema*, and no argument-level result is returned alongside it. A validator that silently skipped what it did not understand would report a clean validation over an argument nobody checked — which is precisely the smuggling channel `FR-02` exists to close, reintroduced by the thing meant to close it. Returning a partial result would be a claim the evaluator has no basis for: it did not apply the whole schema, so it does not know.

3. **`ArgumentValidator` is a `typing.Protocol`.** A full JSON Schema implementation can land behind it later without editing a caller. The decision here is about what ships in the TCB now, not about foreclosing the other option.

4. **Violations are typed, not prose.** `ViolationKind` is a closed enum and `ArgumentViolation` carries a JSON Pointer, a kind and an expectation. It deliberately does **not** carry the offending value: a violation travels towards a decision record and, through the repair channel, towards the governed model, and echoing the value back would hand the model a way to put chosen text into its own context (`SEC-07`, `T-11`).

5. **Every violation is returned, not the first.** `FR-90`'s repair budget is small. An evaluator that stopped early would turn a payload with three fixable arguments into three round trips, and the third would be refused for budget rather than for anything about the action.

6. **A test asserts the bet.** `test_the_reference_registrys_schemas_are_all_within_the_vocabulary` fails if any registry in the tree uses a ninth keyword. The bounded vocabulary is only safe while real action classes stay inside it, so the day one does not, the choice becomes explicit rather than silently degraded.

## Alternatives considered

- **Promote `jsonschema` to a runtime dependency.** The obvious option, and it remains available behind the protocol. Rejected for now on the three grounds above, of which replay determinism is the strongest: the harness's whole evidentiary claim is that a recorded decision can be reproduced, and a validator whose semantics move between patch releases weakens that claim in a way no test in this repository would detect.
- **Generate a pydantic model per action class from the schema.** Attractive, because the validation would then run on the dependency already in the TCB. Rejected: the generation step is itself an unvalidated translation from JSON Schema to pydantic, so the same "does the evaluator mean what the schema says" question moves to a place with no tests, and a registry hot-reload (`FR-83`) would have to regenerate and recompile models on the decision path.
- **Validate nothing and rely on the registry's `additionalProperties: false` guarantee.** This is the status quo, stated. Rejected: the registry guarantees the *schema* is closed, which is a statement about the schema, not about any payload. Nothing had ever applied it to arguments.
- **A permissive evaluator that ignores unknown keywords.** Rejected explicitly, and it is worth naming because it is what most hand-written validators do by default. It is strictly worse than no validation, because the decision record would assert that `FR-02` was applied.

## Consequences

- **Positive.** `FR-02` is enforced at the layer it names, for the first time. `MUT-30`'s gate now has a second killing route at that layer, in addition to the resource-key one that already existed. The TCB stays at one runtime dependency. The evaluator is deterministic by construction — it has no version and no configuration.
- **Negative.** A schema author who reaches for `minLength`, `items`, `oneOf` or `format` gets a refusal rather than a validation, and the remedy is a deliberate change to `SUPPORTED_KEYWORDS` with the semantics argued for. That friction is intended, but it is friction, and it will be met by the first author who wants an array argument. Each keyword added is one whose semantics somebody has to get exactly right; a keyword implemented *almost* right is worse than one that refuses.
- **Follow-ups.** `P1-02` is not closed by this: the WBS gives it the acceptance criterion "`MUT-17`, `MUT-30` active and killed", and `MUT-17`'s gate is the PDP input builder rather than the envelope builder. The proposed WBS amendment moving `MUT-17` to `P1-04` is part of increment 2. Whether to extend the vocabulary or take the dependency should be revisited when the policy pack (`P1-15`) shows what real schemas need.
