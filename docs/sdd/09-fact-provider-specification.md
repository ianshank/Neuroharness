# Neuroharness Fact-Provider Specification

**Status:** Draft v0.1 · **Date:** 2026-09-18 · **Closes:** `P0-06` · **Unblocks:** `P1-18` · **Implements:** `FR-10`–`FR-14`, `SEC-09`, `SEC-11` · **Constrained by:** `00-constitution.md` Art. I, II, VI · **Decides:** `D-2` (`08-increment-2-plan.md` §11), recorded as `ADR-0022`

Facts are the only statements policy may read (`INV-08`, glossary §2 of `01-specification.md`). This document says where they come from, what shape they arrive in, when they stop counting, what happens when the provider does not answer, and what stops an agent from writing its own evidence. It is a specification, not a design: `P1-18` builds against it, `P1-04` builds the input document that consumes it, and `05-evaluation-plan.md` §4 already names the fixtures that must kill each gate (`MUT-04`, `MUT-14`, `MUT-23`).

Requirement language follows RFC 2119. Claims about `src/neuroharness/` cite `file.py:line` against the tree as merged; the deterministic core is built, the fact layer is not, and §9 says which of the two this document is describing in each case.

---

## 1. What a fact provider is, and what it is not

A **fact provider** is a registered, authenticated, versioned component that answers one question — *what does the backing system say about this key, right now* — and returns a typed answer the harness converts into a `Fact` (`FR-10`). It is inside the trusted computing base (`02-technical-plan.md` §4.3) and it is subject to Art. VI: it is read-only toward every governed system, it holds no write capability, and its output is typed data with no free text.

Three things a provider is **not**:

1. **Not a rule.** A provider reports; it never decides. It has no notion of `required`, `approvable` or `verdict`, and no knowledge of which action class asked. The freshness bound it is judged against comes from the action class (§4), not from the provider, so the same fact can be fresh for one class and stale for another in the same second.
2. **Not a channel for agent output.** Nothing the governed model writes may reach a provider's answer except as the *key* of the lookup (`INV-01`). A provider that echoes a proposal field back as a value has made the model authorize the action.
3. **Not optional infrastructure.** `FR-14` closes the set: only registered providers may supply facts. A fact whose `source` is not a registered provider is not a fact the harness will carry, and the envelope model already refuses to represent one — `Fact.source` is a bounded `SourceName` and `Fact.provider_version` is mandatory (`src/neuroharness/models/envelope.py:453-454`).

---

## 2. The fact-provider registry (`FR-14`, `SEC-09`)

### 2.1 Document shape

One signed, versioned JSON document, loaded atomically beside the action-class registry and the resource-key registry. It is refused whole or accepted whole; there is no partial load, because a registry half of which is trusted is a registry nobody can reason about.

```json
{
  "schema_version": "1.0",
  "registry_version": "2026.09.18-1",
  "digest": "sha256:<64 hex>",
  "signature": {"key_id": "fpr-signing-2026q3", "key_alg": "ES256", "value": "<base64>"},
  "backing_systems": [
    {
      "id": "ci",
      "description": "Continuous-integration service of record for build and test outcomes.",
      "writable_by_harness_tools": false,
      "isolation_evidence": {
        "attested_by": "<role named in P0-12>",
        "attested_at": "2026-09-18T00:00:00Z",
        "reference": "SEC-11-attestation/ci",
        "next_review": "2026-12-18T00:00:00Z"
      }
    }
  ],
  "providers": [
    {
      "id": "ci_result",
      "backing_system": "ci",
      "trust_level": "authoritative",
      "asserts_principal": true,
      "auth": {"kind": "mtls", "identity": "spiffe://<synthetic>/fact/ci", "credential_ref": "kms://<synthetic>"},
      "value_schema": { "...": "see 2.3" },
      "default_ttl_seconds": 900,
      "timeout_ms": 500,
      "max_concurrent": 8,
      "rate_limit_per_minute": 120,
      "cache": {"max_age_seconds": 30, "negative_cache_seconds": 0},
      "provider_version": "1.0.0",
      "error_taxonomy_version": "1.0"
    }
  ]
}
```

`credential_ref` is a reference to key material, never key material (`../../CLAUDE.md`: no secrets, credentials or real data in fixtures; nothing in this document or in any fixture derived from it carries one). Every example identifier here and in §7 is synthetic.

### 2.2 Every field, and why it is closed

The action-class registry earns its strictness by refusing anything it cannot evaluate (`registry/models.py:11-14`: frozen, `extra="forbid"`). This registry gets the same treatment. "Closed" below means the field's value set is finite and checked at load; an open field is one the loader can only bound, and each open field says what bounds it.

