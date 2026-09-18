# ADR-0008: Digest-bound single-use decision tokens and approval binding

**Status:** Superseded by [ADR-0015](ADR-0015-*.md) — two digests and post-approval re-evaluation · **Date:** 2026-09-18 · **Deciders:** Tech lead, Security lead · **Origin:** peer review M2, M9

## Context
The research evaluated an envelope and then executed "via a broker" with nothing binding the executed call to the evaluated one. This leaves time-of-check/time-of-use gaps, argument substitution, replay of old decisions, and approvals that silently apply to modified actions.

## Decision
- The envelope is canonicalized with RFC 8785 (JCS) and hashed with SHA-256; the digest is the identity of the action.
- On `ALLOW` (or resolved approval) the token service issues a **decision token** `{token_id, decision_id, envelope_digest, policy_bundle_digest, tenant, issued_at, expires_at, key_id, nonce}` signed with a KMS-held key (Ed25519 across services; HMAC acceptable in single-deployment mode).
- The broker recomputes the digest of the envelope it will execute, verifies the token (signature, expiry, tenant, digest equality) and **atomically consumes** the nonce before dispatch. Default TTL 60 s.
- Approval requests and approvals are bound to the same digest; any re-evaluation of the same `action_id` supersedes pending approvals; an approval whose digest no longer matches is void.
- Facts carry `fetched_at` and per-rule max age so stale evidence cannot underwrite a decision.
- No token is issued before the decision record is durably persisted (ADR-0006).

## Alternatives considered
- **Trust the gateway→broker channel (mTLS) without tokens.** Rejected: does not bind *which* envelope was approved, nor prevent reuse.
- **Long-lived capability tokens per session.** Rejected: reusable; widens the window for substitution.
- **Broker re-evaluates policy itself.** Rejected: duplicates the TCB; still needs binding to the approval.

## Consequences
- Positive: closes TOCTOU, replay and substitution; approvals mean exactly one action.
- Negative: key management becomes a hard dependency (`NFR-17`, `OQ-03`); short TTLs require the broker to be reachable promptly; a nonce store is needed (`NFR-14`).
