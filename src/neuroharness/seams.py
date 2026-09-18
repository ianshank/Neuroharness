"""Injection seams.

Every non-deterministic or environment-bound capability enters the decision path
through one of these protocols. Nothing in ``resolve``, ``tokens``, ``evidence``
or ``registry`` may call :func:`datetime.now` or :func:`uuid.uuid4` directly.

That is not style. Replay (``FR-71``) re-evaluates a recorded decision using the
record's own timestamp as "now"; a module that reads the wall clock cannot be
replayed, and a harness that cannot be replayed cannot prove ``INV-09``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from neuroharness.errors import ClockUnavailableError

__all__ = ["Clock", "IdGenerator", "SystemClock", "FrozenClock", "UuidGenerator", "SequenceIdGenerator"]


@runtime_checkable
class Clock(Protocol):
    """Source of trusted time.

    Implementations raise :class:`ClockUnavailableError` rather than returning a
    best guess. A skewed clock silently revalidates stale facts and expired
    tokens, so guessing is worse than failing (``NFR-13``).
    """

    def now(self) -> datetime:
        """Return the current time as an aware UTC datetime."""
        ...


@runtime_checkable
class IdGenerator(Protocol):
    """Source of identifiers for actions, decisions, records and tokens."""

    def new_id(self) -> str:
        ...


class SystemClock:
    """Wall-clock time, with an optional health gate for testing outages."""

    __slots__ = ("_healthy",)

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    def now(self) -> datetime:
        if not self._healthy:
            raise ClockUnavailableError("trusted clock source is unhealthy")
        return datetime.now(timezone.utc)


class FrozenClock:
    """A clock that only moves when a test moves it."""

    __slots__ = ("_now", "_healthy")

    def __init__(self, start: datetime, *, healthy: bool = True) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires an aware datetime")
        self._now = start.astimezone(timezone.utc)
        self._healthy = healthy

    def now(self) -> datetime:
        if not self._healthy:
            raise ClockUnavailableError("trusted clock source is unhealthy")
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy


class UuidGenerator:
    """Random identifiers for production use."""

    __slots__ = ()

    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequenceIdGenerator:
    """Deterministic identifiers so tests can assert on exact records."""

    __slots__ = ("_prefix", "_counter")

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def new_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:08d}"