| Field | Type | Closed? | Required | Why it exists, and why it is shaped this way |
|---|---|---|---|---|
| `schema_version` | const | yes | yes | Mirrors `action-envelope.schema.json`'s `schema_version` const. A registry the loader cannot version is a registry that silently changes meaning. |
| `registry_version` | string | no (bounded: `^[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9]+$`) | yes | Recorded in the decision record so a replay knows which provider set produced the facts (`FR-71`, Art. VIII). |
| `digest`, `signature` | `sha256:` + detached signature | n/a | yes | `SEC-05`. The loader refuses an unsigned or mismatched document; failure is `REGISTRY_INTEGRITY_FAILED` → `ABSTAIN`, a non-escalating infrastructure reason (§5.5 of `01-specification.md`). Increment 1 ships a digest and a `DigestVerifier` only; a real signature verifier is `P0-05`/`P0-13` and is **not** closed by this document (§8). |
| `backing_systems[].id` | identifier | **yes** — closed enumeration, and it is the enumeration `writes_to` draws from | yes | The anti-laundering relation (§6) is a set operation over these ids. If they were free strings, `ci` and `ci-prod` would be different systems to the checker and the same system to the world. |
| `backing_systems[].description` | string | no (bounded 512) | yes | Inspection artifact for `P0-07`. Never rendered to the agent (`SEC-07`). |
| `backing_systems[].writable_by_harness_tools` | bool | yes | yes | The *declared* answer to `SEC-11`'s question. §6 derives the *checked* answer and refuses the registry when the two disagree, so this field is a claim the loader audits rather than a claim the loader trusts. |
| `backing_systems[].isolation_evidence` | object | no | yes | The named gap, made structural rather than left in prose (§8). Carries who attested that this system is not writable outside the harness, when, against what reference, and when the attestation expires. A provider whose attestation is past `next_review` loads with a warning and is surfaced by `P1-13`'s alerting; it does not silently keep counting as isolated. |
| `providers[].id` | identifier matching `^[a-z][a-z0-9_]*$`, ≤ 64 | yes | yes | This is the **fact name**. It is emitted as a reason-code subject (`FACT_MISSING:<fact>`, §5.6) and reason subjects are never free text (`SEC-07`). The pattern is already the envelope's `FactName` (`models/envelope.py:177`) and the registry's `_IDENTIFIER_PATTERN` is wider, so the narrower of the two governs. |
| `providers[].backing_system` | enum over `backing_systems[].id` | **yes** | yes | **The new field `D-2` turns on.** Without it, `SEC-11` cannot be checked by a machine at all. |
| `providers[].trust_level` | enum `authoritative` \| `corroborating` \| `advisory` | **yes** | yes | `authoritative` — the system of record; a hard rule may rest on it alone. `corroborating` — usable only alongside an authoritative fact for the same key; a rule that rests on it alone is refused by policy review. `advisory` — readable by soft critics only; the PDP input builder omits it (`P1-04`). Three levels, not a score: a numeric trust level invites arithmetic, and a gate that computes a weighted sum of evidence is a gate nobody can mutation-test. |
| `providers[].asserts_principal` | bool | yes | yes | `FR-14` names it. True means every fresh fact from this provider carries `asserted_by` (`models/envelope.py:457`); the loader refuses a non-isolated provider that declares `false` (§6). |
| `providers[].auth` | object; `kind` ∈ {`mtls`, `oauth_client_credentials`, `workload_identity`, `none`} | **yes** on `kind` | yes | `SEC-09`. `none` is legal only for a provider whose `backing_system` is the harness itself (`harness_approval`, `execution_completion`), and the loader enforces exactly that; an unauthenticated external provider is a spoofing surface (`T-13`). |
| `providers[].value_schema` | JSON Schema, `additionalProperties: false` | structurally, yes | yes | §2.3. |
| `providers[].default_ttl_seconds` | int ≥ 0 | no (bounded ≤ 86 400) | yes | The provider's own statement of how long its answer is worth believing, used when the backing system does not supply one. It is one half of the `min()` in `FR-11`; the class's `max_age_seconds` is the other (§4). Separate because the provider knows its refresh cadence and the class knows its risk appetite, and neither can speak for the other. |
| `providers[].timeout_ms` | int ≥ 1 | no (bounded ≤ 5 000) | yes | A bounded wait, because an unbounded one turns a fail-closed harness into a hung one. Exhaustion is `PROVIDER_ERROR`, never a retry that outlives the evaluation (§5). |
| `providers[].max_concurrent`, `rate_limit_per_minute` | int ≥ 1 | no | yes | `SEC-09` requires providers to be rate-limited. Stated per provider because the limit belongs to the backing system, not to the harness. |
| `providers[].cache` | object | no | yes | `max_age_seconds` bounds how old a cached answer may be *before* freshness is computed; a cache entry older than it is a miss. `negative_cache_seconds` is `0` by default and MUST be `0` for any `authoritative` provider: caching "no CI result exists" is caching the absence of evidence, and the failure is that a fact which has just appeared stays invisible for the length of the cache. |
| `providers[].provider_version` | string ≤ 64 | no | yes | Stamped onto every `Fact` (`models/envelope.py:454`) and therefore into the record and the replay inputs (Art. VIII). |
| `providers[].error_taxonomy_version` | string | no | yes | §5's mapping is versioned with the provider, so a provider that gains an outcome cannot quietly reuse an old mapping. |

Fields the registry deliberately does **not** have:

- **No per-provider `required` flag.** Requiredness is a property of the action class's use of the fact (`FactRequirement.required`, `registry/models.py:125`), not of the provider. A provider that could declare itself required would be a provider that could declare itself a gate.
- **No per-provider `escalatable` flag.** Same reason, and §5.5 places escalation entirely in the class.
- **No retry policy.** A retry inside an evaluation is a timeout the operator cannot see. Retries belong to the provider adapter's transport layer, inside `timeout_ms`, and never extend it.

### 2.3 `value_schema`

Each provider declares a JSON Schema for the `value` its fresh facts carry (`FR-10`, `FR-14`). Three constraints, each with the same motive as `FR-02`'s constraints on `argument_schema`:

