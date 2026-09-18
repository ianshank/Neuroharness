"""Every decision-path error names the reason code its record will carry."""

from __future__ import annotations

import inspect

import pytest

from neuroharness import errors
from neuroharness.errors import (
    ClassHaltedError,
    ClockUnavailableError,
    EvidenceUnavailableError,
    FailClosedError,
    SchemaVersionError,
    TokenConsumedError,
    TokenDigestMismatchError,
    TokenError,
    TokenModeMismatchError,
    UnregisteredActionClassError,
)
from neuroharness.reason import ReasonName, TokenInvalidReason


def _fail_closed_subclasses() -> list[type[FailClosedError]]:
    return [
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, FailClosedError) and obj is not FailClosedError
    ]


@pytest.mark.parametrize("cls", _fail_closed_subclasses(), ids=lambda c: c.__name__)
def test_every_fail_closed_error_declares_a_reason(cls: type[FailClosedError]) -> None:
    """Article III: a decision that cannot be recorded was not made."""
    assert isinstance(cls.reason_name, ReasonName)


def test_schema_version_error_reports_what_it_can_read() -> None:
    err = SchemaVersionError(
        schema_kind="action-envelope", found_version="9.9", supported_versions=["1.1"]
    )
    assert err.reason_code.name is ReasonName.SCHEMA_INVALID
    assert "9.9" in str(err) and "1.1" in str(err)


def test_unregistered_action_class_names_the_pair() -> None:
    err = UnregisteredActionClassError("dns.update", "rotate_record")
    assert err.reason_code.name is ReasonName.ACTION_CLASS_UNREGISTERED
    assert err.tool == "dns.update" and err.intent == "rotate_record"


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        (TokenConsumedError, TokenInvalidReason.CONSUMED),
        (TokenDigestMismatchError, TokenInvalidReason.DIGEST_MISMATCH),
        (TokenModeMismatchError, TokenInvalidReason.MODE_MISMATCH),
    ],
)
def test_token_errors_carry_their_precise_refusal_reason(
    cls: type[TokenError], expected: TokenInvalidReason
) -> None:
    code = cls("refused").reason_code
    assert code.name is ReasonName.TOKEN_INVALID
    assert code.subject == expected.value


@pytest.mark.parametrize(
    ("cls", "name"),
    [
        (ClassHaltedError, ReasonName.CLASS_HALTED),
        (EvidenceUnavailableError, ReasonName.EVIDENCE_UNAVAILABLE),
        (ClockUnavailableError, ReasonName.CLOCK_UNAVAILABLE),
    ],
)
def test_named_failures_map_to_their_reason(cls: type[FailClosedError], name: ReasonName) -> None:
    assert cls("x").reason_code.name is name


def test_configuration_error_is_not_a_decision_outcome() -> None:
    """Misconfiguration means refuse to start, not abstain at runtime."""
    assert not issubclass(errors.ConfigurationError, FailClosedError)
