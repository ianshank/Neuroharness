# Neuroharness Threat Model

**Status:** Draft v0.2 — needs security review · **Date:** 2026-09-18 · **Method:** assets → trust boundaries → adversaries → STRIDE per boundary → controls → residual risk · **Feeds:** `01-specification.md` §6.12 (`SEC-`), `05-evaluation-plan.md` §7

## 1. Scope
The harness control plane, execution plane and audit plane described in `02-technical-plan.md` §2, the interfaces to the governed runtime, tool servers, fact providers, approvers and auditors. Out of scope: the internal safety of the LLM itself, the security of tool servers beyond the broker boundary, and the host platform (Kubernetes, cloud IAM), which are assumed to be operated to a baseline standard.

## 2. Assets
| ID | Asset | Why it matters |
|---|---|---|
| AS-1 | Tool credentials (held only by the broker) | Direct path to real-world effects. |
| AS-2 | Token signing key; bundle/registry signing keys; checkpoint signing key | Forgery = unguarded execution or undetectable tampering. |
| AS-3 | Policy bundles, action-class registry, critic packs | Define what is allowed. |
| AS-4 | Decision records and hash chain | Evidence; regulatory value; forensic value. |
| AS-5 | Approval decisions | Human authorization of high-impact actions. |
| AS-6 | Fact-provider credentials and fact integrity | Facts are the only inputs policy trusts. |
| AS-7 | Monitor state and token nonce store | Ordering guarantees and single-use guarantees. |
| AS-8 | Tool results and record contents (may contain personal or confidential data) | Confidentiality and privacy obligations. |
| AS-9 | Resource leases and the rollout-mode setting | Mutual exclusion and whether gates block at all. |

## 3. Trusted computing base (TCB) inventory
Components whose compromise can produce an unwarranted `ALLOW` or hide one:

| Component | Trust required | Notes |
|---|---|---|
| Envelope builder / gateway core | Integrity | Builds context; strips claims. |
| Action-class registry + its signature verification | Integrity | Decides which critics run and in which mode. |
| PDP (OPA) + bundle verification | Integrity | Deterministic rules. |
| Hard critics (SMT, monitor; Prolog only once promoted) | Integrity, availability | Timeouts bound them. |
| Verdict resolver | Integrity | Pure function; table-tested. |
| Token service + nonce store | Integrity, confidentiality of key | Single-use binding. |
| Broker / PEP + connectors | Integrity, confidentiality of tool credentials | Executes exactly the digested envelope. |
| Evidence store + checkpointing | Integrity, availability | Write-ahead; append-only. |
| Fact providers (CI, approvals, deploy state, clock, identity) | Integrity, freshness | **Often overlooked:** a spoofed `ci_result` is an allow. |
| Identity provider / delegation resolver | Integrity | Source of actor and approver identity. |
| Key management (KMS/HSM) | Confidentiality, availability | Rotation without downtime. |
| Approval service | Integrity | Records approvals and supplies the `harness_approval` fact; **it cannot issue tokens** (`FR-47`), which keeps it out of the mint path but still authoritative over oversight. |
| Lease store | Integrity, availability | Mutual exclusion (`FR-25`); a lost lease is a double execution. |
| Override service | Integrity | Can halt or (with two principals) demote (`FR-48`). |
| Effect critic and state fact source | Integrity | Detects executed-but-wrong effects (`FR-57`). |

Explicitly **not** in the TCB: the LLM, the agent framework, the hook adapter, soft critics, dashboards, the approval UI's presentation layer (its API is).

## 4. Trust boundaries and flows
| Boundary | From → To | Data | Direction of trust |
|---|---|---|---|
| B1 | Governed runtime → Gateway | Proposal (untrusted) | None; validated and re-contextualized. |
| B2 | Gateway → Fact providers | Keys; facts returned | Providers trusted, authenticated, versioned, digested. |
| B3 | Gateway → PDP / critics | Harness-built input | Sandboxed evaluators; no egress. |
| B4 | Gateway → Evidence store | Records | Append-only; write-ahead. |
| B5 | Gateway → Token service → Broker | Digest, token | Cryptographic binding. |
| B6 | Broker → Tool servers | Executed call; result | Least privilege out; results untrusted in. |
| B7 | Broker → Governed runtime | Typed, tagged result | Untrusted data flowing into the model context. |
| B8 | Approver → Approval service | Approval on digest | Authenticated human; eligibility enforced. |
| B9 | Policy author → Bundle/registry pipeline | Rego, registry, critic packs | Two-person review; signing; mutation gates. |
| B10 | Auditor → Evidence export | Records, checkpoints | Read-only; chain-verifiable. |

