# ADR-0012: Licence and contribution model

**Status:** Accepted (licence decided Apache-2.0 by repository owner 2026-09-21) · **Date:** 2026-09-18 · **Deciders:** Repository owner (Ian Cruickshank, licence), Tech lead (contribution model) · **Relates to:** ADR-0013 · **Origin:** `P0-11`, `OQ-09`

## Context

`pyproject.toml` declared `license = { text = "UNLICENSED" }`, there was initially no `LICENSE` file and no `CONTRIBUTING.md`. That was a live blocker on contribution: without a licence, nobody outside the repository owner has permission to use, modify or distribute this code, and a contributor has no terms to contribute under.

`OQ-09` ties the licence to a technical constraint, which is why the two belong in one ADR. `P3-01` needs an LTLf-to-DFA compiler for the temporal properties (`WF-06a`). The mature tools are **MONA** (GPL-3.0) and **Spot** (GPL-3.0). If this project links either, the combined work is GPL-3.0, and a permissive licence becomes unavailable.

`ADR-0013` already chose to **build a bounded LTLf subset in-house** rather than vendor. That decision was taken on determinism and scope grounds, not licence grounds — and it has the side effect of removing the GPL constraint from the critical path. `OQ-09` is therefore narrower than it was written: the question is no longer "which licence does the toolchain force on us" but "which licence do we want, given that nothing forces one".

## Decision

Both halves are now decided and closed.

### The contribution model — decided

`CONTRIBUTING.md` records what `06-delivery-and-governance.md` and `CLAUDE.md` already require, in one place a contributor will actually find:

- trunk-based development, short-lived branches, squash merge, Conventional Commits;
- the Definition of Done in `06-delivery-and-governance.md` §6 applies to every change;
- **hard-gate changes need two-person review**, and policy under `policy/**/*.rego` is production code;
- **never skip, disable, weaken or quarantine a test or mutation fixture to get CI green** — fix the root cause or surface it;
- the spec changes before the code: requirements carry stable IDs and a change cites them;
- no secrets, credentials or real customer data in fixtures.

None of that is new. Writing it down is the change, because a contribution model that lives only in an AI assistant's instruction file and a governance document is one a human contributor has to reverse-engineer.

### The licence — decided: Apache-2.0

The repository owner has accepted the recommendation and chosen **Apache-2.0**.
`LICENSE` is added at repository root, `pyproject.toml` declares `license = { text = "Apache-2.0" }`, and `CONTRIBUTING.md` includes the licence terms and the `SPDX-License-Identifier: Apache-2.0` convention.

**The options, and what each costs here:**

| Option | For | Against |
|---|---|---|
| **Apache-2.0** (chosen) | Permissive, so the harness can be embedded by the organisations most likely to want it. Its explicit patent grant matters for a project whose subject is enforcement mechanisms. Compatible with GPL-3.0 in one direction, so a future GPL-licensed critic could still be run out-of-process | No copyleft: a vendor can build a proprietary fork of the enforcement layer |
| **AGPL-3.0** | Copyleft reaches network use, which is the deployment shape this harness actually has — a service other systems call. Keeps modifications to a governance mechanism visible | Many organisations forbid AGPL dependencies outright, which would exclude much of the intended audience from adopting it at all |
| **Proprietary / source-available (BSL, PolyForm)** | Preserves commercial optionality | Undermines the project's own argument: a verification harness whose verification logic cannot be inspected asks for exactly the trust it is built to avoid extending to a model |
| **Stay `UNLICENSED`** | No decision needed | The status quo, and it is not neutral: it blocks contribution, blocks `P0-11`, and leaves every contributor's work in an undefined legal state |

**Decision: Apache-2.0.** It matches the project's stated purpose — a harness other systems adopt — and its patent grant is worth more here than copyleft would be, because the value being protected is the *specification and the fixtures*, which are documents, not the enforcement code.

The GPL constraint that made `OQ-09` hard is no longer binding on this choice, given `ADR-0013`. If that decision is ever reversed and MONA or Spot is vendored, this ADR is superseded, not amended: the licence would be forced, and that is a different decision.

## Alternatives considered

- **Decide the licence here and ship a `LICENSE` file.** Originally deferred until the owner decided; now decided and closed by the owner (`P0-11`).
- **Leave `CONTRIBUTING.md` until the licence lands.** Rejected: the contribution *process* is independent of the licence, it is already decided in two other documents, and writing it down closes the half of `P0-11` that can honestly be closed.
- **Reopen `OQ-09` as a licence question.** Unnecessary. `ADR-0013` removed the constraint; `OQ-09` should be closed with a pointer to both ADRs rather than carried as an open question about a toolchain the project decided not to use.

## Consequences

- **Positive.** `CONTRIBUTING.md` exists, so a contributor has terms of engagement. `OQ-09` is resolved and closed against `ADR-0013` and this decision. `LICENSE` is present, `pyproject.toml` declares Apache-2.0, closing `P0-11`. Outside contributions have a clear legal basis.
- **Negative.** No copyleft protection; downstream consumers may build proprietary components around the open core.
- **Follow-up.** `P0-11` is closed. Future source files may include the standard `SPDX-License-Identifier: Apache-2.0` header.
