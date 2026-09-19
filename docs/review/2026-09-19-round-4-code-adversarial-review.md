# Round-4 Peer Review: the implementation, attacked

| | |
|---|---|
| **Artifact reviewed** | `src/neuroharness/` at `944e077` — the deterministic core as merged: reason catalogue, grammar, canonicalization and digests, registries, resolver and safety order, token service and nonce ledger, evidence chain, WAL and store, pipeline, and the agent-facing response. |
| **Method** | Read adversarially against the constitution and specification, then **executed**: properties fuzzed, edge cases probed, and each claimed defence attacked directly rather than taken from its docstring. §8 lists every command. |
| **Review date** | 2026-09-19 |
| **Findings** | 6 (1 high — a live defect in merged code, fixed in this change; 3 medium; 2 low), plus 6 verified positives |
| **Verdict** | **The core holds, and one repair did not finish.** Every security property this layer claims was attacked and every one held: the resolver is monotone over 55,392 transitions, the canonicalizer is correct on the case implementations get wrong, the evidence chain refuses the truncation a hash chain cannot see, `INV-05` holds structurally rather than by discipline, and the agent-facing projection drops the token by construction. The exception is `R4-S1`: the increment-2 §3.1 repair unified four spellings of the resource-key grammar and left a fifth running, in the three record fields where it matters most. It is fixed here. |

Finding IDs continue the round-3 convention: `R4-S` substantive defect, `R4-D` documentation or specification defect.

---

## 1. Why this round exists

Round 1 reviewed research. Round 2 reviewed the specification, at a point where
no code existed. Round 3 reviewed an external synthesis against the tree. None of
them reviewed the implementation, and the only prior code review is the one in
`08-increment-2-plan.md` §3 — written by the people who wrote the code.

That matters because of what §3 found: six defects in merged code that no work-
breakdown line owned, including a resource-key grammar with **four sources of
truth diverging on five axes**. Those were repaired. This round asked the
narrower question a second reader is for: *did the repair finish?*

It did not.

---

## 2. The defect

### R4-S1 (High) — the "four sources become one" repair left a fifth, in the three fields where it bites

`08-increment-2-plan.md` §3.1 enumerated four spellings of the resource-key
grammar, chose `registry/resource_keys.py` as the owner, and landed
`src/neuroharness/grammar.py`, `ADR-0023` and
`tests/property/test_grammar_agreement.py` to hold them together.

A fifth was not in the enumeration and is not in the property test:
**`models/envelope.py:188`'s `ResourceKey` annotated alias**, which spells the
grammar by hand. Measured at `944e077`, it disagrees with the signed registry on
all five of §3.1's axes and in **both** directions:

| resource key | registry | schema | wire model |
|---|---|---|---|
| `service:example-api/target:production` | True | True | True |
| `cluster-prod:svc-a` | True | True | **False** |
| `k8s-namespace:default` | True | True | **False** |
| `s3-bucket:logs` | True | True | **False** |
| `s3:bucket_name` | False | False | **True** |
| `my_kind:foo` | False | False | **True** |
| `repo:-foo` | False | False | **True** |

