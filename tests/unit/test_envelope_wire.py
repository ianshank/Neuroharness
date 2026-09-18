"""One serialization convention, and the bug that having two was hiding.

``proposal_digest`` and ``envelope_digest`` take a plain mapping by design, so
the package never fixed how an :class:`ActionEnvelope` becomes one. Pydantic
offers more than one answer and **the repository used two of them**:
``test_envelope_model.py`` digested a ``model_dump(mode="json")``,
``test_schema_conformance.py`` asserted against ``exclude_none=True``. They
produce different envelope digests.

The failure that would have caused is invisible until production and
undiagnosable from either end. Under ``FR-21`` step 1 the broker recomputes the
envelope digest from the bytes it received - which conform, because they crossed
a wire. A gateway digesting a raw dump hands it a token bound to a digest the
broker cannot reproduce, so the broker refuses an untampered envelope. Nothing
unsafe executes; nothing explains itself either, because both components are
doing exactly what they were written to do.

These tests pin the convention and, more importantly, pin the *reason* for it:
the conforming rendering is the one that gets digested, because the bytes the
harness digests must be the bytes it publishes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.unit.test_schema_conformance import ENVELOPE_VALIDATOR, build_envelope

from neuroharness.canonical.digest import envelope_digest, proposal_digest
from neuroharness.canonical.jcs import canonicalize, parse_json
from neuroharness.envelope import WIRE_DUMP_OPTIONS, canonical_document, digests
from neuroharness.models.envelope import ActionEnvelope


@pytest.fixture
def envelope() -> ActionEnvelope:
    return build_envelope()


def test_the_canonical_document_conforms_to_the_published_schema(
    envelope: ActionEnvelope,
) -> None:
    """The whole argument, in one assertion.

    ``docs/sdd/schemas/action-envelope.schema.json`` is what the CI
    schema-conformance job calls "the authoritative wire contract". If the
    document the harness digests does not satisfy it, the harness is computing
    an identity for a document that will never exist on any wire.
    """
    document = dict(canonical_document(envelope))
    errors = sorted(
        ENVELOPE_VALIDATOR.iter_errors(document), key=lambda e: list(e.absolute_path)
    )
    assert not errors, "\n".join(
        f"/{'/'.join(str(part) for part in e.absolute_path)}: {e.message}" for e in errors
    )


def test_the_other_convention_does_not_conform_and_digests_differently(
    envelope: ActionEnvelope,
) -> None:
    """The regression that stops anyone "simplifying" the convention back.

    Both halves matter. Without the schema half, the choice looks arbitrary and
    the next reader drops ``exclude_none`` as noise. Without the digest half,
    the schema violation looks cosmetic - five nulls, who cares - and the reader
    does not see that it moves the identity a token is bound to.

    The nulls come from ``FR-11``: a fact that is not fresh carries no value, so
    a stub's ``asserted_by``, ``digest``, ``fetched_at``, ``observed_at`` and
    ``ttl_seconds`` are ``None``, and the schema types them present-or-absent
    rather than nullable.
    """
    naive: dict[str, Any] = envelope.model_dump(mode="json")
    pinned = dict(canonical_document(envelope))

    errors = list(ENVELOPE_VALIDATOR.iter_errors(naive))
    assert errors, (
        "the naive dump now conforms, so this fixture no longer carries a "
        "value-less fact and the test has stopped demonstrating the divergence"
    )
    assert all("None is not of type" in e.message for e in errors), (
        f"the divergence is no longer about explicit nulls: {[e.message for e in errors]}"
    )

    assert envelope_digest(naive) != envelope_digest(pinned), (
        "the two renderings now digest the same, so the convention no longer matters "
        "- which would mean the FR-11 stub stopped carrying absent fields"
    )


def test_the_proposal_digest_is_indifferent_to_the_convention(
    envelope: ActionEnvelope,
) -> None:
    """And this is why the bug was narrow, which is why it stayed hidden.

    ``ADR-0020`` fixes the proposal digest to a narrow projection that excludes
    facts, so the ``FR-11`` nulls never reach it and both renderings agree. Only
    the *envelope* digest diverges - and the envelope digest is precisely the one
    the broker recomputes under ``FR-21``. A reviewer checking "do the digests
    match under both dumps?" on the proposal digest alone would have concluded
    there was nothing here.
    """
    naive = envelope.model_dump(mode="json")
    pinned = dict(canonical_document(envelope))
    assert proposal_digest(naive) == proposal_digest(pinned)


def test_a_broker_recomputing_from_the_wire_bytes_gets_the_same_digest(
    envelope: ActionEnvelope,
) -> None:
    """``FR-21`` step 1, simulated end to end.

    This is the round trip the convention exists to make hold: serialize the way
    the gateway does, put it through canonical JSON as a wire would, parse it
    back the way a broker does, and recompute. Any convention mismatch shows up
    here as two different digests over one untampered envelope.
    """
    gateway_document = dict(canonical_document(envelope))
    issued = envelope_digest(gateway_document)

    on_the_wire = canonicalize(gateway_document)
    broker_document = parse_json(on_the_wire)
    assert isinstance(broker_document, dict)

    assert envelope_digest(broker_document) == issued


def test_both_digests_come_from_one_rendering(envelope: ActionEnvelope) -> None:
    """The pair, not two calls.

    Computing them separately is how they would come to be taken over two
    different renderings, which is the same mistake one level up from the one
    this module fixes.
    """
    pair = digests(envelope)
    document = dict(canonical_document(envelope))
    assert pair.proposal == proposal_digest(document)
    assert pair.envelope == envelope_digest(document)


def test_the_canonical_document_cannot_be_mutated(envelope: ActionEnvelope) -> None:
    """A caller that edited it after digesting would hold a stale identity."""
    document = canonical_document(envelope)
    with pytest.raises(TypeError):
        document["context"] = {}  # type: ignore[index]


def test_the_convention_itself_cannot_be_mutated() -> None:
    """``WIRE_DUMP_OPTIONS`` is read-only.

    A mutable module-level default is one import away from changing what every
    later digest in the process covers - the shape of finding ``C-06``, which
    the round-two review closed on the resource-key enumerations.
    """
    with pytest.raises(TypeError):
        WIRE_DUMP_OPTIONS["exclude_none"] = False  # type: ignore[index]


def test_the_convention_is_exactly_what_the_docstring_claims() -> None:
    """Pinned as data, so a change to it is a change to this test.

    The options are an argument, not a preference: ``mode="json"`` because a
    digest is over JSON bytes and a ``datetime`` is not its ISO rendering;
    ``exclude_none=True`` because the schema types a stub's absent fields as
    absent rather than null.
    """
    assert dict(WIRE_DUMP_OPTIONS) == {"mode": "json", "exclude_none": True}


def test_digesting_is_stable_across_a_round_trip_through_the_model(
    envelope: ActionEnvelope,
) -> None:
    """Validate the wire form back into a model and it digests the same.

    ``FR-71`` replay depends on this: a decision replayed from its recorded
    envelope must reach the same identity, or the replay is of a different
    action.
    """
    document = dict(canonical_document(envelope))
    revalidated = ActionEnvelope.model_validate(json.loads(json.dumps(document)))
    assert digests(revalidated) == digests(envelope)
