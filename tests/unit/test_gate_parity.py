"""The Makefile and the workflow run the same gates.

``Makefile`` exists so a contributor can reproduce CI locally, and that is only
worth having while the two agree. A Makefile that runs less than the pipeline is
worse than none: it is trusted, so the first place the missing gate appears is a
red check on a pushed branch, which is the loop the file was written to close.

Parity is asserted over *what is checked*, not over command text. The workflow
interpolates ``${{ env.MIN_COVERAGE }}``, installs tooling, and carries
``actions/checkout`` steps that have no local equivalent; requiring byte equality
would force the Makefile to grow noise and would fail on every unrelated workflow
edit. What must not drift is the set of test modules each side names, the
coverage floor, and the marker selections -- because those are the gate.

The direction that matters is **workflow ⊆ Makefile**: every module CI blocks on
must be reachable from ``make gate``. The reverse is deliberately allowed. The
Makefile names ``tools/render_schema_patterns.py --check`` and a resolver branch
gate that the workflow reaches only through the full suite; a local target that
checks *more* is not a drift, it is the point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_MAKEFILE: Final[Path] = _REPO_ROOT / "Makefile"
_WORKFLOW: Final[Path] = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: A pytest target that is a path into `tests/`. Deliberately not a general
#: argument parser: what is being compared is which modules are named, and a
#: regex that finds paths finds exactly that.
_TEST_PATH_RE: Final[re.Pattern[str]] = re.compile(r"tests/[\w/]+\.py")

#: `--cov-fail-under=90` or `--cov-fail-under=${{ env.MIN_COVERAGE }}`.
_FAIL_UNDER_RE: Final[re.Pattern[str]] = re.compile(r"--cov-fail-under=([^\s\\]+)")

#: `MIN_COVERAGE: "90"` in the workflow, `MIN_COVERAGE ?= 90` in the Makefile.
_WORKFLOW_FLOOR_RE: Final[re.Pattern[str]] = re.compile(r'MIN_COVERAGE:\s*"?(\d+)"?')
_MAKEFILE_FLOOR_RE: Final[re.Pattern[str]] = re.compile(r"MIN_COVERAGE\s*\?=\s*(\d+)")


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} is missing; gate parity cannot be checked without it"
    return path.read_text(encoding="utf-8")


def _named_test_modules(text: str) -> set[str]:
    return set(_TEST_PATH_RE.findall(text))


def test_both_files_exist_and_name_test_modules() -> None:
    """Guard on the guard.

    Every assertion below compares two sets. If either extraction silently
    returned nothing -- a renamed workflow, a changed recipe syntax -- the
    comparisons would pass while checking nothing.
    """
    workflow_modules = _named_test_modules(_read(_WORKFLOW))
    makefile_modules = _named_test_modules(_read(_MAKEFILE))

    assert len(workflow_modules) >= 8, (
        f"the workflow names {len(workflow_modules)} test modules; the extraction has broken"
    )
    assert len(makefile_modules) >= 8, (
        f"the Makefile names {len(makefile_modules)} test modules; the extraction has broken"
    )


@pytest.mark.parametrize("module", sorted(_named_test_modules(_read(_WORKFLOW))))
def test_every_module_ci_blocks_on_is_reachable_from_make_gate(module: str) -> None:
    """Workflow ⊆ Makefile, one case per module so the failure names the gap."""
    makefile = _read(_MAKEFILE)
    assert module in makefile, (
        f"{module} is named by a CI job and by no Makefile target, so `make gate` is "
        "weaker than the pipeline it stands in for"
    )


def test_every_module_the_makefile_names_exists() -> None:
    """A target pointing at a deleted module is a gate that silently stopped."""
    for module in sorted(_named_test_modules(_read(_MAKEFILE))):
        assert (_REPO_ROOT / module).is_file(), (
            f"the Makefile names {module}, which does not exist"
        )


def test_the_coverage_floor_is_one_number() -> None:
    """Three declarations of the floor, and they agree.

    ``pyproject.toml`` holds it so a bare ``pytest --cov`` enforces it, the
    workflow holds it so the number is visible where the job is read, and the
    Makefile holds it so the local run matches. Three copies is two too many to
    leave unchecked.
    """
    workflow_floor = _WORKFLOW_FLOOR_RE.search(_read(_WORKFLOW))
    makefile_floor = _MAKEFILE_FLOOR_RE.search(_read(_MAKEFILE))
    pyproject = _read(_REPO_ROOT / "pyproject.toml")
    pyproject_floor = re.search(r"fail_under\s*=\s*(\d+)", pyproject)

    assert workflow_floor, "the workflow no longer declares MIN_COVERAGE"
    assert makefile_floor, "the Makefile no longer declares MIN_COVERAGE"
    assert pyproject_floor, "pyproject.toml no longer declares a coverage fail_under"

    floors = {
        "ci.yml": workflow_floor.group(1),
        "Makefile": makefile_floor.group(1),
        "pyproject.toml": pyproject_floor.group(1),
    }
    assert len(set(floors.values())) == 1, f"the coverage floor disagrees with itself: {floors}"


def test_the_marker_selections_match() -> None:
    """``-m mutation`` is a CI job; the Makefile must run the same selection."""
    workflow = _read(_WORKFLOW)
    makefile = _read(_MAKEFILE)

    assert "-m mutation" in workflow, "the workflow no longer runs the mutation selection"
    assert "-m mutation" in makefile, (
        "`make mutation` does not run `-m mutation`, so the local gate skips the stage "
        "whose specification says no manual override exists"
    )


def test_the_resolver_gate_is_declared_where_the_specification_asks_for_it() -> None:
    """Governance section 3, stage 2: "resolver 100% branches".

    Pinned here because it is the one gate in this file that the workflow reaches
    only as its own step: a floor of 100 on ``neuroharness.resolve``. If the
    number is ever lowered to make a red build green, this fails and names it.
    """
    makefile = _read(_MAKEFILE)
    resolver_floors = [
        match.group(1)
        for match in _FAIL_UNDER_RE.finditer(makefile)
        if "neuroharness.resolve" in makefile[max(0, match.start() - 200) : match.start()]
    ]
    assert resolver_floors == ["100"], (
        f"the resolver branch gate is declared at {resolver_floors or 'nothing'}; the "
        "specification asks for 100"
    )
