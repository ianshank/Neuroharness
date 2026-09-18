"""Shared test fixtures.

Determinism is the point. Every test that touches time or identity uses the
injected seams, so a failure is reproducible and a replay test means something.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from neuroharness.observability.logging import configure_logging
from neuroharness.seams import FrozenClock, SequenceIdGenerator

#: A fixed instant every test can anchor on. Chosen to match the worked example
#: in the specification so fixtures and documentation agree.
ANCHOR = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)

#: The process-global logger :func:`configure_logging` installs handlers on.
HARNESS_LOGGER_NAME = "neuroharness"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(ANCHOR)


@pytest.fixture
def ids() -> SequenceIdGenerator:
    return SequenceIdGenerator("test")


@pytest.fixture
def harness_logger_state() -> Iterator[None]:
    """Restore the process-global harness logger after a test reconfigures it.

    ``configure_logging`` removes the logger's handlers, sets its level and
    turns off propagation, and a test that swaps in a capturing stream leaves
    that stream installed. Without this restore, whether an evidence assertion
    sees the events it claims to depends on which test ran before it, so a
    suite can go green with several of its strongest assertions inspecting a
    stream nobody is writing to any more.

    Function-scoped on purpose: anything that wants to configure logging must
    take this fixture, and a broader-scoped fixture asking for it fails loudly
    with a scope mismatch rather than quietly sharing one restore across a
    module.
    """
    logger = logging.getLogger(HARNESS_LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.fixture(autouse=True)
def _quiet_logging(harness_logger_state: None) -> None:
    """Keep test output readable without disabling the logging path itself."""
    configure_logging(level="CRITICAL", json_output=True)
