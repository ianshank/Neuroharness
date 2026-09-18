"""Shared vocabulary: enums and validated value types.

Everything here is used across module boundaries, so it deliberately has no
dependencies beyond :mod:`neuroharness.defaults` and :mod:`neuroharness.errors`.
Keeping this layer thin is what lets the resolver, the token service and the
evidence store be tested in isolation from one another.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Final

from neuroharness.defaults import DIGEST_PREFIX

__all__ = [
    "Verdict",
    "VerifierResult",
    "Mode",
    "EffectClass",
    "FactStatus",
    "ApprovalState",
    "ReceiptStatus",
    "OverrideKind",
    "CriticKind",
    "RuleOutcomeKind",
    "Digest",
    "Principal",
    "PrincipalKind",
]


class Verdict(str, Enum):
    """The harness's decision on an envelope (specification section 5.2).

    ``REPAIR`` is the only non-terminal member. Ordering between members is not
    defined here: it is the safety order, and it lives in
    :mod:`neuroharness.resolve.safety` where the procedure that depends on it
    can be read alongside it.
    """

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    ABSTAIN = "ABSTAIN"
    REPAIR = "REPAIR"

    @property
    def is_terminal(self) -> bool:
        return self is not Verdict.REPAIR

    @property
    def permits_execution(self) -> bool:
        """Only ``ALLOW`` may lead to a token that authorises execution."""
        return self is Verdict.ALLOW


class VerifierResult(str, Enum):
    """Outcome of one critic or policy rule (specification section 5.1)."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def is_indeterminate(self) -> bool:
        """True when the verifier could not decide, which abstains (section 5.3)."""
        return self in {VerifierResult.UNKNOWN, VerifierResult.TIMEOUT, VerifierResult.ERROR}


class Mode(str, Enum):
    """Rollout mode for an action class or critic (``FR-80``, ADR-0016).

    Modes change only whether the broker consults the *verdict*. They never
    disable evaluation, recording or the fail-closed path (``INV-11``).
    """

    SHADOW = "shadow"
    ADVISORY = "advisory"
    ENFORCE = "enforce"
    HALTED = "halted"

    @property
    def blocks_on_verdict(self) -> bool:
        """True when a blocking verdict actually prevents execution."""
        return self in {Mode.ENFORCE, Mode.HALTED}

    @property
    def rank(self) -> int:
        """How enforcing this mode is. Used to reject a critic mode looser than
        its class allows, and stricter than its class permits."""
        return {Mode.SHADOW: 0, Mode.ADVISORY: 1, Mode.ENFORCE: 2, Mode.HALTED: 3}[self]


class EffectClass(str, Enum):
    """What an action can do to the world (``FR-35``)."""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @property
    def is_mutating(self) -> bool:
        return self in {EffectClass.WRITE, EffectClass.DESTRUCTIVE}

    @property
    def takes_fast_path(self) -> bool:
        """Non-mutating classes get policy-only evaluation, never approval."""
        return self in {EffectClass.NONE, EffectClass.READ}


class FactStatus(str, Enum):
    """Freshness of a fact as presented to policy (``FR-11``).

    Only ``FRESH`` facts carry a value. Stale, missing and errored facts arrive
    as status-only stubs so that no rule can read a stale value even if its
    author forgot the freshness check.
    """

    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    PROVIDER_ERROR = "provider_error"

    @property
    def is_usable(self) -> bool:
        return self is FactStatus.FRESH


class ApprovalState(str, Enum):
    """Approval lifecycle (``FR-40``, technical plan section 4.6)."""

    REQUESTED = "requested"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    VOID = "void"
    CONSUMED = "consumed"

    @property
    def is_open(self) -> bool:
        return self in {ApprovalState.REQUESTED, ApprovalState.PENDING}

    @property
    def is_final(self) -> bool:
        return not self.is_open


