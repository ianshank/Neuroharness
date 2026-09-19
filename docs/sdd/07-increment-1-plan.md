# Increment 1 — The Deterministic Core

**Status:** Revised after peer review · **Date:** 2026-09-18 · **Implements:** `P0-04`, `P1-02`, `P1-03`, `P1-05`, `P1-07`, `P1-09`, part of `P1-06a` · **Constrained by:** `00-constitution.md`

## 1. The case

We have 671 lines of specification and zero lines of code. Every guarantee the project makes is currently an assertion. Round two demonstrated the cost of that: four reviewers found 71 defects in prose that looked finished, including a critical one where the fail-closed principle was false for every action class at the moment of rollout. Prose review has now reached diminishing returns. The next defect class is the one only execution finds.

**Build the deterministic core first.** It is the subset of the harness that is pure computation: no network, no database, no policy engine, no solver. It is also where the constitution concentrates:

| Component | Carries |
|---|---|
| Canonicalization and dual digests | `INV-09` determinism, `FR-04`, the identity every approval and token binds to |
| Verdict resolver | `INV-03` fail-closed, `INV-11` mode independence, the safety order, `FR-05` |
| Token service | `INV-04` no execution without a matching token, `FR-20`–`FR-23` |
| Evidence chain | `INV-05` no token without a record, `FR-70` |
| Action-class registry | Article V bounded formalization, `FR-30`–`FR-35`, and the substrate that makes "no hardcoded values" structural rather than aspirational |

Three arguments for this slice over any other:

1. **It proves the round-two fixes.** The two digests, the token carrying verdict and mode, mode-independent fail-closed, and monotonic resolution are exactly what the reviews found broken. They are all in this slice. Executing them converts four accepted arguments into four passing test suites.
2. **It is fully testable without infrastructure.** No OPA, no PostgreSQL, no key service, no agent. Continuous integration runs in seconds and is deterministic, which is the precondition for the replay gate (`NFR-07`) that everything later depends on.
3. **It unblocks every other component.** The policy decision point, critics, broker, approval service and gateway all consume these types. Building them in any other order means building against types that do not exist yet.

**What this increment deliberately excludes:** OPA integration, PostgreSQL, the MCP gateway, SMT critics, the trajectory monitor, and the approval service. Each needs infrastructure from `P0-13` or is I/O-bound. They attach to this core through the protocol seams defined below.

## 2. Non-negotiable engineering constraints

| Constraint | How it is enforced structurally, not by discipline |
|---|---|
| **No hardcoded policy values** | Every *policy knob* (budgets, TTLs, modes, approver groups, fact freshness) comes from the signed action-class registry. `defaults.py` supplies a value only where the registry omits an optional field; settings never override the registry. Two tests enforce it: an abstract-syntax-tree scan rejecting numeric **and domain-string** literals in comparisons and argument defaults across the decision path, and a provenance test that mutates each registry field and asserts behaviour changes, because a knob nobody reads is a hardcoded knob. |
| **Constitutional constants are *not* overridable** | The safety order, the verdict set, the closed reason-code catalogue, the fixed non-escalating infrastructure set and the hard ceiling on the repair budget change only by amending the specification. A test asserts none of them is reachable from settings or the environment. Peer review caught the earlier wording ("every default overridable") as a fail-closed regression: an operator must not be able to weaken a hard gate with an environment variable, with no signature, no two-person review and no record (`FR-48`, `SEC-14`). |
| **Backwards compatible** | Every wire model carries `schema_version`. A compatibility matrix declares which versions each build reads and writes. An unknown version is a typed, fail-closed rejection, never a silent accept. Protocol seams are `typing.Protocol`, so implementations can be swapped without touching callers. |
| **Reusable, dynamic** | Seams for `Clock`, `Signer`, `NonceStore`, `EvidenceStore`, `IdGenerator`. In-memory implementations for tests; the same core runs unchanged against real infrastructure later. No component constructs its own dependencies. |
| **Determinism** | No module in the decision path calls `datetime.now()` or `uuid4()` directly; both arrive through injected seams. This is what makes replay (`FR-71`) possible at all. |
| **Logging and debugging** | Structured logging with bound context (trace, action, tenant, decision), a redaction filter that refuses to emit secrets, token material or model free text (`NFR-18`), and a decision-trace helper that explains any verdict from its inputs. |
| **Testing** | Unit, property-based (Hypothesis), the complete resolution truth table, and executable mutation fixtures. Coverage gates on the trusted computing base. |

