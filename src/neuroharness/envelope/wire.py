"""The one way an :class:`ActionEnvelope` becomes bytes (``FR-04``, ``FR-21``).

:func:`~neuroharness.canonical.digest.envelope_digest` and
:func:`~neuroharness.canonical.digest.proposal_digest` take a plain
``Mapping[str, Any]``, deliberately: the digest layer knows about JSON, not
about pydantic. The consequence nobody had closed is that **the package never
fixed how an envelope becomes that mapping**, and pydantic offers more than one
answer:

    model_dump(mode="json")                     -> 5 schema violations
    model_dump(mode="json", exclude_none=True)  -> conforms

The two are not cosmetically different. Measured on the conformance fixture,
they produce **different envelope digests**. The divergence comes from
``FR-11``: a fact that is not fresh carries no value, so ``asserted_by``,
``digest``, ``fetched_at``, ``observed_at`` and ``ttl_seconds`` are ``None`` on
a stub, and the published schema types them as strings and integers rather than
as nullable. A plain ``mode="json"`` dump emits those nulls; the schema rejects
the document; and the digest is taken over bytes that are not the wire form.

**Why that is a production-only bug.** Under ``FR-21`` step 1 the broker
recomputes the envelope digest independently, from the bytes it received - which
conform, because they crossed a wire. If the gateway digested a raw dump, the
broker computes a different digest and refuses an untampered envelope. It is
fail-closed, so nothing unsafe executes; it is also undiagnosable from either
side, because both components are behaving exactly as written. Two
implementations of one contract, checked only against each other, is the
``C-04`` shape.

The repository had both conventions live at once, in two test modules
(``test_envelope_model.py`` digesting a ``mode="json"`` dump,
``test_schema_conformance.py`` asserting against an ``exclude_none=True`` one),
which is how it stayed invisible.

**The rule, stated once:** *the bytes the harness digests are the bytes the
harness publishes.* Anything else makes the digest an identity for a document
that never existed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from neuroharness.canonical.digest import envelope_digest, proposal_digest
from neuroharness.models.common import Digest, freeze_document
from neuroharness.models.envelope import ActionEnvelope
from neuroharness.observability.logging import get_logger

__all__ = [
    "WIRE_DUMP_OPTIONS",
    "EnvelopeDigests",
    "canonical_document",
    "digests",
]

_LOG: Final = get_logger("neuroharness.envelope")

_EVENT_DIGESTED: Final[str] = "envelope.digested"

#: The pinned dump. Read-only so it cannot be mutated at a call site and quietly
#: change what every later digest covers.
#:
#: ``mode="json"`` because the digest is taken over JSON, not over Python
#: objects: a ``datetime`` and its ISO-8601 rendering are not the same bytes.
#: ``exclude_none=True`` because the published schema types a value-less fact's
#: fields as present-or-absent rather than nullable (``FR-11``), so a document
#: carrying explicit nulls is not a document any broker will ever receive.
WIRE_DUMP_OPTIONS: Final[Mapping[str, Any]] = MappingProxyType(
    {"mode": "json", "exclude_none": True}
)


@dataclass(frozen=True, slots=True)
class EnvelopeDigests:
    """The two digests, computed together from one document.

    Returned as a pair rather than by two separate calls so they cannot be
    computed from two different renderings of the same envelope - which is the
    class of mistake this module exists to remove, one level up.

    They answer different questions and bind different things (``ADR-0015``,
    ``ADR-0020``): an approval binds to :attr:`proposal`, which survives a fact
    refresh and a new ``proposed_at``; a token binds to :attr:`envelope`, which
    does not survive either, deliberately.
    """

    proposal: Digest
    envelope: Digest


def canonical_document(envelope: ActionEnvelope) -> Mapping[str, Any]:
    """The envelope as the bytes the harness will publish and digest.

    The single place ``model_dump`` is called on an envelope. Every digest, every
    schema check and every wire write goes through here, so the gateway that
    issues a token and the broker that verifies it cannot disagree about what
    the document was.

    Returned **deeply** read-only: a caller that mutated the document after
    digesting it would hold a digest for a document it no longer has.

    ``MappingProxyType`` alone was not enough and read as though it were. It
    freezes the top level and hands out the nested ``dict`` and ``list`` objects
    ``model_dump`` built, so ``canonical_document(env)["proposal"]["arguments"]
    ["target"] = ...`` succeeded against a mapping whose docstring promised it
    could not - the identity-drift defect this function exists to prevent,
    surviving one level down. It is the same shape as the frozen ``StagedRecord``
    whose payload was editable, and ``freeze_document`` is the helper that
    already existed for it.

    The JCS canonicaliser dispatches on ``Mapping`` and ``(list, tuple)``, which
    is what a frozen document is made of, so the digests are byte-identical to
    the shallow rendering's.

    **Thaw at the JSON boundary.** A frozen document is not ``json.dumps``-able,
    deliberately, and that is the convention this package already runs on:
    ``models.common`` holds documents frozen inside models and registers
    ``thaw_document`` as the serialiser used when one is dumped. So a caller
    writing this to a wire or handing it to a JSON Schema validator calls
    ``thaw_document`` first, and the call is a visible act at the one place the
    document stops being an identity and becomes bytes. Thawing rebuilds
    containers and touches no scalar, so it cannot change what canonicalises -
    which is asserted, not assumed, in ``test_envelope_wire.py``.
    """
    document: Mapping[str, Any] = freeze_document(envelope.model_dump(**WIRE_DUMP_OPTIONS))
    return document


def digests(envelope: ActionEnvelope) -> EnvelopeDigests:
    """Both digests, over one canonical rendering (``FR-04``).

    Logs the pair against the envelope's trace so a digest mismatch reported by
    a broker can be traced back to what the gateway actually digested - which is
    the diagnostic that was missing when the two conventions were live at once.
    """
    document = canonical_document(envelope)
    pair = EnvelopeDigests(
        proposal=proposal_digest(document),
        envelope=envelope_digest(document),
    )
    _LOG.debug(
        _EVENT_DIGESTED,
        proposal_digest=str(pair.proposal),
        envelope_digest=str(pair.envelope),
        action_class=envelope.action_class_key,
        trace_id=envelope.context.trace_id,
    )
    return pair
