"""Redaction is enforced by the logger, not by asking callers to remember."""

from __future__ import annotations

import io
import json

import pytest

from neuroharness.observability.logging import (
    REDACTED,
    _SAFE_KEYS,
    _SENSITIVE_KEYS,
    bind_context,
    configure_logging,
    current_context,
    get_logger,
    redact,
)

#: The value a leak test writes. Distinctive enough that its absence from the
#: whole stream, not just from one field, is a meaningful assertion.
CANARY = "SHOULD-NOT-APPEAR"

#: The exact redaction surface (``NFR-18``). Pinned rather than derived from the
#: module, so that removing a field name from the production set fails here
#: instead of silently writing that field's value to the log for the lifetime of
#: the deployment. Adding one is equally deliberate: the new key must be listed
#: here, which is where a reviewer sees it.
EXPECTED_SENSITIVE_KEYS = frozenset(
    {
        "secret",
        "signature",
        "token",
        "token_material",
        "key",
        "private_key",
        "password",
        "authorization",
        "credential",
        "credentials",
        "reasoning",
        "completion",
        "model_output",
        "prompt",
    }
)

#: The exact set of keys that outrank the sensitive set. Pinned for the mirror
#: reason: an identifier or digest quietly added here is a value that stops
#: being investigable, and one quietly removed takes an incident's only handle
#: on the event with it.
EXPECTED_SAFE_KEYS = frozenset(
    {
        "token_id",
        "key_id",
        "key_alg",
        "signing_algorithm",
        "prompt_family_digest",
        "credential_digest",
        "credential_status",
    }
)

#: Spellings a real caller produces: an HTTP header copied verbatim, a
#: constant-style field name, a title-cased attribute lifted from a third-party
#: payload, and a fully upper-cased one from an environment variable.
MIXED_CASE_SENSITIVE_KEYS = (
    "Authorization",
    "SECRET",
    "Signature",
    "Token_Material",
    "PRIVATE_KEY",
    "Completion",
)


@pytest.fixture
def captured(harness_logger_state: None) -> io.StringIO:
    """A stream the harness logger writes to for the duration of one test.

    Takes ``harness_logger_state`` so the handler installed here is removed
    again: a capturing stream left behind on the process-global logger makes the
    next test's evidence assertions read a stream nothing is writing to.
    """
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return stream


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_the_sensitive_key_set_is_the_declared_one() -> None:
    """``NFR-18``: logs carry no secrets, and the set is what makes that true.

    A key dropped from the production set is a value that starts being written
    in full to every log sink the deployment ships to, with no other symptom.
    """
    assert _SENSITIVE_KEYS == EXPECTED_SENSITIVE_KEYS


def test_the_safe_key_set_is_the_declared_one() -> None:
    """These keys outrank the sensitive set, so adding to it is a bypass.

    A name added here is exempt from redaction for good; ``NFR-20`` wants the
    identifiers and digests an operator investigates with, and nothing else.
    """
    assert _SAFE_KEYS == EXPECTED_SAFE_KEYS


@pytest.mark.parametrize("key", sorted(_SENSITIVE_KEYS))
def test_sensitive_keys_never_reach_the_log(captured: io.StringIO, key: str) -> None:
    """Every declared key, not a sample: an unexercised entry is not a control."""
    get_logger("neuroharness.test").info("event", **{key: CANARY})
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["fields"][key] == REDACTED


@pytest.mark.parametrize("key", MIXED_CASE_SENSITIVE_KEYS)
def test_sensitive_keys_are_redacted_whatever_their_case(
    captured: io.StringIO, key: str
) -> None:
    """Field names arrive in whatever case their source used.

    Nothing normalises a caller's keyword before it reaches the redactor, so a
    header logged as ``Authorization`` or a constant logged as ``SECRET`` is
    emitted verbatim unless the match itself is case-insensitive - and it is
    exactly the fields copied from headers and environment variables that carry
    credentials.
    """
    get_logger("neuroharness.test").info("event", **{key: CANARY})
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["fields"][key] == REDACTED


def test_case_insensitivity_reaches_into_nested_structures() -> None:
    """A payload forwarded from another service keeps that service's casing."""
    cleaned = redact({"headers": {"Authorization": CANARY, "X-Request-Id": "req-1"}})
    assert cleaned["headers"]["Authorization"] == REDACTED
    assert cleaned["headers"]["X-Request-Id"] == "req-1"


@pytest.mark.parametrize("key", sorted(_SAFE_KEYS))
def test_identifiers_that_merely_look_sensitive_are_kept(captured: io.StringIO, key: str) -> None:
    """A digest or an identifier is what makes an incident investigable."""
    get_logger("neuroharness.test").info("event", **{key: "value-1"})
    assert _lines(captured)[0]["fields"][key] == "value-1"


def test_redaction_reaches_into_nested_structures() -> None:
    payload = {"outer": {"signature": "abc", "safe": 1}, "list": [{"secret": "x"}]}
    cleaned = redact(payload)
    assert cleaned["outer"]["signature"] == REDACTED
    assert cleaned["outer"]["safe"] == 1
    assert cleaned["list"][0]["secret"] == REDACTED


def test_redaction_bounds_depth_rather_than_recursing_forever() -> None:
    deep: dict = {}
    cursor = deep
    for _ in range(20):
        cursor["next"] = {}
        cursor = cursor["next"]
    assert "[truncated]" in json.dumps(redact(deep))


def test_long_values_are_truncated_not_dropped() -> None:
    result = redact("x" * 5000)
    assert result.endswith("...[truncated]")
    assert len(result) < 5000


def test_bound_context_is_attached_to_every_event(captured: io.StringIO) -> None:
    log = get_logger("neuroharness.test")
    with bind_context(trace_id="0" * 32, tenant_id="t1"):
        with bind_context(action_id="act-1"):
            log.info("inner")
        log.info("outer")
    inner, outer = _lines(captured)
    assert inner["action_id"] == "act-1" and inner["tenant_id"] == "t1"
    assert "action_id" not in outer, "context must not leak past its block"
    assert outer["tenant_id"] == "t1"


def test_context_is_restored_even_when_the_block_raises() -> None:
    before = current_context()
    with pytest.raises(RuntimeError):
        with bind_context(action_id="act-9"):
            raise RuntimeError("boom")
    assert current_context() == before


def test_unknown_context_fields_are_ignored_rather_than_crashing() -> None:
    """A logging call must never be the thing that takes the harness down."""
    with bind_context(not_a_field="x") as ctx:  # type: ignore[arg-type]
        assert not hasattr(ctx, "not_a_field")