1. `type: object`, `additionalProperties: false`. A value the harness does not recognise is a value it cannot evaluate (Art. II).
2. Every field a policy rule compares MUST be an enumeration or a bounded scalar. A rule comparing a free string from a provider is `T-17` with an extra hop.
3. No floating-point field may be policy-compared, and no integer outside ±(2^53 − 1) may appear anywhere in the value, because the fact is part of the envelope and the envelope is digested (`ADR-0021`). A provider that would return a larger identifier returns it as a string.

A response that does not validate against `value_schema` is `PROVIDER_ERROR`, not a malformed fact (§5). The harness never repairs, coerces or truncates a provider response.

### 2.4 Load-time cross-registry checks

The provider registry is not independently meaningful; four checks run when it and the action-class registry are loaded together, and each failure is `REGISTRY_INTEGRITY_FAILED` → `ABSTAIN`:

| # | Check | Why it is load-time and not evaluation-time |
|---|---|---|
| C1 | Every `FactRequirement.name` in every action class resolves to a `providers[].id`. | A class requiring a fact nobody can supply is a class that abstains forever. Discovering that at evaluation time means discovering it during an incident. |
| C2 | Every `providers[].backing_system` resolves to a `backing_systems[].id`. | Referential integrity for the `SEC-11` relation; an unresolvable id would silently drop out of a set intersection. |
| C3 | The anti-laundering relation of §6 holds for every (provider, action class) pair. | §6. |
| C4 | Every `auth.kind = none` provider's backing system is the harness itself. | `SEC-09`; an unauthenticated external provider must not be representable. |

C1 is what makes "a provider cannot invent a status" (§5) complete: there is no evaluation-time "unknown provider" outcome, because an unknown provider cannot survive load.

---

## 3. The `Fact` lifecycle

### 3.1 The four steps

```
provider adapter        fact loader                envelope                  resolver
────────────────        ───────────                ────────                  ────────
ProviderOutcome  ──►    classify (§4, §5)   ──►    Fact (FR-10, FR-11)  ──►  FactState (FR-11)
+ raw response          against the trusted        status + provenance;      name + status
                        clock and the class's      value ONLY when fresh     + required + escalatable
                        FactRequirement
```

1. **The adapter** calls the backing system within `timeout_ms` and returns exactly one member of the closed `ProviderOutcome` set (§5). It performs no time arithmetic and assigns no `FactStatus`.
2. **The fact loader** (built by `P1-18`) is the only component that assigns a `FactStatus`. It reads the trusted `Clock` seam once per evaluation (§4), applies the freshness bound, applies §5's mapping, and constructs the `Fact`.
3. **The envelope** carries the `Fact`. `context.facts` is bounded at `MAX_FACTS = 64` (`models/envelope.py:212`, `:553`), and the fact is part of the envelope digest — so the exact evidence a token authorizes against is pinned (`FR-04`).
4. **The resolver** sees only a `FactState`: `name`, `status`, `required`, `escalatable` (`resolve/inputs.py:169-180`). No value crosses that boundary at all.

### 3.2 The `FR-11` contract: anything not fresh carries no value

This is already enforced in code, and the enforcement is structural rather than procedural:

- `Fact.value` defaults to a dedicated `ABSENT` sentinel rather than `None`, because `null` is itself a legal fact value and the wire form is emitted with `exclude_none`; `None` cannot carry the meaning "this key is not present" (`models/envelope.py:112-139`, `:456`).
- `Fact._enforce_freshness_contract` (`models/envelope.py:464-494`) refuses both directions: a `FRESH` fact missing `value`, `fetched_at`, `ttl_seconds` or `digest` cannot be constructed, and **a fact with any other status that carries a `value` cannot be constructed** (`:489-493`).
- `Fact._emit_value_only_when_present` (`models/envelope.py:496-509`) re-adds `value` on serialisation if and only if it is present, so a present-but-null value survives and an absent one stays absent.
- `Fact.is_usable` (`models/envelope.py:516-525`) checks both the status and the presence of the value rather than trusting the status alone.
- The published schema says the same thing independently: `docs/sdd/schemas/action-envelope.schema.json`, `$defs.Fact`, whose `allOf` makes `value` required when `status` is `fresh`.

A provider therefore cannot deliver a stale value to a rule even if the rule's author forgot the freshness check, and a fact loader with a bug cannot construct the document that would let it. `P1-18` MUST NOT add a path that reaches the PDP input with a non-fresh value; the model makes that path unrepresentable, and `MUT-14` is the fixture that proves the gate blocks.

### 3.3 `Fact` → `FactState`

`FactState` is constructed from the class's `FactRequirement` and the fact's status:

| `FactState` field | Source | Note |
|---|---|---|
| `name` | `FactRequirement.name` (`registry/models.py:122`) | Not the provider's answer. A provider cannot rename the fact it is answering for. |
| `status` | the loader's classification (§4, §5) | |
| `required` | `FactRequirement.required` (`registry/models.py:125`) | |
| `escalatable` | `FactRequirement.escalatable` (`registry/models.py:126`) | Subject to `D-7`; see §9. |

`FactState.blocks` is `required and not status.is_usable` (`resolve/inputs.py:186-192`), and the resolver's abstention arm reads exactly that (`resolve/resolver.py:178-182`). An optional fact that is missing is not an inability to evaluate: policy declared it may be absent, so rules that need it do not fire.

**`FactState` has no producer in `src/` today.** `grep -rn "FactState(" src/` returns nothing; the only constructions are in `tests/unit/test_resolver_truth_table.py`. `P1-18`'s fact loader is the first, which means it is also the first component that could construct a `FactState` the specification forbids. That is `08-increment-2-plan.md` §3.2 and `D-7`, and it is a hard precondition on `P1-18` alongside this document.

