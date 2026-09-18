"""The closed reason-code catalogue (specification section 5.6).

Reason codes are the harness's machine-readable explanation of every non-ALLOW
verdict. They are deliberately *typed* rather than free strings: a compromised
critic or a hostile fact provider must not be able to deliver instruction-shaped
text to the governed model through the repair channel (``SEC-07``, threat T-11).

This module also encodes two sets the resolution procedure depends on:

* :data:`INFRASTRUCTURE_REASONS` - the fixed non-escalating set (section 5.5).
  These abstentions are terminal; no configuration can turn them into a request
  for human approval, because a human cannot vouch for a policy engine that is
  down or a bundle whose signature does not verify.
* :data:`ESCALATABLE_REASONS` - the only reasons an action class may declare
  escalatable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = [
    "ReasonName",
    "ReasonCode",
    "INFRASTRUCTURE_REASONS",
    "ESCALATABLE_REASONS",
    "PARAMETERISED_REASONS",
    "TokenInvalidReason",
]


class ReasonName(str, Enum):
    """Every reason name the harness may emit. The catalogue is closed."""

    # Rule and critic outcomes
    RULE_FAILED = "RULE_FAILED"
    SOLVER_UNKNOWN = "SOLVER_UNKNOWN"
    SOLVER_TIMEOUT = "SOLVER_TIMEOUT"
    CRITIC_ERROR = "CRITIC_ERROR"
    MONITOR_VIOLATION = "MONITOR_VIOLATION"
    EFFECT_MISMATCH = "EFFECT_MISMATCH"

    # Facts
    FACT_MISSING = "FACT_MISSING"
    FACT_STALE = "FACT_STALE"
    FACT_PROVIDER_ERROR = "FACT_PROVIDER_ERROR"

    # Infrastructure (non-escalating, see INFRASTRUCTURE_REASONS)
    POLICY_ENGINE_UNAVAILABLE = "POLICY_ENGINE_UNAVAILABLE"
    BUNDLE_INTEGRITY_FAILED = "BUNDLE_INTEGRITY_FAILED"
    REGISTRY_INTEGRITY_FAILED = "REGISTRY_INTEGRITY_FAILED"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    CLOCK_UNAVAILABLE = "CLOCK_UNAVAILABLE"
    MONITOR_STATE_LOST = "MONITOR_STATE_LOST"
    HARNESS_UNHEALTHY = "HARNESS_UNHEALTHY"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    ACTION_CLASS_UNREGISTERED = "ACTION_CLASS_UNREGISTERED"
    CLASS_HALTED = "CLASS_HALTED"

    # Repair
    REPAIR_BUDGET_EXHAUSTED = "REPAIR_BUDGET_EXHAUSTED"
    REPAIR_RATE_LIMITED = "REPAIR_RATE_LIMITED"

    # Approval
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_NOT_PERMITTED = "APPROVAL_NOT_PERMITTED"
    APPROVAL_VOID = "APPROVAL_VOID"
    APPROVER_NOT_ELIGIBLE = "APPROVER_NOT_ELIGIBLE"

    # Execution
    RESOURCE_BUSY = "RESOURCE_BUSY"
    RETRY_UNRESOLVED = "RETRY_UNRESOLVED"
    TOKEN_INVALID = "TOKEN_INVALID"
    BATCH_DEPENDENCY_DENIED = "BATCH_DEPENDENCY_DENIED"


class TokenInvalidReason(str, Enum):
    """Why the broker refused a token (``FR-21``)."""

    EXPIRED = "expired"
    CONSUMED = "consumed"
    DIGEST_MISMATCH = "digest_mismatch"
    VERDICT_MISMATCH = "verdict_mismatch"
    MODE_MISMATCH = "mode_mismatch"
    BUNDLE_STALE = "bundle_stale"
    REVOKED = "revoked"
    SIGNATURE = "signature"


#: Abstentions that may never escalate to human approval (section 5.5).
INFRASTRUCTURE_REASONS: Final[frozenset[ReasonName]] = frozenset(
    {
        ReasonName.POLICY_ENGINE_UNAVAILABLE,
        ReasonName.BUNDLE_INTEGRITY_FAILED,
        ReasonName.REGISTRY_INTEGRITY_FAILED,
        ReasonName.EVIDENCE_UNAVAILABLE,
        ReasonName.CLOCK_UNAVAILABLE,
        ReasonName.MONITOR_STATE_LOST,
        ReasonName.HARNESS_UNHEALTHY,
        ReasonName.CRITIC_ERROR,
        ReasonName.SCHEMA_INVALID,
        ReasonName.ACTION_CLASS_UNREGISTERED,
    }
)

#: The only reasons an action class may list in ``escalate_on`` (section 5.5).
ESCALATABLE_REASONS: Final[frozenset[ReasonName]] = frozenset(
    {
        ReasonName.SOLVER_UNKNOWN,
        ReasonName.SOLVER_TIMEOUT,
        ReasonName.FACT_MISSING,
        ReasonName.FACT_STALE,
    }
)

#: Reason names that require a subject (the part after the colon).
PARAMETERISED_REASONS: Final[frozenset[ReasonName]] = frozenset(
    {
        ReasonName.RULE_FAILED,
        ReasonName.SOLVER_UNKNOWN,
        ReasonName.SOLVER_TIMEOUT,
        ReasonName.CRITIC_ERROR,
        ReasonName.MONITOR_VIOLATION,
        ReasonName.EFFECT_MISMATCH,
        ReasonName.FACT_MISSING,
        ReasonName.FACT_STALE,
        ReasonName.FACT_PROVIDER_ERROR,
        ReasonName.APPROVAL_REQUIRED,
        ReasonName.APPROVAL_NOT_PERMITTED,
        ReasonName.APPROVAL_VOID,
        ReasonName.RESOURCE_BUSY,
        ReasonName.RETRY_UNRESOLVED,
        ReasonName.TOKEN_INVALID,
        ReasonName.BATCH_DEPENDENCY_DENIED,
    }
)

# A subject is an identifier, never prose. This is the structural half of
# SEC-07; the gateway additionally validates subjects against the registry.
_SUBJECT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,158}$")
_MAX_RENDERED_LENGTH: Final[int] = 320


@dataclass(frozen=True, slots=True, order=True)
class ReasonCode:
    """A single typed reason, renderable as ``NAME`` or ``NAME:subject``."""

    name: ReasonName
    subject: str | None = None

    def __post_init__(self) -> None:
        requires_subject = self.name in PARAMETERISED_REASONS
        if requires_subject and self.subject is None:
            raise ValueError(f"reason {self.name.value} requires a subject")
        if not requires_subject and self.subject is not None:
            raise ValueError(f"reason {self.name.value} does not take a subject")
        # ``fullmatch``, not ``match``: ``$`` also matches before a final
        # newline, and a subject carrying one forges a line in a JSONL evidence
        # export (``SEC-07``).
        if self.subject is not None and not _SUBJECT_PATTERN.fullmatch(self.subject):
            raise ValueError(
                f"reason subject {self.subject!r} is not an identifier; "
                "reason codes never carry free text (SEC-07)"
            )
        if len(self.render()) > _MAX_RENDERED_LENGTH:
            raise ValueError("rendered reason code exceeds the maximum length")

    def render(self) -> str:
        return self.name.value if self.subject is None else f"{self.name.value}:{self.subject}"

    def __str__(self) -> str:  # pragma: no cover - trivial delegation
        return self.render()

    @property
    def is_infrastructure(self) -> bool:
        """True when this reason may never escalate to human approval."""
        return self.name in INFRASTRUCTURE_REASONS

    @property
    def is_escalatable(self) -> bool:
        """True when an action class is permitted to declare this escalatable."""
        return self.name in ESCALATABLE_REASONS

    @classmethod
    def parse(cls, rendered: str) -> ReasonCode:
        """Parse a rendered reason code. Raises ``ValueError`` on anything else."""
        head, _, tail = rendered.partition(":")
        try:
            name = ReasonName(head)
        except ValueError as exc:
            raise ValueError(f"unknown reason name: {head!r}") from exc
        return cls(name=name, subject=tail if tail else None)

    @classmethod
    def token_invalid(cls, reason: TokenInvalidReason) -> ReasonCode:
        return cls(ReasonName.TOKEN_INVALID, reason.value)