**Where it reaches.** `models/record.py:69` imports the alias, so it types three
record fields: `RecordActionClassRef.resource_key` (`:381`),
`ExecutionLease.resource_key` (`:733` — *"The per-`(tenant, resource_key)` lease
the broker held (`FR-25`)"*), and `EffectVerification.resource_key` (`:814`),
which is **required** and is the field that carries `EFFECT_MISMATCH:<resource_key>`.

So §3.1's own prediction was still true, verbatim, one layer over from where it
was fixed:

> A hyphenated resource kind — `cluster-prod`, `s3-bucket`, `k8s-namespace`,
> which is what any real deployment writes — is accepted by the signed registry,
> becomes the broker's lease identity under `FR-25`, and then cannot be recorded.
> […] **So lease contention on a hyphen-named resource is reported to an operator
> as a harness malfunction rather than as contention, and the whole class abstains
> until someone renames the resource.**

Verified end to end before the fix: `is_resource_key("cluster-prod:svc-a")` is
`True`; `ReasonCode(RESOURCE_BUSY, "cluster-prod:svc-a")` renders; and both
`ExecutionLease` and `EffectVerification` refuse to hold it. The reason code the
operator would read is constructible and the record that must carry it is not.
Under Article III that is a decision that was not made.

**The detail that makes it a good finding rather than a lucky one.**
`test_grammar_agreement.py` pins the pre-repair pattern as
`_HISTORICAL_RECORD_PATTERN`, a museum piece kept so the property cannot pass
vacuously. That pattern is **byte-identical** to the one that was still live in
`models/envelope.py`. The repository kept a copy of the bug as a regression
fixture and left the original running.

**Why nothing caught it.** The property test enumerates its consumers by import,
and the alias was not one of them (`R4-D3` is the general form). The schema side
could not catch it either: `action-envelope.schema.json`'s `resource_key` node is
*generated* from `grammar.py`, so the published schema was already correct and the
model was the only thing out of step. And the one golden conformance fixture uses
`service:example-api/target:production`, which every spelling accepts.

**Fixed in this change**, under `ADR-0023` rather than a new ADR — the decision
was already made, and this applies it where it was missed:

- `models/envelope.py` now validates with `grammar.RESOURCE_KEY_PATTERN` itself.
  Not `StringConstraints(pattern=grammar.RESOURCE_KEY_SOURCE)`: pydantic v2
  compiles string patterns with the Rust `regex` crate, which has no look-around,
  and the grammar's length guard is a look-ahead — so passing the source fails at
  class construction, and the tempting repair there is to strip the guard, which
  is how a sixth spelling gets written. Running the compiled object means the
  model and the registry share one pattern and cannot drift by a character.
- `test_grammar_agreement.py` gains the alias as a consumer in all three
  properties and in the regression test. **It was added first and failed on nine
  tests before the source changed** (§8); the length axis failed too, via
  `test_one_character_past_the_bound_is_refused_everywhere`, because the alias
  bounded the key at 256 against the grammar's 158.
- Its docstring is rewritten to be about *consumers* rather than about four of
  them, since enumerating the ones you know about is precisely what failed here.

No new mutation fixture: this repairs an existing contract rather than adding a
hard gate, and the property test is the guard `ADR-0023` already chose.

---

## 3. Documentation and specification defects

### R4-D1 (Medium) — ADR-0021 describes a rule its implementation deliberately does not follow

Decision point 2 said the restriction is over **integers** and that floats
*"are not affected … so they are permitted"*. The implementation has always been
type-agnostic: `renders_as_unsafe_integer` (`digest.py:167`) tests the canonical
*form*. Measured: `float(2**60)` is refused (it renders `1152921504606847000`)
while `1e21` and `1e300` are accepted (they render in exponential form).

The **code is right and the ADR was wrong**, and the docstring carries the
argument the ADR lacked: `1e20` is a Python `float` but canonicalizes to
`100000000000000000000`, which the broker parses back as an `int` before
recomputing the digest (`FR-21`) — so a rule phrased over `isinstance(value, int)`
*"admits the document at the gateway and refuses it at the broker … and one that
only appears in production, on the side of the system that has already decided."*

Under Article VII the ADR is the governing artifact, so an ADR that misdescribes
its own implementation is exactly the drift this project exists to refuse.
**Amended in this change**, including the rejected type-keyed alternative and the
consequence that surprises: the refused set is not "large numbers" — `1e300` is
digestible and `float(2**60)` is not.

### R4-D2 (Medium) — a hard critic in its own shadow rollout can error on every input and resolve to `ALLOW` with no reason codes

Verified:

| critic's own mode | class mode | verdict | reason codes | shadow_verdict |
|---|---|---|---|---|
| `enforce` | enforce | `ABSTAIN` | `CRITIC_ERROR:c1` | — |
| `advisory` | enforce | **`ALLOW`** | **none** | `ABSTAIN` |
| `shadow` | enforce | **`ALLOW`** | **none** | `ABSTAIN` |
| `enforce` | shadow | `ABSTAIN` | `CRITIC_ERROR:c1` | `ABSTAIN` |

`CRITIC_ERROR` is one of the ten fixed non-escalating **infrastructure** reasons
(`01-specification.md:184`), and `FR-80` says *"infrastructure failures block in
every mode."*

**The behaviour is right.** §5.3 step 0 demotes on the critic's own declared mode,
and a critic whose errors blocked could never be trialled at all. What is wrong is
the sentence: `FR-80`'s clause is true of **class** mode and false of **critic**
mode, and the two are deliberately independent in the same requirement.

The consequence is observability, not authorization: `FR-81` requires *"`ABSTAIN`
rate by reason"*, and a critic erroring on 100% of inputs during its own shadow
rollout produces neither an `ABSTAIN` nor a reason code — it is invisible on the
metric that exists to watch it, precisely when it is newest. State honestly that
the control which does work is the mutation gate: `ADR-0010` requires 100% kill on
the class's hard gates before promotion, and a critic that always errors kills
nothing. Fix is one qualifying clause in `FR-80`. Not applied here — it is a
specification change and belongs with its own fixture review.

### R4-D5 (Medium) — `T-18` names counterexamples as the probing channel; reason codes are the wider one

`T-18` is *"Repair channel probing: the agent learns gate internals from
counterexamples"*, and all three of its controls are counterexample- or
repair-scoped: minimal counterexamples, repair budget, rate-limit repairs.

Reason codes are the larger surface and are, by comparison, ungoverned.
`AgentResponse` (`response/agent.py:237`) **requires** at least one reason code on
every non-`ALLOW` verdict, and `project_evaluation_outcome` passes
`outcome.reason_codes` through verbatim — carrying `RULE_FAILED:<rule_id>`,
`APPROVAL_REQUIRED:<rule_id>`, `APPROVAL_NOT_PERMITTED:<rule_id>`,
`SOLVER_UNKNOWN:<critic_id>`, `FACT_MISSING:<fact>`,
`MONITOR_VIOLATION:<property_id>` and `RESOURCE_BUSY:<resource_key>`.

Three asymmetries with counterexamples, all verified in code:

1. **Never gated by mode.** `discloses_counterexamples` withholds counterexamples
   and `approval_ref` in `shadow` and `advisory`, and does not touch reason codes.
2. **Not bounded by the repair budget.** They travel on `DENY` and `ABSTAIN`,
   which are terminal, so the budget never applies. Only `FR-93`'s 10 new actions
   per hour per `(session_root, action_class)` bounds them — about 240
   rule-identifier probes per class per day.
3. **Mandatory, not optional.** A non-`ALLOW` verdict cannot omit them.

This is deliberate and specified (`01-specification.md:187`; `SEC-07` validates the
suffixes against the registry) and the implementation is careful about it. So this
is a threat-model completeness gap, not a defect: `T-18`'s channel and controls
should name reason codes. Worth noting the pattern — with round 3's `R3-S2`,
**both** of the threat model's information-flow rows under-describe their channel.

### R4-D3 (Low) — nothing tests schema↔model field-set parity, which is why `R4-S1` survived

`test_schema_conformance.py` parametrizes over `build_records()`, not over
`RecordKind`; nothing asserts that `Context.model_fields` matches the schema's
`$defs/Context/properties`, or that the two `required` lists agree. Coverage is
one maximally-populated golden fixture per side, so a divergence is caught only
if that fixture happens to exercise it. `R4-S1` is the specific case; this is the
general one.

### R4-D4 (Low) — `RecordKind` and `RECORD_PAYLOAD_FIELDS` are not statically tied

`record.py:917-926` maps kind → payload field, and `_kind_matches_payload`
subscripts it directly. A new `RecordKind` member without a dict entry raises a
bare `KeyError` from inside a validator rather than a `ValidationError` — an
untyped exception on the decision path, which is the shape `reason.py`'s
import-time totality checks exist to prevent elsewhere in the package.

---

## 4. Verified positives

Recorded because round 3 asserted this layer was sound without testing it, and
because Article X asks claims to name their evidence class. These were executed,
not read.

- **Resolver monotonicity holds.** 55,392 add-one-constraint transitions across
  modes × approvability × repair budget × iteration × rate-limit × approval state
  × critic subsets × fact subsets: **zero violations**. Adding a failure never
  moved a verdict toward `ALLOW`. The ordering that earns this — every denial
  decided before every abstention — is the `ADR-0014` insight, and it is real.
- **JCS is correct on the case implementations get wrong.** Key ordering is by
  UTF-16 code unit, so `U+1F600` (surrogate pair `D83D DE00`) sorts **before**
  `U+FFFF` — a naive codepoint sort gets this backwards. Also verified: `-0` → `0`,
  the `1e21` positional/exponential boundary, `2^53-1` exactly, unpaired-surrogate
  refusal, and total rejection of NaN, Infinity, bytes, sets and non-string keys.
- **The digest layer refuses out-of-range integers at any depth**, naming the JSON
  Pointer path (`/a/b/1`, `/a/0/0/k`), per `ADR-0021`.
- **The evidence chain refuses the truncation a hash chain cannot see.** Tail
  truncation is caught by `verify_against_checkpoint`; prefix truncation — the
  worse one, because the checkpoint's head still matches — by opt-in
  `expect_genesis`, and the call site is **correct**: `store.py:367` passes
  `expect_genesis=start_seq == GENESIS_SEQ`. The checkpoint's own signature is
  verified before the chain is accused.
- **`INV-05` holds structurally, not by discipline.** The `token_issued` record is
  not written after the mint; it is handed to the mint as its issuance claim, with
  the record id derived deterministically from the decision, so appending it *is*
  the at-most-once claim and there is no mint-then-record gap (`ADR-0026`).
- **The agent-facing projection drops the credential by construction.**
  `AgentResponse` has no field the `SignedToken` or the chain position could
  occupy, so the leak cannot be introduced by forgetting an omission; the drop is
  logged (`token_dropped`, `record_dropped`) so a later path that stopped dropping
  shows up in a trace. A result attached to a response carrying no token is a
  typed refusal, closing the "return content under a `DENY`" channel.

---

## 5. What was not reviewed

- Components that do not exist: gateway, PDP client, critic bank, broker, and the
  `policy/` and `critics/` trees. Round 3 covers their specifications.
- `registry/loader.py`'s signature and strictness paths, and `evidence/wal.py`'s
  replay, were read but not attacked; no finding is claimed about them either way.
- The six defects `08-increment-2-plan.md` §3–§4 already records are not restated
  here. `D-1` and `D-7` remain open decisions for product and security.
- No concurrency or crash-injection testing was done. `R3-S1`'s dispatch-record
  gap remains the known crash-window finding and is not addressed by this change.

---

## 6. What was measured, and how

Before the fix, at `944e077`:

```
# the property test, added first, failed on nine tests:
PYTHONPATH=src python3 -m pytest tests/property/test_grammar_agreement.py -q
  FAILED test_the_registry_and_the_record_model_agree
  FAILED test_every_key_the_registry_accepts_can_be_recorded
  FAILED test_every_key_the_record_model_accepts_resolves_in_the_registry
  FAILED test_one_character_past_the_bound_is_refused_everywhere
  FAILED test_the_property_catches_the_drift_it_was_written_for[cluster-prod:svc-a]
  FAILED test_the_property_catches_the_drift_it_was_written_for[s3-bucket:data]
  FAILED test_the_property_catches_the_drift_it_was_written_for[k8s-namespace:default]
  FAILED test_the_property_catches_the_drift_it_was_written_for[s3:bucket_name]
  FAILED test_the_property_catches_the_drift_it_was_written_for[my_kind:foo]
```

After:

```
PYTHONPATH=src python3 -m pytest tests/property/test_grammar_agreement.py -q   # 10 passed
PYTHONPATH=src python3 -m pytest                                               # 2709 passed
python3 -m ruff check src tests                                                # All checks passed
python3 -m mypy src                                                            # no issues, 43 files
```

Parity across registry, published schema and wire model, on all eight keys of the
table in §2: **agree on every one**. `ExecutionLease(resource_key="cluster-prod:svc-a", …)`
now constructs, and `RESOURCE_BUSY:cluster-prod:svc-a` still renders.

The suite count is unchanged from baseline, which is the useful fact: the narrowing
direction of this fix broke **no** existing fixture, so nothing in the tree was
depending on the old charset.

Probes used for `R4-D1`, `R4-D2` and the §4 positives were run in-process against
the installed package and wrote no files; each is reproducible from the tables above.

## 7. Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-09-19 | Initial round-four review: the implementation at `944e077`, attacked and executed. `R4-S1` and `R4-D1` fixed in the same change. |
