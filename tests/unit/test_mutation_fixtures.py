"""The mutation catalogue is data, and these are the checks that keep it honest.

Constitution Article III: *every hard gate has at least one negative mutation
fixture that proves it blocks; a gate without a killing fixture does not exist.*

That sentence is unenforceable unless something can tell the difference between
a fixture that was never written, a fixture that was written and deleted, and a
fixture that is deliberately not due yet. An empty directory looks identical to
all three. So every identifier in the evaluation plan's catalogue has a checked-in
declaration carrying its lifecycle state (evaluation plan section 1a), and the
tests below close the loop in both directions:

* the catalogue, the declarations and the increment plan name the same fixtures
  in the same states - three documents that would otherwise drift apart, each
  one looking authoritative on its own;
* every fixture declared ``active`` or ``partial`` is named by a test that
  carries ``@pytest.mark.mutation``, so a state cannot claim a gate no test
  exercises;
* every such test names a fixture that is declared and not ``reserved``, so a
  test cannot quietly claim to kill a fixture whose gate nobody has built.

None of this replaces the killing fixtures themselves. It is the bookkeeping
that makes their absence loud, which is the part that was missing: with the
directory empty, nothing at all would have noticed a hard gate shipped without
one.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

from neuroharness.models.common import Verdict
from neuroharness.reason import ReasonName

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
TESTS_ROOT: Final[Path] = REPO_ROOT / "tests"
FIXTURE_DIR: Final[Path] = TESTS_ROOT / "fixtures" / "mutations"
EVALUATION_PLAN: Final[Path] = REPO_ROOT / "docs" / "sdd" / "05-evaluation-plan.md"
INCREMENT_PLAN: Final[Path] = REPO_ROOT / "docs" / "sdd" / "07-increment-1-plan.md"

#: The marker a killing fixture carries. Named once: a test that spells it
#: differently is a test the mutation stage does not select.
MUTATION_MARKER: Final[str] = "mutation"

#: States that assert a gate exists here and is exercised here.
CLAIMING_STATES: Final[frozenset[str]] = frozenset({"active", "partial"})

_FIXTURE_ID_RE: Final[re.Pattern[str]] = re.compile(r"MUT-[0-9]{2,3}[a-z]?")

#: ``test_mut_13_...`` names ``MUT-13``, and ``test_mut_12b_...`` names
#: ``MUT-12b``. Test names are lowercase and cannot carry the hyphen, so the two
#: spellings are reconciled here rather than in every test file.
#:
#: The lookahead is ``(?=_|$)`` rather than ``\b`` on purpose: an underscore is a
#: word character, so ``\b`` never matches between ``mut_13`` and the ``_`` that
#: follows it, and every test that named its fixture only in its name would have
#: gone uncounted - the checker would have looked like it was working.
_FIXTURE_IN_NAME_RE: Final[re.Pattern[str]] = re.compile(r"mut_([0-9]{2,3})([a-z]?)(?=_|$)")

#: A catalogue row: ``| `MUT-13` | gate | mutation | expected | 1 |``.
_CATALOGUE_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"^\|\s*`(MUT-[0-9]{2,3}[a-z]?)`\s*\|.*\|\s*[0-9]\s*\|\s*$"
)

#: An increment-plan row: ``| `MUT-13` | what this increment owns |`` (and, for
#: ``partial``, a third column naming what is still missing).
_PLAN_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^\|\s*`(MUT-[0-9]{2,3}[a-z]?)`\s*\|")


def _load_declarations() -> dict[str, dict[str, Any]]:
    declarations: dict[str, dict[str, Any]] = {}
    for path in sorted(FIXTURE_DIR.glob("MUT-*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        declarations[path.stem] = document
    return declarations


def _catalogue_ids() -> frozenset[str]:
    """Every identifier the evaluation plan's table declares."""
    return frozenset(
        match.group(1)
        for line in EVALUATION_PLAN.read_text(encoding="utf-8").splitlines()
        if (match := _CATALOGUE_ROW_RE.match(line))
    )


