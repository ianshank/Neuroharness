# Neuroharness Constitution

**Status:** Draft for ratification · **Version:** 0.2 · **Date:** 2026-09-18

The constitution states the properties of Neuroharness that no requirement, design, task, or pull request may violate. Each article has a *test*: the question a reviewer asks to check compliance. Amendments follow §Amendment.

## Article I — The model proposes; the harness decides
No model output, model-reported confidence, model-generated reasoning, or model-generated text may authorize an action. Model-authored fields (tool, intent, typed arguments) may *select which rules apply*; they may never *satisfy* an authorization, evidence or precondition rule. Evidence the agent can manufacture through its own tools is not evidence (`SEC-11`).
*Test:* Trace every input of every allow/deny rule to a harness-obtained fact or a harness-stamped field, and ask who can write to the system that fact came from. If the governed agent can, the change is rejected.

## Article II — Fail closed, in every mode
Any condition under which the harness cannot fully evaluate an action results in no execution. This includes verifier `unknown`, timeouts, stale or missing facts, policy-engine, bundle, registry, clock or audit-store failures, and lost monitor state. Uncertainty is `ABSTAIN` or `REQUIRES_APPROVAL`, never `ALLOW`. Rollout modes change only whether the broker consults the *verdict*; they never disable the fail-closed path. No mode, override or incident procedure may cause execution without a completed evaluation and a durable record.
*Test:* For each new failure path, name the verdict it produces, the mode-independent behaviour, and the test that exercises it. "It shouldn't happen" is not a verdict.

## Article III — Evidence is a byproduct of enforcement
A decision that has not been durably recorded has not been made. The evaluation record is written before any decision token exists; token issuance, approvals, receipts, completions, effect verifications and overrides are appended as linked records; the chain is hash-linked and checkpointed. Records plus retained envelopes, bundles and critic versions must reproduce the verdict without the model.
*Test:* Can this decision be replayed from its record, the retained envelope, the referenced bundle and critic versions, using the record's own timestamp as "now"?

## Article IV — A gate without a killing fixture does not exist
Every hard gate ships with at least one negative mutation fixture that proves the gate blocks the condition it exists to block. A fixture is `active` from the change that lands its gate; mutation-kill for active hard-gate fixtures is a blocking check at 100%, and every hard rule in the registry must have one.
*Test:* Point to the active fixture ID for each gate the change adds or modifies.

## Article V — Formalize only what is bounded
Symbolic verification is applied only to action classes registered as formalizable, with a typed argument schema whose policy-compared fields are enumerations from the resource-key registry. Unregistered action classes receive policy-only evaluation or `ABSTAIN`. Natural-language requirements are stored beside their compiled rules, and formalization failure is an observable event.
*Test:* Which registry entry authorizes this critic to run, where is the source requirement it encodes, and are its inputs enumerated rather than free strings?

## Article VI — Verifiers are read-only, typed, non-instructional and external
Verifiers and the policy decision point run outside the governed runtime, hold no credentials for and no write capability toward any governed or production system (a critic's own state store excepted), have no network egress, accept only typed inputs, and return only typed, non-instructional results. Verifier output is never inserted into a model context as free text, and every identifier it returns is validated against the registry.
*Test:* What can this component write to, what can it reach, and what would happen if its output were adversarial?

## Article VII — Specification before code, decision record before architecture
Behaviour is specified with a stable ID before it is implemented; architectural changes are recorded as ADRs before they are built; acceptance scenarios are written before the code they accept. Tests reference requirement IDs.
*Test:* Which `FR-`/`NFR-`/`SEC-` IDs does this change implement, which ADR authorizes its design, and which scenario accepts it?

## Article VIII — Determinism and replayability
Given the same canonical envelope, facts, policy bundle, critic and solver versions, and evaluation time, the harness produces the same verdict. Non-deterministic components are bounded by deterministic resource limits, not wall-clock alone, and any residual non-determinism may only move a verdict toward a safer outcome on the order `ALLOW < REQUIRES_APPROVAL < REPAIR < ABSTAIN < DENY`.
*Test:* Replay the golden corpus; any diff is a defect or a documented, versioned policy change.

## Article IX — Human decisions are bounded, attributed, recorded and never loosening
Humans may approve what the harness would not allow on its own, only within action classes policy declares approvable, never for classes marked otherwise, always bound to the exact proposal digest, with separation of duties across the session tree, an expiry, and a re-evaluation on fresh facts before anything executes. Operational overrides may halt, revoke or void; only a two-person, time-boxed, recorded action may reduce enforcement, and no human action grants an individual `ALLOW`.
*Test:* Who decided, on exactly what, until when, on what evidence, and could the proposer or their agent have decided it?

## Article X — Honest claims
The project claims only what its encoded rules and trusted facts cover. Every published performance claim states rule coverage, bypass conditions, latency percentiles and unsupported domains. Rationale without a checked-in primary source is labelled engineering judgement. The project never claims to "eliminate hallucinations", to make agents reliable, or to verify open-world truth.
*Test:* Does the claim name its scope, its exclusions and the evidence class behind each number?

## Amendment
An amendment requires: a written proposal as an ADR; review by the tech lead and the security lead; a check that no accepted requirement or fixture becomes inconsistent; and a version bump of this document. Articles I, II, III, IV and IX may be strengthened but not weakened.

## Change log
| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-09-18 | Initial draft. |
| 0.2 | 2026-09-18 | Round-two revision: Art. I extended to evidence provenance; Art. II made mode-independent; Art. III extended to the record chain and replay inputs; Art. IV fixture lifecycle; Art. V typed arguments and enumerations; Art. VI egress and identifier validation; Art. VII acceptance scenarios; Art. VIII safety order and deterministic resource limits; Art. IX rewritten to cover approvals and operational overrides; Art. X evidence classes. |
