"""Hard enforcing rules and the killing fixtures they are supposed to have.

Governance section 3 stage 4 is two checks: *"100% of `active` fixtures killed,
plus a check that every hard registry rule has an active fixture"*. Only the
first half was ever written. This module is the second half.

**What is checked.** Every critic declared ``hard: true`` **and**
``mode: "enforce"`` in every registry document the build loads must have an
``active`` mutation fixture. It does not today, for any of them, so the shortfall
is held in a checked-in register - ``tests/fixtures/hard_rule_gaps.json`` - that
names each uncovered rule, the fixtures that would cover it and the task that
owes it. The check is blocking and the register may only shrink: a hard
enforcing rule that appears in neither list fails, and a registered rule whose
candidate fixture has become ``active`` fails until it is moved to ``covered``.

**Why the module and the CI job are named for the check and not for Article IV.**
The constitution says a gate without a killing fixture does not exist. Naming
this check after that article would put a green tick beside a sentence that is
currently false for every hard rule in the reference registry, and a green tick
is precisely the thing that stops people building the gate - the inversion the
evaluation plan's section 1a invented the ``partial`` state to prevent, applied
to the gate instead of to the fixture. So the job is
``hard-rules-have-fixtures``, it prints the count the register holds, and the
count is a field in the register rather than a number in this file. Green here
means *the register is consistent with the registries*. It never means Article
IV is satisfied, and it cannot be misread that way while the count is non-zero
and printed next to it.

**Why advisory critics are excluded.** A critic that cannot block is not a gate.
That is not an opinion restated here: it is
:attr:`neuroharness.resolve.CriticOutcome.counts_as_hard`, which requires
``Mode.ENFORCE``, and one of the tests below derives the exclusion from that
property rather than from a list. Their requirements are still catalogued, and
another test holds that too, so "advisory" cannot become a way to drop a rule
out of sight.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

from neuroharness.models.common import Mode, VerifierResult
from neuroharness.registry import load_registry_file
from neuroharness.registry.models import ActionClass, CriticRef
from neuroharness.resolve import CriticOutcome

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
FIXTURE_ROOT: Final[Path] = REPO_ROOT / "tests" / "fixtures"
REGISTRY_DIR: Final[Path] = FIXTURE_ROOT / "registry"
MUTATION_DIR: Final[Path] = FIXTURE_ROOT / "mutations"
REGISTER_PATH: Final[Path] = FIXTURE_ROOT / "hard_rule_gaps.json"
REGISTER_SCHEMA_PATH: Final[Path] = FIXTURE_ROOT / "hard_rule_gaps.schema.json"
EVALUATION_PLAN: Final[Path] = REPO_ROOT / "docs" / "sdd" / "05-evaluation-plan.md"
WORK_BREAKDOWN: Final[Path] = REPO_ROOT / "docs" / "sdd" / "03-work-breakdown.md"

#: The declaration state that means the gate exists and the fixture proves it
#: blocks. Nothing weaker counts: ``partial`` says in its own schema that it is
#: "not counted toward hard-gate coverage", and counting it here would be the
#: same claim the evaluation plan wrote that state to stop.
COVERING_STATE: Final[str] = "active"

#: A work-breakdown task identifier, used to check that an owed task exists.
_TASK_ID_RE: Final[re.Pattern[str]] = re.compile(r"\bP[0-4]-[0-9]{2}[a-z]?\b")


def _registry_paths() -> list[Path]:
    """Every registry document in the fixture tree, found rather than named.

    A glob, not a filename. The first draft of this check was keyed on the one
    reference registry, which would have made a hard critic added to a second
    registry invisible - and "fails the day a rule is added" is the only
    property that makes this check worth running.
    """
    return sorted(REGISTRY_DIR.glob("*.json"))


def _load_registries() -> dict[Path, Any]:
    loaded: dict[Path, Any] = {}
    for path in _registry_paths():
        loaded[path] = load_registry_file(path)
    return loaded


class _Rule:
    """One critic, and where it was declared."""

    __slots__ = ("critic", "action_class", "registry")

    def __init__(self, critic: CriticRef, action_class: ActionClass, registry: Path) -> None:
        self.critic = critic
        self.action_class = action_class
        self.registry = registry

    @property
    def key(self) -> tuple[str, str]:
        """A rule's identity: the critic and the class that declares it.

        Not the critic id alone. One rule identifier appears in several classes
        in the reference registry, and a fixture that kills it in one class has
        not been shown to kill it in another.
        """
        return (self.critic.id, self.class_label)

    @property
    def class_label(self) -> str:
        return f"{self.action_class.tool}/{self.action_class.intent}"

    @property
    def registry_path(self) -> str:
        return str(self.registry.relative_to(REPO_ROOT))

    @property
    def counts_as_hard(self) -> bool:
        """Whether this critic can block, answered by the resolver's own rule.

        Built as the outcome the resolver would see, so the definition of "is a
        gate" has one home. A copy of the condition here would be a second
        definition, and the drift that matters is the quiet one: a critic that
        stops blocking while this check still counts it, or starts blocking
        while this check does not.
        """
        return CriticOutcome(
            critic_id=self.critic.id,
            result=VerifierResult.FAIL,
            hard=self.critic.hard,
            effective_mode=self.critic.mode,
        ).counts_as_hard

    def describe(self) -> str:
        return f"{self.critic.id} in {self.class_label} ({self.registry_path})"


def _rules() -> list[_Rule]:
    found: list[_Rule] = []
    for path, registry in REGISTRIES.items():
        for action_class in registry.action_classes:
            for critic in action_class.critics:
                found.append(_Rule(critic, action_class, path))
    return found


def _declaration_states() -> dict[str, str]:
    """Fixture id to lifecycle state, read from the checked-in declarations."""
    states: dict[str, str] = {}
    for path in sorted(MUTATION_DIR.glob("MUT-*.json")):
        states[path.stem] = json.loads(path.read_text(encoding="utf-8"))["state"]
    return states


REGISTRIES: Final[dict[Path, Any]] = _load_registries()
RULES: Final[list[_Rule]] = _rules()
GATES: Final[list[_Rule]] = [rule for rule in RULES if rule.counts_as_hard]
HARD_BUT_NOT_ENFORCING: Final[list[_Rule]] = [
    rule for rule in RULES if rule.critic.hard and not rule.counts_as_hard
]
DECLARATION_STATES: Final[dict[str, str]] = _declaration_states()

REGISTER: Final[dict[str, Any]] = json.loads(REGISTER_PATH.read_text(encoding="utf-8"))
REGISTER_SCHEMA: Final[dict[str, Any]] = json.loads(
    REGISTER_SCHEMA_PATH.read_text(encoding="utf-8")
)
REGISTER_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(REGISTER_SCHEMA)

GAPS: Final[list[dict[str, Any]]] = REGISTER["gaps"]
COVERED: Final[list[dict[str, Any]]] = REGISTER["covered"]


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (entry["critic_id"], entry["action_class"])


REGISTERED: Final[dict[tuple[str, str], dict[str, Any]]] = {
    _entry_key(entry): entry for entry in GAPS
}
CLAIMED_COVERED: Final[dict[tuple[str, str], dict[str, Any]]] = {
    _entry_key(entry): entry for entry in COVERED
}


# --- guards on the guard -----------------------------------------------------


def test_the_check_reads_at_least_one_registry() -> None:
    """A check that silently found nothing to check passes forever.

    The glob returning an empty list, a renamed directory or a registry the
    loader refuses would all leave every assertion below vacuously true, and a
    vacuous stage 4 is worse than no stage 4: it is a stage 4 somebody trusts.
    """
    assert REGISTRIES, (
        f"no registry document loaded from {REGISTRY_DIR.relative_to(REPO_ROOT)}; "
        "this check has nothing to check and would pass whatever the registries said"
    )
    assert GATES, "no hard enforcing critic found in any loaded registry"


def test_every_registry_document_in_the_tree_loads() -> None:
    """A registry the loader refuses must not sit silently beside ones it accepts.

    Loading happens at import time, so a refusal already fails this module. The
    test states the contract anyway: a document here is a document the build
    loads, and a fixture written to be *unloadable* belongs with the test that
    builds it in memory, not in the directory this check globs.
    """
    assert set(REGISTRIES) == set(_registry_paths())


# --- the register is well formed --------------------------------------------


def test_the_register_schema_is_itself_valid() -> None:
    """A malformed schema validates everything, silently.

    ``Draft202012Validator(schema)`` does not check the schema it is given: a
    misspelled keyword is ignored rather than rejected, so a constraint can be
    deleted by typo and every declaration still passes. ``check_schema`` is the
    only thing that notices.
    """
    Draft202012Validator.check_schema(REGISTER_SCHEMA)


def test_the_register_conforms_to_its_schema() -> None:
    errors = sorted(
        REGISTER_VALIDATOR.iter_errors(REGISTER),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, "\n".join(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )


def test_the_register_states_the_count_it_holds() -> None:
    """The count is data, so the CI job can print it and nobody has to count rows.

    A green ``hard-rules-have-fixtures`` check beside a non-zero count is the
    whole design: the register is consistent, and the constitutional property is
    still false for that many rules.
    """
    assert REGISTER["gap_count"] == len(GAPS), (
        f"the register says {REGISTER['gap_count']} gaps and lists {len(GAPS)}; "
        "the published count is what the CI job prints, so it cannot be the stale one"
    )


def test_no_rule_is_registered_twice() -> None:
    """One rule, one entry. Two entries could disagree and both look authoritative."""
    keys = [_entry_key(entry) for entry in [*GAPS, *COVERED]]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"registered more than once: {duplicates}"


# --- the register and the registries describe the same rules -----------------


def test_every_hard_enforcing_rule_is_covered_or_registered() -> None:
    """The growth check: a new hard gate cannot arrive unnoticed.

    This is the sentence governance stage 4 asks for, with the shortfall made
    explicit rather than hidden. A rule that is neither proven by an ``active``
    fixture nor written down as a known gap is a gate nobody can point at, which
    by Article IV does not exist - and the failure arrives in the change that
    adds the rule, when the cost of writing the fixture is lowest.
    """
    unaccounted = sorted(
        rule.describe()
        for rule in GATES
        if rule.key not in REGISTERED and rule.key not in CLAIMED_COVERED
    )
    assert not unaccounted, (
        "these hard `mode: enforce` rules have no active fixture and are not on the "
        f"register in {REGISTER_PATH.relative_to(REPO_ROOT)}:\n  " + "\n  ".join(unaccounted)
    )


def test_the_register_names_no_rule_the_registries_do_not_declare() -> None:
    """The other direction: an entry for a rule that no longer exists is a stale claim.

    It inflates the gap count, which makes the register read as worse than the
    tree is - and a register nobody believes is a register nobody shrinks.
    """
    declared = {rule.key for rule in GATES}
    orphans = sorted(
        f"{critic} in {label}"
        for critic, label in [*REGISTERED, *CLAIMED_COVERED]
        if (critic, label) not in declared
    )
    assert not orphans, (
        "the register names rules that no loaded registry declares as hard and "
        "enforcing; remove the entry or restore the rule:\n  " + "\n  ".join(orphans)
    )


@pytest.mark.parametrize("key", sorted([*REGISTERED, *CLAIMED_COVERED]))
def test_each_entry_matches_the_registry_it_cites(key: tuple[str, str]) -> None:
    """A rule renamed in the registry must not keep its old description here.

    ``source_requirement`` is the field Article V says every critic must be able
    to answer with. If the registry's answer and the register's answer drift
    apart, the gap is filed against a requirement that is no longer the one the
    rule encodes, and the fixture written to close it would close nothing.
    """
    entry = {**REGISTERED, **CLAIMED_COVERED}[key]
    rule = next(candidate for candidate in GATES if candidate.key == key)
    assert entry["source_requirement"] == rule.critic.source_requirement
    assert entry["registry"] == rule.registry_path


@pytest.mark.parametrize("key", sorted([*REGISTERED, *CLAIMED_COVERED]))
def test_every_fixture_the_register_names_is_declared(key: tuple[str, str]) -> None:
    """A gap closed by a fixture identifier nobody declared is not closed."""
    entry = {**REGISTERED, **CLAIMED_COVERED}[key]
    named = [*entry.get("candidate_fixtures", []), *entry.get("proving_fixtures", [])]
    undeclared = sorted(set(named) - DECLARATION_STATES.keys())
    assert not undeclared, (
        f"{key[0]} in {key[1]} names {', '.join(undeclared)}, which has no declaration in "
        f"{MUTATION_DIR.relative_to(REPO_ROOT)}"
    )


@pytest.mark.parametrize("key", sorted(REGISTERED))
def test_every_registered_gap_names_a_task_that_exists(key: tuple[str, str]) -> None:
    """A gap owed by nobody is a gap that never closes.

    The identifier is checked against the work breakdown rather than accepted on
    its shape, because a plausible-looking task id is exactly how an owner
    disappears: the row reads as assigned and no schedule contains it.
    """
    owed_by = REGISTERED[key]["owed_by"]
    tasks = set(_TASK_ID_RE.findall(WORK_BREAKDOWN.read_text(encoding="utf-8")))
    assert owed_by in tasks, (
        f"{key[0]} in {key[1]} is owed by {owed_by}, which is not a task in "
        f"{WORK_BREAKDOWN.relative_to(REPO_ROOT)}"
    )


# --- the ratchet -------------------------------------------------------------


def test_no_registered_gap_has_already_gained_an_active_fixture() -> None:
    """Shrink-only, enforced from the other side.

    Without this the register would be a place to park a rule: the fixture gets
    written, the gate starts blocking, and the entry stays, so the published
    count overstates the gap and the ratchet never turns. Promotion of a fixture
    and the entry's move to ``covered`` have to be the same change.
    """
    stale = sorted(
        f"{critic} in {label} (candidates: {', '.join(entry['candidate_fixtures'])})"
        for (critic, label), entry in REGISTERED.items()
        if any(
            DECLARATION_STATES.get(fixture) == COVERING_STATE
            for fixture in entry["candidate_fixtures"]
        )
    )
    assert not stale, (
        "these registered gaps now have an active fixture; move them to `covered` and "
        "lower `gap_count`:\n  " + "\n  ".join(stale)
    )


def test_every_covered_rule_names_an_active_fixture() -> None:
    """Moving out of the register requires a fixture, not a decision to stop counting.

    ``covered`` is the only exit, so it is the only place the claim can be
    forged, and a fixture that is merely declared - ``reserved``, or ``partial``
    against a component nobody has built - is the shape that forgery takes.

    Written as one test over the list rather than parametrized over it: the list
    is empty today, and a parametrize over an empty set reports as a skip. A
    skipped test in the stage whose specification says no manual override exists
    is the wrong thing for a reader to see.
    """
    unproven = sorted(
        f"{critic} in {label} claims {({f: DECLARATION_STATES.get(f) for f in entry['proving_fixtures']})}"
        for (critic, label), entry in CLAIMED_COVERED.items()
        if not any(
            DECLARATION_STATES.get(fixture) == COVERING_STATE
            for fixture in entry["proving_fixtures"]
        )
    )
    assert not unproven, (
        f"these rules are listed as covered by no {COVERING_STATE!r} fixture; a rule leaves "
        "the register when a fixture kills it, not when somebody stops counting it:\n  "
        + "\n  ".join(unproven)
    )


# --- what is deliberately not a gate, and why it is still visible ------------


def test_a_hard_critic_that_cannot_block_is_not_treated_as_a_gate() -> None:
    """The exclusion rule, derived from the resolver instead of restated.

    ``counts_as_hard`` requires ``Mode.ENFORCE``: a hard critic in shadow or
    advisory is resolved as soft and its would-be effect is only recorded. If
    that ever changes, this check must change with it, and reading the property
    is what makes that automatic rather than a thing somebody has to remember.
    """
    outcome = CriticOutcome(
        critic_id="fsa.example",
        result=VerifierResult.FAIL,
        hard=True,
        effective_mode=Mode.ENFORCE,
    )
    assert outcome.counts_as_hard
    for mode in Mode:
        demoted = CriticOutcome(
            critic_id="fsa.example",
            result=VerifierResult.FAIL,
            hard=True,
            effective_mode=mode,
        )
        assert demoted.counts_as_hard == (mode is Mode.ENFORCE)


def test_excluded_hard_critics_are_absent_from_the_register() -> None:
    """A rule that cannot block must not be counted as a gap it is not.

    Counting them would inflate the number this check publishes, and an inflated
    count is as corrosive as a suppressed one: the first thing anyone does with
    a number they do not trust is stop reading it.
    """
    excluded = {rule.key for rule in HARD_BUT_NOT_ENFORCING}
    registered = sorted(key for key in [*REGISTERED, *CLAIMED_COVERED] if key in excluded)
    assert not registered, (
        f"these critics are hard but not enforcing, so they cannot block: {registered}"
    )


@pytest.mark.parametrize(
    "requirement",
    sorted({rule.critic.source_requirement for rule in HARD_BUT_NOT_ENFORCING}),
)
def test_an_excluded_critics_requirement_is_still_catalogued(requirement: str) -> None:
    """Advisory must not become a way out of the catalogue.

    A hard critic running in advisory is a rollout stage, not a decision to stop
    proving the rule: it is promoted to enforce later, and the fixture has to
    exist before that happens rather than after. Checking the requirement is
    still named in the evaluation plan is what stops the demotion from quietly
    taking the rule off the books.
    """
    catalogue = EVALUATION_PLAN.read_text(encoding="utf-8")
    assert requirement in catalogue, (
        f"{requirement} is encoded by a hard critic running in a non-enforcing mode and "
        f"appears nowhere in {EVALUATION_PLAN.relative_to(REPO_ROOT)}; it is excluded from "
        "the gap count and catalogued nowhere, which is the one combination that loses a rule"
    )
