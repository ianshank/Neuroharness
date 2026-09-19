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

The direction that matters is **workflow ⊆ the closure of ``gate-all``**: every
module CI blocks on must be reachable from ``make gate-all``, which is the
aggregate that covers all eight jobs. Not ``make gate`` -- that one deliberately
excludes the external security scans so it stays runnable without gitleaks
installed, and asserting against it would let a blocking job go uncovered.

The reverse direction is deliberately allowed. The Makefile names
``tools/render_schema_patterns.py --check`` and a resolver branch gate that the
workflow reaches only through the full suite; a local target that checks *more*
is not a drift, it is the point.
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


#: `target: prereq prereq ## comment` at the start of a line. `.PHONY` and
#: pattern rules are filtered out by the caller.
_TARGET_RULE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<target>[a-z][a-z0-9-]*):(?P<prereqs>[^=#\n]*)", re.MULTILINE
)

#: The aggregate that must cover every blocking CI job.
_AGGREGATE_TARGET: Final[str] = "gate-all"


def _prerequisites(makefile: str) -> dict[str, list[str]]:
    """target -> its declared prerequisites, with line continuations joined."""
    joined = makefile.replace("\\\n", " ")
    graph: dict[str, list[str]] = {}
    for match in _TARGET_RULE_RE.finditer(joined):
        prereqs = match.group("prereqs").split("##")[0].split()
        graph[match.group("target")] = prereqs
    return graph


def _recipe_lines(makefile: str, target: str) -> list[str]:
    """The tab-indented recipe of one target."""
    joined = makefile.replace("\\\n", " ")
    lines = joined.splitlines()
    out: list[str] = []
    collecting = False
    for line in lines:
        rule = _TARGET_RULE_RE.match(line)
        if rule is not None:
            collecting = rule.group("target") == target
            continue
        if collecting and line.startswith("\t"):
            out.append(line)
        elif collecting and line.strip() and not line.startswith("\t"):
            collecting = False
    return out


def _closure_commands(makefile: str, root: str) -> str:
    """Every recipe line reachable from ``root`` through its prerequisites.

    This is what ``make <root>`` would actually run, computed from the
    dependency graph rather than from the file as a whole. Static parsing
    rather than shelling out to ``make -n``: it needs no subprocess and no
    ``make`` on the runner, and it is the choice ``test_mutation_fixtures.py``
    already makes and justifies for its own collection -- a source-level answer
    stays true whether or not the tool was available.
    """
    graph = _prerequisites(makefile)
    seen: set[str] = set()
    stack = [root]
    commands: list[str] = []
    while stack:
        target = stack.pop()
        if target in seen or target not in graph:
            continue
        seen.add(target)
        commands.extend(_recipe_lines(makefile, target))
        stack.extend(graph[target])
    return "\n".join(commands)


def test_the_aggregate_target_exists_and_reaches_several_targets() -> None:
    """Guard on the guard: the closure is computed, not empty."""
    makefile = _read(_MAKEFILE)
    graph = _prerequisites(makefile)
    assert _AGGREGATE_TARGET in graph, (
        f"the Makefile declares no {_AGGREGATE_TARGET!r} target; the parity check below "
        "would compare against nothing"
    )
    commands = _closure_commands(makefile, _AGGREGATE_TARGET)
    # `$(PYTEST)`, not `pytest`: the recipes invoke the variable, and counting
    # the lowercase word here would find almost nothing and report a healthy
    # walk as a broken one.
    invocations = commands.count("$(PYTEST)")
    assert invocations >= 8, (
        f"the closure of {_AGGREGATE_TARGET!r} invokes $(PYTEST) {invocations} times; "
        "the dependency walk has broken"
    )
    assert len(_named_test_modules(commands)) >= 8, (
        "the closure names too few test modules for the comparison below to mean anything"
    )


@pytest.mark.parametrize("module", sorted(_named_test_modules(_read(_WORKFLOW))))
def test_every_module_ci_blocks_on_is_reachable_from_the_aggregate(module: str) -> None:
    """Workflow ⊆ the closure of ``gate-all``, one case per module.

    The assertion this module was written to make, and did not. The first
    version checked that each CI-named module appeared *somewhere in the
    Makefile text*, which a module used only by an unreachable target satisfies:
    `tests/unit/test_secret_scan_allowlist.py` lived in `security`, `gate` did
    not depend on `security`, and the check passed while `make gate` genuinely
    did not run it. A parity test that proves less than its docstring claims is
    the vacuity this file exists to prevent, one level up.
    """
    reachable = _closure_commands(_read(_MAKEFILE), _AGGREGATE_TARGET)
    assert module in reachable, (
        f"{module} is named by a CI job and is not reachable from `make {_AGGREGATE_TARGET}`, "
        "so the local gate is weaker than the pipeline it stands in for"
    )


@pytest.mark.parametrize("module", sorted(_named_test_modules(_read(_WORKFLOW))))
def test_every_module_ci_blocks_on_is_named_somewhere(module: str) -> None:
    """The weaker check, kept: a module named in no target at all.

    Distinct from the reachability failure above -- "named nowhere" and "named
    in a target nothing depends on" are different mistakes with different fixes,
    and a failure should say which one happened.
    """
    assert module in _read(_MAKEFILE), (
        f"{module} is named by a CI job and by no Makefile target at all"
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


#: A workflow job, split on the two-space-indented key that names it.
_JOB_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"\n  (?=[a-z][\w-]*:\n)")

#: The tooling that only exists after `pip install -e ".[dev]"`.
_NEEDS_INSTALL: Final[tuple[str, ...]] = (
    "python -m pytest",
    "python -m ruff",
    "python -m mypy",
)


def _jobs(workflow: str) -> dict[str, str]:
    return {
        chunk.strip().split(":", 1)[0]: chunk
        for chunk in _JOB_SPLIT_RE.split(workflow)
        if chunk.strip() and ":" in chunk
    }


def test_every_job_that_runs_python_tooling_installs_it_first() -> None:
    """A job cannot run ``python -m pytest`` without the dev extra.

    This is a regression test for a defect this file's own change introduced.
    The ``security`` job needed no Python packages -- gitleaks is a downloaded
    binary and pip-audit installs itself -- so it had no install step. Adding a
    pytest step to it produced ``No module named pytest`` on the first CI run,
    which is the cheapest possible failure and also the most avoidable: seven
    other jobs carry the line, and the eighth did not.

    Asserted over every job rather than over the one that broke, because the
    next job added will be the next one to forget.
    """
    jobs = _jobs(_read(_WORKFLOW))
    assert len(jobs) >= 8, f"found {len(jobs)} jobs; the split has broken: {sorted(jobs)}"

    for name, body in sorted(jobs.items()):
        used = [tool for tool in _NEEDS_INSTALL if tool in body]
        if not used:
            continue
        assert 'pip install -e ".[dev]"' in body, (
            f"the {name!r} job runs {used} and never installs the dev extra, so the step "
            "fails with a missing module rather than with the finding it exists to report"
        )


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
