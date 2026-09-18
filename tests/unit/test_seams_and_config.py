"""Seams make the decision path deterministic; settings never hide a surprise."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from neuroharness import defaults
from neuroharness.config import Settings, SigningAlgorithm, UnregisteredClassPolicy
from neuroharness.errors import ClockUnavailableError, ConfigurationError
from neuroharness.seams import Clock, FrozenClock, IdGenerator, SequenceIdGenerator, SystemClock, UuidGenerator

#: Enough draws that a generator returning a constant, or cycling through a
#: short fixed list, shows up as a duplicate rather than as luck.
UUID_SAMPLE_SIZE = 64


def test_implementations_satisfy_their_protocols() -> None:
    assert isinstance(FrozenClock(datetime.now(timezone.utc)), Clock)
    assert isinstance(SystemClock(), Clock)
    assert isinstance(SequenceIdGenerator(), IdGenerator)
    assert isinstance(UuidGenerator(), IdGenerator)


def test_frozen_clock_only_moves_when_told(clock: FrozenClock) -> None:
    first = clock.now()
    assert clock.now() == first
    assert clock.advance(30) == first.replace(minute=first.minute, second=30)
    assert clock.now() > first


def test_frozen_clock_requires_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="aware"):
        FrozenClock(datetime(2026, 9, 18, 12, 0, 0))


@pytest.mark.parametrize("factory", [lambda: FrozenClock(datetime.now(timezone.utc)), SystemClock])
def test_an_unhealthy_clock_fails_rather_than_guesses(factory) -> None:
    """NFR-13: a guessed time silently revalidates stale facts and expired tokens."""
    source = factory()
    source.set_healthy(False)
    with pytest.raises(ClockUnavailableError):
        source.now()


def test_the_system_clock_reports_aware_utc_time_while_it_is_healthy() -> None:
    """Every window in the harness is a subtraction of two datetimes.

    One of them naive raises at the moment of comparison - inside expiry,
    staleness or retention, on the decision path - and one of them in local time
    silently shifts every window by the host's offset, which revalidates stale
    facts and expired tokens without a symptom. The production clock therefore
    returns an aware UTC value or refuses (``NFR-13``).
    """
    before = datetime.now(timezone.utc)
    observed = SystemClock().now()
    after = datetime.now(timezone.utc)

    assert observed.tzinfo is timezone.utc
    assert before <= observed <= after


def test_uuid_identifiers_are_unique_and_are_actually_uuids() -> None:
    """``record_id``, ``decision_id``, ``action_id`` and ``token_id`` are UUIDs.

    The published schemas type them that way, so a generator that returned some
    other shape would emit records an auditor's validator rejects. One that
    repeated a value would be worse: two decisions would share an identity, and
    the evidence for one would answer questions asked about the other.
    """
    generator = UuidGenerator()
    produced = [generator.new_id() for _ in range(UUID_SAMPLE_SIZE)]

    assert len(set(produced)) == UUID_SAMPLE_SIZE
    assert all(str(UUID(value)) == value for value in produced)


def test_sequence_ids_are_deterministic_and_unique() -> None:
    gen = SequenceIdGenerator("dec")
    produced = [gen.new_id() for _ in range(5)]
    assert produced == [f"dec-{n:08d}" for n in range(1, 6)]
    assert len(set(produced)) == len(produced)


def test_settings_fall_back_to_documented_defaults() -> None:
    from neuroharness import defaults

    settings = Settings.from_env({})
    assert settings.unregistered_class_policy is UnregisteredClassPolicy.STRICT
    assert settings.token_ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS
    assert settings.repair_budget == defaults.DEFAULT_REPAIR_BUDGET


def test_settings_read_the_environment_with_the_prefix() -> None:
    settings = Settings.from_env(
        {
            "NEUROHARNESS_TOKEN_TTL_SECONDS": "30",
            "NEUROHARNESS_UNREGISTERED_CLASS_POLICY": "permissive",
            "NEUROHARNESS_SIGNING_ALGORITHM": "ecdsa-p256",
            "NEUROHARNESS_LOG_JSON": "false",
        }
    )
    assert settings.token_ttl_seconds == 30
    assert settings.unregistered_class_policy is UnregisteredClassPolicy.PERMISSIVE
    assert settings.signing_algorithm is SigningAlgorithm.ECDSA_P256
    assert settings.log_json is False


@pytest.mark.parametrize(
    "environ",
    [
        {"NEUROHARNESS_TOKEN_TTL_SECONDS": "not-a-number"},
        {"NEUROHARNESS_LOG_JSON": "perhaps"},
        {"NEUROHARNESS_REPAIR_BUDGET": "999"},
        {"NEUROHARNESS_LOG_LEVEL": "CHATTY"},
        {"NEUROHARNESS_UNREGISTERED_CLASS_POLICY": "lenient"},
    ],
)
def test_bad_settings_fail_at_startup_not_at_runtime(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env(environ)


def test_a_misspelled_setting_fails_at_startup_instead_of_defaulting() -> None:
    """A typo in a manifest must not leave the operator believing a control is set.

    ``NEUROHARNESS_REPAIR_BUDGT=1`` reads correct to everyone who reviews the
    deployment, and reading only the known fields would let it do nothing while
    the documented default stayed in force. The harness would then run a repair
    budget nobody chose, and the first sign of it would be behaviour that
    contradicts the manifest during an incident. ``extra="forbid"`` cannot
    catch this: a name nobody recognises never reaches the model.
    """
    with pytest.raises(ConfigurationError) as exc:
        Settings.from_env({"NEUROHARNESS_REPAIR_BUDGT": "1"})

    # The operator has to be able to find the typo from the message alone.
    assert "NEUROHARNESS_REPAIR_BUDGT" in str(exc.value)
    assert "NEUROHARNESS_REPAIR_BUDGET" in str(exc.value)


def test_every_unknown_variable_in_the_namespace_is_named_at_once() -> None:
    """Reporting one typo at a time turns a bad manifest into a restart loop.

    Each restart of a fail-closed harness is a window in which nothing is
    evaluated, so the refusal has to be complete the first time.
    """
    with pytest.raises(ConfigurationError) as exc:
        Settings.from_env(
            {"NEUROHARNESS_TOKEN_TTL": "30", "NEUROHARNESS_SIGNING_ALGO": "ed25519"}
        )

    assert "NEUROHARNESS_TOKEN_TTL" in str(exc.value)
    assert "NEUROHARNESS_SIGNING_ALGO" in str(exc.value)


def test_variables_outside_the_namespace_are_not_the_harness_s_business() -> None:
    """The process environment is shared; only the prefix belongs to settings.

    Refusing to start over an unrelated variable would make the harness
    impossible to deploy beside anything else, and a control that cannot be
    deployed gets switched off.
    """
    settings = Settings.from_env(
        {
            "PATH": "/usr/bin",
            "REPAIR_BUDGT": "1",
            "OTHER_HARNESS_REPAIR_BUDGET": "1",
            "NEUROHARNESS_REPAIR_BUDGET": "1",
        }
    )
    assert settings.repair_budget == 1


def test_the_default_signing_algorithm_is_the_one_the_adr_chose() -> None:
    """``ADR-0019``: HMAC-SHA256 is for single-process deployments only.

    The gateway and the broker are separate processes, so a shared-secret
    default hands the broker the key that mints the tokens it exists to verify.
    A broker that can mint its own authorisation is not constrained by one, and
    nothing in a record would show that the separation had never existed.
    """
    assert Settings.from_env({}).signing_algorithm is SigningAlgorithm.ECDSA_P256


def test_settings_are_frozen_and_reject_unknown_fields() -> None:
    """Settings a component can edit are settings nothing downstream can trust.

    Both failures are named precisely rather than caught as ``Exception``: a
    bare catch is satisfied by any error at all, including a ``TypeError`` from
    an unrelated signature change, so it would keep passing after ``frozen`` or
    ``extra="forbid"`` were dropped from the model config. Losing ``frozen``
    lets one component mutate the ttl every other component already read;
    losing ``extra="forbid"`` makes a misspelled environment variable a silent
    no-op, which is an operator running with settings they did not intend.
    """
    settings = Settings.from_env({})
    with pytest.raises(ValidationError, match="frozen"):
        settings.token_ttl_seconds = 1  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(not_a_real_setting=1)  # type: ignore[call-arg]
    # The original value survived the refused assignment.
    assert settings.token_ttl_seconds == defaults.DEFAULT_TOKEN_TTL_SECONDS


def test_an_empty_prefix_is_refused() -> None:
    """Claiming a namespace is only safe when there is one.

    ``from_env`` treats every variable under its prefix as a setting and refuses
    the ones it does not recognise. With an empty prefix that rule turns on the
    whole environment: ``PATH`` and ``HOME`` become unknown settings and no
    deployment starts. The parameter is public, so the guard is a real one
    rather than a comment.
    """
    with pytest.raises(ConfigurationError, match="non-empty prefix"):
        Settings.from_env({"PATH": "/usr/bin"}, prefix="")


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_every_supported_log_level_is_accepted(level: str) -> None:
    """The accept path, not only the reject path.

    A validator tested only on what it refuses can be refusing everything and
    still look correct.
    """
    assert Settings.from_env({"NEUROHARNESS_LOG_LEVEL": level}).log_level == level


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_boolean_settings_accept_the_spellings_an_operator_writes(
    value: str, expected: bool
) -> None:
    """A deployment manifest says ``true``; a shell script says ``1``.

    Refusing either would be a startup failure over a spelling, and a
    fail-closed harness that will not start is an outage.
    """
    assert Settings.from_env({"NEUROHARNESS_LOG_JSON": value}).log_json is expected