## 3. Module plan

```
src/neuroharness/
  version.py        schema compatibility matrix, package version
  errors.py         typed, fail-closed exception hierarchy
  defaults.py       the single home for documented default values
  config.py         Settings, environment-driven, validated
  observability/    structured logging, context binding, redaction, decision traces
  models/           common enums and value types, envelope, decision record
  canonical/        RFC 8785 canonicalization, proposal and envelope digests
  registry/         action-class and resource-key registries, loader, validation
  resolve/          safety order, verdict resolution (specification 5.3)
  tokens/           signer protocol, nonce store, issue / verify / consume
  evidence/         hash chain, append-only store protocol, write-ahead semantics
  pipeline/         the one component that *sequences* resolve -> record -> token
  seams.py          Clock, IdGenerator and other injection protocols
```

Tests mirror the tree, plus `tests/fixtures/mutations/` holding the executable negative fixtures.

`pipeline/` exists because of a peer-review finding worth recording. Without it,
`INV-05` ("no token without a durable record") would be a property of the *test*
rather than of the code: the test does the sequencing, so no production component
holds the ordering, and the acceptance criterion passes while the requirement is
unenforced. The fix makes the ordering unconstructible rather than merely tested.
`EvidenceStore.append` returns a `DurableRecord` carrying the assigned sequence
and record hash; `TokenService.issue` accepts only a `DurableRecord`; and
`DurableRecord` cannot be constructed outside `evidence/`. A token without a
durable record then fails to type-check, not just to test.

## 4. Fixtures this increment activates

Peer review found the first draft of this section activating fixtures whose gate
this increment does not build, which inverts Constitution Article IV from "a gate
without a fixture does not exist" into "a fixture without a gate" and retires the
pressure to build the real thing. The accounting below is the corrected one, and
it adds a third lifecycle state to `05-evaluation-plan.md` §1a.

**`active` (the gate exists here and the fixture kills it):**

| Fixture | Gate in this increment |
|---|---|
| `MUT-19` | Registry `halted` mode plus resolution step 0 (`FR-49`). |
| `MUT-20` | Token carries mode; verification rejects a shadow token against an enforcing class (`FR-21`). |
| `MUT-30` | Resource-key enumeration rejects `Production` and `prod-eu` (`FR-02`, `FR-34`, threat `T-17`). |
| `MUT-34` | Resolution step 6 denies when the class is not approvable (`FR-45`). |
| `MUT-36` | Revocation list checked at verification (`FR-21`). |

**`partial` (this increment owns part of the check; the fixture is blocking at
that layer and is *not* counted toward hard-gate coverage until its owner lands):**

| Fixture | Owned here | Still missing |
|---|---|---|
| `MUT-09` | Nonce store rejects a second consumption. | Broker-side refusal and the replay alert (`P1-08`). |
| `MUT-13` | The pipeline refuses to issue a token when the record is not durable. | Real durability against a database (`P1-06a`). |
| `MUT-21` | Infrastructure failure in a shadow class yields no token. | Broker confirming nothing executed (`P1-08`). |
| `MUT-26` | Override authority validation rejects a single-principal demotion. | Delegation-chain eligibility, which needs session data (`P1-10a`). |
| `MUT-07` | The registry refuses an action class whose `argument_schema` is open, so an undeclared argument has nowhere to hide. | The envelope builder validating an envelope's arguments against that schema (`P1-02`). |
| `MUT-10` | Token verification recomputes the envelope binding and refuses a mismatch. | The broker recomputing the digest over what it is about to execute (`P1-08`). |

**Returned to `reserved`** because their gate is the broker, the approval service
or the repair-linkage store, none of which exist here: `MUT-15`, `MUT-16`,
`MUT-22`, `MUT-31`, `MUT-35`. Everything else stays `reserved` as before,
including `MUT-12b`, which the first draft omitted from both lists.