def _increment_plan_states() -> dict[str, str]:
    """The states section 4 of the increment plan claims, read from its tables.

    Read rather than restated. A constant here would be a fourth place for the
    same fact to live, and the drift this module exists to catch is exactly the
    drift between places that each look authoritative.
    """
    states: dict[str, str] = {}
    current: str | None = None
    for line in INCREMENT_PLAN.read_text(encoding="utf-8").splitlines():
        lowered = line.lower()
        if lowered.startswith("**`active`"):
            current = "active"
        elif lowered.startswith("**`partial`"):
            current = "partial"
        elif lowered.startswith("**returned to `reserved`"):
            current = None
        elif line.startswith("## "):
            current = None
        elif current is not None and (match := _PLAN_ROW_RE.match(line)):
            states[match.group(1)] = current
    return states


class _KillingTest:
    """One test function and the fixtures it claims to kill."""

    __slots__ = ("path", "name", "marked", "fixtures")

    def __init__(self, path: Path, name: str, marked: bool, fixtures: frozenset[str]) -> None:
        self.path = path
        self.name = name
        self.marked = marked
        self.fixtures = fixtures

    @property
    def location(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}::{self.name}"


def _is_mutation_marker(expression: ast.expr) -> bool:
    """``pytest.mark.mutation``, however the module spelled the import."""
    target = expression.func if isinstance(expression, ast.Call) else expression
    return isinstance(target, ast.Attribute) and target.attr == MUTATION_MARKER


def _module_is_marked(tree: ast.Module) -> bool:
    """True when a module-level ``pytestmark`` applies the marker to every test.

    pytest treats ``pytestmark = pytest.mark.mutation`` as equivalent to
    decorating each test in the file, so a checker that only read decorators
    would report a whole mutation module as unmarked - and would then be
    switched off, which is the usual fate of a checker that cries wolf.
    """
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            continue
        value = node.value
        if value is None:
            continue
        candidates = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
        if any(_is_mutation_marker(candidate) for candidate in candidates):
            return True
    return False


