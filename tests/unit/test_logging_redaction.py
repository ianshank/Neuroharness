"""Redaction is enforced by the logger, not by asking callers to remember."""

from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path

import pytest

from neuroharness.errors import EvidenceUnavailableError
from neuroharness.observability.logging import (
    REDACTED,
    REDACTED_EVENT,
    _JsonFormatter,
    _MAX_EVENT_LENGTH,
    _MAX_EXCEPTION_CAUSES,
    _scrub_event,
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


#: The package root, scanned to check that every call site already writes an
#: event code. The rule is only cheap to keep because that is true.
SRC = Path(__file__).resolve().parents[2] / "src" / "neuroharness"

#: The levels a logger is called at, and so the calls the scan looks for.
LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical"})

#: Shapes the package writes today: a dotted namespace, a deep one, and the flat
#: underscored form the token service uses.
EVENT_CODES_IN_USE = ("pipeline.evaluated", "evidence.wal.replay.failed", "token_issued")

#: Ways free text arrives in an event, each carrying the canary so its absence
#: from the stream means something: a sentence, an interpolated identifier, a
#: code with a rendered value stuck on the end, an opaque value logged as the
#: event itself, and one too long to be a name.
NOT_EVENT_CODES = (
    f"leaked secret {CANARY}",
    f"refused action {CANARY}",
    f"pipeline.evaluated: {CANARY}",
    CANARY,
    CANARY + "e" * _MAX_EVENT_LENGTH,
)


@pytest.fixture
def captured_text(harness_logger_state: None) -> io.StringIO:
    """The human-readable sink, which ``configure_logging`` also installs."""
    stream = io.StringIO()
    configure_logging(level="DEBUG", json_output=False, stream=stream)
    return stream


def _logger_call_events(tree: ast.Module) -> list[tuple[int, ast.expr]]:
    """Return the first argument of every logger call in a parsed module."""
    literals: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                literals[target.id] = node.value.value

    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in LOGGER_METHODS or not node.args:
            continue
        if "log" not in ast.unparse(node.func.value).lower():
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Name) and argument.id in literals:
            argument = ast.Constant(value=literals[argument.id])
        found.append((node.lineno, argument))
    return found