---

## 4. Staleness and the clock (`FR-11`, `NFR-13`)

### 4.1 The rule

A fact is **stale** when

```
age > min(fact.ttl_seconds, class.required_facts[].max_age_seconds)
```

`FR-11` fixes the bound and says age is "measured by the trusted clock at evaluation time"; it does not say which of the fact's two timestamps the age is measured *from*, and this document closes that. **`age = now − fact.observed_at` when the backing system reports an observation time, and `now − fact.fetched_at` otherwise.** `observed_at` is preferred because the question is how old the *evidence* is, not how recently the harness asked. A provider whose backing system exposes no observation time MUST NOT synthesise one; it omits `observed_at` and the loader falls back to `fetched_at`, which is the conservative direction only when fetch follows observation — so a provider with a cache is required to report `observed_at` or to set `cache.max_age_seconds` to `0`.

Both bounds are mandatory and neither may be defaulted away: `ttl_seconds` is required on a fresh fact by the model (`models/envelope.py:479`) and `max_age_seconds` is required on every `FactRequirement` (`registry/models.py:124`). `min()` rather than either alone, because the provider knows its refresh cadence and the class knows how stale is too stale for *this* gate — `deploy_state` at 60 s and `change_approval` at 3 600 s in the same class (§7) is the whole point.

### 4.2 Which clock

The trusted `Clock` seam: `neuroharness.seams.Clock` (`src/neuroharness/seams.py:31-42`), the same instance the pipeline, the token service and the evidence store are constructed with. Not `datetime.now()`, not the provider's clock, not the backing system's clock. Two consequences follow and both are load-bearing:

- **One reading per evaluation.** The loader reads `clock.now()` once and classifies every fact against that instant, so two facts cannot be judged against two different "now"s in the same decision, and the recorded instant is what a replay re-uses (`FR-71`, Art. VIII).
- **A timestamp from the backing system is data, not time.** `observed_at` is compared against the trusted clock; it never *becomes* the clock. A backing system whose clock runs fast would otherwise be able to rejuvenate its own evidence.

### 4.3 When the clock is unavailable

`Clock` implementations raise `ClockUnavailableError` rather than returning a best guess (`seams.py:35-38`, `:64-67`, `errors.py:131-134`), because a skewed clock silently revalidates stale facts. The behaviour is therefore fixed and is *not* a fact status:

- The evaluation abstains with `CLOCK_UNAVAILABLE` (`NFR-13`, scenario `A-22`). No fact is classified, because classifying a fact requires the arithmetic the clock failure prevented.
- `CLOCK_UNAVAILABLE` is in the fixed **non-escalating infrastructure** set (§5.5 of `01-specification.md`), so the abstention is terminal: no repair, no escalation, no approval queue. A human cannot vouch for an age nobody could measure.
- This holds in every rollout mode (`INV-11`, `ADR-0016`): infrastructure failures block in `shadow` and `advisory` too.
- The broker independently refuses to consume a token while its own clock source is unhealthy (`FR-21`), so a token minted before the outage does not become spendable during it.

A provider that cannot be reached is a *provider* failure (§5). A clock that cannot be read is a *harness* failure. Conflating them would let an infrastructure abstention inherit the escalatability of a fact, which is the softening §5.5 exists to prevent.

---

## 5. Error taxonomy: a total mapping (`FR-13`)

### 5.1 The closed outcome set

A provider adapter returns exactly one `ProviderOutcome`. The set is closed, and it is the adapter's entire vocabulary — an adapter cannot return a `FactStatus`, cannot return a reason code, and cannot return prose.

| `ProviderOutcome` | Meaning | `FactStatus` | Reason code when the fact blocks |
|---|---|---|---|
| `ok` | An answer was received and validates against `value_schema` | `FRESH` if `age ≤ min(ttl, max_age)`, else `STALE` (§4) | — when fresh; `FACT_STALE:<fact>` when stale |
| `not_found` | The backing system authoritatively reports no record for this key | `MISSING` | `FACT_MISSING:<fact>` |
| `key_unsupported` | The provider cannot answer for this key shape at all | `MISSING` | `FACT_MISSING:<fact>` |
| `timeout` | `timeout_ms` elapsed | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `transport_error` | Connection refused, reset, DNS failure, TLS failure | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `auth_failure` | The provider's credential was rejected | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `rate_limited` | The provider or backing system refused for volume | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `upstream_error` | The backing system returned a failure | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `schema_violation` | The answer does not validate against `value_schema` (§2.3) | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `key_mismatch` | The answer is for a key other than the one asked for | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `ambiguous` | More than one record matches the key | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `provenance_missing` | `asserts_principal: true` but the answer carries no `asserted_by` | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |
| `digest_mismatch` | The provider's own integrity check on the answer failed | `PROVIDER_ERROR` | `FACT_PROVIDER_ERROR:<fact>` |

`ok` is the only outcome whose status depends on anything beyond the outcome itself, and the dependence is exactly the §4 computation: the adapter reports *that an answer arrived*, the loader decides *whether it still counts*. The adapter does no time arithmetic, so it cannot report an answer as fresh (§5.2). Every other outcome maps to one status unconditionally.

Three properties make this total rather than merely long:

