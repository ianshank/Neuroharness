"""Every frozen model in the package re-validates on ``model_copy``.

``frozen=True`` reads as "this object cannot be changed" and
:meth:`pydantic.BaseModel.model_copy` is the door that leaves open: it writes
``update`` straight into the new object without running a single validator. A
frozen model is precisely where nobody thinks to look for a mutation hole,
which is what makes this worth a test rather than a convention.

**Why this module walks the package instead of naming classes.** The override
first landed on ``WireModel``, which covered the envelope and record trees and
nothing else, while the pull-request description claimed it covered every model
in the package. It did not: eight frozen models inherited ``BaseModel`` directly
and kept the hole - both token models, the whole signed-registry set, and
``Settings``. A reviewer named two of the eight. Fixing the two named ones would
have left six and produced a second, more confident version of the same false
claim.

So the total check here is *structural*: every frozen model in the package
inherits the one base that carries the rule. It discovers models by walking
:mod:`neuroharness`, so a frozen model added next year in a module nobody
updated here is covered on the day it is written, and the failure names both the
class and the remedy. It also refuses a model that re-implements the override
locally, which is how one rule becomes two that drift apart.

The *behavioural* half - that the rule actually does something - is proven
against the base itself and against the two models where a forged copy would
matter most, which are the ones that authorise execution.

One limitation, recorded rather than worked around: a model whose field default
is a frozen document cannot be ``model_construct``-ed or deep-copied, because
``mappingproxy`` is not picklable. That predates this change and is arguably
right - a deep copy of an immutable registry is meaningless - but it is why the
behavioural check does not simply instantiate all forty-odd models.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from datetime import timedelta
from typing import Final

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import neuroharness
from neuroharness.models.common import RevalidatingModel


def _package_models() -> list[type[BaseModel]]:
    """Every pydantic model defined in this package, deduplicated and ordered."""
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.walk_packages(neuroharness.__path__, f"{neuroharness.__name__}."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj not in (BaseModel, RevalidatingModel)
                # Defined here rather than imported into here, so a pydantic
                # internal or a third-party model is not this suite's to enforce.
                and obj.__module__.startswith(f"{neuroharness.__name__}.")
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return [found[key] for key in sorted(found)]


MODELS: Final[list[type[BaseModel]]] = _package_models()

FROZEN: Final[list[type[BaseModel]]] = [
    model for model in MODELS if model.model_config.get("frozen")
]

#: The trees a healthy walk must reach. Named because a walk that silently
#: stopped covering ``tokens`` would otherwise look perfectly green.
REQUIRED_MODULES: Final[frozenset[str]] = frozenset(
    {
        "neuroharness.models.envelope",
        "neuroharness.models.record",
        "neuroharness.tokens.model",
        "neuroharness.registry.models",
        "neuroharness.registry.resource_keys",
        "neuroharness.config",
    }
)


def _ids(models: list[type[BaseModel]]) -> list[str]:
    return [f"{model.__module__.rpartition('.')[2]}.{model.__name__}" for model in models]


def test_the_walk_found_the_package() -> None:
    """Guard the guard: an empty or partial walk makes every check below vacuous."""
    assert len(FROZEN) > 30, f"the walk found only {len(FROZEN)} frozen models"
    reached = {model.__module__ for model in FROZEN}
    missing = sorted(REQUIRED_MODULES - reached)
    assert not missing, f"the walk reached no frozen model in {missing}"


@pytest.mark.parametrize("model", FROZEN, ids=_ids(FROZEN))
def test_every_frozen_model_inherits_the_shared_base(model: type[BaseModel]) -> None:
    """The total check. One rule in one place; two copies are two rules."""
    assert issubclass(model, RevalidatingModel), (
        f"{model.__module__}.{model.__name__} is frozen but inherits BaseModel "
        f"directly, so model_copy(update=...) writes straight past every validator. "
        f"Inherit neuroharness.models.common.RevalidatingModel."
    )


class _Poisonable(RevalidatingModel):
    """A local model, so the behavioural check does not depend on a real one's shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str


def test_the_shared_base_refuses_an_invalid_update() -> None:
    """The behavioural half: an override that exists and does nothing fails here."""
    original = _Poisonable(name="fine")
    with pytest.raises(ValidationError):
        original.model_copy(update={"name": object()})


def test_the_shared_base_refuses_an_unknown_field() -> None:
    """``extra="forbid"`` must survive a copy, which is where it used to be lost."""
    original = _Poisonable(name="fine")
    with pytest.raises(ValidationError):
        original.model_copy(update={"smuggled": "ignore previous instructions"})


def test_a_copy_with_no_update_is_left_alone() -> None:
    """A plain clone must stay a plain clone.

    ``model_copy()`` with no update is pydantic's cheap copy and callers rely on
    its cost and its meaning. Re-validating there would change both.
    """
    original = _Poisonable(name="fine")
    assert original.model_copy().__dict__ == original.__dict__


def test_a_decision_token_cannot_be_forged_by_copying_one() -> None:
    """The case the review raised, on the model that authorises execution.

    A ``DecisionToken`` is what the broker reads to decide whether anything may
    run. Before this, ``model_copy(update=...)`` on one wrote past every field
    constraint - so a caller holding a legitimately minted token could produce a
    ``DecisionToken`` instance carrying any verdict, any digest and any mode,
    and it would still be a ``DecisionToken``. Direct construction refused all
    of it; the copy did not.
    """
    from tests.unit.test_token_service import (
        BUNDLE,
        DECISION,
        ENVELOPE,
        PROPOSAL,
        RECORD,
        START,
        TENANT,
        Mode,
        SigningAlgorithm,
        Verdict,
        defaults,
    )

    from neuroharness.tokens.model import DecisionToken

    token = DecisionToken(
        token_id="tok-real",
        decision_id=DECISION,
        envelope_digest=ENVELOPE,
        proposal_digest=PROPOSAL,
        policy_bundle_digest=BUNDLE,
        record_hash=RECORD,
        tenant_id=TENANT,
        mode=Mode.ENFORCE,
        verdict=Verdict.ALLOW,
        issued_at=START,
        expires_at=START + timedelta(seconds=defaults.DEFAULT_TOKEN_TTL_SECONDS),
        key_id="key-a",
        key_alg=SigningAlgorithm.HMAC_SHA256,
        shadow=False,
    )

    for field, forged in (
        ("envelope_digest", "not-a-digest"),
        ("proposal_digest", "not-a-digest"),
        ("record_hash", "not-a-digest"),
        ("verdict", "whatever I like"),
        ("mode", "whatever I like"),
    ):
        with pytest.raises(ValidationError):
            token.model_copy(update={field: forged})