## 5. Adversaries
| ID | Adversary | Capability | Goal |
|---|---|---|---|
| ADV-1 | External attacker via indirect prompt injection (content in tool results, retrieved documents, tickets) | Influence model output | Cause an unauthorized or harmful action; exfiltrate data. |
| ADV-2 | The governed model itself (erroneous or objective-gaming behaviour) | Craft any proposal; probe via repairs | Get an unsafe action allowed; satisfy the letter of checks. |
| ADV-3 | Malicious or compromised tool server | Return arbitrary results; behave differently from declared semantics | Inject instructions; cause side effects. |
| ADV-4 | Insider with policy-author rights (or an LLM assisting authoring) | Change rules, registry, critics | Weaken gates deliberately or accidentally. |
| ADV-5 | Insider approver / compromised approver account | Approve requests | Authorize what should not be. |
| ADV-6 | Compromised or stale fact provider | Return false or old facts | Satisfy evidence rules falsely. |
| ADV-7 | Network attacker between components | Replay, tamper, intercept | Reuse tokens; substitute envelopes; read records. |

## 6. Threats and controls

| ID | STRIDE | Boundary | Threat | Impact | Controls | Residual / test |
|---|---|---|---|---|---|---|
| T-01 | Spoofing | B1, B3 | Agent claims (e.g. `ci_passed: true`) are read as facts by policy | Unwarranted `ALLOW` | `SEC-01`, `INV-08`, `FR-12`: PDP input built from context only; schema test asserts no `claims` key | `MUT-17` |
| T-02 | Tampering | B1, B3 | Model-generated strings in arguments interpreted by rules or critics (injection into Rego data paths, SMT strings) | Rule bypass | Typed argument schemas; enumerations for anything policy compares; no string interpolation into rule paths; Regal rule against dynamic refs | contract tests; code review checklist |
| T-03 | Elevation | B1, B6 | Governed runtime calls tools directly, bypassing the gateway | Unguarded execution | `SEC-02`: only the broker holds credentials; network policy; deployment checklist | bypass fixture in `P4-05`; residual: misconfigured deployments (documented) |
| T-04 | Tampering | B5 | Envelope substituted between decision and execution (TOCTOU / argument swap) | Executing an un-evaluated action | `SEC-03`, `FR-21`: broker recomputes digest; token bound to digest | `MUT-10` |
| T-05 | Spoofing/Replay | B5, B7 | Decision token replayed or stolen | Repeat or unauthorized execution | `SEC-03`, `FR-20`, `FR-22`: single-use nonce, short TTL, keyed signature, mTLS between gateway and broker | `MUT-09`; alert on reuse |
| T-06 | Replay/Tampering | B8 | Approval reused for a different envelope, or an approval granted on an earlier version silently applies to a modified one | Human oversight defeated | `SEC-04`, `FR-41`, `FR-44`, `FR-46`: digest binding; supersede on re-evaluation; TTL | `MUT-11` |
| T-07 | Elevation | B8 | Self-approval; agent-as-approver; approval fatigue leading to rubber-stamping | Oversight defeated | `FR-42` eligibility; `FR-43` full disclosure to approver; approval-latency and volume metrics; `never_approvable` classes | `MUT-18`; residual: fatigue (monitored, `R-06`) |
| T-08 | Tampering | B9 | Unsigned or modified policy bundle / registry / critic pack loaded | Arbitrary policy | `SEC-05`: signature verification on load; `BUNDLE_INTEGRITY_FAILED` → `ABSTAIN` | `MUT-12` |
| T-09 | Tampering | B9 | Malicious or erroneous policy change, including LLM-assisted rules with low recall (AgentSpec reports ~71% recall for LLM-generated rules in one domain) | Silent weakening | `SEC-06`: two-person review; mutation fixtures must still kill; shadow before enforce (`ADR-0010`); rule provenance to NL requirement (`FR-32`) | mutation gate; residual: fixtures may not cover the new hole (`R-03`) |
| T-10 | Tampering | B9 | Test or fixture weakened/disabled to get CI green | Gate exists on paper only | CI forbids deleting fixtures without an ADR reference; mutation stage has no manual override; `CLAUDE.md` rule; CODEOWNERS on `fixtures/` and `policy/` | code review; residual: collusion (two-person rule) |
| T-11 | Tampering | B3 → B1 | Verifier output used as instructions to the model (free-text counterexamples become a prompt-injection channel) | Model steered by a compromised critic | `SEC-07`, `FR-56`, `FR-90`: typed counterexamples only; string fields outside schema rejected; harness never renders text | schema tests |
| T-12 | Tampering | B6 → B7 | Tool result contains injected instructions that re-enter the model context | Next proposal manipulated (ADV-1 main vector) | `SEC-08`, `FR-63`: typed per output schema, size-bounded, tagged untrusted; harness still evaluates the *next* proposal from facts, so injection cannot authorize | `A-06` (forged claim); residual: injection can still cause *proposals* that waste repair budget (`R-08`) |
| T-13 | Spoofing | B2 | Fact provider spoofed or compromised | False evidence → `ALLOW` | `SEC-09`: authenticated providers, versioning, digests, provider allowlist; provider in TCB inventory; cross-check where possible (CI result vs artifact digest) | residual: provider compromise is an accepted risk with detection via receipts vs state audits (`R-04`) |
| T-14 | Replay | B2 | Stale or replayed fact | Outdated evidence | `FR-11`: per-rule max age; `fetched_at` from trusted clock; `FACT_STALE` | `MUT-14` |
| T-15 | Repudiation/Tampering | B4, B10 | Records altered or deleted; decisions denied after the fact | Loss of evidence | `SEC-10`, `FR-70`: append-only triggers, hash chain, signed checkpoints, export verifier | chain-verifier test |
| T-16 | Denial of service | B1, B3 | Flooding the harness forces `ABSTAIN` (fail-closed becomes denial) | Availability | Rate limits per session/tenant; queue bounds; autoscaling; `NFR-10` SLO | load test; accepted residual: fail-closed is the design |
| T-17 | Elevation | B1, B6 | Specification gaming: proposal satisfies every check but the executed effect differs (argument aliasing, a target name that maps elsewhere in tool config, side effects outside declared semantics) | Real-world harm despite `ALLOW` | Broker executes exactly the digested arguments; connectors map 1:1 with no defaults; environment and target resolved by the harness from deployment config, not free arguments; receipts compared with deploy-state facts post hoc (`P4-05` audit) | residual: tool semantics drift (`R-05`) |
| T-18 | Information disclosure | B1 | Repair channel probing: the agent learns gate internals from counterexamples and searches for bypasses | Faster bypass discovery | Minimal counterexamples (property ID, fields, expected domain only); repair budget; rate limit repairs per session; monitor for repeated near-miss patterns | residual accepted; monitored |
| T-19 | Information disclosure | B4, B10 | Cross-tenant leakage of records or facts | Confidentiality | `NFR-15`: tenant-scoped keys, tables, bundles | integration test |
| T-20 | Spoofing | B5, B9 | Signing-key compromise | Forged tokens or bundles | `NFR-17`: KMS/HSM, rotation, short token TTL, key-ID in tokens, revocation list, alerting on unknown key IDs | residual: window until rotation |