- **Onto, and no gaps.** Every member of `FactStatus` (`models/common.py:191-206`) is reachable, and every `ProviderOutcome` has an image — one, or for `ok` a two-way split on a predicate that is itself total. `FRESH` is the only status with no reason code, because `_FACT_REASON_BY_STATUS` (`resolve/inputs.py:66-72`) maps precisely the three unusable statuses.
- **Enforced at import, not at call.** `P1-18` MUST assert the mapping's totality over `ProviderOutcome` at import time, raising `ConfigurationError`, in the same shape as `resolve/safety.py:70-78`. A missing entry discovered inside the decision path is a `KeyError` on the one path that must stay alive (Art. III: an unrecorded decision was not made).
- **No default arm.** A mapping with a fallback is a mapping that will acquire a member nobody classified. The adapter's return type is the closed enum; a new failure mode is a new member and a new row, and that is a reviewable diff.

### 5.2 What the harness refuses to let a provider decide

- A provider cannot report `FRESH`. Freshness is arithmetic on the trusted clock against the class's bound (§4); the adapter does not know the class.
- A provider cannot distinguish `not_found` from `timeout` on the harness's behalf by returning an empty value. An empty successful answer is `ok` with an empty value if `value_schema` permits it, and `schema_violation` otherwise. "I got nothing back" is never silently `MISSING`.
- A provider cannot suppress its own failure. `FR-13` requires provider failure or timeout to abstain with `FACT_PROVIDER_ERROR`; an adapter that swallowed an error and returned `not_found` would convert an infrastructure failure into an evidence absence, which reads identically in the record and is not the same event.

### 5.3 Two corrections this taxonomy forces

1. **`02-technical-plan.md:154` is wrong.** It says *"Provider failures produce `FACT_MISSING` (`FR-13`)"*; `FR-13` says `ABSTAIN` with `FACT_PROVIDER_ERROR`, and `FACT_PROVIDER_ERROR` is in the closed catalogue (§5.6) precisely so the two are distinguishable. The specification governs; the plan line is corrected with this document.
2. **`FACT_PROVIDER_ERROR` is not escalatable and must not become so.** §5.5 permits escalation only for `SOLVER_UNKNOWN`, `SOLVER_TIMEOUT`, `FACT_MISSING` and `FACT_STALE`, and `ESCALATABLE_REASONS` encodes that (`registry/models.py:240-258` rejects anything else in `escalate_on`). A provider outage is an infrastructure condition; letting it escalate would turn every outage into an approval queue that grants exactly what the outage prevented anyone from checking.

---

## 6. Anti-laundering (`SEC-11`, `T-21`) — the clause that blocks `P1-18`

### 6.1 The threat, stated exactly

`T-21`: the agent uses a permitted tool to write to the system an evidence provider reads — approves its own change ticket, triggers a trivial CI run — and the provider then returns an authentic, fresh, correctly-signed fact. Every control is green and the action is self-authorized. This is Art. I's second sentence in operational form: *evidence the agent can manufacture through its own tools is not evidence*.

`SEC-11` gives two tiers: either the backing system is not writable by any tool registered for the same tenant, or facts carry `asserted_by` and rules require `asserted_by ∉ delegation_chain ∪ {agent_id}`. Both tiers need something the harness does not currently have: a machine-readable statement of *which tools write where*. That statement is `D-2`.

### 6.2 The two forms

**Option A — enumerated per provider.** Each provider entry lists the tools that can write to its backing system:

```json
{"id": "ci_result", "backing_system": "ci", "written_by_tools": ["ci.trigger_build", "ci.publish_artifact"]}
```

The loader refuses an action class whose `tool` appears in the list of a provider the class requires. Direct, readable, no change to the action-class registry, and the reviewer can see the whole control on one screen.

**Option B — derived from action-class writes.** Each provider declares its `backing_system`; each action class declares `writes_to: [<backing_system id>, …]`; the harness derives the relation and refuses the combination. Nothing is listed twice, and the statement lives next to the thing it describes.

### 6.3 The argument, honestly

Option A is better on exactly two axes and worse on the one that decides it.

*Where A wins.* It requires no change to the action-class registry, so it needs no ADR, no schema change and no migration of `tests/fixtures/registry/reference_deploy_registry.json`. And it is locally legible: a reviewer reading one provider entry can see the whole control for that provider without holding the action-class registry in their head.

*Where B wins.* The statement `writes_to` makes is one the action class's own author is competent to make and the provider's author is not. An engineer adding `ci.trigger_build` knows it writes to CI; the person who wrote the `ci_result` provider entry six months earlier does not know that `ci.trigger_build` was ever added. Under A, the correct update is in a file the change does not touch, made by a person who is not in the review.

*The failure modes, side by side.* Under A, a new tool is registered, nobody updates `written_by_tools`, and the loader keeps accepting the registry. Every gate stays green. Laundering becomes possible **and no document changed to say so** — the control is silently absent, and the only way to notice is for someone to re-audit the provider list against the tool list, which is exactly the review nobody schedules. Under B, the same new tool arrives with a `writes_to` the loader requires it to declare; the derived intersection becomes non-empty; the registry is refused at load with a named reason. The failure is loud, it lands on the change that caused it, and it lands on the person who can fix it.

That asymmetry is decisive, and it is the same argument `05-evaluation-plan.md` §1a makes about `partial` fixtures and `02-technical-plan.md` §4.4's loader makes about inert `escalate_on`: a control that is believed to be in force and is not is worse than a control that is visibly missing. A hand-maintained list of what writes where is a control with a silent expiry date.

**Recommendation: the derived form (Option B).** Recorded as `ADR-0022`.