**Two corrections this build produced**, both found by making the lifecycle
checkable (`tests/fixtures/mutations/`) rather than by re-reading the plan:

- `MUT-07` was `active`. The registry does enforce the structural half -- an
  action class whose `argument_schema` is open is refused at load, so there is
  no class against which an undeclared argument would be legal. Nothing here
  validates an envelope's arguments against that schema, which is the clause
  the fixture names. `active` would have been a green stage proving a gate
  nobody wrote, which is the inversion section 1a's `partial` state exists for.
- `MUT-10` was `reserved` on the grounds that its gate is the broker. Half of
  it is not: token verification already recomputes the envelope binding and
  refuses a mismatch, and a test already killed it. A fixture that reads as
  not-yet-built while a test proves otherwise understates coverage in every
  document that quotes it.

Acceptance scenario `A-19` (unregistered class abstains) is executable here and
is claimed, by `tests/unit/test_registry_loader.py`.

> **Corrected 2026-09-18, during increment 2.** This paragraph originally also
> claimed `A-20`, `A-21` and `A-22`. Measured against the tree: `A-21` appears in
> no test and no source file at all; `A-20`'s abstention half is covered by the
> resolver truth table but its "and alerts" half has no alerting to cover; and no
> test drives `CLOCK_UNAVAILABLE` through the resolver for `A-22` — what is
> tested is that `ClockUnavailableError` is raised by the seam. Three scenarios
> were claimed and one was true.
>
> This is the defect the mutation-fixture registry was built to catch, in the
> half of the evaluation plan that had no registry. Increment 2 built that
> registry (`tests/fixtures/scenarios/`, `tests/unit/test_scenario_coverage.py`):
> of 45 acceptance scenarios, **3 are referenced by a test** and 42 carry a
> declaration naming the task that owes them. A claim of coverage that nothing
> enforces is the same defect whichever document makes it.

## 4a. CI stages this increment runs

`06-delivery-and-governance.md` section 3 specifies twelve blocking stages.
`.github/workflows/ci.yml` runs four of them, and runs only the ones with
something real to check against the code that exists. A stage added ahead of
the thing it checks is a green check proving nothing -- the same inversion
section 1a of the evaluation plan added the `partial` fixture state to prevent,
and it retires the pressure to build the real gate in exactly the same way.

| Stage | Status here | Owner of the rest |
|---|---|---|
| 2. Unit tests with coverage | **Running**, floor at 90% lines and branches across the trusted computing base | Resolver 100% branches: see the open item below |
| 4. Negative mutation fixtures | **Running**, and blocking with no override: every `active` fixture is killed, and the catalogue, the declarations and this plan are checked against each other | The remaining fixtures activate with their gates |
| 10. Schema conformance | **Running** for the envelope and decision record, in both directions, plus a validity check on the published schemas | PDP input (asserts no `claims`) needs `P1-04` |
| 11. Docs (partial) | **Running** as the constitutional-constants and no-magic-values scanners | Link check and ADR index consistency are unbuilt |
| 1. Lint and type-check | Not running | `ruff` reports 168 findings on the current tree and `mypy --strict` has never been run; landing this stage is its own change, not a line in a workflow file |
| 3. Policy unit tests, Regal | Not running | No `policy/` yet (`P1-15`) |
| 5. Behaviour scenarios | Not running | pytest-bdd harness unbuilt |
| 6. Integration (testcontainers) | Not running | Needs OPA, PostgreSQL, broker (`P1-04`, `P1-06a`, `P1-08`) |
| 7. Golden replay | Not running | `P1-16` |
| 8. Code mutation score | Not running | Blocking from Phase 2 by design |
| 9. Security (CodeQL, pip-audit, gitleaks, Trivy) | Not running | `P0-10`, and several parts need repository-admin settings |
| 12. Build, SBOM, provenance | Not running | `P0-10`; no container yet |

Branch protection, commit signing and the Scorecard target are also `P0-10` and
need repository-admin rights rather than a commit.

## 5. Acceptance for this increment

