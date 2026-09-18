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

## 4. Defects in the code, found by adversarially reviewing the implementation

Seventeen findings, each reproduced with a throwaway script before it was
believed. The five most serious are recorded here; the rest are in the commit
history with their regression tests. `C-06` came later, from a review of the
pull request that carried all of the above.

### C-01 Every decision record the pipeline wrote was invalid (Critical)
The pipeline assembled the `evaluation` block as a free-form dictionary and
handed it straight to the hash chain. Nothing between the resolver and the chain
validated it, and the result failed `DecisionRecord.model_validate` with fifteen
errors. A hash chain makes a record immutable, so every one of those records was
permanently unreadable by the validator an auditor would use — and the discovery
would have happened at the auditor rather than at the writer, on a corpus that
could not be corrected. Constitution Article IV says evidence is a byproduct of
enforcement; evidence that does not parse is not evidence.
**Resolution:** both payloads are built through the typed models and validated
before anything is appended. A validation failure is fail-closed
(`SCHEMA_INVALID`), so the response is overridden to `ABSTAIN` and no token
exists. The caller may no longer supply the fields that state what was decided.
`Resolution.explain` no longer reaches the record: the schema has no place for
prose, and `NFR-20` asks for a verdict explainable *from* its record, which
`pdp_outcomes`, `critic_results`, `facts_used` and `reason_codes` supply in a
form a replay can compare.

### C-02 A separation-of-duties bypass through a trailing newline (Critical)
`re.match` with a `$`-anchored pattern accepts a trailing newline, because in
Python `$` also matches immediately before one. `Principal("user:alice\n")`
therefore validated and compared unequal to `user:alice`, so a proposer could
approve their own request (`FR-42`, `SEC-14`). The same shape sat on `Digest`,
on reason-code subjects — where a newline forges a line in a JSONL evidence
export (`SEC-07`) — and on three sites in the resource-key registry that the
first pass of the review missed, where a signed enumeration could hold the kind
`"service\n"` that no lookup can ever match, denying every call keyed on it
*after* the decision was recorded `ALLOW`.
**Resolution:** eight sites, all `fullmatch`, each with a killing fixture.

### C-03 Nothing bound a decision to at most one token (High)
Two calls with one `decision_id` minted two independently spendable
authorisations. The nonce store enforces single *use*; nothing enforced single
*issue*.
**Resolution:** an injectable issuance ledger claims `(tenant_id, decision_id)`
before signing, so a losing racer never produces a signature. It raises rather
than returning the first token, because the second call's arguments need not
match the first and answering a request about one envelope with an authorisation
for another is worse than refusing.

### C-04 The digest's number domain was defined over the Python type (High)
A float in `[2^53, 1e21)` serialises to a plain integer literal under RFC 8785.
The gateway digested it; the broker, which re-parses the canonical bytes and
recomputes the digest before executing (`FR-21`), read it back as an
out-of-range integer and refused. A fail-closed refusal of an envelope nobody
tampered with, raised on the far side of the decision.
**Resolution:** the rule now asks what a value *canonicalises to*. `1e21` and
`1e300` still digest, because they carry an exponent and round-trip exactly;
over-rejecting is not the safe direction either.

### C-05 A single bad record wedged the write-ahead log permanently (High)
`replay` caught only `EvidenceUnavailableError`. One staged record the store
refuses for any other reason — a schema version ahead of this build — propagated
out and left every later staged record unreplayable, for good.
**Resolution:** failures are reported per record with their reason code, and the
log can be quarantined and drained.