| T-21 | Spoofing/Tampering | B2, B6 | **Fact laundering:** the agent uses a permitted tool to write to the system an evidence provider reads (approve its own change ticket, trigger a trivial CI run), then the provider returns an authentic fresh fact | Self-authorized action with every control green | `SEC-11`: evidence providers' backing systems are not writable by the tenant's registered tools, or facts carry `asserted_by` and rules reject evidence from the delegation chain; effect verification catches divergence after the fact | `MUT-23`, `A-34`; residual: a provider compromised upstream still produces authentic facts (`R-04`) |
| T-22 | Spoofing | B1, B8 | Forged session identity or delegation chain: a fresh session per proposal defeats per-session limits and monitor state; an unverified chain hop chooses which human "holds the right" and who is excluded from approving | Oversight and ordering defeated | `SEC-12` server-derived session IDs from an authenticated host; `SEC-13` verifiable delegation credentials per hop with recorded digests; `FR-06` session tree; `FR-42` eligibility across the tree | `MUT-25`, `MUT-33`, `A-36`, `A-40` |
| T-23 | Elevation | B9 | Operational override abused: a single principal demotes a class to advisory, which executes everything, bypassing the two-person rule that governs policy changes | Total bypass, cheaper than changing policy | `FR-48` two principals, no affected-chain principals, reason, ticket, ≤ 1 h auto-expiry; `FR-49` halt is the incident lever instead; `SEC-14` | `MUT-26`, `A-39` |
| T-24 | Tampering | B5 | Shadow token used against a class that has since been promoted to enforce, or a token replayed after its bundle was superseded | Execution under stale authority | `FR-20` token carries verdict and mode; `FR-21` broker compares against the class's current mode and bundle currency and checks the revocation list | `MUT-20`, `MUT-36`, `A-31` |
| T-25 | Denial of service / Tampering | B1 | Retry storms and duplicate deliveries indistinguishable from replay, drowning the replay alert | The one alert that matters is lost | `FR-22` duplicate-delivery classification; `FR-27` idempotency and `RETRY_UNRESOLVED` | `MUT-27`, `A-32` |
| T-26 | Tampering | B6 | Executed-but-wrong effect: the call is authorized and the receipt says success, but the effect differs (wrong artifact, wrong target resolution) | Real-world harm despite ALLOW | `FR-57` effect critic against a fresh state fact; `T-17` controls on argument resolution | `MUT-32`, `A-38` |

