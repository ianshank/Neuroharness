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
from collections.abc import Mapping
from typing import Any

import pytest
from tests.unit.test_schema_conformance import ENVELOPE_VALIDATOR, build_envelope

from neuroharness.canonical.digest import envelope_digest, proposal_digest
from neuroharness.canonical.jcs import canonicalize, parse_json
from neuroharness.envelope import WIRE_DUMP_OPTIONS, canonical_document, digests
from neuroharness.envelope.wire import EnvelopeDigests
from neuroharness.models.common import thaw_document
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
    document = thaw_document(canonical_document(envelope))
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
    pinned = thaw_document(canonical_document(envelope))

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
    pinned = thaw_document(canonical_document(envelope))
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
    gateway_document = thaw_document(canonical_document(envelope))
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
    document = thaw_document(canonical_document(envelope))
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
    document = thaw_document(canonical_document(envelope))
    revalidated = ActionEnvelope.model_validate(json.loads(json.dumps(document)))
    assert digests(revalidated) == digests(envelope)


# --- The document is read-only all the way down ------------------------------


def _nested_paths(document: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every path to a nested container, so the test cannot miss one by hand."""
    found: list[tuple[str, ...]] = []
    if isinstance(document, Mapping):
        for key, value in document.items():
            if isinstance(value, (Mapping, list, tuple)) and not isinstance(value, str):
                found.append((*prefix, str(key)))
                found.extend(_nested_paths(value, (*prefix, str(key))))
    return found


def test_the_canonical_document_has_nested_containers_to_freeze(
    envelope: ActionEnvelope,
) -> None:
    """Guard the guard below: a flat document would make it vacuous."""
    assert _nested_paths(canonical_document(envelope)), (
        "the fixture envelope renders flat, so the freeze test proves nothing"
    )


def test_no_nested_container_in_the_canonical_document_can_be_mutated(
    envelope: ActionEnvelope,
) -> None:
    """``MappingProxyType`` froze the top level and handed out live dicts below it.

    ``canonical_document`` promises in its own docstring that a caller cannot
    mutate the document it digested. That held for ``doc["tool"] = ...`` and not
    for ``doc["proposal"]["arguments"]["target"] = ...``, one level down, where
    the interesting content is - so the digest named a document the caller could
    still edit. Same shape as the frozen ``StagedRecord`` whose payload was
    editable, and the fix is the same deep ``freeze_document``.

    Walks every nested container rather than naming one, because a field added
    later must not reopen this.
    """
    document = canonical_document(envelope)
    for path in _nested_paths(document):
        node: Any = document
        for step in path:
            node = node[step]
        with pytest.raises(TypeError, match="does not support item assignment"):
            if isinstance(node, Mapping):
                node["injected"] = "ignore previous instructions"  # type: ignore[index]
            else:
                node[0] = "ignore previous instructions"  # type: ignore[index]


def test_freezing_did_not_change_a_single_digest(envelope: ActionEnvelope) -> None:
    """The fix must be invisible to the wire contract.

    The JCS canonicaliser dispatches on ``Mapping`` and ``(list, tuple)``, so a
    frozen document canonicalises identically - but "should" is how the two
    serialisation conventions this module exists to unify both looked correct.
    Pinned against the digests of a plain, unfrozen dump of the same envelope.
    """
    plain = envelope.model_dump(**WIRE_DUMP_OPTIONS)
    frozen = canonical_document(envelope)
    assert canonicalize(frozen) == canonicalize(plain)
    assert thaw_document(frozen) == plain, (
        "thawing must reproduce the plain dump exactly; it rebuilds containers "
        "and must touch no scalar"
    )
    assert digests(envelope) == EnvelopeDigests(
        proposal=proposal_digest(plain), envelope=envelope_digest(plain)
    )