### C-06 The registry made the shadow rollout unrepresentable (High)
**Found by:** a pull-request review that read the registry against the `F-01`
amendment instead of against the technical plan.
`ActionClass` rejected any critic whose mode was more enforcing than its class
mode, citing technical plan §4.4. `F-01` had already established the opposite
for resolution: §5.3 step 0 demotes a hard critic on the critic's **own**
declared mode, because a class mode that propagated down made the verdict
mode-dependent and left a shadow class unable to record the denial it exists to
measure. The registry layer was never revisited, so the two halves of the same
decision disagreed: the resolver was ready to resolve a `shadow` class with
enforcing critics, and the registry refused to load one. That configuration is
not an edge case — it is the whole of `A-16` and `MUT-16`, and the first step of
any progressive rollout under `ADR-0010`. An operator wanting to start a shadow
week had exactly one way through the loader, which was to demote the critics
themselves, and a week of evidence gathered with the gates turned down says
nothing about what enforcement would have blocked.
The validator's argument for the rule was that a critic exceeding its class
would let a policy author raise enforcement past what the rollout approved.
That argument does not survive `INV-11`. Enforcement is decided at the broker,
on the class mode alone, downstream of every critic; an `enforce` critic in a
`shadow` class produces a recorded `DENY` that the broker ignores. The ceiling
is structural, so the comparison bought nothing and cost the rollout.
One constraint was left, and the old rule had it backwards. `halted` is the
incident lever and it belongs to a class: a halted class denies at step 0 before
any critic is consulted. On a *critic* the word is not inert but inverted,
because `CriticOutcome.counts_as_hard` demotes every mode that is not `enforce`
— so a critic declared `halted` quietly stops blocking, and the strongest word
in the vocabulary turns the gate off. The rank comparison accepted exactly that
(`halted` critic in a `halted` class, equal rank) while rejecting the shadow
rollout.
**Resolution:** the class mode no longer constrains its critics; `halted` is
refused on a critic in every class mode. Specification v0.7 (`FR-80`) and
technical plan §4.4 amended to match — §4.4 was the document that was wrong, and
it is now the one that explains why the comparison is absent. `Mode.rank`
survives as a reporting order with a docstring saying it is not a gate. Killing
tests both ways: the `shadow`-class-with-`enforce`-critic entry must load,
through the model and through the signed-document loader, and a `halted` critic
must be refused under all four class modes.

Also fixed in the same review: a signed resource-key registry's `enumerations`
was a mutable `dict` inside a `frozen=True` model, so any code in the process
could widen a canonical enumeration in place — adding `Production` beside
`production` — without moving the registry digest and without leaving a record,
which is `MUT-30`/`T-17` reached from inside the runtime; it now reuses the
`freeze_document` machinery that closed the same shape on `Proposal.arguments`.
`Settings.from_env` iterated the known fields, so a misspelled
`NEUROHARNESS_*` variable was silently ignored and the default stayed in force,
which for a fail-closed harness means a control the operator believes is
configured is not; the whole prefix namespace now belongs to settings and an
unknown member is a startup failure. And the default `signing_algorithm` was
HMAC-SHA256, contradicting `ADR-0019` decision 1: a deployment on defaults chose
a shared secret across the gateway/broker boundary, which lets the broker mint
the tokens it exists to verify. The default is now ECDSA P-256, and because
`HmacSigner` is the only signer this build implements, a startup path
(`signer_for_settings`) refuses to start when the configured algorithm has no
factory rather than falling back to the one that happens to be registered.

Also fixed: the token-issuance record was written outside the pipeline's
fail-closed boundary, so an evidence outage between the two records escaped
`evaluate()` *after* the token was minted; mutable dictionaries inside frozen
models allowed a document to be digested and then edited, with the aliasing
reaching the caller's nested objects; the record schema version had two sources
of truth; `yaml.safe_load` accepted duplicate keys, non-string keys and merge
keys in a registry document before the digest was computed; an unvalidated
record timestamp entered the hash chain, which replay uses as "now"; and a
repeated sequence number was diagnosed as a deletion.

## 5. Defects in the tests, found by mutating the source