## 7. Agent-specific abuse cases (for red-team fixtures)
1. **Claim smuggling**: put fact-shaped keys in arguments or nested objects hoping a rule reads them (T-01, T-02).
2. **Two-step split**: decompose a forbidden action into allowed sub-actions whose sequence is unsafe (motivates the monitor, `WF-06a`).
3. **Approval piggybacking**: obtain approval for a benign envelope, then repair it into a harmful one (T-06).
4. **Environment aliasing**: `target: "prod-eu"` where the allowlist checks `production` (T-17; enumerations, not strings).
5. **Result-driven escalation**: a tool result instructs the model to call a privileged tool (T-12).
6. **Budget exhaustion as signal**: use repairs to map the contract (T-18).
7. **Race**: two sessions deploy the same service simultaneously (T-17, `NFR-14`, now blocked by the broker lease `FR-25` rather than by an uncertified monitor).
8. **Evidence manufacturing**: use a low-risk permitted tool to satisfy an evidence rule (T-21).
9. **Session laundering**: open a new session per proposal, or spawn a sub-agent, to reset per-session limits or to become an eligible approver (T-22).
10. **Override abuse**: request a demotion during a manufactured incident to get a window of unguarded execution (T-23).
11. **Approval staleness**: obtain approval, wait for the evidence to expire, then execute (closed by `FR-47`).

## 8. Assumptions and residual risks
- Host platform, identity provider and KMS are operated to a baseline standard.
- Fact providers are as trustworthy as the systems they front; the harness detects staleness and absence, not sophisticated forgery by a compromised provider.
- The harness reduces the attack surface to encoded invariants; unencoded harms remain possible and are stated in `01-specification.md` §3.3.
- Residual risks are tracked as `R-` items in `06-delivery-and-governance.md` §9 with owners.
- **Known residuals after round two:** a compromised fact provider still produces authentic facts (detection is post-hoc via `FR-57` and state audits); the trajectory monitor ships advisory in v1.0, so general ordering invariants beyond precedence-at-token-issue and the broker lease are not enforced until certification; delegation credentials depend on an identity layer the project does not own, and where the environment cannot issue them `WF-03` degrades to a trusted string and must be marked as such per deployment.
