"""Anchored identifier patterns must reject a trailing newline (``F1``).

Python's ``$`` also matches immediately *before* a final newline, so a pattern
written ``^...$`` and applied with :meth:`re.Pattern.match` silently accepts
``"<valid value>\\n"``. Every validated identifier in this package is anchored
that way, which made the newline a universal smuggling character:

* ``Principal("user:alice\\n")`` reported ``.kind`` as ``USER`` yet compared
  UNEQUAL to ``Principal("user:alice")``. Separation of duties (``FR-42``,
  ``FR-46``, ``SEC-13``, Constitution Art. IX) is an equality or membership test
  over :class:`~neuroharness.models.common.Principal`, so one human proposing as
  ``user:alice`` and approving as ``user:alice\\n`` satisfied it.
* A reason-code subject carrying a newline forges a line in a JSONL evidence
  export (``SEC-07``).
* A resource key carrying a newline becomes a second, distinct lease identity
  for one resource (``FR-25``, ``FR-34``).
* A digest carrying a newline is a second spelling of one document's identity.

The fix is :meth:`re.Pattern.fullmatch` at every site. These tests hold that
line: they fail against ``.match`` and pass against ``.fullmatch``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import pytest
from pydantic import TypeAdapter, ValidationError

from neuroharness.errors import RegistryValidationError
from neuroharness.models.common import Digest, Principal, PrincipalKind
from neuroharness.models.record import ReasonCodeField
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.registry.resource_keys import (
    ResourceKeyRegistry,
    is_resource_key,
    render_template,
)

#: A syntactically valid ``sha256:`` digest. Content is irrelevant here; shape
#: is the whole subject of the test.
VALID_DIGEST: Final[str] = "sha256:" + ("ab" * 32)

#: The proposer identity used by the separation-of-duties narrative above.
VALID_PRINCIPAL: Final[str] = "user:alice"

#: A rule id in the shape ``_RULE_ID_PATTERN`` accepts, used as the subject of a
#: parameterised reason name.
VALID_RULE_ID: Final[str] = "FR-42"

#: ``RULE_FAILED:<rule id>`` -- a reason code the record catalogue accepts.
VALID_REASON_CODE: Final[str] = f"{ReasonName.RULE_FAILED.value}:{VALID_RULE_ID}"

#: A well-formed ``kind:id`` resource key.
VALID_RESOURCE_KEY: Final[str] = "service:checkout"

#: The three ways a newline can be attached to an otherwise valid value. A
#: leading newline and a doubled trailing newline are included because ``$``
#: tolerates exactly one trailing newline and ``^`` tolerates none, so a fix
#: that only stripped the common case would still leave a gap.
_NEWLINE_SHAPES: Final[tuple[Callable[[str], str], ...]] = (
    lambda value: f"{value}\n",
    lambda value: f"\n{value}",
    lambda value: f"{value}\n\n",
)

_SHAPE_IDS: Final[tuple[str, ...]] = ("trailing", "leading", "doubled-trailing")


def _newline_variants(value: str) -> list[Any]:
    """Return ``value`` decorated with each newline shape, as pytest params.

    Typed ``list[Any]`` because pytest does not export the type of
    :func:`pytest.param`'s result.
    """
    return [
        pytest.param(shape(value), id=identifier)
        for shape, identifier in zip(_NEWLINE_SHAPES, _SHAPE_IDS, strict=True)
    ]


# --- Digest (document identity) ---------------------------------------------


@pytest.mark.parametrize("value", _newline_variants(VALID_DIGEST))
def test_digest_rejects_embedded_newline(value: str) -> None:
    """A digest with a newline is a second spelling of one document's identity."""
    with pytest.raises(ValueError):
        Digest(value)


def test_digest_still_accepts_its_canonical_form() -> None:
    """The fix must tighten the grammar, not break it."""
    assert Digest(VALID_DIGEST) == VALID_DIGEST


# --- Principal (separation of duties: FR-42, FR-46, SEC-13) ------------------