A second review applied thirty-six hand-written mutations to copies of the
source. Nine survived the whole suite. The pattern was consistent and worth
naming: **the suite verified logic thoroughly and data poorly.** Constants and
boundaries were used everywhere and pinned almost nowhere, so a refactor could
reasonably change one and ship green.

### T-01 A gate without a killing fixture could not be noticed (High, structural)
`tests/fixtures/mutations/` was empty. Constitution Article III says a gate
without a killing fixture does not exist — and an empty directory looks
identical to a fixture never written, a fixture written and deleted, and a
fixture deliberately not due yet. Forty tests carried the mutation marker and
between them named seven of thirty-seven catalogued identifiers.
**Resolution:** every identifier has a checked-in declaration with its lifecycle
state, and four structural checks hold the catalogue, the declarations and the
increment plan to each other. It found seven fixtures claimed `active` or
`partial` with no test naming them, and corrected two states: `MUT-07` down to
`partial` (the registry owns the structural half; the runtime check is `P1-02`)
and `MUT-10` up from `reserved` (verification already kills half of it).

### T-02 The `Production` defect the project is named for was untested (High)
The evaluation plan names `target: "Production"` as fixture `MUT-30`, the threat
model calls it `T-17`, and `test_no_magic_values.py` carries a lint dedicated to
preventing it. The one test checking the enumeration probed with `"prod"`, so a
case-insensitive membership mutation survived the entire suite. Two spellings of
production are two policies, one of which nobody reviewed — and they render
different lease identities, so two concurrent production deploys would not even
exclude each other (`FR-25`).
**Resolution:** killed on the render path and the lookup path, over case, suffix
and whitespace, and on a second enumeration, because a gate guarding only
`target` would pass a test that only probed `target`.

### T-03 Truncation had a test and no detector (High)
The test named for it ended by asserting arithmetic on values it had just
constructed. No production function compared a chain head against a checkpoint,
so the checkpoint was a value produced, stored and never read.
**Resolution:** `verify_against_checkpoint`, on the primitive and on the store,
with `TRUNCATED`, `CHECKPOINT_MISMATCH` and `CHECKPOINT_INVALID` as distinct
typed outcomes.

### T-04 Four assertions could not fail
`assert sorted([b, a])` (always truthy, the ordering unchecked); two `verify`
calls with no assertion; `assert verify(...) is not None` where `verify` raises
rather than returning `None`; `assert DEFAULT_MAX_PENDING_RECORDS > 0`. Three
property tests were tautological, one of them asserting verbatim the body of the
function under test, and one had no assertion at all.
**Resolution:** replaced with properties that can fail, including the UTF-16
key-ordering rule — which needed a deliberately discriminating alphabet, because
the two orders differ only for a key above U+FFFF drawn alongside one in
U+E000–U+FFFF, a pair a generic text strategy essentially never draws.

### T-05 A refusal that could not be rendered (Medium)
Found by the new lone-surrogate property. The canonicaliser interpolated the
offending key into its error message verbatim; an unpaired surrogate has no
UTF-8 encoding, so the message raised `UnicodeEncodeError` the moment anything
wrote it. A clean fail-closed refusal became a crash carrying no reason code, in
the caller's logging path (Article II).

### T-06 A token refusal declared itself an infrastructure failure (Medium)
Found by pinning the error-to-reason table by value rather than by type.
`TokenError` and its eight subclasses inherited `reason_name =
HARNESS_UNHEALTHY`; only the instance's `reason_code` carried the correct
`TOKEN_INVALID:<why>`. Section 5.5 puts `HARNESS_UNHEALTHY` in the fixed
infrastructure set, whose abstentions are terminal and never escalate — so
anything reading the declared name off one of these classes would file a
working control (a broker refusing a token whose envelope digest does not
match) as an outage, and would reason about its escalation behaviour backwards.
Latent today, because only the unreachable fallback in `FailClosedError`
reads it.
**Resolution:** `reason_name` is `TOKEN_INVALID`. The fallback stays
unreachable, and if it ever became reachable it raises for want of a subject,
which is the right failure: there is no honest way to say *which* binding
failed without being told.

