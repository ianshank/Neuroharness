# ADR-0012: Licence and contribution model

**Status:** Proposed — **the licence itself is not decided here and cannot be** · **Date:** 2026-09-18 · **Deciders:** Repository owner (licence), Tech lead (contribution model) · **Relates to:** ADR-0013 · **Origin:** `P0-11`, `OQ-09`

## Context

`pyproject.toml` declares `license = { text = "UNLICENSED" }`, there is no `LICENSE` file and no `CONTRIBUTING.md`. That is a live blocker on contribution: without a licence, nobody outside the repository owner has permission to use, modify or distribute this code, and a contributor has no terms to contribute under.

`OQ-09` ties the licence to a technical constraint, which is why the two belong in one ADR. `P3-01` needs an LTLf-to-DFA compiler for the temporal properties (`WF-06a`). The mature tools are **MONA** (GPL-3.0) and **Spot** (GPL-3.0). If this project links either, the combined work is GPL-3.0, and a permissive licence becomes unavailable.

`ADR-0013` already chose to **build a bounded LTLf subset in-house** rather than vendor. That decision was taken on determinism and scope grounds, not licence grounds — and it has the side effect of removing the GPL constraint from the critical path. `OQ-09` is therefore narrower than it was written: the question is no longer "which licence does the toolchain force on us" but "which licence do we want, given that nothing forces one".

## Decision

**Two halves, and only one of them is decided here.**

### The contribution model — decided

`CONTRIBUTING.md` records what `06-delivery-and-governance.md` and `CLAUDE.md` already require, in one place a contributor will actually find:

- trunk-based development, short-lived branches, squash merge, Conventional Commits;
- the Definition of Done in `06-delivery-and-governance.md` §6 applies to every change;
- **hard-gate changes need two-person review**, and policy under `policy/**/*.rego` is production code;
- **never skip, disable, weaken or quarantine a test or mutation fixture to get CI green** — fix the root cause or surface it;
- the spec changes before the code: requirements carry stable IDs and a change cites them;
- no secrets, credentials or real customer data in fixtures.

None of that is new. Writing it down is the change, because a contribution model that lives only in an AI assistant's instruction file and a governance document is one a human contributor has to reverse-engineer.

### The licence — **not decided, and deliberately so**

This ADR sets out the options and a recommendation. It does not add a `LICENSE` file and does not change `pyproject.toml`, because choosing a software licence is a legally consequential, effectively irreversible act of the repository owner: once code is published under a licence, that grant cannot be withdrawn from anyone who already received it. It is not a decision an engineering plan should make on the owner's behalf, and it is not one that should arrive as a side effect of closing a work-breakdown task.

**The options, and what each costs here:**

| Option | For | Against |
|---|---|---|
| **Apache-2.0** (recommended) | Permissive, so the harness can be embedded by the organisations most likely to want it. Its explicit patent grant matters for a project whose subject is enforcement mechanisms. Compatible with GPL-3.0 in one direction, so a future GPL-licensed critic could still be run out-of-process | No copyleft: a vendor can build a proprietary fork of the enforcement layer |
| **AGPL-3.0** | Copyleft reaches network use, which is the deployment shape this harness actually has — a service other systems call. Keeps modifications to a governance mechanism visible | Many organisations forbid AGPL dependencies outright, which would exclude much of the intended audience from adopting it at all |
| **Proprietary / source-available (BSL, PolyForm)** | Preserves commercial optionality | Undermines the project's own argument: a verification harness whose verification logic cannot be inspected asks for exactly the trust it is built to avoid extending to a model |
| **Stay `UNLICENSED`** | No decision needed | The status quo, and it is not neutral: it blocks contribution, blocks `P0-11`, and leaves every contributor's work in an undefined legal state |

**Recommendation: Apache-2.0.** It matches the project's stated purpose — a harness other systems adopt — and its patent grant is worth more here than copyleft would be, because the value being protected is the *specification and the fixtures*, which are documents, not the enforcement code.

The GPL constraint that made `OQ-09` hard is no longer binding on this choice, given `ADR-0013`. If that decision is ever reversed and MONA or Spot is vendored, this ADR is superseded, not amended: the licence would be forced, and that is a different decision.

## Alternatives considered

- **Decide the licence here and ship a `LICENSE` file.** Rejected. The plan's own `R-B` already records that none of these ADRs has an approver until `P0-12` names one; publishing a licence grant is the one decision in this increment where acting without that approver is not merely procedurally wrong but practically irreversible.
- **Leave `CONTRIBUTING.md` until the licence lands.** Rejected: the contribution *process* is independent of the licence, it is already decided in two other documents, and writing it down closes the half of `P0-11` that can honestly be closed.
- **Reopen `OQ-09` as a licence question.** Unnecessary. `ADR-0013` removed the constraint; `OQ-09` should be closed with a pointer to both ADRs rather than carried as an open question about a toolchain the project decided not to use.

## Consequences

- **Positive.** `CONTRIBUTING.md` exists, so a contributor has terms of engagement. `OQ-09`'s technical half is resolved and can be closed against `ADR-0013`.
- **Negative, and it is the honest state.** `P0-11` remains **half open**, and the Phase 0 exit gate does not move on it. `pyproject.toml` still says `UNLICENSED` and no `LICENSE` file exists, so the repository still cannot accept an outside contribution. This is escalated to the repository owner alongside `P0-12` and `P0-13` rather than quietly closed.
- **Follow-up.** When the licence is chosen: add `LICENSE`, set `pyproject.toml`'s `license` field, add the `SPDX-License-Identifier` header convention to `CONTRIBUTING.md`, close `OQ-09`, and mark this ADR `Accepted`.