@pytest.mark.parametrize("value", _newline_variants(VALID_PRINCIPAL))
def test_principal_rejects_embedded_newline(value: str) -> None:
    """The self-approval bypass: two unequal spellings of one human."""
    with pytest.raises(ValueError):
        Principal(value)


def test_principal_newline_variant_is_not_a_second_identity() -> None:
    """State the bypass as the property it violates, not just as a raise.

    Before the fix this assertion failed twice over: the newline variant
    constructed, and it compared unequal to the principal it impersonates while
    still reporting ``PrincipalKind.USER``.
    """
    approver = Principal(VALID_PRINCIPAL)
    with pytest.raises(ValueError):
        impostor = Principal(f"{VALID_PRINCIPAL}\n")
        # Unreachable once the guard holds; documents what used to happen.
        assert impostor.kind is PrincipalKind.USER and impostor != approver


# --- Reason-code subject (SEC-07) -------------------------------------------


@pytest.mark.parametrize("subject", _newline_variants(VALID_RULE_ID))
def test_reason_code_subject_rejects_embedded_newline(subject: str) -> None:
    """A newline in a subject forges a line in a JSONL evidence export."""
    with pytest.raises(ValueError):
        ReasonCode(ReasonName.RULE_FAILED, subject)


@pytest.mark.parametrize("rendered", _newline_variants(VALID_REASON_CODE))
def test_record_reason_code_field_rejects_embedded_newline(rendered: str) -> None:
    """The wire route into a record: a rendered reason code read off JSON."""
    adapter: TypeAdapter[ReasonCode] = TypeAdapter(ReasonCodeField)
    with pytest.raises((ValidationError, ValueError)):
        adapter.validate_python(rendered)


def test_record_reason_code_catalogue_guards_independently() -> None:
    """The record's own catalogue check must not lean on ``ReasonCode``.

    ``_coerce_reason_code`` accepts an already-constructed :class:`ReasonCode`
    without re-running its ``__post_init__``, so the catalogue regex in
    ``record.py`` is the only guard on that path. Forging the subject after
    construction is the only way to reach it, and it is exactly what a
    deserialiser or a caching layer that rehydrates objects would do.
    """
    forged = ReasonCode(ReasonName.RULE_FAILED, VALID_RULE_ID)
    object.__setattr__(forged, "subject", f"{VALID_RULE_ID}\n")
    assert forged.render() == f"{VALID_REASON_CODE}\n"

    adapter: TypeAdapter[ReasonCode] = TypeAdapter(ReasonCodeField)
    with pytest.raises((ValidationError, ValueError)):
        adapter.validate_python(forged)


def test_record_reason_code_field_still_accepts_the_catalogue() -> None:
    adapter: TypeAdapter[ReasonCode] = TypeAdapter(ReasonCodeField)
    assert adapter.validate_python(VALID_REASON_CODE).render() == VALID_REASON_CODE


# --- Resource keys (FR-25 lease identity, FR-34 grammar) ---------------------


@pytest.mark.parametrize("value", _newline_variants(VALID_RESOURCE_KEY))
def test_is_resource_key_rejects_embedded_newline(value: str) -> None:
    """A newline-bearing key is a second lease identity for one resource."""
    assert is_resource_key(value) is False


def test_is_resource_key_still_accepts_a_well_formed_key() -> None:
    assert is_resource_key(VALID_RESOURCE_KEY) is True


def test_resource_kind_enumeration_rejects_embedded_newline() -> None:
    """``_check_enumerations`` anchors kinds and members the same way.

    Not named by the review, but the same defect: a kind ``"service\\n"`` was
    accepted into a signed enumeration it can never be looked up from, so every
    call keyed on it would deny after the decision was already recorded.
    """
    with pytest.raises(ValueError):
        ResourceKeyRegistry(enumerations={"service\n": (VALID_RESOURCE_KEY.split(":")[1],)})
    with pytest.raises(ValueError):
        ResourceKeyRegistry(enumerations={"service": ("checkout\n",)})


def test_render_template_rejects_an_argument_value_with_a_newline() -> None:
    """The substituted value is validated before it becomes a lease identity."""
    with pytest.raises(RegistryValidationError):
        render_template("service:{name}", {"name": "checkout\n"})
