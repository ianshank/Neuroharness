"""Typed exception hierarchy.

Every error the decision path can raise is a subclass of :class:`FailClosedError`
and carries the reason code it maps to. That pairing is the point: Constitution
Article II says any inability to evaluate ends in no execution, so an exception
type that cannot name its reason code is an exception that cannot be recorded,
and an unrecorded decision was not made (Article III).
"""

from __future__ import annotations

from neuroharness.reason import ReasonCode, ReasonName, TokenInvalidReason

__all__ = [
    "NeuroharnessError",
    "ConfigurationError",
    "FailClosedError",
    "SchemaVersionError",
    "CanonicalizationError",
    "RegistryError",
    "RegistryValidationError",
    "RegistryIntegrityError",
    "UnregisteredActionClassError",
    "ClassHaltedError",
    "EvidenceUnavailableError",
    "TokenError",
    "TokenSignatureError",
    "TokenExpiredError",
    "TokenConsumedError",
    "TokenDigestMismatchError",
    "TokenVerdictMismatchError",
    "TokenModeMismatchError",
    "TokenBundleStaleError",
    "TokenRevokedError",
    "ClockUnavailableError",
]


class NeuroharnessError(Exception):
    """Base class for every error this package raises."""


class ConfigurationError(NeuroharnessError):
    """Operator error: the harness was assembled or configured incorrectly.

    Distinct from :class:`FailClosedError` because it is not a runtime decision
    outcome; it means the process should refuse to start rather than run in a
    state whose behaviour is undefined.
    """


class FailClosedError(NeuroharnessError):
    """An error that must result in no execution.

    ``reason_code`` is what the decision record will carry.
    """

    reason_name: ReasonName = ReasonName.HARNESS_UNHEALTHY

    def __init__(self, message: str, *, reason_code: ReasonCode | None = None) -> None:
        super().__init__(message)
        self._reason_code = reason_code

    @property
    def reason_code(self) -> ReasonCode:
        if self._reason_code is not None:
            return self._reason_code
        return ReasonCode(self.reason_name)


class SchemaVersionError(FailClosedError):
    """A document declared a schema version this build cannot read."""

    reason_name = ReasonName.SCHEMA_INVALID

    def __init__(
        self, *, schema_kind: str, found_version: str, supported_versions: list[str]
    ) -> None:
        self.schema_kind = schema_kind
        self.found_version = found_version
        self.supported_versions = supported_versions
        super().__init__(
            f"cannot read {schema_kind} schema version {found_version!r}; "
            f"this build reads {', '.join(supported_versions)}"
        )


class CanonicalizationError(FailClosedError):
    """A value could not be canonicalised, so it has no stable identity."""

    reason_name = ReasonName.SCHEMA_INVALID


class RegistryError(FailClosedError):
    """Base for action-class and resource-key registry failures."""

    reason_name = ReasonName.REGISTRY_INTEGRITY_FAILED


class RegistryValidationError(RegistryError):
    """The registry document is internally inconsistent and was refused."""


class RegistryIntegrityError(RegistryError):
    """The registry failed its signature or digest check."""


class UnregisteredActionClassError(RegistryError):
    """No registry entry exists for this (tool, intent) under strict policy."""

    reason_name = ReasonName.ACTION_CLASS_UNREGISTERED

    def __init__(self, tool: str, intent: str) -> None:
        self.tool = tool
        self.intent = intent
        super().__init__(f"action class ({tool!r}, {intent!r}) is not registered")


class ClassHaltedError(FailClosedError):
    """An operator has halted this action class (``FR-49``)."""

    reason_name = ReasonName.CLASS_HALTED


class EvidenceUnavailableError(FailClosedError):
    """The decision record could not be made durable, so no token may exist."""

    reason_name = ReasonName.EVIDENCE_UNAVAILABLE


class ClockUnavailableError(FailClosedError):
    """The trusted clock is unreachable, so ages and expiries cannot be judged."""

    reason_name = ReasonName.CLOCK_UNAVAILABLE


class TokenError(FailClosedError):
    """Base for every reason the broker refuses a decision token."""

    token_reason: TokenInvalidReason = TokenInvalidReason.SIGNATURE

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code=ReasonCode.token_invalid(self.token_reason))


class TokenSignatureError(TokenError):
    token_reason = TokenInvalidReason.SIGNATURE


class TokenExpiredError(TokenError):
    token_reason = TokenInvalidReason.EXPIRED


class TokenConsumedError(TokenError):
    token_reason = TokenInvalidReason.CONSUMED


class TokenDigestMismatchError(TokenError):
    token_reason = TokenInvalidReason.DIGEST_MISMATCH


class TokenVerdictMismatchError(TokenError):
    token_reason = TokenInvalidReason.VERDICT_MISMATCH


class TokenModeMismatchError(TokenError):
    token_reason = TokenInvalidReason.MODE_MISMATCH


class TokenBundleStaleError(TokenError):
    token_reason = TokenInvalidReason.BUNDLE_STALE


class TokenRevokedError(TokenError):
    token_reason = TokenInvalidReason.REVOKED
