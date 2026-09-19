"""Resource-key registry tests (``FR-34``, ``FR-25``).

The resource key is two things at once: the broker's mutual-exclusion identity
(``WF-06c``) and the subject of ``RESOURCE_BUSY:<resource_key>`` in the closed
reason catalogue (section 5.6). These tests hold both properties.
"""

from __future__ import annotations

import json

import pytest

from neuroharness.errors import RegistryValidationError
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.registry.resource_keys import (
    MAX_RESOURCE_KEY_LENGTH,
    ResourceKeyRegistry,
    is_resource_key,
    render_template,
    split_resource_key,
    template_placeholders,
)

ENUMERATIONS = {
    "service": ("checkout", "payments"),
    "target": ("development", "test", "staging", "production"),
}


@pytest.fixture()
def registry() -> ResourceKeyRegistry:
    return ResourceKeyRegistry(enumerations=ENUMERATIONS)


# --- grammar (FR-34) ---------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "service:checkout",
        "service:checkout/target:production",
        "service:checkout/target:production/region:eu-west-1",
        "db:orders.replica-2",
    ],
)
def test_well_formed_keys_are_accepted(registry: ResourceKeyRegistry, key: str) -> None:
    assert is_resource_key(key)
    assert registry.is_well_formed(key)


@pytest.mark.parametrize(
    ("key", "why"),
    [
        ("checkout", "no kind"),
        ("service:", "no identifier"),
        (":checkout", "no kind"),
        ("Service:checkout", "kind is lowercase"),
        ("service:check out", "space"),
        ("service:check_out", "underscore is not a reason-code subject character"),
        ("service:checkout/", "trailing separator"),
        ("service:checkout//target:production", "empty segment"),
        ("service:checkout:extra", "colon inside the identifier"),
        ("", "empty"),
    ],
)
def test_malformed_keys_are_rejected(registry: ResourceKeyRegistry, key: str, why: str) -> None:
    assert not is_resource_key(key), why
    assert not registry.is_well_formed(key), why


def test_key_length_is_bounded_so_it_fits_a_reason_code() -> None:
    """A key must be renderable as ``RESOURCE_BUSY:<resource_key>`` (section 5.6)."""
    longest = "service:" + "a" * 63
    assert is_resource_key(longest)
    assert ReasonCode(ReasonName.RESOURCE_BUSY, longest).render().endswith(longest)

    oversized = "/".join([f"service:{'a' * 63}"] * 4)
    assert len(oversized) > MAX_RESOURCE_KEY_LENGTH
    assert not is_resource_key(oversized)


def test_every_valid_key_is_a_valid_reason_subject() -> None:
    """The grammar is a subset of the reason-subject charset (``SEC-07``)."""
    for key in ("service:checkout/target:production", "db:orders.replica-2"):
        assert ReasonCode(ReasonName.RESOURCE_BUSY, key).render() == f"RESOURCE_BUSY:{key}"


def test_split_resource_key_returns_segments() -> None:
    assert split_resource_key("service:checkout/target:production") == (
        ("service", "checkout"),
        ("target", "production"),
    )


def test_split_resource_key_refuses_a_malformed_key() -> None:
    with pytest.raises(RegistryValidationError, match="malformed resource key"):
        split_resource_key("service:check out")


# --- enumerations (FR-34) ----------------------------------------------------


def test_enumeration_lookup(registry: ResourceKeyRegistry) -> None:
    assert registry.enumeration("service") == ("checkout", "payments")
    assert registry.has_kind("target")
    assert registry.contains("target", "production")
    assert not registry.contains("target", "prod")


def test_unknown_kind_raises_rather_than_returning_empty(registry: ResourceKeyRegistry) -> None:
    """An empty answer reads as 'nothing allowed' and 'no constraint' alike."""
    with pytest.raises(RegistryValidationError, match="no canonical enumeration"):
        registry.enumeration("queue")


def test_is_known_checks_membership_for_enumerated_kinds(registry: ResourceKeyRegistry) -> None:
    assert registry.is_known("service:checkout/target:production")
    assert not registry.is_known("service:checkout/target:prod")
    assert not registry.is_known("not a resource key")
    # A kind the registry never declared is not this registry's business.
    assert registry.is_known("queue:orders")
    registry.assert_known("service:payments")
    with pytest.raises(RegistryValidationError, match="not canonical"):
        registry.assert_known("service:unknown-service")


@pytest.mark.parametrize(
    ("enumerations", "match"),
    [
        ({"Service": ("checkout",)}, "malformed"),
        ({"service": ()}, "enumerates nothing"),
        ({"service": ("checkout", "checkout")}, "duplicate"),
        ({"service": ("check out",)}, "not a resource identifier"),
    ],
)
def test_malformed_enumerations_are_rejected(enumerations: dict, match: str) -> None:
    with pytest.raises(Exception, match=match):
        ResourceKeyRegistry(enumerations=enumerations)


# --- the catalogue is read-only once signed (FR-34, SEC-05) ------------------


def test_a_signed_enumeration_cannot_be_widened_in_place(
    registry: ResourceKeyRegistry,
) -> None:
    """Widening an enumeration from inside the process is an unrecorded policy change.

    ``frozen=True`` stops attribute assignment and nothing else, so a plain
    dictionary here would let any code in the harness add a second spelling of
    an approved target - ``Production`` beside ``production`` - to a catalogue
    an operator signed. The registry digest would not move, the change would
    appear in no record, and the next call keyed on the new spelling would pass
    a gate nobody reviewed. That is the target-aliasing defect (``A-35``),
    reached from inside the runtime rather than from the document.
    """
    aliased = "service:checkout/target:Production"
    assert not registry.is_known(aliased)

    with pytest.raises(TypeError):
        registry.enumerations["target"] = ("staging", "production", "Production")

    assert not registry.is_known(aliased)
    assert registry.enumeration("target") == ENUMERATIONS["target"]