### 6.4 What the derived form requires, exactly

1. **`backing_system` on every provider entry** — a required enum over `backing_systems[].id` (§2.2). Already in this document's registry format.
2. **`writes_to` on every action class** — a required array of `backing_systems[].id`, on `ActionClass` (`src/neuroharness/registry/models.py:189-235`, which has no such field today). Required, not defaulted: an empty array is a positive assertion that this tool writes to no system any provider reads, and the operator must write it down. This is the cost `D-2` names, and it applies to `effect_class: none` and `read` classes too, where the loader additionally requires `writes_to` to be `[]` — a read-only class that claims to write is a contradiction the loader can catch for free.
3. **The derived relation, checked at registry load** (check C3 of §2.4), for every action class `C` and every fact `f` in `C.required_facts`, with `P = providers[f.name]`:

   ```
   tenant_writes = ⋃ { A.writes_to : A ∈ action_classes }

   isolated(P)   ≡ P.backing_system ∉ tenant_writes

   REFUSE the registry unless, for every (C, f):
        isolated(P)                                  # SEC-11 tier 1
     or (P.asserts_principal = true                  # SEC-11 tier 2
         and P.trust_level ≠ "advisory")
   ```

   `tenant_writes` is the union over the **whole** action-class registry, not over the classes this agent may currently dispatch. `SEC-11` says "any tool registered for the same tenant", and a per-principal computation would make the control depend on an authorization model that is evaluated later and can change without a registry reload.

4. **The declared/derived agreement check.** `backing_systems[].writable_by_harness_tools` is compared against `(id ∈ tenant_writes)`. Disagreement refuses the registry. The declared field is not redundant: it is how an operator states an expectation, and the check is how the harness tells them the expectation is now false.

5. **The tier-2 rule remains a policy rule.** Where `isolated(P)` is false, the fact is delivered fresh, carrying `asserted_by`, and a hard rule denies when `asserted_by ∈ context.actor.chain_principals ∪ {agent_id}`. The chain is already flattened for exactly this purpose: `Actor.chain_principals` (`src/neuroharness/models/envelope.py:390-399`) includes *unverified* hops, because an unverified hop confers no authority but still taints evidence it asserted. `MUT-23` and `A-34` are the killing fixture and the scenario.

6. **The harness does not reclassify a laundered fact**, and this is deliberate. Marking it `PROVIDER_ERROR` or `MISSING` would move the verdict from `DENY RULE_FAILED:WF-04` to `ABSTAIN`, which is *down* the safety order (`resolve/safety.py:57-63`), and an abstention on an escalatable fact in an approvable class can escalate to `REQUIRES_APPROVAL` (§5.5). Laundering would then end in a human approval queue rather than a denial — a human asked to wave through evidence the harness detected as manufactured. The fact arrives intact and the hard rule refuses it.

### 6.5 This is a registry schema change, and therefore needs the ADR

`writes_to` is a new required field on `ActionClass`. That means: the published action-class registry format changes; `tests/fixtures/registry/reference_deploy_registry.json` and every derived fixture gain the field; the loader gains a required-field failure that existing signed registries will hit; and `MUT-23`'s gate is defined by whichever option is chosen (`08-increment-2-plan.md` §11). `02-technical-plan.md` §4.4's registry example and `FR-30`'s field list both need the field added. Under Art. VII a decision of this shape is recorded before it is built, so it is `ADR-0022`, and `ADR-0022` cannot be accepted until `P0-12` names a security owner to approve it (`R-B`, `OQ-10`).

---

## 7. Provider entries for the reference workflow

Populated from `tests/fixtures/registry/reference_deploy_registry.json`, whose `required_facts` already declare the keys and `max_age_seconds`. Every identifier below is synthetic.

### 7.1 Backing systems

| `id` | What it is | `writable_by_harness_tools` (declared) | Note |
|---|---|---|---|
| `ci` | CI service of record for build and test outcomes | `false` | Held only by `isolation_evidence` (§8). |
| `change_management` | External change-record system | `false` | Same. |
| `deploy_control_plane` | The deployment control plane whose state `deployment.apply` changes | **`true`** | `deployment.apply` writes here by definition, so `deploy_state` is a non-isolated provider and takes `SEC-11` tier 2. |
| `harness` | The harness's own approval service and execution-completion source | `false` | Not reachable by any tool the broker dispatches; the broker holds the only tool credentials (`SEC-02`). |

### 7.2 Providers

| `id` | Backing system | `trust_level` | `asserts_principal` | `default_ttl_seconds` | Class bound (`max_age_seconds`) | `auth.kind` | Isolated? |
|---|---|---|---|---|---|---|---|
| `ci_result` | `ci` | `authoritative` | `true` | 900 | 900 (`deploy_service`, key `[service, version]`); 900 (`rollback`, key `[service, to_version]`) | `mtls` | yes, by attestation |
| `change_approval` | `change_management` | `authoritative` | `true` | 3 600 | 3 600 (`deploy_service`, key `[service, version, target]`) | `oauth_client_credentials` | yes, by attestation |
| `deploy_state` | `deploy_control_plane` | `authoritative` | `true` | 60 | 60 (both mutating classes, key `[service, target]`) | `workload_identity` | **no** — tier 2 |
| `harness_approval` | `harness` | `authoritative` | `true` | 300 | 300 (both mutating classes, key `[proposal_digest]`) | `none` (harness-internal, §2.2) | yes, by construction |