def _non_code_events(path: Path) -> list[str]:
    """Report every logger call in ``path`` whose event is not a literal code."""
    offences: list[str] = []
    for lineno, argument in _logger_call_events(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(argument, ast.Name):
            continue  # A name from elsewhere; the sink is what guards that one.
        if not isinstance(argument, ast.Constant) or _scrub_event(argument.value)[1]:
            offences.append(f"{path.name}:{lineno} {ast.unparse(argument)}")
    return offences


@pytest.mark.parametrize("event", NOT_EVENT_CODES, ids=lambda e: repr(e[:24]))
def test_free_text_in_an_event_never_reaches_the_sink(
    captured: io.StringIO, event: str
) -> None:
    """``NFR-18``: the event is a sink-bound value like any field, and less guarded.

    Nothing stops a caller writing ``log.info(f"refused {value}")``, and the
    event is the part a human reads, so it is the part somebody makes
    descriptive - with an argument, a credential or a model completion in it.
    While fields were redacted and the event was not, that one interpolation
    shipped verbatim to every sink the deployment feeds, which is the leak the
    field redaction exists to prevent.
    """
    get_logger("neuroharness.test").info(event)
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["event"] == REDACTED_EVENT


def test_an_empty_event_is_replaced_rather_than_emitted_blank(
    captured: io.StringIO,
) -> None:
    """An event nobody can search for is a record nobody will find.

    An empty event is not a code, and emitting it would leave a line in the
    stream that no dashboard groups and no query matches - which is the same
    outcome as dropping it, arrived at without anyone deciding to.
    """
    get_logger("neuroharness.test").info("")
    assert _lines(captured)[0]["event"] == REDACTED_EVENT


def test_a_replaced_event_keeps_its_record_and_says_so(captured: io.StringIO) -> None:
    """Article IV: a logging path that swallows its own input loses evidence.

    The record whose event had to be replaced is the one an operator most needs
    to find. Dropping it would take the decision it belongs to out of the
    timeline, leaving no trace that anything happened at all; so everything but
    the text survives, and the substitution is stated rather than silent.
    """
    with bind_context(trace_id="0" * 32, action_id="act-1"):
        get_logger("neuroharness.test").warning(f"denied {CANARY}", token_id="tok-1")
    line = _lines(captured)[0]
    assert line["event_redacted"] is True
    assert line["level"] == "WARNING"
    assert line["trace_id"] == "0" * 32 and line["action_id"] == "act-1"
    assert line["fields"]["token_id"] == "tok-1"


@pytest.mark.parametrize("event", EVENT_CODES_IN_USE)
def test_event_codes_are_emitted_unchanged(captured: io.StringIO, event: str) -> None:
    """The rule must cost nothing, or the next event gets written around it.

    If a legitimate event came out altered, callers would learn to distrust the
    event field, and the pressure would be to relax the rule rather than to name
    the event - which is how the guard stops being a guard.
    """
    get_logger("neuroharness.test").info(event)
    line = _lines(captured)[0]
    assert line["event"] == event
    assert "event_redacted" not in line


def test_the_package_writes_only_event_codes() -> None:
    """The rule is enforceable only because every call site already obeys it.

    This is where a caller finds out: an event assembled at the call site fails
    the build, instead of turning into a placeholder in production on the day
    something goes wrong. If it ever fires, the fix is to name the event, which
    is what the rule wanted.
    """
    modules = sorted(SRC.rglob("*.py"))
    assert modules, "no package modules were scanned; this test proves nothing"
    offences = [offence for path in modules for offence in _non_code_events(path)]
    assert not offences, "events must be literal codes, not assembled text:\n  " + "\n  ".join(
        offences
    )


def test_the_call_site_scan_catches_an_assembled_event(tmp_path: Path) -> None:
    """The scan must actually catch something, or it proves nothing."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "def refuse(_LOG, action_id):\n"
        "    _LOG.warning(f'refused {action_id}')\n",
        encoding="utf-8",
    )
    assert _non_code_events(planted)


def test_the_call_site_scan_reaches_the_real_call_sites() -> None:
    """A scan that matched nothing would pass while every call site leaked."""
    calls = [
        call
        for path in sorted(SRC.rglob("*.py"))
        for call in _logger_call_events(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert len(calls) > len(EVENT_CODES_IN_USE), "the logger-call pattern no longer matches"


def test_an_exception_message_never_reaches_the_sink(captured: io.StringIO) -> None:
    """``NFR-18``: a traceback is free text arriving by the other route.

    The harness quotes envelope fields, registry entries and caller-supplied
    values into the errors it raises, and an ``exc_info`` call rendered the whole
    traceback verbatim - so every value any error message interpolated was
    written to the log, with no field name to catch it.
    """
    try:
        raise ValueError(f"envelope contained {CANARY}")
    except ValueError:
        get_logger("neuroharness.test").error("token_issue_refused", exc_info=True)
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["error"]["type"] == "ValueError"


def test_the_exception_description_keeps_what_a_responder_needs(
    captured: io.StringIO,
) -> None:
    """``NFR-20``: a record with the failure removed is not explainable.

    Refusing the message must not cost the diagnosis, or the next incident gets
    debugged by turning the redaction off. Type, reason code and frames are the
    parts that say what broke and where, and none of them is text a caller
    writes.
    """
    try:
        raise EvidenceUnavailableError("record for act-1 could not be made durable")
    except EvidenceUnavailableError:
        get_logger("neuroharness.test").error("pipeline.evidence_unavailable", exc_info=True)
    error = _lines(captured)[0]["error"]
    innermost = error["frames"][-1]
    assert error["type"] == "EvidenceUnavailableError"
    assert error["reason_code"] == "EVIDENCE_UNAVAILABLE"
    assert innermost["func"] == test_the_exception_description_keeps_what_a_responder_needs.__name__
    assert innermost["file"].endswith("test_logging_redaction.py")
    assert "durable" not in json.dumps(error)


def test_chained_exceptions_are_named_without_repeating_what_they_said(
    captured: io.StringIO,
) -> None:
    """A wrapped failure hides its cause's message inside the outer traceback.

    ``raise TokenError(...) from exc`` renders both messages, so redacting only
    the outer one leaves the inner value in the log. The chain is still worth
    recording - it is the difference between a signature error and the parse
    failure under it - so its types are kept and its text is not.
    """
    try:
        try:
            raise ValueError(f"inner {CANARY}")
        except ValueError as exc:
            raise EvidenceUnavailableError("outer") from exc
    except EvidenceUnavailableError:
        get_logger("neuroharness.test").error("pipeline.evidence_unavailable", exc_info=True)
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["error"]["caused_by"] == ["ValueError"]


def test_the_cause_chain_is_bounded(captured: io.StringIO) -> None:
    """A re-raising handler can chain exceptions to any depth, or in a loop.

    ``__context__`` points back at itself when a handler raises inside its own
    ``except``, so an unbounded walk turns one log call into a hang or a record
    big enough to cost the sink.
    """
    error = ValueError("head")
    cursor = error
    for _ in range(_MAX_EXCEPTION_CAUSES * 2):
        nested = ValueError("link")
        cursor.__cause__ = nested
        cursor = nested
    cursor.__cause__ = error  # close the loop
    get_logger("neuroharness.test").error(
        "token_issue_refused", exc_info=(type(error), error, None)
    )
    assert len(_lines(captured)[0]["error"]["caused_by"]) == _MAX_EXCEPTION_CAUSES


def test_a_reason_code_that_cannot_render_still_leaves_a_record(
    captured: io.StringIO,
) -> None:
    """Describing a failure must not become a second failure.

    An error whose ``reason_code`` raises is already a bad day; if reading it
    also took down the log call, the failure that caused it would go unrecorded
    and the harness would look healthy at the moment it was not.
    """

    class _Broken(Exception):
        @property
        def reason_code(self) -> object:
            raise RuntimeError("reason code unavailable")

    error = _Broken()
    get_logger("neuroharness.test").error(
        "token_issue_refused", exc_info=(type(error), error, None)
    )
    line = _lines(captured)[0]
    assert line["error"]["type"].endswith("_Broken")
    assert "reason_code" not in line["error"]


def test_exc_info_without_a_live_exception_still_leaves_a_record(
    captured: io.StringIO,
) -> None:
    """``exc_info=True`` outside an ``except`` block hands the sink three Nones.

    It is a caller mistake, and a formatter that raised on it would lose the
    event the caller was trying to report - the fail-closed path's own error
    logging is exactly where this gets written.
    """
    get_logger("neuroharness.test").error("token_issue_refused", exc_info=True)
    assert _lines(captured)[0]["error"]["type"] == "unknown"


def test_a_message_whose_arguments_do_not_match_still_leaves_a_record() -> None:
    """Rendering a record must not be able to lose it.

    ``logging`` swallows a formatter's exception and prints its own error to
    stderr, so a bad ``%`` argument in a call made anywhere under the harness
    logger would quietly remove that event from the JSON stream an audit reads.
    Formatted directly, because the pytest capture handler fails the record
    before the harness sink ever sees it.
    """
    record = logging.LogRecord(
        "neuroharness.foreign", logging.INFO, __file__, 1, "count %d", (CANARY,), None
    )
    payload = json.loads(_JsonFormatter().format(record))
    assert payload["event"] == REDACTED_EVENT
    assert payload["event_redacted"] is True
    assert CANARY not in json.dumps(payload)


def test_a_non_string_event_does_not_take_the_logging_path_down(
    captured: io.StringIO,
) -> None:
    """``log.error(exc)`` is a reflex, and it is how an error message becomes an event.

    The object's text is not an event code and must not be written; raising on
    it instead would turn a logging mistake in an error handler into the failure
    the handler was reporting.
    """
    get_logger("neuroharness.test").error(ValueError(CANARY))  # type: ignore[arg-type]
    assert CANARY not in captured.getvalue()
    assert _lines(captured)[0]["event"] == REDACTED_EVENT


def test_the_human_readable_sink_applies_the_same_rules(
    captured_text: io.StringIO,
) -> None:
    """``json_output=False`` is a supported sink, not a debug-only exemption.

    It rendered ``%(message)s`` and the raw traceback, so the same run that was
    safe as JSON leaked the event text and every exception message the moment an
    operator asked for readable output.
    """
    try:
        raise ValueError(f"envelope contained {CANARY}")
    except ValueError:
        get_logger("neuroharness.test").error(f"refused {CANARY}", exc_info=True)
    written = captured_text.getvalue()
    assert CANARY not in written
    assert REDACTED_EVENT in written
    assert "ValueError" in written


def test_the_human_readable_sink_still_names_the_event(
    captured_text: io.StringIO,
) -> None:
    """A readable sink that dropped the event would be unreadable.

    The line an operator greps for is ``LEVEL logger event``; losing any part of
    it while making the sink safe would push them back to the unsafe one.
    """
    get_logger("neuroharness.test").warning("token_issue_refused")
    assert captured_text.getvalue().strip() == "WARNING neuroharness.test token_issue_refused"
