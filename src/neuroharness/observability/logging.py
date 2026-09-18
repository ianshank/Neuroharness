"""Structured logging with bound context and mandatory redaction.

Two requirements shape this module.

``NFR-18`` says logs carry no secrets, token material or model free text. That is
enforced here by a redactor that runs on every record, not by asking callers to
remember. A logging call that would leak is rewritten, not dropped, so the event
still appears in the timeline with its shape intact.

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
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterator, Mapping

__all__ = [
    "LogContext",
    "configure_logging",
    "get_logger",
    "bind_context",
    "current_context",
    "redact",
    "REDACTED",
]

REDACTED = "[redacted]"

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
    if _depth > 6:
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


class _JsonFormatter(logging.Formatter):
    """Renders records as one JSON object per line, context included."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "context", {}))
        fields = getattr(record, "fields", None)
        if fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


class StructuredLogger:
    """Thin adapter that attaches bound context and redacts every field."""

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        exc_info = fields.pop("exc_info", None)
        safe = {k: redact(v, key=k) for k, v in fields.items()}
        self._logger.log(
            level,
            event,
            extra={"context": current_context().as_dict(), "fields": safe},
            exc_info=exc_info,
        )

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
    handler.setFormatter(
        _JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper()))
    root.propagate = False
