"""Acceptance scenarios are data too, and this is what keeps that honest.

Constitution Article VII: acceptance scenarios are written before the code they
accept, and the test a reviewer applies to a change is *"which scenario accepts
it?"*. Section 8 of ``docs/sdd/01-specification.md`` carries forty-five of them.
Three are named by any test.

That gap was invisible, and it was invisible in the way that matters: a written
scenario and an executed scenario look identical from the specification. The
count is not a criticism of the tree - most of these scenarios drive a gateway,
a policy decision point, a broker or a monitor that nobody has built - it is a
number that nobody could state. The mutation catalogue solved the same problem
for fixtures by giving every identifier a checked-in declaration with a
lifecycle state; this is the same mechanism applied to the specification.

Every scenario gets one declaration, and it is one of two things:

* ``referenced`` - a named test mentions it, so a reader can go from the
  scenario to the code that accepts it, and this module checks that the named
  test exists and really does name the scenario;
* ``not_executable`` - the components it drives do not exist, and the
  declaration names the task that owes them.

There is no third state on purpose. "Partially covered", "covered by a fixture"
or "planned" would each be a way to stop counting a scenario without executing
it, and the whole value of the register is that the number it publishes cannot
be improved by editing it.

**The matching is anchored, and that is not a detail.** An unanchored
``A-[0-9][0-9]`` matches ``SHA-256``. An increment plan measured this exact
coverage with that pattern, counted a digest constant in a canonicalisation test
as an acceptance scenario, and published the wrong number in the section arguing
that unenforced claims rot. The near-miss is pinned in a regression row below.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests"
SCENARIO_DIR: Final[Path] = TESTS_ROOT / "fixtures" / "scenarios"
SPECIFICATION: Final[Path] = REPO_ROOT / "docs" / "sdd" / "01-specification.md"
WORK_BREAKDOWN: Final[Path] = REPO_ROOT / "docs" / "sdd" / "03-work-breakdown.md"

#: The state that claims a test executes the scenario.
REFERENCED: Final[str] = "referenced"

#: A scenario identifier, anchored on both sides. The trailing ``[a-z]?`` is
#: real: ``A-12b`` is a scenario in its own right, added when bundle integrity
#: was split out of engine availability.
_SCENARIO_ID_RE: Final[re.Pattern[str]] = re.compile(r"\bA-[0-9]{2}[a-z]?\b")

#: ``  Scenario: A-01 Undeclared tool is denied (WF-01, MUT-01)`` inside the
#: specification's Gherkin block.
_SCENARIO_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*Scenario:\s+(A-[0-9]{2}[a-z]?)\s+(.+?)\s*$"
)

#: A work-breakdown task identifier, used to check that an owed task exists.
_TASK_ID_RE: Final[re.Pattern[str]] = re.compile(r"\bP[0-4]-[0-9]{2}[a-z]?\b")


def _specification_scenarios() -> dict[str, str]:
    """Identifier to title, read from the specification's own scenario lines."""
    found: dict[str, str] = {}
    for line in SPECIFICATION.read_text(encoding="utf-8").splitlines():
        match = _SCENARIO_LINE_RE.match(line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def _declarations() -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCENARIO_DIR.glob("A-*.json"))
    }


def _tests_naming_scenarios() -> dict[str, set[str]]:
    """Scenario identifier to the ``path::test_name`` locations that name it.

    A static walk of the checked-in source, like the mutation checker's: the
    question is what the source claims, and a source-level answer stays true
    whether or not the test was selected, skipped or errored.

    This module excludes itself, as the mutation checker does. Its regression
    row below deliberately contains a string that *looks* like a scenario
    citation, and a checker that counted its own examples would report coverage
    it invented.
    """
    naming: dict[str, set[str]] = {}
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            for identifier in _SCENARIO_ID_RE.findall(segment):
                location = f"{path.relative_to(REPO_ROOT)}::{node.name}"
                naming.setdefault(identifier, set()).add(location)
    return naming


SCENARIOS: Final[dict[str, str]] = _specification_scenarios()
DECLARATIONS: Final[dict[str, dict[str, Any]]] = _declarations()
NAMING_TESTS: Final[dict[str, set[str]]] = _tests_naming_scenarios()

SCHEMA: Final[dict[str, Any]] = json.loads(
    (SCENARIO_DIR / "_scenario.schema.json").read_text(encoding="utf-8")
)
VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(SCHEMA)


# --- guards on the guard -----------------------------------------------------


def test_the_specification_still_parses_as_scenarios() -> None:
    """A changed Gherkin block would make every check below vacuous.

    A floor rather than "not empty", because the failure mode is a parser that
    reads some of the block: the register would then look complete while the
    scenarios it missed went uncounted, which is the state this module exists to
    end.
    """
    assert len(SCENARIOS) > 40, (
        f"only {len(SCENARIOS)} scenarios parsed out of {SPECIFICATION.relative_to(REPO_ROOT)}; "
        "the scenario block changed shape and this register is no longer reading it"
    )


# --- the specification and the declarations name the same scenarios ----------


def test_every_specified_scenario_has_a_declaration() -> None:
    """The check that makes an unexecuted scenario countable rather than invisible."""
    missing = sorted(SCENARIOS.keys() - DECLARATIONS.keys())
    assert not missing, (
        f"the specification carries {', '.join(missing)} with no declaration in "
        f"{SCENARIO_DIR.relative_to(REPO_ROOT)}; a scenario nobody declared is a scenario "
        "nobody can say is executed"
    )


