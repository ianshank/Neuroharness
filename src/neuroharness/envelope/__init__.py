"""Turning a proposed tool call into an :class:`ActionEnvelope`, and back to bytes.

Increment 2 builds three of the four pieces: :mod:`~neuroharness.envelope.wire`,
the one place an envelope becomes the document the harness digests and publishes;
:mod:`~neuroharness.envelope.proposal`, the ``FR-03`` strip; and
:mod:`~neuroharness.envelope.arguments`, the ``FR-02`` schema check. The fourth -
assembling a whole :class:`~neuroharness.models.envelope.ActionEnvelope` - needs
an identity layer (``P0-13``) and a fact layer (``P0-06``) that do not exist, so
it is not here and is not half-built.

The order is deliberate. A builder written before the serialization convention
was pinned would have had to pick one, and the repository already contained two
live conventions producing two different envelope digests for the same envelope.
"""

from neuroharness.envelope.arguments import (
    SUPPORTED_KEYWORDS,
    ArgumentValidator,
    ArgumentViolation,
    BoundedSchemaValidator,
    ViolationKind,
)
from neuroharness.envelope.proposal import (
    PROPOSAL_FIELDS,
    StrippedProposal,
    strip_context_keys,
)
from neuroharness.envelope.wire import (
    WIRE_DUMP_OPTIONS,
    EnvelopeDigests,
    canonical_document,
    digests,
)

__all__ = [
    # FR-03: separating what the model said from what the harness knows
    "PROPOSAL_FIELDS",
    "StrippedProposal",
    "strip_context_keys",
    # FR-02: checking arguments against the action class
    "SUPPORTED_KEYWORDS",
    "ArgumentValidator",
    "ArgumentViolation",
    "BoundedSchemaValidator",
    "ViolationKind",
    # FR-04: the one rendering the harness digests and publishes
    "WIRE_DUMP_OPTIONS",
    "EnvelopeDigests",
    "canonical_document",
    "digests",
]
