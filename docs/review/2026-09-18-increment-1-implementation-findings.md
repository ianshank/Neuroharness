# Increment 1: what building it found

**Date:** 2026-09-18 · **Scope:** the deterministic core (`docs/sdd/07-increment-1-plan.md`) · **Status:** open findings tracked to closure below

Two rounds of prose review had already been run on this specification, the second of them adversarial and by four independent reviewers. This document records what was still wrong, and was only found by writing the code and running it. That ratio is the argument for building early: every defect below survived review by people reading carefully, and none survived execution.

## 1. Defects in the specification, found by the implementation

### F-01 A shadow class could never record the denial it exists to measure (High)
**Found by:** the pipeline's shadow-mode test.
Section 5.3 step 0 demoted "a hard critic whose *effective* mode (class mode, further restricted by per-critic mode) is shadow or advisory". Because the class mode propagated down, every hard critic in a shadow class became soft, so the verdict itself became mode-dependent. That contradicts `INV-11` ("evaluation is identical in every mode"), contradicts scenario `A-16` (which expects a shadow record to show `DENY`), and destroys the purpose of shadow mode: a rollout would measure a program that always allowed.
**Resolution:** step 0 now demotes only on a critic's **own** declared mode. The class mode governs only whether the broker consults the verdict and whether the token is marked shadow. Specification v0.4; schema description corrected.

### F-02 Adding a problem could soften a settled denial into an approval request (High)
**Found by:** a Hypothesis monotonicity property, which failed and could not be made to pass honestly.
Section 5.3 placed the repair-budget and approvability denials *below* the abstention steps. So a `DENY` at "repair budget exhausted" became an `ABSTAIN` when an abstention was added. Both refuse execution, which is why this looks harmless, and it is not: an abstention on an escalatable fact in an approvable class escalates to `REQUIRES_APPROVAL`. An agent that had exhausted its repair budget could therefore let a required fact go stale and convert a definitive denial into a human approval request. Adding a constraint bought a path to `ALLOW` that did not exist before.
**Resolution:** every `DENY`-deciding step now precedes every `ABSTAIN`-deciding one, so monotonicity holds unconditionally. A denial that also has abstention reasons carries them as contributing reason codes. Specification v0.5; `ADR-0014` amended, with the disproved claim recorded rather than quietly dropped.
**Worth noting:** the agent implementing the resolver initially documented this as "the single permitted softening" and wrote a test asserting its exact shape. That was the right instinct — it refused to order around the defect — but the correct answer was to fix the order.

### F-03 The proposal digest voided approvals for unrelated reasons (High)
**Found by:** the increment plan's peer review, before code.
As specified, the proposal digest covered the whole actor and the whole action-class reference, both of which carry volatile fields: `agent_version`, `model_identity`, `mode`, `repair_budget`. An approval may be pending for 24 hours. So an agent redeploy, a model upgrade or a registry tuning change would silently supersede a pending human approval, and the approver would see their decision evaporate for a reason that had nothing to do with what they agreed to.
**Resolution:** `ADR-0020` fixes the projection to what an approver actually approves. Specification §2 and `FR-04` amended. The digest projection is declared as data, and a test asserts the constant and the hashed bytes cannot drift apart.

### F-04 The version matrix claimed to read a version the published schema rejects (Medium)
Both JSON Schemas pin `schema_version` to a single value, while the compatibility matrix claimed to read two. A build that accepted a `1.0` envelope would then fail to validate it against the schema it validates against. A compatibility matrix that disagrees with the schema is worse than none, because it is believed.
**Resolution:** the readable sets are single-valued until a `1.2` lands and the schema moves from a fixed value to an enumeration in the same change.

### F-10 The same softening reappeared by a second route (High)
**Found by:** implementing the F-02 reordering, then looking for the shape again rather than assuming one instance was the only one.
Fixing the step order closed the path from `DENY` to `ABSTAIN`. It did not close the path from `REPAIR` to `REQUIRES_APPROVAL`: §5.5 as written permitted an escalatable abstention to escalate even when a hard critic had already failed. Adding an abstention to a request that would have been `REPAIR` therefore produced `REQUIRES_APPROVAL`, which is less safe on the safety order, and put a human in front of an action a hard gate had already rejected.
The distinction that resolves it: a human may be asked to decide what the harness *could not evaluate*; a human may not be asked to overrule what it *evaluated and refused*.
**Resolution:** escalation is refused while any hard critic has failed. Specification v0.6, with a killing fixture. The resolver's exhaustive search over 4,492,800 base/mutation pairs then found no further regressions of this shape.