The remaining findings — the unverified redaction key set, untested inclusive
boundaries on the bundle grace window and the HMAC secret floor, major-only
schema-version negatives, `pytest.raises(Exception)` guarding constitutional
constraints, and an error-to-reason mapping pinned by type rather than by value
— are the same shape and are tracked to closure in the commit history.

## 6. An environment observation, not a code finding

While working in this repository, one agent reported that a connected tool server's instruction block ends with a directive telling the agent to change how it uses its tools. It correctly ignored it, on the grounds that instructions arriving through tool content are not instructions from the user.

That is worth recording here for one reason: it is precisely the threat this project exists to address. `T-11` and `T-12` in the threat model describe instructions crossing a trust boundary through tool content, and the control is that verifier and tool output is typed data, never instruction text. The incident is a live example of the class, encountered during the build of the thing designed to stop it.

## 7. Open items

| ID | Item | Owner | Blocking? |
|---|---|---|---|
| F-06 | Reason-code subject shape not validated per name at construction. Still open, and now with a second reason to fix it: the per-name patterns live in `models/record.py` while the catalogue lives in `reason.py`, so the shapes have two sources of truth. The fix is to move them to `reason.py` and have the record model validate through it. | Tech lead | No, fail-closed |
| — | `TokenInvalidReason` has three sources of truth: the enum in `reason.py`, `_TOKEN_INVALID_PATTERN` in `models/record.py`, and a literal alternation in the published record schema. Same shape as F-06, and the reason the new duplicate-issuance refusal reports `HARNESS_UNHEALTHY` rather than a `TOKEN_INVALID` subject the schema does not carry. | Tech lead | No |
| — | §5.5's "never for facts marked `required: true`" is enforced by the registry loader, not the resolver, because only required facts abstain at all. Confirm that placement is intended. | Policy owner | No |
| — | `ADR-0021` follow-up: decide what an `argument_schema` must say about an integer-typed argument so that a value outside the IEEE-754 safe range cannot reach a digest. The rule is now enforced at the digest boundary, so this is defence in depth rather than the only control. | Policy owner | No |
| — | `_SAFE_KEYS` in `observability/logging.py` is unreachable. Redaction matches the whole lowered key exactly, and the two sets are disjoint, so the `normalised not in _SAFE_KEYS` clause can never fire. The comment ("safe despite containing a sensitive substring") suggests substring matching was once intended. Decide which rule is wanted: substring matching, which makes `_SAFE_KEYS` live and also catches `auth_token` and `api_secret`, or exact matching, in which case `_SAFE_KEYS` should go. Both sets are now pinned by value, so an overlap would surface in review. | Tech lead | No |
| — | The resolver's 100%-branch gate (governance section 3, stage 2) is not enforceable as written. Three branches are provably unreachable: `safety.py`'s import-time guard against an unranked `Verdict`, and two `if code is not None` narrowings in `resolver.py` whose ``None`` case cannot occur inside `is_indeterminate` / `fact.blocks`. Either restate the gate with those exclusions named, or make the narrowings unreachable by construction. Shaping the code to reach a number is the wrong fix, so CI enforces the 90% trusted-computing-base floor and this stays open. | Tech lead | No |
| — | `SequenceIdGenerator` is not thread-safe. A test seam only today; worth naming before anything real depends on it. | Tech lead | No |
| — | Digest test vectors remain provisional until both `ADR-0021` rules are in force | Tech lead | Yes, for publishing vectors |
| — | No `main` branch exists in the repository, so no pull request can be opened for this work. Needs a base branch created by someone with push rights. | Repository owner | Yes, for review |

**Closed in this round:** the independent code review (seventeen findings,
section 4) and the test-quality review (eighteen findings, section 5) have both
been run and their findings fixed, each with a regression test verified to fail
before the change.