Effective freshness is `min(default_ttl_seconds, max_age_seconds)` per §4; here the two agree by construction for the reference workflow, which is a property of this fixture and not a rule.

### 7.3 Notes per provider

**`ci_result` (`WF-04`).** Keyed on the exact `(service, version)` so a CI result for one version can never be read as evidence for another — `FactRequirement.key` names *arguments*, not free strings (`registry/models.py:113-117`, `:148-153`). `asserts_principal` is `true` even though `ci` is attested isolated, because `WF-04` states the `asserted_by ∉ delegation_chain` clause unconditionally and `MUT-23` kills it; belt and braces here costs one field and removes a dependency on an attestation staying true. Absence is `ABSTAIN FACT_MISSING:ci_result` (`MUT-04`); expiry is `ABSTAIN FACT_STALE:ci_result` (`MUT-14`).

**`change_approval` (`WF-05`).** Keyed on `(service, version, target)`. `WF-05` is evidence that the change was *planned*; `WF-02` is oversight of *this action*. They are different facts from different systems and must not be collapsed into one provider, because a system that can satisfy both is a single point of laundering.

**`deploy_state` (`WF-06c`).** The one non-isolated provider in the reference workflow, and its non-isolation is structural rather than accidental: the control plane whose state answers "is a deployment in flight" is the control plane `deployment.apply` writes to. Under §6.4 it therefore requires `asserts_principal: true` and the tier-2 rule. It is also why `WF-06c` is enforced primarily by the **broker's resource lease** (`FR-25`, `ADR-0017`) and only secondarily by the fact: a lease is held by the harness and cannot be written by the agent at all, while `deploy_state` can. A 60-second bound because an in-flight flag is worth nothing stale.

**`harness_approval` (`WF-02`, `WF-06a`) — special, and deliberately so.** This is the channel by which a human approval enters policy **as a fact**: `02-technical-plan.md` §4.6 (`:295`) — *"On approval the service appends its record and hands off to the gateway. It never calls the token service. The gateway re-fetches facts, re-resolves, and issues a token only on a fresh `ALLOW`"* — and `FR-47`, which names the approval service as a fact provider supplying `harness_approval{proposal_digest, bundle_digest, approver, decided_at, expires_at}`. Three consequences:

- Oversight travels the same path as every other input. There is no side channel by which an approval reaches the resolver, so an approval is subject to the same freshness, provenance and recording rules as a CI result. Art. IX's "on what evidence, until when" is answered by the same machinery that answers it for CI.
- It is keyed on `proposal_digest`, not on `action_id` or `envelope_digest`, so an approval survives the fact re-fetch that must happen before anything executes and is voided by a change to what was actually approved (`ADR-0015`, `ADR-0020`).
- Its 300-second `max_age_seconds` is *not* the approval TTL. `approval_ttl_seconds` is 86 400 for `deploy_service` and 900 for `rollback`; the 300 seconds bounds how stale the *fact of the approval* may be when the fresh evaluation reads it. Two different windows with two different owners, and collapsing them would either expire approvals in five minutes or let a five-minute-old read of a voided approval authorize a token.

Its backing system is the harness, so `auth.kind` is `none` per §2.2 and check C4 — and it is isolated by `SEC-02`: no tool the broker dispatches has credentials for the approval service.

### 7.4 What `writes_to` looks like on the reference classes

| Action class | `effect_class` | `writes_to` | Derived result |
|---|---|---|---|
| `deployment.apply` / `deploy_service` | `write` | `["deploy_control_plane"]` | `deploy_state` non-isolated → tier 2; `ci_result`, `change_approval`, `harness_approval` isolated |
| `deployment.apply` / `rollback` | `destructive` | `["deploy_control_plane"]` | same |
| `deployment.status` / `get_status` | `read` | `[]` | required by the loader to be empty for a non-mutating class (§6.4) |

`tenant_writes = {deploy_control_plane}`, which intersects `deploy_control_plane` and nothing else — so under §6.4 the registry loads, `deploy_state` is flagged tier 2, and the three isolated providers are evidence on their own. If a `ci.trigger_build` class were ever registered with `writes_to: ["ci"]`, `tenant_writes` would gain `ci`, `ci_result` would fall to tier 2, and — because `ci_result` already declares `asserts_principal: true` — the registry would still load, with the `WF-04` provenance rule now load-bearing rather than belt-and-braces. Had `ci_result` declared `asserts_principal: false`, the registry would be refused at load. That is the derived form doing the job the enumerated form would have done only if someone remembered.

---

## 8. The named gap

**What this document cannot supply: the truth about who can write to a backing system outside the harness.**

The derived relation of §6.4 computes `tenant_writes` over the action-class registry — that is, over the tools the harness knows about, dispatched through the broker, which holds the only tool credentials for them (`SEC-02`). It is complete with respect to *governed* writes and silent about every other write path:

- a CI job token, a deploy key or a service account that writes to the CI or change-management system without passing the broker;
- a human with a browser and the same permissions the agent is trying to launder through;
- another agent, in the same tenant, not behind this harness;
- a tool the agent reaches by any route `FR-01`'s interception does not cover — interception is `P1-01a`, which is not built, and `P4-05`'s bypass exercise is what goes looking for the routes it misses.

No registry field can settle this. It is a fact about the deployment's topology and identity model, it is true or false on the day it is asserted, and it decays. So the document makes it structural instead of pretending: `backing_systems[].isolation_evidence` (§2.2) requires a named attester, a date, a reference and a `next_review`, and `writable_by_harness_tools` is a claim the loader audits against the derived relation rather than a claim it trusts.