1. The resolution truth table passes over a **declared finite abstraction** of the input (per-category presence flags rather than raw combinations, which are unbounded), and the table is checked in as reviewable data rather than derived by the same reading of §5.3 that produced the resolver.
2. A property test proves monotonicity: adding any failure never moves a verdict toward `ALLOW` on the safety order. The generator must be shown to reach every verdict and every reason code it can produce, so the property cannot pass vacuously.
3. Canonicalization is idempotent and order-independent under property test; digest vectors are published and stable.
4. No token can be produced without a durable record, proven by fixture, not by inspection.
5. A token bound to one envelope is rejected against another, and a shadow token is rejected when the class enforces.
6. Every *policy* value is registry-supplied and no decision path branches on a numeric or domain-string literal; every *constitutional* constant is provably not reachable from configuration.
7. Coverage on the trusted computing base packages meets the gate; the resolver has full branch coverage.

## 6. Deviations from the specification, for confirmation

| Item | Specification | This increment | Why |
|---|---|---|---|
| Python floor | 3.12+ (`ADR-0019`) | 3.11+ | The build environment runs 3.11. A lower floor is strictly more compatible and costs nothing here. Confirm before `P0-10` pins CI. |
| Settings library | `pydantic-settings` implied | Hand-rolled loader on Pydantic | Removes a dependency from the trusted computing base for a class we fully control. Ships with a precedence test. |
| Token signing | ECDSA P-256 in a key service (`ADR-0019`, `NFR-17`) | HMAC-SHA256 in process | The key service is provisioned by `P0-13`, which has not run. The signer interface is algorithm-agnostic so the swap is configuration, and the in-process signer must refuse to start outside a development environment. |
| Repository layout | `02-technical-plan.md` §10 places canonicalisation under `gateway/` | Top-level `canonical/`, plus `reason.py`, `defaults.py`, `seams.py`, `pipeline/` | The decision core should not live inside the transport adapter. `02` §10 and `CLAUDE.md` are amended in the same change. |
| Phase-0 prerequisites | `P0-05`, `P0-08`, `P0-13`, `P0-14` precede Phase 1 | `P0-05` (registry schema) and `P0-08` (verdict matrix) are closed *inside* this increment; `P0-13` and `P0-14` are declared outstanding | Building a registry loader against an unfrozen format is the "code before spec" Article VII forbids, so the schema lands first, as the increment's first commit. |

## 7. Deferred to an architecture decision, not to chance

Peer review identified four seams this increment does not need but increments 2
and 3 cannot be built without, and getting their signatures wrong now forces a
caller rewrite later. They are recorded so the omission is a decision:

- **`ExecutionGuard`** replacing separate nonce and lease protocols. `FR-25` and
  `ADR-0017` require the resource lease to be acquired *atomically with token
  consumption*; two protocols with two transactions cannot express that.
- **`PolicyEngine`** returning `Ok(outcomes) | InfraFailure(reason)` rather than
  raising, so `POLICY_ENGINE_UNAVAILABLE` and `BUNDLE_INTEGRITY_FAILED` are
  values the resolver consumes rather than exceptions reconstructed at a call site.
- **`FactSource`** fetching a *set* of requirements against a deadline, because a
  per-fact call cannot express parallel fetch or partial failure within the
  latency budget.
- **`ActionStateStore`** for repair linkage (§5.4), retry resolution (`FR-27`) and
  the rate limit (`FR-93`).

Two specification amendments follow from the same review and are made alongside
this increment: the resolver's declared input list gains rate-limit state (so
`REPAIR_RATE_LIMITED` has a home without breaking resolver purity), and the
**proposal-digest field set is narrowed**. As originally specified the proposal
digest covered the whole actor and the whole action-class reference, so an agent
version bump or a registry knob change would supersede a pending human approval
for an unrelated reason. `ADR-0020` fixes the field set to what an approver
actually approves.

## 8. What comes after

Increment 2 attaches the policy decision point and fact providers to these seams (`P1-04`, `P1-18`), which is where OPA and the provider registry land. Increment 3 is the broker, leases and receipts (`P1-08`). Neither requires changes to this core, which is the test of whether the seams were drawn correctly.
