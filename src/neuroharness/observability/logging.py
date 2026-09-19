"""Structured logging with bound context and mandatory redaction.

Two requirements shape this module.

``NFR-18`` says logs carry no secrets, token material or model free text. That is
enforced here by a redactor that runs on every record, not by asking callers to
remember. A logging call that would leak is rewritten, not dropped, so the event
still appears in the timeline with its shape intact.

Redaction covers the whole record, not only its fields. The event string and the
exception text are free text that no field name guards, and they are the parts a
human reads, so they are the parts someone is tempted to make descriptive by
interpolating an argument, a credential or a model completion into them. An event
must therefore be a *code* (see :data:`_EVENT_CODE`) and an exception is reported
by its type, its reason code and its frames rather than by its message.

``NFR-20`` says every verdict is explainable from its record without the model.
:func:`get_logger` returns a logger whose events carry the bound decision context
(trace, action, decision, tenant), so a debugging session can follow one action
across components without correlating by timestamp.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, replace
from types import TracebackType
from typing import Any

__all__ = [
    "LogContext",
    "configure_logging",
    "get_logger",
    "bind_context",
    "current_context",
    "redact",
    "REDACTED",
    "REDACTED_EVENT",
]

REDACTED = "[redacted]"

#: Written in place of an event that is not an event code. It is a code itself,
#: so a sink that groups by event sees the leak attempts as one named bucket
#: rather than as an unbounded spray of distinct strings.
REDACTED_EVENT = "event.redacted"

#: Field names whose values are never emitted, matched case-insensitively on
#: whole words so ``signature`` is caught but ``signature_algorithm`` is not.
_SENSITIVE_KEYS = frozenset(
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

#: Keys that are safe despite containing a sensitive substring: they are
#: identifiers or digests, not the material itself.
_SAFE_KEYS = frozenset(
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

_MAX_VALUE_LENGTH = 512

#: How far :func:`redact` walks into a nested value before it stops. Deep enough
#: for the payloads the harness logs (a record wrapping a context wrapping a
#: registry entry), shallow enough that a cyclic or hostile structure cannot turn
#: a log call into an outage.
_MAX_REDACTION_DEPTH = 6

#: What an event may look like: dot-separated lower-case identifier segments,
#: such as ``pipeline.evaluated``, ``evidence.wal.replay.failed`` or
#: ``token_issued``. Every event in this package is already written this way, as
#: a module-level constant, so the rule costs nothing to keep - and it makes the
#: leaking shape unspellable, because interpolated text carries spaces, quotes,
#: punctuation or case that no code segment can contain.
_EVENT_CODE = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*")

#: Longest accepted event. An event names a kind of thing that happened, and
#: those names are short; an opaque string long enough to be a credential is not
#: a name, even on the rare occasion it is spellable as one.
_MAX_EVENT_LENGTH = 64

#: Frames kept from a traceback. The innermost ones say where it broke; keeping
#: the whole stack would let a deep recursion write an unbounded record.
_MAX_TRACEBACK_FRAMES = 12

#: How far the ``__cause__`` / ``__context__` chain is followed when naming the
#: exception types behind a failure.
_MAX_EXCEPTION_CAUSES = 4


@dataclass(frozen=True, slots=True)
class LogContext:
    """Correlation identifiers bound to every event in a decision."""

    trace_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    action_id: str | None = None
    decision_id: str | None = None
    action_class: str | None = None

    def merged(self, **fields: str | None) -> LogContext:
        known = {k: v for k, v in fields.items() if v is not None and hasattr(self, k)}
        return replace(self, **known) if known else self

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v is not None}


_CONTEXT: ContextVar[LogContext] = ContextVar("neuroharness_log_context", default=LogContext())


def current_context() -> LogContext:
    """Return the context bound to the current execution."""
    return _CONTEXT.get()


@contextmanager
def bind_context(**fields: str | None) -> Iterator[LogContext]:
    """Bind correlation fields for the duration of the block.

    Nested binds merge, so a component can add ``decision_id`` without knowing
    which ``trace_id`` an outer layer already bound.
    """
    merged = _CONTEXT.get().merged(**fields)
    token: Token[LogContext] = _CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _CONTEXT.reset(token)


def redact(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Return ``value`` with sensitive content replaced.

    Recurses through mappings and sequences. Depth is bounded so a cyclic or
    pathologically nested structure cannot turn a log call into an outage.
    """
    if _depth > _MAX_REDACTION_DEPTH:
        return "[truncated]"
    if key is not None:
        normalised = key.lower()
        if normalised not in _SAFE_KEYS and normalised in _SENSITIVE_KEYS:
            return REDACTED
    if isinstance(value, Mapping):
        return {k: redact(v, key=str(k), _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
        return value[:_MAX_VALUE_LENGTH] + "...[truncated]"
    return value


_ExcInfo = tuple[type[BaseException] | None, BaseException | None, TracebackType | None]


def _scrub_event(value: object) -> tuple[str, bool]:
    """Return the event to emit, and whether it replaced what the caller wrote.

    An event that is not an event code is replaced by :data:`REDACTED_EVENT`
    rather than refused. That is a choice between two fail-closed readings, and
    it is worth recording which one this module takes.

    *Refusing* would mean raising at the call site. It surfaces the mistake
    loudly, but it does so by destroying the record: the one event an operator
    most needs - the one somebody made descriptive by interpolating a value into
    it - is precisely the one that would never reach the timeline, and
    Constitution Article IV says a decision that is not recorded was not made. It
    would also let whatever reached the event string decide whether the harness
    raises, and let the observability layer fail a decision it exists only to
    describe.

    *Replacing* keeps the record. Its timestamp, level, logger, bound correlation
    context and redacted fields all survive, and the substitution is announced by
    ``event_redacted`` rather than performed quietly, so nothing is swallowed.
    The offending text is dropped and not hashed: a digest of a low-entropy
    secret is still a handle on that secret, and the record's ``logger`` already
    says which module to go and read.

    Loudness is not given up, only moved earlier. Every call site in the package
    passes a literal code, and a test scans for that structurally, so an event
    built by interpolation fails the build instead of waiting to be spotted in a
    log.
    """
    if isinstance(value, str) and len(value) <= _MAX_EVENT_LENGTH and _EVENT_CODE.fullmatch(value):
        return value, False
    return REDACTED_EVENT, True


def _reason_code_of(exc: BaseException | None) -> str | None:
    """Return the reason code a fail-closed error carries, if it carries one."""
    try:
        code = getattr(exc, "reason_code", None)
        rendered = code.render() if code is not None else None
    except Exception:
        # Describing a failure must never become a second failure: an error
        # whose reason code cannot be rendered is still an error worth recording.
        return None
    return rendered if isinstance(rendered, str) else None


def _cause_types(exc: BaseException | None) -> list[str]:
    """Name the exceptions behind this one, without repeating what they said.

    Bounded rather than cycle-checked: ``__context__`` loops back on itself when
    a handler re-raises inside its own ``except``, and a fixed depth ends that
    walk without a second mechanism that could itself be wrong.
    """
    names: list[str] = []
    cursor = exc
    while cursor is not None and len(names) < _MAX_EXCEPTION_CAUSES:
        cursor = cursor.__cause__ or cursor.__context__
        if cursor is not None:
            names.append(type(cursor).__qualname__)
    return names


def _describe_exception(exc_info: _ExcInfo) -> dict[str, Any]:
    """Describe a raised exception without repeating anything it says.

    An exception message is free text. The harness quotes envelope fields,
    registry entries and other caller-supplied values into the errors it raises,
    so a traceback rendered verbatim carries all of that to every sink - the same
    leak as an interpolated event, arriving by the other route (``NFR-18``).

    What survives is the part a responder needs and no caller can write into: the
    exception type, the reason code when the error carries one (``NFR-20``), the
    types it was chained from, and the innermost frames as file, line and
    function.
    """
    exc_type, exc, tb = exc_info
    described: dict[str, Any] = {"type": exc_type.__qualname__ if exc_type else "unknown"}
    reason = _reason_code_of(exc)
    if reason is not None:
        described["reason_code"] = reason
    causes = _cause_types(exc)
    if causes:
        described["caused_by"] = causes
    frames = [
        {"file": frame.filename, "line": frame.lineno, "func": frame.name}
        for frame in traceback.extract_tb(tb)[-_MAX_TRACEBACK_FRAMES:]
    ]
    if frames:
        described["frames"] = frames
    return described


class _RedactingFormatter(logging.Formatter):
    """Base for the formatters this module installs: no free text leaves them.

    The rules live here rather than in :class:`StructuredLogger` alone because a
    record reaches a handler by more than one route - the structured logger, a
    bare ``logging.getLogger("neuroharness.x").info(...)`` elsewhere in the
    process, or a library logging under the harness's name - and the formatter is
    the only place that sees all of them.
    """

    def _event(self, record: logging.LogRecord) -> tuple[str, bool]:
        """Return the record's event as a code, and whether text was replaced."""
        try:
            text = record.getMessage()
        except Exception:
            # A message whose ``%`` arguments do not match cannot be rendered.
            # The record still happened, so it is emitted under the placeholder
            # rather than dropped by the handler's error path.
            return REDACTED_EVENT, True
        event, redacted = _scrub_event(text)
        return event, redacted or bool(getattr(record, "event_redacted", False))

    def formatException(self, ei: Any) -> str:
        """Render an exception as structure, never as its message."""
        return json.dumps(_describe_exception(ei), default=str, sort_keys=True)


class _JsonFormatter(_RedactingFormatter):
    """Renders records as one JSON object per line, context included."""

    def format(self, record: logging.LogRecord) -> str:
        event, redacted = self._event(record)
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
        }
        if redacted:
            payload["event_redacted"] = True
        payload.update(getattr(record, "context", {}))
        fields = getattr(record, "fields", None)
        if fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["error"] = _describe_exception(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


class _TextFormatter(_RedactingFormatter):
    """One readable line per record for a local run, scrubbed the same way.

    JSON is what ships; this exists so a developer can read the stream. It
    applies the same rules, because a formatter rendering ``%(message)s`` would
    put back exactly the free text the JSON sink refuses.
    """

    def format(self, record: logging.LogRecord) -> str:
        event, _ = self._event(record)
        line = f"{record.levelname} {record.name} {event}"
        if record.exc_info:
            line = f"{line} {self.formatException(record.exc_info)}"
        return line


class StructuredLogger:
    """Attaches bound context, redacts every field, reduces the event to a code."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        exc_info = fields.pop("exc_info", None)
        safe = {k: redact(v, key=k) for k, v in fields.items()}
        code, redacted = _scrub_event(event)
        extra: dict[str, Any] = {"context": current_context().as_dict(), "fields": safe}
        if redacted:
            # Scrubbed here as well as in the formatter so that the record handed
            # to every handler - including one an operator attached with the
            # stdlib default formatter - never carries the text at all.
            extra["event_redacted"] = True
        self._logger.log(level, code, extra=extra, exc_info=exc_info)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, event, **fields)


def get_logger(name: str) -> StructuredLogger:
    """Return a structured logger for ``name``."""
    return StructuredLogger(logging.getLogger(name))


def configure_logging(*, level: str = "INFO", json_output: bool = True, stream: Any = None) -> None:
    """Install the harness log handler. Safe to call more than once."""
    root = logging.getLogger("neuroharness")
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_JsonFormatter() if json_output else _TextFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper()))
    root.propagate = False
