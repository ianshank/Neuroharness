"""Every skill still describes the tree it documents.

`.claude/skills/` encodes the procedures this repository gets wrong when rushed:
adding a reason code, adding a record kind, adding a mutation fixture, running
the gates. Each one is a multi-site coupling, and round four is the evidence that
these are worth writing down — a repair unified four spellings of the
resource-key grammar and left a fifth running, in the three record fields where
it mattered most.

A skill that has gone stale is worse than no skill. It reads authoritative, it
names a file that moved or a constant that was renamed, and the person following
it does the wrong thing confidently. Instructions rot exactly the way the
documents in `docs/sdd/` rot, and this project's answer to that has always been
the same: make the claim executable.

So each `SKILL.md` carries a ```json coupling``` block naming the sites it tells
a contributor to change, and this module holds those names to the tree. The
block is JSON rather than YAML because parsing it must not depend on a package
outside the standard library; the whole point is that the check cannot be
skipped for want of a dependency.

What is asserted is that every path exists, every anchor is still present in the
file it names, and every `verify` entry is a real Makefile target. What is
deliberately *not* asserted is prose: a skill may explain as much as it likes,
and only the claims it makes about identifiers are mechanical.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SKILLS_DIR: Final[Path] = _REPO_ROOT / ".claude" / "skills"
_MAKEFILE: Final[Path] = _REPO_ROOT / "Makefile"

#: The fenced block this module reads. Tagged so a skill can carry other JSON
#: examples without them being mistaken for the contract.
_COUPLING_RE: Final[re.Pattern[str]] = re.compile(
    r"```json coupling\n(?P<body>.*?)\n```", re.DOTALL
)

#: `name:` and `description:` in the YAML frontmatter. Read with a regex rather
#: than a YAML parser for the dependency reason in the module docstring.
_FRONTMATTER_RE: Final[re.Pattern[str]] = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)

#: A Makefile target declaration at the start of a line.
_MAKE_TARGET_RE: Final[re.Pattern[str]] = re.compile(r"^([a-z][a-z0-9-]*):", re.MULTILINE)


def _skill_files() -> list[Path]:
    return sorted(_SKILLS_DIR.glob("*/SKILL.md"))


def _coupling(path: Path) -> dict[str, Any]:
    match = _COUPLING_RE.search(path.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{path.relative_to(_REPO_ROOT)} carries no ```json coupling``` block, so nothing "
        "holds its instructions to the tree"
    )
    return json.loads(match.group("body"))


def _make_targets() -> set[str]:
    return set(_MAKE_TARGET_RE.findall(_MAKEFILE.read_text(encoding="utf-8")))


def test_there_are_skills_to_check() -> None:
    """Guard on the guard.

    Every parametrized test below expands over `_skill_files()`. If the
    directory is renamed or emptied, each one collects zero cases and the suite
    reports green over a check that ran against nothing — the failure mode
    `CONTRIBUTING.md` calls out by name.
    """
    skills = _skill_files()
    assert len(skills) >= 4, (
        f"expected the skill set under {_SKILLS_DIR.relative_to(_REPO_ROOT)}; "
        f"found {[p.parent.name for p in skills]}"
    )


@pytest.mark.parametrize("skill", _skill_files(), ids=lambda p: p.parent.name)
def test_the_frontmatter_names_the_skill_after_its_directory(skill: Path) -> None:
    """A skill is addressed by name; the name and the directory must agree."""
    match = _FRONTMATTER_RE.search(skill.read_text(encoding="utf-8"))
    assert match is not None, f"{skill.parent.name} has no YAML frontmatter"

    body = match.group("body")
    name = re.search(r"^name:\s*(\S+)\s*$", body, re.MULTILINE)
    description = re.search(r"^description:\s*(.+)$", body, re.MULTILINE)

    assert name is not None, f"{skill.parent.name} declares no `name`"
    assert description is not None, f"{skill.parent.name} declares no `description`"
    assert name.group(1) == skill.parent.name, (
        f"{skill.parent.name} declares name {name.group(1)!r}; a skill is invoked by the "
        "name in its frontmatter, so the two must not disagree"
    )
    # The description is the only part read at startup: it decides whether the
    # skill fires at all. A one-word description cannot route.
    assert len(description.group(1)) >= 80, (
        f"{skill.parent.name}'s description is {len(description.group(1))} characters. It is "
        "the routing rule, not a title: say when it fires and what it covers"
    )


@pytest.mark.parametrize("skill", _skill_files(), ids=lambda p: p.parent.name)
def test_every_coupling_site_still_exists(skill: Path) -> None:
    """The files a skill tells you to change are still there, and still say so.

    This is the assertion the module exists for. A renamed constant or a moved
    file leaves the prose pointing at nothing, and the only symptom is a
    contributor following confident instructions into the wrong edit.
    """
    coupling = _coupling(skill)
    sites = coupling.get("sites", [])
    assert sites, f"{skill.parent.name} declares no coupling sites"

    for site in sites:
        for key in ("path", "contains", "why"):
            assert key in site, f"{skill.parent.name}: a site is missing {key!r}: {site}"

        target = _REPO_ROOT / site["path"]
        assert target.is_file(), (
            f"{skill.parent.name} names {site['path']}, which does not exist "
            f"(cited for: {site['why']})"
        )
        assert site["contains"] in target.read_text(encoding="utf-8"), (
            f"{skill.parent.name} says {site['path']} contains {site['contains']!r} "
            f"(for: {site['why']}) and it does not. The skill has gone stale."
        )


@pytest.mark.parametrize("skill", _skill_files(), ids=lambda p: p.parent.name)
def test_every_verify_step_is_a_real_make_target(skill: Path) -> None:
    """A skill that ends "then run X" must name an X that exists."""
    coupling = _coupling(skill)
    targets = _make_targets()
    assert "gate" in targets, "the Makefile no longer declares `gate`; the extraction has broken"

    for step in coupling.get("verify", []):
        assert step in targets, (
            f"{skill.parent.name} says to verify with `make {step}`, which is not a target. "
            f"Available: {sorted(targets)}"
        )


def test_the_record_kind_skill_names_the_coupling_that_fails_silently() -> None:
    """The one site whose omission is a bare ``KeyError``, cross-checked in code.

    Every other edit in `add-record-kind` fails loudly — pydantic, the schema
    validator or a conformance test says so. `RECORD_PAYLOAD_FIELDS` is
    subscripted directly by `_kind_matches_payload`, so a kind added to the enum
    and not to the dict raises from inside a validator. That asymmetry is the
    reason the skill exists, so it is asserted here rather than trusted to the
    prose, and it is checked against the real table rather than against a copy.
    """
    from neuroharness.models.record import RECORD_PAYLOAD_FIELDS, RecordKind

    assert set(RECORD_PAYLOAD_FIELDS) == set(RecordKind), (
        "RECORD_PAYLOAD_FIELDS and RecordKind disagree, which is the defect the "
        "add-record-kind skill warns about — in the tree, now"
    )

    skill = _SKILLS_DIR / "add-record-kind" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "RECORD_PAYLOAD_FIELDS" in text
    assert "KeyError" in text, (
        "the skill no longer explains that a missing RECORD_PAYLOAD_FIELDS entry fails as a "
        "bare KeyError, which is the whole reason that site is called out"
    )


def test_the_reason_code_skill_agrees_with_the_catalogue_it_describes() -> None:
    """`PARAMETERISED_REASONS` and `SUBJECT_GRAMMAR` are total over each other.

    The skill tells a contributor these two must move together and that the
    package refuses at import otherwise. Asserted against the code so the claim
    cannot quietly stop being true.
    """
    from neuroharness.reason import (
        ESCALATABLE_REASONS,
        INFRASTRUCTURE_REASONS,
        PARAMETERISED_REASONS,
        SUBJECT_GRAMMAR,
    )

    assert set(SUBJECT_GRAMMAR) == set(PARAMETERISED_REASONS), (
        "the reason catalogue's two tables disagree; the add-reason-code skill says they "
        "cannot, and the import-time guard says so too"
    )
    assert not (INFRASTRUCTURE_REASONS & ESCALATABLE_REASONS), (
        "an infrastructure reason is escalatable, which the skill states is impossible"
    )
