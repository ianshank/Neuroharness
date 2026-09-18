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
from neuroharness.reason import (
    INFRASTRUCTURE_REASONS,
    ReasonCode,
    ReasonName,
    TokenInvalidReason,
)
from neuroharness.version import SchemaKind


#: Errors whose constructors ask for more than a message.
_CONSTRUCTORS = {
    "SchemaVersionError": lambda cls: cls(
        schema_kind=SchemaKind.ENVELOPE, found_version="9.9", supported_versions=["1.1"]
    ),
    "UnregisteredActionClassError": lambda cls: cls("dns.update", "rotate_record"),
}

#: The rendered reason code each fail-closed error puts in the record, by class.
#:
#: Pinned as a table rather than derived from the classes, because the reason
#: code *is* the record (Art. III). A type check - "it declares some reason" -
#: passes just as happily when an error is remapped to the wrong one, and every
#: judgement downstream reads the reason and not the exception type: whether an
#: abstention may escalate to a human (5.5), whether it counts as
#: infrastructure, what an operator is paged about. A new error class fails this
#: test until its line is written here, which is where the mapping gets reviewed
#: instead of inherited by accident.
#:
#: The token entries are the reason this table records the *rendered code* and
#: not ``reason_name``: they carry a parameterised ``TOKEN_INVALID:<why>`` code
#: built in :class:`TokenError`, while their inherited ``reason_name`` attribute
#: still reads ``HARNESS_UNHEALTHY``. Anything reading the attribute instead of
#: the code would label every broker refusal a harness fault.
EXPECTED_REASON_CODES = {
    "CanonicalizationError": "SCHEMA_INVALID",
    "ClassHaltedError": "CLASS_HALTED",
    "ClockUnavailableError": "CLOCK_UNAVAILABLE",
    "EvidenceUnavailableError": "EVIDENCE_UNAVAILABLE",
    "RegistryError": "REGISTRY_INTEGRITY_FAILED",
    "RegistryIntegrityError": "REGISTRY_INTEGRITY_FAILED",
    "RegistryValidationError": "REGISTRY_INTEGRITY_FAILED",
    "SchemaVersionError": "SCHEMA_INVALID",
    "TokenBundleStaleError": "TOKEN_INVALID:bundle_stale",
    "TokenConsumedError": "TOKEN_INVALID:consumed",
    "TokenDigestMismatchError": "TOKEN_INVALID:digest_mismatch",
    "TokenError": "TOKEN_INVALID:signature",
    "TokenExpiredError": "TOKEN_INVALID:expired",
    "TokenModeMismatchError": "TOKEN_INVALID:mode_mismatch",
    "TokenRevokedError": "TOKEN_INVALID:revoked",
    "TokenSignatureError": "TOKEN_INVALID:signature",
    "TokenVerdictMismatchError": "TOKEN_INVALID:verdict_mismatch",
    "UnregisteredActionClassError": "ACTION_CLASS_UNREGISTERED",
}


def _fail_closed_subclasses() -> list[type[FailClosedError]]:
    return [
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, FailClosedError) and obj is not FailClosedError
    ]


def _instantiate(cls: type[FailClosedError]) -> FailClosedError:
    return _CONSTRUCTORS.get(cls.__name__, lambda c: c("refused"))(cls)


def test_every_fail_closed_error_records_the_reason_it_is_declared_to() -> None:
    """Article III: a decision that cannot be recorded was not made.

    Equality against the whole table, in one assertion, so that both halves are
    forced: an error class added without a reason fails because it is missing
    from the table, and an error remapped to a different reason fails because
    its value changed. Either would otherwise ship silently, and the record it
    produces is the only account of the refusal anyone ever gets.
    """
    discovered = {
        cls.__name__: _instantiate(cls).reason_code.render()
        for cls in _fail_closed_subclasses()
    }
    assert discovered == EXPECTED_REASON_CODES


@pytest.mark.parametrize("cls", _fail_closed_subclasses(), ids=lambda c: c.__name__)
def test_every_fail_closed_error_declares_a_reason(cls: type[FailClosedError]) -> None:
    """A reason code that is not a catalogue member cannot be put in a record.

    ``SEC-07`` closes the catalogue precisely so that no free text reaches a
    record or the repair channel, so the declared reason has to be a
    :class:`ReasonName`, not a string that happens to look like one.
    """
    assert isinstance(cls.reason_name, ReasonName)
    assert _instantiate(cls).reason_code.name in ReasonName


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


# --- a token refusal is not an infrastructure failure ------------------------


def _token_error_classes() -> list[type[TokenError]]:
    found: list[type[TokenError]] = []
    pending = [TokenError]
    while pending:
        cls = pending.pop()
        found.append(cls)
        pending.extend(cls.__subclasses__())
    return sorted(found, key=lambda cls: cls.__name__)


@pytest.mark.parametrize("cls", _token_error_classes(), ids=lambda cls: cls.__name__)
def test_a_token_refusal_is_never_declared_an_infrastructure_reason(
    cls: type[TokenError],
) -> None:
    """Section 5.5's infrastructure set is terminal and never escalates.

    A token error means a binding did not hold: the envelope was edited, the
    nonce was spent, the class mode moved. That is the harness working. Declaring
    it ``HARNESS_UNHEALTHY`` -- which the base class used to do by inheritance —
    would file a successful control as an outage, and would give it the
    escalation behaviour of an outage, which is the opposite of what section 5.5
    intends by putting engine and bundle failures in that set.
    """
    assert cls.reason_name is ReasonName.TOKEN_INVALID
    assert cls.reason_name not in INFRASTRUCTURE_REASONS


@pytest.mark.parametrize("cls", _token_error_classes(), ids=lambda cls: cls.__name__)
def test_a_token_refusal_names_which_binding_failed(cls: type[TokenError]) -> None:
    """``TOKEN_INVALID`` alone tells an operator nothing; the subject is the report."""
    code = cls("refused").reason_code
    assert code.name is ReasonName.TOKEN_INVALID
    assert code.subject == cls.token_reason.value
    assert code.render() == f"TOKEN_INVALID:{cls.token_reason.value}"
    assert not code.is_infrastructure


def test_the_unparameterised_fallback_refuses_rather_than_guessing() -> None:
    """The path that would render a bare ``TOKEN_INVALID`` is not silently allowed.

    ``TokenError.__init__`` always supplies the parameterised code, so this is
    unreachable in the harness. It is asserted because the alternative design —
    a name that renders without a subject -- would let a refusal be recorded as
    "the token was invalid" with no way to say why, and a reason code that
    cannot be acted on is the thing the closed catalogue exists to prevent.
    """
    with pytest.raises(ValueError, match="requires a subject"):
        ReasonCode(ReasonName.TOKEN_INVALID)