**Owner.** The **security lead**, with the **named workflow owner** as second reviewer, per the two-person rule in `06-delivery-and-governance.md` §10 (`:142`) and the labelling protocol in `05-evaluation-plan.md` §3. Neither is a named person today: `06-delivery-and-governance.md:95` is titled *"RACI (to be confirmed in `P0-12`)"*, and `OQ-10` — *"Named workflow owner, second security reviewer, compliance contact and agent-developer contact"* — is open, owned by the delivery lead, needed by `P0-12`. Until `P0-12` closes, **no `isolation_evidence` entry can be validly attested**, so every provider in a real deployment is tier 2 by default: `asserts_principal: true` and the provenance rule load-bearing. That is the conservative reading and it is what `P1-18` MUST assume.

The residual, once attested, is `R-04`-shaped and already recorded in `04-threat-model.md` `T-21`: *a provider compromised upstream still produces authentic facts*. Accepting it is `P0-07`'s job (threat-model sign-off), which depends on `P0-04`–`P0-06` — so this document makes `P0-07` reachable and cannot perform it, for the same reason: it needs a named security reviewer.

---

## 9. Where this document is ahead of the code

Stated plainly, because `08-increment-2-plan.md` Appendix B exists for the same reason.

| Claim here | State in the tree |
|---|---|
| `Fact` enforces `FR-11` | **Built.** `models/envelope.py:464-494`, `:496-509`, `:516-525`; schema `$defs.Fact`. |
| Trusted `Clock` seam, `CLOCK_UNAVAILABLE` on failure | **Built.** `seams.py:31-42`, `:64-67`; `errors.py:131-134`. |
| `FactState`, `blocks`, fact abstention arm | **Built.** `resolve/inputs.py:169-197`; `resolve/resolver.py:178-182`. |
| `FactRequirement` with `key`, `max_age_seconds`, `required`, `escalatable` | **Built.** `registry/models.py:110-153`. |
| Fact-provider registry, adapters, loader, `ProviderOutcome`, the §5 mapping | **Not built.** `P1-18`. Nothing in `src/` constructs a `FactState` today. |
| `writes_to` on `ActionClass`; the §6.4 relation; checks C1–C4 | **Not built, and a registry schema change.** `ADR-0022`, then `P1-18`. |
| A real signature on any registry | **Not built.** `P0-05`, gated on `P0-13`. This document specifies the field and the refusal; the verifier is someone else's task. |
| Provider timeout default | **Not present.** `src/neuroharness/defaults.py` has no fact-provider constant; `P1-18` adds one rather than hard-coding `timeout_ms` anywhere. |

**Settled here by citation:** `D-7` (2026-09-20 — Yes). Missing or stale *required* facts may warrant human escalation when the class opts in. Normative rules: [ADR-0027](adr/ADR-0027-d7-required-fact-escalation.md). Loosen `FactRequirement._check_escalation`; `FactState` check is `escalatable` ⇒ class permits fact escalation; keep resolver arm and truth-table rows; land the §6 killing fixture from `08-increment-2-plan.md`. The pre-decision inert shape (e.g. `rollback` with optional `deploy_state` escalatable) remains valid; required+escalatable becomes constructible under the ADR.

One thing this document deliberately does **not** settle:

- **`D-1`** — the resource-key grammar. Facts are keyed on argument names, not resource keys, so this document does not depend on it; `deploy_state`'s key is `[service, target]`, not the rendered `service:{service}/target:{target}`.

---

## 10. Acceptance

`P0-06` is Done when this document exists, `ADR-0022` records `D-2`, and both are reviewed per `06-delivery-and-governance.md` §6. `P1-18` is accepted against:

| Gate | Fixture / scenario | Expected |
|---|---|---|
| A required fact absent abstains | `MUT-04` | `ABSTAIN FACT_MISSING:ci_result` |
| A required fact past its bound abstains | `MUT-14` | `ABSTAIN FACT_STALE:ci_result` |
| Evidence asserted by the delegation chain is not evidence | `MUT-23`, `A-34` | `DENY RULE_FAILED:WF-04` |
| Clock unavailable abstains any evaluation with a required fact | `A-22` | `ABSTAIN CLOCK_UNAVAILABLE`, terminal |
| Non-fresh facts arrive as value-less stubs | `P1-18` acceptance criterion, `03-work-breakdown.md:59` | no `value` key present |
| The §5 mapping is total | import-time `ConfigurationError`, shape of `resolve/safety.py:70-78` | a `ProviderOutcome` with no row fails at import |
| The §6.4 relation refuses a laundering-capable registry | new fixture owed by `P1-18`, declared before it is written (`05-evaluation-plan.md` §1a) | `REGISTRY_INTEGRITY_FAILED` → `ABSTAIN` at load |

The last row is a fixture this document creates the need for and does not create: per Art. IV a gate without a killing fixture does not exist, so `P1-18` owes it a declaration in the mutation matrix in the same change that lands check C3.

## 11. Change log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-09-18 | Initial draft. Closes `P0-06`: provider registry format and its load-time checks, the `Fact` lifecycle against the built `FR-11` enforcement, staleness on the trusted clock, a total provider-outcome taxonomy, and the `SEC-11`/`T-21` anti-laundering clause decided in favour of the derived form (`D-2`, `ADR-0022`). Corrects `02-technical-plan.md:154`, which maps provider failure to `FACT_MISSING` where `FR-13` says `FACT_PROVIDER_ERROR`. |
