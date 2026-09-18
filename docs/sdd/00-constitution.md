# Neuroharness Constitution

**Status:** Draft for ratification · **Version:** 0.1 · **Date:** 2026-09-18

The constitution states the properties of Neuroharness that no requirement, design, task, or pull request may violate. Each article has a *test*: the question a reviewer asks to check compliance. Amendments follow §Amendment.

## Article I — The model proposes; the harness decides
No model output, model-reported confidence, model-generated reasoning, or model-generated text may authorize an action. Model artifacts are inputs to *what is proposed*, never to *whether it is allowed*.
*Test:* Trace every input of every allow/deny rule to a harness-obtained fact or a harness-stamped field. If any input originates from the model, the change is rejected.

## Article II — Fail closed
Any condition under which the harness cannot fully evaluate an action results in no execution. This includes verifier `unknown`, timeouts, stale or missing facts, policy-engine or bundle failures, clock unavailability, and audit-store failures. Uncertainty is `ABSTAIN` or `REQUIRES_APPROVAL`, never `ALLOW`.
*Test:* For each new failure path, name the verdict it produces and the test that exercises it. "It shouldn't happen" is not a verdict.

## Article III — Evidence is a byproduct of enforcement
A decision that has not been durably recorded has not been made. The decision record is written before any decision token is issued, and every record is versioned, hash-chained and replayable.
*Test:* Can this decision be reproduced from its record, the referenced policy bundle version, and the recorded facts, without the model?

## Article IV — A gate without a killing fixture does not exist
Every hard gate ships with at least one negative mutation fixture that proves the gate blocks the condition it exists to block. Mutation-kill for hard gates is a blocking CI check at 100%.
*Test:* Point to the fixture ID in `05-evaluation-plan.md` §4 for each gate the change adds or modifies.

## Article V — Formalize only what is bounded
Symbolic verification is applied only to action classes whose inputs, rules and effects are registered as formalizable. Unregistered action classes receive policy-only evaluation or `ABSTAIN`. Natural-language requirements are stored beside their compiled rules, and formalization failure is an observable event.
*Test:* Which action-class registry entry authorizes this critic to run, and where is the source requirement it encodes?

## Article VI — Verifiers are read-only, typed, non-instructional and external
Verifiers and the policy decision point run outside the governed runtime, hold no write capability and no credentials for production systems, accept only typed inputs, and return only typed, non-instructional results. Verifier output is never inserted into a model context as free text.
*Test:* What can this component write to, and what would happen if its output were adversarial?

## Article VII — Specification before code, decision record before architecture
Behaviour is specified in `01-specification.md` with a stable ID before it is implemented; architectural changes are recorded as ADRs before they are built. Tests reference requirement IDs.
*Test:* Which `FR-`/`NFR-`/`SEC-` IDs does this change implement, and which ADR authorizes its design?

## Article VIII — Determinism and replayability
Given the same canonical envelope, the same facts, and the same policy bundle and critic versions, the harness produces the same verdict. Non-deterministic components (solvers with timeouts) are bounded so that non-determinism can only move a verdict toward a safer outcome.
*Test:* Replay the golden decision corpus; any diff is a defect or a documented, versioned policy change.

## Article IX — Human override is bounded, attributed and recorded
Humans may approve what the harness would not allow on its own, only within action classes that policy declares approvable, never for action classes marked `DENY`, always bound to the exact envelope digest, with separation of duties, an expiry, and a record.
*Test:* Who approved, what exactly, until when, and could the proposer have approved their own action?

## Article X — Honest claims
The project claims only what its encoded rules and trusted facts cover. Every published performance claim states rule coverage, bypass conditions, latency percentiles and unsupported domains. The project never claims to "eliminate hallucinations" or to verify open-world truth.
*Test:* Does the claim name its scope and its exclusions?

## Amendment
An amendment requires: a written proposal as an ADR; review by the tech lead and the security lead; a check that no accepted requirement or fixture becomes inconsistent; and a version bump of this document. Articles I, II, III and IV may be strengthened but not weakened.