## 2. Conflicts between the schemas and the specification

### F-05 `CriticKind.REGO` cannot be recorded (Medium, resolved as intended behaviour)
The shared enum offers `rego`; the record schema's `CriticResult.kind` does not. The resolution is that both are right and the relationship was undocumented: the policy decision point is a critic in the *registry's* vocabulary (listed, versioned, carrying a source requirement) but its output is recorded per rule as a `RuleOutcome`, because §5.1 maps each rule outcome individually. Filing it as one aggregate critic result would lose which rule fired. Now documented on the enum.

### F-06 A reason code can be constructed that cannot be recorded (Medium, **open**)
`reason.py` accepts any identifier-shaped subject; the record schema pins a shape per reason name (`RULE_FAILED` takes a rule id, `FACT_*` takes a fact name, `TOKEN_INVALID` takes one of eight literals). So `ReasonCode(RULE_FAILED, "lowercase_rule")` constructs happily and then fails at the record boundary. The failure mode is fail-closed — the record write fails, so nothing executes — but it converts a programming error into a runtime evidence failure, which is the expensive place to find it.
**Status: open.** The fix is to validate the subject shape per name at construction. Deferred deliberately: the resolver's test data uses subjects that the tightened rule would reject, and changing both while that work was in flight would have caused churn. Tracked for the review round.

### F-07 An evaluation record could say a call was in a batch without saying which (Low)
The envelope requires all four batch fields; the record's `Evaluation.batch` required none, so `"batch": {}` validated. A record that cannot say which batch a member belonged to cannot be used to reconstruct the batch's dependency outcomes, which is the reason `FR-07` records batch position at all.
**Resolution:** `batch_id` and `batch_index` are now required in the record.

### F-08 A governing duration lived outside `defaults.py` (Low)
`FR-48`'s one-hour ceiling on a `demote_mode` override was prose in the schema and a literal in the model layer. That is precisely what `defaults.py` exists to prevent.
**Resolution:** moved to `defaults.py` as a named, documented constant.

### F-09 Two different shapes share the name `ActionClassRef` (Low)
The envelope's and the record's definitions are both legitimate and different. Not a contradiction, but a trap for anyone reading both. Named distinctly in code; the schemas still share the name.

## 3. Defects in the plan, found by reviewing it before building

Recorded because the review paid for itself several times over. Full detail in the plan's revision history.

- **Fixture activation without a gate.** The plan activated fourteen mutation fixtures, five of which had no gate in this increment. That inverts Article IV from "a gate without a fixture does not exist" into "a fixture without a gate", and a green blocking check that proves nothing retires the pressure to build the real thing. Corrected to six active, four partial, the rest reserved, and a `partial` lifecycle state added.
- **"Every default is overridable" was a fail-closed regression.** It would have let an operator weaken a hard gate with an environment variable: no signature, no two-person review, no record. Constitutional constants are now provably unreachable from configuration.
- **No component owned the ordering.** Without a pipeline module, `INV-05` ("no token without a durable record") would have been a property of the tests: the test did the sequencing, so no production code held it, and the acceptance criterion would have passed while the requirement was unenforced.

## 4. An environment observation, not a code finding

While working in this repository, one agent reported that a connected tool server's instruction block ends with a directive telling the agent to change how it uses its tools. It correctly ignored it, on the grounds that instructions arriving through tool content are not instructions from the user.

That is worth recording here for one reason: it is precisely the threat this project exists to address. `T-11` and `T-12` in the threat model describe instructions crossing a trust boundary through tool content, and the control is that verifier and tool output is typed data, never instruction text. The incident is a live example of the class, encountered during the build of the thing designed to stop it.

## 5. Open items

| ID | Item | Owner | Blocking? |
|---|---|---|---|
| F-06 | Reason-code subject shape not validated per name at construction | Tech lead | No, fail-closed; fix in the review round |
| — | §5.5's "never for facts marked `required: true`" is enforced by the registry loader, not the resolver, because only required facts abstain at all. Confirm that placement is intended. | Policy owner | No |
| — | `ADR-0021` follow-up: registry `argument_schema` must reject a float for a policy-compared argument | Policy owner | No |
| — | Digest test vectors remain provisional until both `ADR-0021` rules are in force | Tech lead | Yes, for publishing vectors |
| — | Independent code review and test-quality review of this increment | Tech lead | Yes, before the increment is called done |
