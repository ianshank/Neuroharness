"""The suite's own conventions hold, so a selection means what it says.

Two markers are registered (``pyproject.toml``) and both are used to *select* a
subset: CI runs ``pytest -m mutation`` as its own job, and ``-m property`` is how
a contributor runs the slow invariant suite alone. A selection is only as honest
as the markers behind it, and a module that forgets one is invisible to the
selection while still passing in the full run -- so nothing ever goes red.

That is not hypothetical. ``tests/property/test_grammar_agreement.py`` was
written without ``pytestmark = pytest.mark.property`` and ran for two increments
outside every ``-m property`` selection, including the ten properties that hold
the resource-key grammar together. Nothing could have caught it, because the only
symptom is a smaller number in a report nobody compares against anything.

This module compares it against something.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

#: Repository root, from this file rather than from the working directory: the
#: suite must give the same answer whatever directory pytest was started in.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_PROPERTY_DIR: Final[Path] = _REPO_ROOT / "tests" / "property"


def _module_marks(path: Path) -> set[str]:
    """The marker names a module applies to every test in it.

    Read from the source rather than from pytest's own collection, for the
    reason ``test_mutation_fixtures.py`` gives for the same choice: a
    source-level answer stays true whether or not the module was selected,
    skipped or errored.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    marks: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            continue
        for attribute in ast.walk(node.value):
            # `pytest.mark.property`, and the same inside a list for a module
            # that applies more than one.
            if (
                isinstance(attribute, ast.Attribute)
                and isinstance(attribute.value, ast.Attribute)
                and attribute.value.attr == "mark"
            ):
                marks.add(attribute.attr)
    return marks


def _property_modules() -> list[Path]:
    return sorted(p for p in _PROPERTY_DIR.glob("test_*.py"))


def test_there_are_property_modules_to_check() -> None:
    """Guard on the guard.

    ``CONTRIBUTING.md``: *"a checker that passes by finding nothing is the
    failure mode this project is most exposed to. If a test walks a directory or
    a set, assert the walk found something."* This walk found nothing if the
    directory is renamed, and every assertion below would pass.
    """
    modules = _property_modules()
    assert len(modules) >= 4, f"expected the property suite; found {[p.name for p in modules]}"


@pytest.mark.parametrize("path", _property_modules(), ids=lambda p: p.name)
def test_every_property_module_carries_the_property_marker(path: Path) -> None:
    """``-m property`` selects the whole property suite, not most of it.

    A module here without the marker still runs in the full suite, so it is not
    a coverage hole -- it is a *reporting* hole, and the reported number is what
    a reviewer reads when deciding whether the invariants ran.
    """
    marks = _module_marks(path)
    assert "property" in marks, (
        f"{path.name} is in tests/property/ and does not set "
        "`pytestmark = pytest.mark.property`, so `-m property` does not select it"
    )


def test_the_marker_check_reads_a_real_declaration(tmp_path: Path) -> None:
    """The reader finds a marker when one is there and not when it is not.

    Without this pair, :func:`_module_marks` returning an empty set for every
    input would satisfy nothing above and fail everything -- but a version
    returning ``{"property"}`` unconditionally would satisfy all of it.
    """
    marked = tmp_path / "test_marked.py"
    marked.write_text("import pytest\npytestmark = pytest.mark.property\n", encoding="utf-8")
    assert _module_marks(marked) == {"property"}

    bare = tmp_path / "test_bare.py"
    bare.write_text("import pytest\n\n\ndef test_x() -> None:\n    assert True\n", encoding="utf-8")
    assert _module_marks(bare) == set()

    several = tmp_path / "test_several.py"
    several.write_text(
        "import pytest\npytestmark = [pytest.mark.property, pytest.mark.mutation]\n",
        encoding="utf-8",
    )
    assert _module_marks(several) == {"property", "mutation"}


# --- The generated schema describes what the model enforces ------------------


def test_the_resource_key_alias_publishes_its_pattern() -> None:
    """``ResourceKey``'s generated JSON Schema is as narrow as its validator.

    ``AfterValidator`` has no JSON Schema representation. When the alias moved
    from ``StringConstraints(pattern=...)`` to a validator -- necessary, because
    pydantic v2's Rust regex engine has no look-around and the grammar's length
    guard is one -- the generated schema silently became
    ``{"type": "string", "maxLength": 158}``: wider than the model, and wide
    enough to admit `s3:bucket_name`, the exact key the repair had just excluded.

    Nothing in the tree calls ``model_json_schema()`` today, so that was latent
    rather than live. It is asserted anyway: a model whose self-description is
    wider than its behaviour is the same class of defect the alias was repaired
    for, and the first consumer of the generated schema would inherit it silently.
    """
    from pydantic import TypeAdapter

    from neuroharness import grammar
    from neuroharness.models.envelope import ResourceKey

    schema = TypeAdapter(ResourceKey).json_schema()

    assert schema.get("pattern") == grammar.RESOURCE_KEY_PATTERN.pattern, (
        "the generated schema does not carry the grammar's pattern, so it accepts "
        f"keys the model refuses: {schema}"
    )
    assert schema.get("maxLength") == grammar.MAX_RESOURCE_KEY_LENGTH
    assert schema.get("type") == "string"


def test_the_published_pattern_and_the_validator_agree_on_real_keys() -> None:
    """The schema and the model give the same answer, key by key.

    The assertion above compares one string to another. This one checks that the
    string means what the validator means, which is the property a consumer of
    the generated schema actually depends on.
    """
    import re

    from pydantic import TypeAdapter, ValidationError

    from neuroharness.models.envelope import ResourceKey

    adapter = TypeAdapter(ResourceKey)
    published = re.compile(adapter.json_schema()["pattern"])

    cases = [
        "service:example-api/target:production",
        "cluster-prod:svc-a",
        "k8s-namespace:default",
        "s3-bucket:logs",
        "s3:bucket_name",
        "my_kind:foo",
        "repo:-foo",
        "repo:foo",
    ]
    disagreements = []
    for key in cases:
        by_schema = published.fullmatch(key) is not None
        try:
            adapter.validate_python(key)
            by_model = True
        except ValidationError:
            by_model = False
        if by_schema != by_model:
            disagreements.append((key, by_schema, by_model))

    assert not disagreements, (
        f"the published pattern and the validator disagree: {disagreements}"
    )