def test_every_declaration_names_a_specified_scenario() -> None:
    """The other direction: a declaration for a withdrawn scenario inflates the register."""
    orphans = sorted(DECLARATIONS.keys() - SCENARIOS.keys())
    assert not orphans, (
        f"{', '.join(orphans)} are declared and appear in no ``Scenario:`` line of "
        f"{SPECIFICATION.relative_to(REPO_ROOT)}"
    )


def test_the_declaration_schema_is_itself_valid() -> None:
    """A malformed schema validates everything, silently.

    ``Draft202012Validator(schema)`` does not check the schema it is given: a
    misspelled keyword is ignored rather than rejected, so a constraint can be
    deleted by typo and every declaration still passes. ``check_schema`` is the
    only thing that notices.
    """
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("identifier", sorted(DECLARATIONS))
def test_each_declaration_conforms_to_its_schema(identifier: str) -> None:
    errors = sorted(
        VALIDATOR.iter_errors(DECLARATIONS[identifier]),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, "\n".join(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )


@pytest.mark.parametrize("identifier", sorted(DECLARATIONS))
def test_the_declaration_matches_its_filename(identifier: str) -> None:
    assert DECLARATIONS[identifier]["id"] == identifier


@pytest.mark.parametrize("identifier", sorted(DECLARATIONS))
def test_the_declaration_carries_the_scenarios_own_title(identifier: str) -> None:
    """A renamed scenario must surface here rather than drift.

    The title is the only human-readable link between the identifier and what it
    means. If the specification renames ``A-24`` from a lease to something else
    and the register keeps the old words, the register describes a scenario that
    no longer exists while still counting it.
    """
    assert DECLARATIONS[identifier]["title"] == SCENARIOS[identifier], (
        f"{identifier} is declared as {DECLARATIONS[identifier]['title']!r} and specified as "
        f"{SCENARIOS[identifier]!r}"
    )


# --- a claimed reference must be backed by a test, and the reverse -----------


@pytest.mark.parametrize(
    "identifier",
    sorted(i for i, d in DECLARATIONS.items() if d["state"] == REFERENCED),
)
def test_a_referenced_scenario_is_named_by_the_test_it_claims(identifier: str) -> None:
    """Show the test, and show that the test really names the scenario.

    Both halves. A path that no longer resolves is a stale claim; a path that
    resolves to a test which never mentions the scenario is worse, because it
    reads as verified and there is nothing to notice. That is the same inversion
    the mutation register catches for fixtures - a state claiming a gate no test
    exercises - moved one level up, to the scenario.
    """
    claimed = set(DECLARATIONS[identifier]["referenced_by"])
    actual = NAMING_TESTS.get(identifier, set())
    unsupported = sorted(claimed - actual)
    assert not unsupported, (
        f"{identifier} says it is referenced by {', '.join(unsupported)}, which does not name "
        "it; the test was renamed, the citation was removed, or the declaration was written "
        "from intent rather than from the source"
    )


def test_no_scenario_a_test_names_is_declared_unexecutable() -> None:
    """A scenario a test already exercises must not read as owed to a future task.

    Benign-looking and worth catching in its own right: the register understates
    coverage, the task named as owing it has nothing to do, and the next person
    to look writes a test that already exists.
    """
    stale = sorted(
        f"{identifier} (named by {', '.join(sorted(locations))})"
        for identifier, locations in NAMING_TESTS.items()
        if identifier in DECLARATIONS and DECLARATIONS[identifier]["state"] != REFERENCED
    )
    assert not stale, (
        "these scenarios are declared not executable and a test already names them; "
        "promote the declaration:\n  " + "\n  ".join(stale)
    )


def test_every_scenario_a_test_names_is_declared() -> None:
    """A citation of a scenario the specification does not define accepts nothing."""
    undeclared = sorted(
        f"{identifier} (named by {', '.join(sorted(locations))})"
        for identifier, locations in NAMING_TESTS.items()
        if identifier not in DECLARATIONS
    )
    assert not undeclared, (
        "these tests name scenarios with no declaration:\n  " + "\n  ".join(undeclared)
    )


@pytest.mark.parametrize(
    "identifier",
    sorted(i for i, d in DECLARATIONS.items() if d["state"] != REFERENCED),
)
def test_an_unexecutable_scenario_names_a_task_that_exists(identifier: str) -> None:
    """A scenario owed by nobody is a scenario nobody writes.

    The identifier is checked against the work breakdown rather than accepted on
    its shape: a plausible-looking task id is how an owner disappears, because
    the row reads as assigned and no schedule contains it.
    """
    owed_by = DECLARATIONS[identifier]["owed_by"]
    tasks = set(_TASK_ID_RE.findall(WORK_BREAKDOWN.read_text(encoding="utf-8")))
    assert owed_by in tasks, (
        f"{identifier} is owed by {owed_by}, which is not a task in "
        f"{WORK_BREAKDOWN.relative_to(REPO_ROOT)}"
    )


# --- the checker's own reading of a near-miss --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The measurement error this register was built after. `SHA-256`
        # contains `A-25`, and an unanchored pattern reported a canonicalisation
        # test as executing an acceptance scenario.
        ("the SHA-256 digest of the canonical form", []),
        ("SHA-256", []),
        # The anchor must not cost a real citation in the same sentence.
        ("SHA-256 is asserted by A-25", ["A-25"]),
        ("scenario ``A-16``", ["A-16"]),
        ("A-12b", ["A-12b"]),
        # Neither a shorter nor a longer shape is a scenario.
        ("A-1", []),
        ("A-123", []),
    ],
)
def test_the_anchored_pattern_reads_only_real_scenarios(
    text: str, expected: list[str]
) -> None:
    """The regression row for the anchoring, kept where the anchoring is used.

    The wrong answer here is not an exception but a number that is too large by
    one, and a number is what gets published.
    """
    assert _SCENARIO_ID_RE.findall(text) == expected
