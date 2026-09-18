"""Turning a proposed tool call into an :class:`ActionEnvelope`, and back to bytes.

Increment 2 builds the second half only: :mod:`neuroharness.envelope.wire`, the
one place an envelope becomes the document the harness digests and publishes.
The *builder* - stripping context-shaped keys, validating arguments against the
action class's ``argument_schema``, splitting proposal from context (``FR-02``,
``FR-03``) - lands beside it, and the identity and fact layers it needs are
``P0-13`` and ``P0-06`` work.

The order is deliberate. A builder written before the serialization convention
was pinned would have had to pick one, and the repository already contained two
live conventions producing two different envelope digests for the same envelope.
"""

from neuroharness.envelope.wire import (
    WIRE_DUMP_OPTIONS,
    EnvelopeDigests,
    canonical_document,
    digests,
)

__all__ = [
    "WIRE_DUMP_OPTIONS",
    "EnvelopeDigests",
    "canonical_document",
    "digests",
]