class ReceiptStatus(str, Enum):
    """Outcome the broker observed (``FR-24``)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REFUSED = "refused"
    DISPATCHED = "dispatched"

    @property
    def is_unresolved(self) -> bool:
        """A retry while the prior outcome is unknown must abstain (``FR-27``)."""
        return self in {ReceiptStatus.TIMEOUT, ReceiptStatus.DISPATCHED}


class OverrideKind(str, Enum):
    """Operational overrides (``FR-48``). None of them grants an ALLOW."""

    HALT_CLASS = "halt_class"
    DEMOTE_MODE = "demote_mode"
    REVOKE_TOKEN = "revoke_token"
    VOID_APPROVAL = "void_approval"
    ROTATE_KEY = "rotate_key"

    @property
    def is_tightening(self) -> bool:
        """Tightening overrides need one principal; loosening needs two."""
        return self is not OverrideKind.DEMOTE_MODE

    @property
    def required_principals(self) -> int:
        return 1 if self.is_tightening else 2


class CriticKind(str, Enum):
    """Implementation family of a critic."""

    REGO = "rego"
    SMT = "smt"
    PROLOG = "prolog"
    MONITOR = "monitor"
    EFFECT = "effect"
    LLM_SOFT = "llm_soft"
    OTHER = "other"


class RuleOutcomeKind(str, Enum):
    """Policy rule outcome, mapped to a verifier result in section 5.1."""

    PASS = "pass"
    DENY = "deny"
    ABSTAIN = "abstain"
    REQUIRES_APPROVAL = "requires_approval"
    NOT_APPLICABLE = "not_applicable"

    def to_verifier_result(self) -> VerifierResult:
        """Map a policy outcome onto the shared verifier vocabulary."""
        return {
            RuleOutcomeKind.PASS: VerifierResult.PASS,
            RuleOutcomeKind.DENY: VerifierResult.FAIL,
            RuleOutcomeKind.ABSTAIN: VerifierResult.UNKNOWN,
            RuleOutcomeKind.REQUIRES_APPROVAL: VerifierResult.PASS,
            RuleOutcomeKind.NOT_APPLICABLE: VerifierResult.NOT_APPLICABLE,
        }[self]


_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")


class Digest(str):
    """A ``sha256:<64 hex>`` digest, validated on construction.

    Subclassing :class:`str` keeps digests comparable, hashable and printable
    while making an unvalidated digest impossible to construct by accident. The
    algorithm travels with the value so a future migration is visible rather
    than silent.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Digest:
        if not isinstance(value, str) or not _DIGEST_PATTERN.match(value):
            raise ValueError(f"not a sha256 digest: {value!r}")
        return super().__new__(cls, value)

    @classmethod
    def from_hex(cls, hex_digest: str) -> Digest:
        return cls(f"{DIGEST_PREFIX}{hex_digest}")

    @property
    def hex(self) -> str:
        return self[len(DIGEST_PREFIX) :]

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


class PrincipalKind(str, Enum):
    """Who or what a principal is."""

    USER = "user"
    SERVICE = "service"
    WORKFLOW = "workflow"
    AGENT = "agent"
    SYSTEM = "system"

    @property
    def is_human(self) -> bool:
        """Only a human principal may approve (``FR-42``)."""
        return self is PrincipalKind.USER


_PRINCIPAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(user|service|workflow|agent|system):[A-Za-z0-9._-]{1,96}$"
)


class Principal(str):
    """A ``kind:identifier`` principal, validated on construction."""

    __slots__ = ()

    def __new__(cls, value: str) -> Principal:
        if not isinstance(value, str) or not _PRINCIPAL_PATTERN.match(value):
            raise ValueError(f"not a principal: {value!r}")
        return super().__new__(cls, value)

    @property
    def kind(self) -> PrincipalKind:
        return PrincipalKind(self.split(":", 1)[0])

    @property
    def identifier(self) -> str:
        return self.split(":", 1)[1]

    @property
    def is_human(self) -> bool:
        return self.kind.is_human

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())
