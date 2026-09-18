"""Redaction is enforced by the logger, not by asking callers to remember."""

from __future__ import annotations

import io
import json

import pytest

from neuroharness.observability.logging import (
    REDACTED,
    bind_context,
    configure_logging,
    current_context,
    get_logger,
    redact,
)


@pytest.fixture
def captured() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return stream


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.mark.parametrize(
    "key", ["secret", "signature", "token", "password", "reasoning", "prompt", "model_output"]
)
def test_sensitive_keys_never_reach_the_log(captured: io.StringIO, key: str) -> None:
    get_logger("neuroharness.test").info("event", **{key: "SHOULD-NOT-APPEAR"})
    assert "SHOULD-NOT-APPEAR" not in captured.getvalue()
    assert _lines(captured)[0]["fields"][key] == REDACTED


@pytest.mark.parametrize("key", ["token_id", "key_id", "credential_digest", "prompt_family_digest"])
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
