"""The single home for documented default values.

"No hardcoded values" does not mean "no defaults". It means no decision path
branches on a literal buried in logic, and every default is named, documented,
traceable to a requirement, and overridable at the layer that owns it.

Rule for contributors: if a number or duration influences a verdict, a token, a
budget or a retention decision, it belongs here and is referenced by name. A
test (``tests/unit/test_no_magic_values.py``) enforces this by scanning the
decision-path modules for bare numeric literals.

Precedence, lowest to highest: these defaults, then :class:`Settings` (which
reads the environment), then the signed action-class registry, which is the
only authority for per-class policy.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DEFAULT_REPAIR_BUDGET",
    "MAX_REPAIR_BUDGET",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "DEFAULT_APPROVAL_TTL_SECONDS",
    "DEFAULT_LEASE_TIMEOUT_SECONDS",
    "DEFAULT_NEW_ACTIONS_PER_HOUR",
    "DEFAULT_BUNDLE_GRACE_SECONDS",
    "DEFAULT_CRITIC_TIMEOUT_MS",
    "MAX_DEMOTE_MODE_WINDOW_SECONDS",
    "DEFAULT_RECORD_RETENTION_DAYS",
    "DIGEST_PREFIX",
]

# --- Repair (specification section 5.4, FR-91) -------------------------------

#: Repair iterations allowed per action before the verdict becomes DENY.
DEFAULT_REPAIR_BUDGET: Final[int] = 3

#: Ceiling the registry loader enforces on any per-class repair budget. Ten is
#: the bound the LLM-Modulo evaluation used; beyond it the loop is a probing
#: channel rather than a correction mechanism.
MAX_REPAIR_BUDGET: Final[int] = 10

#: New actions per (session root, action class) per hour before rate limiting
#: (``FR-93``). Bounds contract probing that resets the repair budget.
DEFAULT_NEW_ACTIONS_PER_HOUR: Final[int] = 10

# --- Tokens and leases (FR-20, FR-25, FR-26) ---------------------------------

#: Decision-token lifetime. Short by design: it bounds the window between a
#: decision and its execution, which is the time-of-check/time-of-use gap.
DEFAULT_TOKEN_TTL_SECONDS: Final[int] = 60

#: How long a token may still be accepted after its policy bundle is superseded
#: (``FR-84``). Zero for loosening changes; a deployment may widen it.
DEFAULT_BUNDLE_GRACE_SECONDS: Final[int] = 0

#: How long the broker holds a resource lease before reclaiming it (``FR-25``).
DEFAULT_LEASE_TIMEOUT_SECONDS: Final[int] = 1800

# --- Approvals (FR-41) -------------------------------------------------------

#: Approval-request lifetime. Long enough for a human, and safe only because
#: approval binds to the proposal digest and triggers re-evaluation (ADR-0015).
DEFAULT_APPROVAL_TTL_SECONDS: Final[int] = 86_400

# --- Critics (FR-51, FR-55) --------------------------------------------------

#: Wall-clock backstop for a critic. The primary bound for a solver is its
#: deterministic resource limit; this only catches a wedged worker.
DEFAULT_CRITIC_TIMEOUT_MS: Final[int] = 200

# --- Operational overrides (FR-48) -------------------------------------------

#: Ceiling on a ``demote_mode`` override. Demotion reduces enforcement, so it is
#: deliberately short-lived: past this window the change must be made properly,
#: as a signed registry change under two-person review, or lapse.
MAX_DEMOTE_MODE_WINDOW_SECONDS: Final[int] = 3600

# --- Evidence (NFR-16) -------------------------------------------------------

#: Decision-record retention. Subject to jurisdictional review (``OQ-05``).
DEFAULT_RECORD_RETENTION_DAYS: Final[int] = 400

# --- Encoding ----------------------------------------------------------------

#: Prefix for every digest the harness emits, so the algorithm travels with the
#: value and a future migration is detectable rather than silent.
DIGEST_PREFIX: Final[str] = "sha256:"
