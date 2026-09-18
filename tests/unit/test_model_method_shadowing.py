"""No model method shadows a ``BaseModel`` method it does not implement.

``ResourceKeyRegistry.validate(key) -> bool`` shadowed
:meth:`pydantic.BaseModel.validate`, a deprecated v1-compatible classmethod that
parses a document and returns an *instance*. Both names are spelled the same and
mean opposite things, so a caller reaching for pydantic's parser got a shape
check and a ``bool``, with no error raised at either end. The defect had been in
the tree since the registry was written and was invisible to the test suite,
because every test called the method the author meant.

It surfaced the moment ``mypy --strict`` ran with pydantic's plugin enabled -
which is the argument for that CI stage, stated as a test rather than as prose.

This module is the general form of that check: for every ``BaseModel`` subclass
in the package, no attribute defined on the subclass may collide with a public
``BaseModel`` attribute unless it is a deliberate, documented override. Keeping
it general matters more than the one fix: the next model to add a ``copy``, a
``json``, a ``schema`` or a ``construct`` helper would reintroduce exactly this,
and nothing else in the suite would notice.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Final

import pytest
from pydantic import BaseModel

import neuroharness

#: Names a subclass may define even though ``BaseModel`` also defines them,
#: because the subclass genuinely overrides pydantic's behaviour rather than
#: repurposing the name. An addition here is a decision: it says "this really is
#: the pydantic hook, implemented deliberately".
#: Two spellings are accepted: a bare name is allowed on any model, and
#: ``Class.name`` only on that class. The scoped form exists because the cost of
#: the bare one is invisible - every name added here stops being checked
#: everywhere, so one model's justified override quietly licenses the next
#: model's repurposing of the same name, which is the defect this file is about.
DELIBERATE_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        # The documented post-construction hook. ``ActionClassRegistry`` uses it
        # to build its lookup index once, after validation - which is what the
        # hook is for. Overriding it means implementing pydantic's contract, not
        # borrowing its name for something else.
        "model_post_init",
        # ``WireModel`` re-validates a copy. Same signature, same meaning, one
        # added guarantee: ``BaseModel.model_copy(update=...)`` writes values
        # into the copy without running a single validator, so
        # ``proposal.model_copy(update={"tool": "<a sentence>"})`` produced a
        # Proposal whose tool was prose - past ``extra="forbid"``, past every
        # field constraint, past the ``SEC-07`` shape checks - and it serialised
        # and digested exactly like that. ``frozen=True`` made it look
        # impossible, which is why nobody looked.
        #
        # Declared once here, on the base, so every model in the package
        # inherits the fix. Scoped, because a model that overrode ``model_copy``
        # to mean something *else* is exactly what this file is for catching.
        "WireModel.model_copy",
    }
)

#: Pydantic synthesizes these onto every subclass, so they appear in ``vars()``
#: whether or not anybody wrote them. They are configuration and machinery, not
#: authored methods, and are never collisions.
_SYNTHESIZED: Final[frozenset[str]] = frozenset(
    {"model_config", "model_fields", "model_computed_fields"}
)

#: pydantic's own machinery, which every subclass inherits and none redefines.
_DUNDER_PREFIX: Final[str] = "_"


def _package_modules() -> list[str]:
    """Every module in the package, so a new subpackage is covered on arrival."""
    return [
        name
        for _, name, _ in pkgutil.walk_packages(
            neuroharness.__path__, prefix=f"{neuroharness.__name__}."
        )
    ]


def _model_classes() -> list[type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for module_name in _package_modules():
        module = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseModel) or obj is BaseModel:
                continue
            # Defined here, not imported from pydantic or re-exported.
            if not obj.__module__.startswith(neuroharness.__name__):
                continue
            found.setdefault(f"{obj.__module__}.{obj.__qualname__}", obj)
    return [found[key] for key in sorted(found)]


#: Public attributes ``BaseModel`` itself provides.
_BASE_MODEL_NAMES: Final[frozenset[str]] = frozenset(
    name for name in dir(BaseModel) if not name.startswith(_DUNDER_PREFIX)
)


def test_the_package_defines_models_at_all() -> None:
    """Guard the guard.

    ``_model_classes`` walks the package by import. If that walk ever returns
    nothing - a renamed package, a changed ``__path__``, an import that fails
    silently - every parametrized case below would vanish and the file would
    report green while checking no model at all. A checker that passes by
    finding nothing is the failure mode this whole increment is about.
    """
    assert len(_model_classes()) > 10


def _collisions(model: type[BaseModel]) -> set[str]:
    """Names this class body defines that ``BaseModel`` also defines, unlicensed.

    ``vars(model)`` is deliberate: it reads only what *this* class body defines,
    so an attribute inherited from another model in the package is not reported
    twice, and a field declared on a parent is attributed to the parent.
    """
    declared = {
        name
        for name in vars(model)
        if not name.startswith(_DUNDER_PREFIX) and name not in _SYNTHESIZED
    }
    return {
        name
        for name in declared & _BASE_MODEL_NAMES
        if name not in DELIBERATE_OVERRIDES
        and f"{model.__name__}.{name}" not in DELIBERATE_OVERRIDES
    }


@pytest.mark.parametrize(
    "model",
    _model_classes(),
    ids=lambda model: f"{model.__module__.rsplit('.', 1)[-1]}.{model.__qualname__}",
)
def test_no_model_shadows_a_base_model_attribute(model: type[BaseModel]) -> None:
    """A name that means one thing to pydantic and another here is a trap."""
    collisions = _collisions(model)
    assert not collisions, (
        f"{model.__module__}.{model.__qualname__} defines {sorted(collisions)}, "
        f"which pydantic's BaseModel also defines. Rename, or add "
        f"{model.__name__}.<name> to DELIBERATE_OVERRIDES with a comment "
        f"saying why this really is the pydantic method."
    )


def test_a_scoped_override_licenses_only_the_class_it_names() -> None:
    """The control for the scoped entries: they must not license the name at large.

    An allowlist that quietly widened would be worse than no allowlist, because
    the file would still report green while the rule it enforces had become
    "anybody may redefine ``model_copy``". The two classes below define the same
    method; only the one the entry names is allowed to.
    """

    class WireModel(BaseModel):
        def model_copy(self, *, update: object = None, deep: bool = False) -> BaseModel:
            return self

    class Other(BaseModel):
        def model_copy(self, *, update: object = None, deep: bool = False) -> BaseModel:
            return self

    assert not _collisions(WireModel)
    assert _collisions(Other) == {"model_copy"}


def test_the_check_would_have_caught_the_defect_it_was_written_for() -> None:
    """Without this, a collision set that silently went empty would look clean.

    Constructs the shape the registry used to have and asserts the rule rejects
    it. If ``_BASE_MODEL_NAMES`` ever came back empty - a pydantic release that
    moved its API behind ``__getattr__``, say - every case above would pass
    vacuously and this one would fail.
    """

    class Shadowing(BaseModel):
        def validate(self, key: str) -> bool:  # type: ignore[override]
            return bool(key)

    declared = {
        name
        for name in vars(Shadowing)
        if not name.startswith(_DUNDER_PREFIX) and name not in _SYNTHESIZED
    }
    assert declared & _BASE_MODEL_NAMES == {"validate"}
