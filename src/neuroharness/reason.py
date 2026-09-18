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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from neuroharness.grammar import (
    BATCH_POSITION_SOURCE,
    CRITIC_ID_SOURCE,
    MAX_REASON_CODE_LENGTH,
    MAX_SUBJECT_LENGTH,
    PROPERTY_ID_SOURCE,
    RESOURCE_KEY_SOURCE,
    RULE_ID_SOURCE,
    SNAKE_SOURCE,
    SUBJECT_PATTERN,
    UUID_SOURCE,
    anchored,
)

__all__ = [
    "ReasonName",
    "ReasonCode",
    "INFRASTRUCTURE_REASONS",
    "ESCALATABLE_REASONS",
    "PARAMETERISED_REASONS",
    "TokenInvalidReason",
    "SUBJECT_GRAMMAR",
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


#: The subject grammar for each parameterised reason, in one place.
#:
#: This used to live only in ``models.record``, applied at the record boundary,
#: while :class:`ReasonCode` checked the *generic* subject pattern at
#: construction. A reason code whose subject was identifier-shaped but wrong for
#: its name therefore constructed happily and failed hundreds of lines later,
#: when the record was written - and by then the failure is a
#: ``RecordNotConstructibleError``, which the pipeline turns into
#: ``ABSTAIN(SCHEMA_INVALID)`` with nothing recorded.
#:
#: That was not theoretical. The resolver's fallback for a hard critic that
#: FAILs without supplying its own reason is ``RULE_FAILED:<critic_id>``, and
#: the record catalogue required a *rule id* after ``RULE_FAILED``. So a correct
#: hard ``DENY`` - the system working exactly as designed - became an
#: unrecorded harness fault, and an unrecorded decision was not made
#: (Constitution Art. III). ``RULE_FAILED`` now admits either identifier,
#: because either is what genuinely names the rule that failed: its requirement
#: id when the critic declares one, its critic id when it does not. The two
#: grammars are disjoint - one starts uppercase, the other lowercase - so a
#: reader can always tell which they are looking at.
SUBJECT_GRAMMAR: Final[Mapping[ReasonName, str]] = MappingProxyType(
    {
        ReasonName.RULE_FAILED: f"{RULE_ID_SOURCE}|{CRITIC_ID_SOURCE}",
        ReasonName.SOLVER_UNKNOWN: CRITIC_ID_SOURCE,
        ReasonName.SOLVER_TIMEOUT: CRITIC_ID_SOURCE,
        ReasonName.CRITIC_ERROR: CRITIC_ID_SOURCE,
        ReasonName.MONITOR_VIOLATION: PROPERTY_ID_SOURCE,
        ReasonName.EFFECT_MISMATCH: RESOURCE_KEY_SOURCE,
        ReasonName.FACT_MISSING: SNAKE_SOURCE,
        ReasonName.FACT_STALE: SNAKE_SOURCE,
        ReasonName.FACT_PROVIDER_ERROR: SNAKE_SOURCE,
        ReasonName.APPROVAL_REQUIRED: RULE_ID_SOURCE,
        ReasonName.APPROVAL_NOT_PERMITTED: RULE_ID_SOURCE,
        ReasonName.APPROVAL_VOID: UUID_SOURCE,
        ReasonName.RESOURCE_BUSY: RESOURCE_KEY_SOURCE,
        ReasonName.RETRY_UNRESOLVED: UUID_SOURCE,
        ReasonName.BATCH_DEPENDENCY_DENIED: BATCH_POSITION_SOURCE,
        ReasonName.TOKEN_INVALID: "|".join(member.value for member in TokenInvalidReason),
    }
)


#: The same map, compiled, for the construction-time check. Private because the
#: *source* is what ``models.record`` and the published schemas need - a
#: compiled pattern would have to be taken apart again to be embedded in the
#: reason-code alternation, and string surgery on a regex is how a fifth
#: spelling of the grammar would appear.
_COMPILED_SUBJECT_GRAMMAR: Final[Mapping[ReasonName, re.Pattern[str]]] = MappingProxyType(
    {name: anchored(source) for name, source in SUBJECT_GRAMMAR.items()}
)


def _assert_subject_grammar_is_total() -> None:
    """Every reason that takes a subject declares what that subject looks like.

    Import-time, like the resolver's reason-table check and the grammar
    module's charset check. A parameterised reason with no entry would fall
    back to the generic identifier pattern, which is exactly the gap that let
    ``RULE_FAILED:<critic_id>`` be built and not recorded.
    """
    missing = sorted(name.value for name in PARAMETERISED_REASONS if name not in SUBJECT_GRAMMAR)
    if missing:
        raise ValueError(
            f"parameterised reasons {missing} have no subject grammar, so their "
            "subjects are checked only at the record boundary (section 5.6, SEC-07)"
        )
    stray = sorted(name.value for name in SUBJECT_GRAMMAR if name not in PARAMETERISED_REASONS)
    if stray:
        raise ValueError(
            f"reasons {stray} declare a subject grammar but take no subject"
        )


def _assert_every_reason_code_fits() -> None:
    """The rendered-length guards are defence in depth, and that is now checked.

    :class:`ReasonCode` and ``models.record`` both refuse a rendered code longer
    than :data:`MAX_REASON_CODE_LENGTH`. Neither guard can fire: the longest
    name is 25 characters, a subject is bounded at
    :data:`~neuroharness.grammar.MAX_SUBJECT_LENGTH`, so the longest possible
    rendering is 185 against a bound of 320.

    The increment-1 review filed both under "provably unreachable, exclude from
    the coverage gate". Shaping the code to reach the number would be worse than
    the gap, and deleting the guards would leave nothing between a future
    longer reason name and an over-length record. So the arithmetic is asserted
    instead: add a reason name long enough to close the gap, or raise
    ``MAX_SUBJECT_LENGTH``, and this fires at import rather than the guards
    quietly becoming live on the path that needed them.
    """
    longest_name = max(len(name.value) for name in ReasonName)
    longest_rendered = longest_name + len(":") + MAX_SUBJECT_LENGTH
    if longest_rendered > MAX_REASON_CODE_LENGTH:
        raise ValueError(
            f"a reason code can now render to {longest_rendered} characters against a "
            f"bound of {MAX_REASON_CODE_LENGTH}. The length guards in ReasonCode and "
            "models.record are no longer unreachable: give them tests, and check "
            "whether the record schema's maxLength still holds (SEC-07)."
        )




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
#
# Both come from `neuroharness.grammar`, which is also where `models.record`
# and the published JSON Schemas get them. They used to be spelled here and
# restated there, and the two spellings had drifted: a resource key this
# module would happily carry as a subject was one the record model refused.
_SUBJECT_PATTERN: Final = SUBJECT_PATTERN
_MAX_RENDERED_LENGTH: Final[int] = MAX_REASON_CODE_LENGTH


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
        if self.subject is not None:
            if not _SUBJECT_PATTERN.fullmatch(self.subject):
                raise ValueError(
                    f"reason subject {self.subject!r} is not an identifier; "
                    "reason codes never carry free text (SEC-07)"
                )
            # And the shape this *particular* name requires. Checked here rather
            # than only at the record boundary: a code that cannot be recorded
            # must not be constructible, or the refusal lands as an
            # ABSTAIN(SCHEMA_INVALID) with nothing written down.
            shape = _COMPILED_SUBJECT_GRAMMAR.get(self.name)
            if shape is not None and not shape.fullmatch(self.subject):
                raise ValueError(
                    f"reason subject {self.subject!r} is not a valid subject for "
                    f"{self.name.value} (section 5.6); expected {shape.pattern}"
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


# --- Import-time invariants ---------------------------------------------------
#
# At the foot of the module, so everything they check is defined. Each converts
# something that used to be true by coincidence into something that is checked:
# a reason name long enough to reach the length bound, or a parameterised reason
# with no declared subject shape, now stops the process at import rather than
# surfacing as an unrecordable decision in production.
_assert_every_reason_code_fits()
_assert_subject_grammar_is_total()