def test_an_enumerated_kind_cannot_be_added_or_removed_after_loading(
    registry: ResourceKeyRegistry,
) -> None:
    """Deleting a kind is the same attack from the other side.

    ``is_known`` only constrains kinds the registry declares, so dropping
    ``target`` from the catalogue turns every target identifier into "not this
    registry's business" and silently removes the constraint instead of
    widening it.
    """
    with pytest.raises(TypeError):
        del registry.enumerations["target"]
    with pytest.raises(TypeError):
        registry.enumerations["queue"] = ("orders",)

    assert registry.has_kind("target")
    assert not registry.has_kind("queue")
    assert not registry.is_known("service:checkout/target:Production")


def test_the_mapping_the_caller_passed_in_is_no_longer_connected() -> None:
    """A loader that keeps its parsed document must not keep a handle on policy.

    The registry is built from a document the loader parsed and still holds. If
    the model stored that mapping by reference, editing the parsed document
    later - a hot reload assembling the next version in place - would retune the
    live registry underneath in-flight decisions.
    """
    source = {"target": ["staging", "production"]}
    built = ResourceKeyRegistry(enumerations=source)

    source["target"].append("Production")
    source["queue"] = ["orders"]

    assert built.enumeration("target") == ("staging", "production")
    assert not built.has_kind("queue")
    assert not built.is_known("service:checkout/target:Production")


def test_the_empty_default_is_frozen_too() -> None:
    """``ActionClassRegistry`` falls back to one shared ``ResourceKeyRegistry()``.

    A mutable default would be a single dictionary sitting behind every registry
    in the process, so one tenant could populate another tenant's enumerations -
    or, worse, populate a catalogue that is meant to constrain nothing and start
    denying calls that were fine a moment ago.
    """
    empty = ResourceKeyRegistry()
    with pytest.raises(TypeError):
        empty.enumerations["target"] = ("production",)
    assert empty.enumerations == {}


def test_enumerations_still_serialise_as_plain_json(
    registry: ResourceKeyRegistry,
) -> None:
    """Freezing must not leak into the wire form.

    The catalogue is canonicalised and digested, and it is exported for audit. A
    ``mappingproxy`` or a tuple reaching the JSON encoder is a signing failure or
    an export failure, discovered at the auditor rather than at the writer.
    """
    dumped = registry.model_dump(mode="json")["enumerations"]

    assert isinstance(dumped, dict)
    assert all(isinstance(members, list) for members in dumped.values())
    assert json.loads(json.dumps(dumped)) == {k: list(v) for k, v in ENUMERATIONS.items()}


# --- rendering (FR-25) -------------------------------------------------------


def test_template_placeholders_are_deduplicated_in_order() -> None:
    assert template_placeholders("service:{service}/target:{target}") == ("service", "target")
    assert template_placeholders("a:{x}/b:{x}") == ("x",)


def test_render_template_renders_a_canonical_key(registry: ResourceKeyRegistry) -> None:
    rendered = registry.render(
        "service:{service}/target:{target}",
        {"service": "checkout", "target": "production", "replicas": 3},
    )
    assert rendered == "service:checkout/target:production"


def test_render_template_rejects_a_value_outside_the_enumeration() -> None:
    """``FR-34``: policy compares enumerations, never free strings (``A-35``)."""
    with pytest.raises(RegistryValidationError, match="not in the canonical target enumeration"):
        render_template(
            "service:{service}/target:{target}",
            {"service": "checkout", "target": "prod"},
            ENUMERATIONS,
        )


def test_render_template_without_enumerations_checks_shape_only() -> None:
    assert (
        render_template("service:{service}/target:{target}", {"service": "x", "target": "prod"})
        == "service:x/target:prod"
    )


def test_render_template_rejects_a_missing_argument() -> None:
    """A missing argument must never render as an empty identifier (``FR-25``)."""
    with pytest.raises(RegistryValidationError, match="which the call does not supply"):
        render_template("service:{service}/target:{target}", {"service": "checkout"}, ENUMERATIONS)


@pytest.mark.parametrize("value", [True, None, ["checkout"], {"name": "checkout"}, 1.5])
def test_render_template_rejects_non_identifier_types(value: object) -> None:
    with pytest.raises(RegistryValidationError, match=r"not a resource identifier|which is not a"):
        render_template("service:{service}", {"service": value}, ENUMERATIONS)


def test_render_template_rejects_an_injected_separator() -> None:
    """An argument value may not smuggle extra key segments."""
    with pytest.raises(RegistryValidationError, match="is not a resource identifier"):
        render_template("service:{service}", {"service": "checkout/target:production"})


def test_render_template_accepts_an_integer_argument() -> None:
    assert render_template("shard:{shard}", {"shard": 7}) == "shard:7"


def test_render_template_rejects_an_empty_template() -> None:
    with pytest.raises(RegistryValidationError, match="non-empty string"):
        render_template("", {})


def test_render_template_rejects_a_template_that_renders_to_a_non_key() -> None:
    with pytest.raises(RegistryValidationError, match="rendered 'checkout', which is not a"):
        render_template("{service}", {"service": "checkout"})


def test_empty_registry_enumerates_nothing_but_still_validates_shape() -> None:
    empty = ResourceKeyRegistry()
    assert empty.enumerations == {}
    assert empty.is_well_formed("service:checkout")
    assert empty.is_known("service:checkout")
