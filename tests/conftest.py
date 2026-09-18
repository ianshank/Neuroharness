"""Shared test fixtures.

Determinism is the point. Every test that touches time or identity uses the
injected seams, so a failure is reproducible and a replay test means something.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from neuroharness.observability.logging import configure_logging
from neuroharness.seams import FrozenClock, SequenceIdGenerator

#: A fixed instant every test can anchor on. Chosen to match the worked example
#: in the specification so fixtures and documentation agree.
ANCHOR = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(ANCHOR)


@pytest.fixture
def ids() -> SequenceIdGenerator:
    return SequenceIdGenerator("test")


@pytest.fixture(autouse=True)
def _quiet_logging() -> None:
    """Keep test output readable without disabling the logging path itself."""
    configure_logging(level="CRITICAL", json_output=True)