def _collect_killing_tests() -> list[_KillingTest]:
    """Every test in the suite, with the fixtures its name and body mention.

    A static walk rather than a pytest collection hook: the question is what the
    checked-in source claims, and a source-level answer stays true whether or
    not the test was selected, skipped or errored during collection.
    """
    found: list[_KillingTest] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        module_marked = _module_is_marked(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            fixtures = set(_FIXTURE_ID_RE.findall(segment))
            fixtures.update(
                f"MUT-{number}{suffix}"
                for number, suffix in _FIXTURE_IN_NAME_RE.findall(node.name)
            )
            if not fixtures:
                continue
            marked = module_marked or any(
                _is_mutation_marker(decorator) for decorator in node.decorator_list
            )
            found.append(_KillingTest(path, node.name, marked, frozenset(fixtures)))
    return found


DECLARATIONS: Final[dict[str, dict[str, Any]]] = _load_declarations()
CATALOGUE_IDS: Final[frozenset[str]] = _catalogue_ids()
KILLING_TESTS: Final[list[_KillingTest]] = _collect_killing_tests()

VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(
    json.loads((FIXTURE_DIR / "_declaration.schema.json").read_text(encoding="utf-8"))
)


def _claimed_by_marked_tests() -> dict[str, list[str]]:
    claimed: dict[str, list[str]] = {}
    for test in KILLING_TESTS:
        if not test.marked:
            continue
        for fixture in test.fixtures:
            claimed.setdefault(fixture, []).append(test.location)
    return claimed


CLAIMED: Final[dict[str, list[str]]] = _claimed_by_marked_tests()


# --- the three documents must name the same fixtures -------------------------


def test_the_catalogue_is_not_empty() -> None:
    """A guard on the guards: an unreadable plan would make every test below vacuous."""
    assert len(CATALOGUE_IDS) > 30, (
        f"only {len(CATALOGUE_IDS)} fixtures parsed out of the evaluation plan; "
        "the table format changed and these checks are no longer reading it"
    )


def test_every_catalogued_fixture_has_a_declaration() -> None:
    """The check that makes a missing killing fixture loud rather than invisible."""
    missing = sorted(CATALOGUE_IDS - DECLARATIONS.keys())
    assert not missing, (
        f"the evaluation plan catalogues {', '.join(missing)} with no declaration in "
        f"{FIXTURE_DIR.relative_to(REPO_ROOT)}; a fixture nobody declared is a gate "
        "nobody can be shown to have"
    )


def test_every_declaration_is_catalogued() -> None:
    """The other direction: a fixture with no entry in the plan answers to nobody."""
    orphans = sorted(DECLARATIONS.keys() - CATALOGUE_IDS)
    assert not orphans, (
        f"{', '.join(orphans)} are declared but absent from the evaluation plan's "
        "catalogue; add the row or retire the fixture with an ADR"
    )


@pytest.mark.parametrize("fixture_id", sorted(DECLARATIONS))
def test_each_declaration_conforms_to_its_schema(fixture_id: str) -> None:
    errors = sorted(
        VALIDATOR.iter_errors(DECLARATIONS[fixture_id]),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, "\n".join(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )


@pytest.mark.parametrize("fixture_id", sorted(DECLARATIONS))
def test_the_declaration_matches_its_filename(fixture_id: str) -> None:
    assert DECLARATIONS[fixture_id]["id"] == fixture_id


def test_the_declared_states_match_the_increment_plan() -> None:
    """Section 4 of the increment plan and these declarations are one fact.

    Two documents stating the same thing is one document and one place for it to
    go stale. Whichever is edited, the other must be edited in the same change.
    """
    planned = _increment_plan_states()
    declared = {
        fixture_id: document["state"]
        for fixture_id, document in DECLARATIONS.items()
        if document["state"] in CLAIMING_STATES
    }
    assert declared == planned, (
        "the increment plan and the fixture declarations disagree:\n"
        f"  only in the plan:        {sorted(planned.items() - declared.items())}\n"
        f"  only in the declarations: {sorted(declared.items() - planned.items())}"
    )


# --- a claimed state must be backed by a test, and the reverse ---------------


@pytest.mark.parametrize(
    "fixture_id",
    sorted(fid for fid, doc in DECLARATIONS.items() if doc["state"] in CLAIMING_STATES),
)
def test_a_claiming_fixture_is_killed_by_a_marked_test(fixture_id: str) -> None:
    """``active`` and ``partial`` assert a gate exists *here*. Show the test.

    Without this, a fixture could be promoted in a document while the gate it
    names went unexercised - which is the inversion Article III forbids, dressed
    as progress.
    """
    assert fixture_id in CLAIMED, (
        f"{fixture_id} is declared {DECLARATIONS[fixture_id]['state']!r} but no test "
        f"marked @pytest.mark.{MUTATION_MARKER} names it. Either write the killing "
        "fixture or return the declaration to 'reserved'."
    )


def test_no_test_claims_to_kill_a_reserved_fixture() -> None:
    """A test that kills a reserved fixture means the declaration is stale.

    Benign-looking and worth catching: the fixture reads as not-yet-built in
    every document while a test quietly proves it is, so the plan understates
    what is covered and nobody revisits it.
    """
    stale = sorted(
        f"{fixture_id} (killed by {', '.join(sorted(locations))})"
        for fixture_id, locations in CLAIMED.items()
        if DECLARATIONS.get(fixture_id, {}).get("state") == "reserved"
    )
    assert not stale, (
        "these fixtures are declared 'reserved' but a marked test already kills "
        "them; promote the declaration:\n  " + "\n  ".join(stale)
    )


def test_every_fixture_a_test_names_is_declared() -> None:
    undeclared = sorted(
        f"{fixture_id} (named by {', '.join(sorted(locations))})"
        for fixture_id, locations in CLAIMED.items()
        if fixture_id not in DECLARATIONS
    )
    assert not undeclared, (
        "these tests name fixtures with no declaration:\n  " + "\n  ".join(undeclared)
    )


def test_a_test_naming_a_fixture_carries_the_marker() -> None:
    """Naming a fixture without the marker hides it from the mutation stage.

    The evaluation plan runs the mutation fixtures as their own blocking stage,
    selected by marker. A killing test that is not selected there proves nothing
    where it counts, however green it is in the unit run.
    """
    unmarked = sorted(
        f"{test.location} names {', '.join(sorted(test.fixtures))}"
        for test in KILLING_TESTS
        if not test.marked
    )
    assert not unmarked, (
        f"these tests name a mutation fixture without @pytest.mark.{MUTATION_MARKER}:\n  "
        + "\n  ".join(unmarked)
    )


# --- the declarations say something, rather than merely existing -------------


@pytest.mark.parametrize("fixture_id", sorted(DECLARATIONS))
def test_the_expectation_names_an_outcome(fixture_id: str) -> None:
    """"Something fails" kills nothing.

    A fixture earns its place by pinning *which* refusal the harness must
    produce. A mutation that merely produces some error passes against a harness
    that fails for an unrelated reason, which is how a fixture outlives the gate
    it was written for.

    The vocabulary is read from the code - the ``Verdict`` enum and the closed
    reason catalogue - rather than listed here. A list would be a third copy of
    a closed set, and it would go stale in the direction that matters: a new
    reason name would read as vague prose.
    """
    expected = DECLARATIONS[fixture_id]["expected"].lower()
    vocabulary = {verdict.value.lower() for verdict in Verdict}
    vocabulary |= {reason.value.lower() for reason in ReasonName}
    #: Outcomes the catalogue states as an effect rather than as a code, because
    #: the gate is the absence of authority rather than a particular refusal.
    vocabulary |= {"no token", "void", "superseded", "rejected", "refused", "no claims"}

    assert any(token in expected for token in vocabulary), (
        f"{fixture_id} expects {DECLARATIONS[fixture_id]['expected']!r}, which names "
        "neither a verdict, a reason code nor a stated effect; a fixture whose "
        "expectation is vague cannot kill anything"
    )


# --- the checker's own reading of a test name --------------------------------


@pytest.mark.parametrize(
    ("test_name", "expected"),
    [
        ("test_mut_13_evidence_failure_blocks_token_issuance", {"MUT-13"}),
        ("test_mut_30_the_rule_is_not_specific_to_one_enumeration", {"MUT-30"}),
        ("test_mut_12b_bundle_integrity", {"MUT-12b"}),
        ("test_mut_09", {"MUT-09"}),
        ("test_something_unrelated", set()),
        ("test_mutation_helpers", set()),
    ],
)
def test_a_fixture_named_only_in_a_test_name_is_still_counted(
    test_name: str, expected: set[str]
) -> None:
    """The checker reads test names, and once read them wrong.

    ``\\b`` never matches between ``mut_13`` and the underscore after it, because
    an underscore is a word character. Every test that named its fixture only in
    its name went uncounted, and the failure looked exactly like a missing
    fixture - a checker that is wrong in the direction of *more* work is the
    hardest kind to notice is broken.
    """
    found = {
        f"MUT-{number}{suffix}"
        for number, suffix in _FIXTURE_IN_NAME_RE.findall(test_name)
    }
    assert found == expected


def test_at_least_one_fixture_is_claimed_through_a_test_name_alone() -> None:
    """Keeps the name-reading path exercised by the real suite, not only by the row above."""
    by_body = set()
    for test in KILLING_TESTS:
        source = test.path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test.name:
                by_body |= set(_FIXTURE_ID_RE.findall(ast.get_source_segment(source, node) or ""))
    name_only = {f for test in KILLING_TESTS for f in test.fixtures} - by_body
    assert name_only, (
        "no fixture is claimed through a test name alone, so the name-reading "
        "branch of this checker is unexercised by the suite it checks"
    )
