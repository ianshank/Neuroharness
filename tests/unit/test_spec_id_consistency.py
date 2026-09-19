"""Identifiers in the code must name something the specification package defines.

Governance section 3 stage 11: *"spec IDs referenced by tests exist"*. Article
VII asks of every change which ``FR-``/``NFR-``/``SEC-`` identifiers it
implements and which scenario accepts it; the whole spec-driven workflow rests
on a test being able to cite a requirement. A citation nobody checks is a
citation that survives the requirement being renamed, split or withdrawn - and
it survives *quietly*, because the test still passes. The reader of that test
then believes a rule is enforced whose text no longer exists.

So: every requirement, threat, scenario, fixture, decision, question and task
identifier written anywhere under ``src/`` or ``tests/`` must appear somewhere
under ``docs/sdd/``. It passes today with no orphans, which is the point of
landing it now - the cheapest moment to install a ratchet is while it is
already green.

**The reverse direction is deliberately not checked.** Most documented
requirements are implemented by components nobody has built, so "every
documented identifier is referenced by code" would fail by design and would be
switched off within a week. Where a genuine two-way check is possible it
already exists and is stricter than this one: ``test_mutation_fixtures.py``
holds the mutation catalogue and its declarations to each other, and
``test_adr_index.py`` holds the ADR index and the ADR files to each other.

**The anchoring is the substance of this module.** ``A-[0-9][0-9]`` without a
word boundary matches ``SHA-256``. That is not hypothetical: an increment plan
published a scenario-coverage count measured with exactly that pattern, counted
a digest as an acceptance scenario, and the section it got wrong was the one
arguing that unenforced claims rot. Every pattern here is anchored with ``\\b``
on both sides, once, in one table, and a regression table below pins the
near-misses so the anchoring cannot be lost in a refactor.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SDD_ROOT: Final[Path] = REPO_ROOT / "docs" / "sdd"

#: The trees whose identifiers must resolve. Code and tests only: the SDD
#: package is the definition, so checking it against itself would say nothing.
SCANNED_ROOTS: Final[tuple[Path, ...]] = (REPO_ROOT / "src", REPO_ROOT / "tests")

#: One family per row: the identifier prefix and the shape of what follows.
#: Anchoring is applied to every row in one place below rather than written into
#: each pattern, because an anchor that has to be repeated is an anchor that
#: will eventually be forgotten on one row - and a single unanchored row is
#: enough to make the whole check report nonsense.
ID_FAMILIES: Final[dict[str, str]] = {
    "requirement": r"FR-[0-9]{2,3}",
    "non-functional requirement": r"NFR-[0-9]{2,3}",
    "security requirement": r"SEC-[0-9]{2}",
    "invariant": r"INV-[0-9]{2}",
    "mutation fixture": r"MUT-[0-9]{2,3}[a-z]?",
    "decision record": r"ADR-[0-9]{4}",
    "open question": r"OQ-[0-9]{2}",
    "workflow invariant": r"WF-[0-9]{2}[a-z]?",
    "acceptance scenario": r"A-[0-9]{2}[a-z]?",
    "threat": r"T-[0-9]{2}",
    "work-breakdown task": r"P[0-4]-[0-9]{2}[a-z]?",
}


def _anchored(pattern: str) -> re.Pattern[str]:
    r"""``\b`` on both sides, applied identically to every family.

    The leading boundary is what keeps ``FR-02`` out of ``NFR-02`` and ``A-25``
    out of ``SHA-256``; the trailing one is what keeps a two-digit identifier
    from being read out of a three-digit one. Both matter, and the failure mode
    of losing either is silent over-counting rather than an error.
    """
    return re.compile(rf"\b(?:{pattern})\b")


FAMILY_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    name: _anchored(pattern) for name, pattern in ID_FAMILIES.items()
}

#: One pass over each file finds every family. Alternation, not a loop, so a
#: longer prefix wins at a given position: ``NFR-01`` is one identifier, not an
#: ``FR-01`` hiding inside a word.
ANY_ID: Final[re.Pattern[str]] = _anchored("|".join(ID_FAMILIES.values()))

#: Directories that hold build products rather than authored text.
IGNORED_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset({"__pycache__"})


def _authored_files(root: Path) -> list[Path]:
    """Every authored file under ``root``.

    Extension-blind on purpose. An allowlist of suffixes decides in advance
    which file types may cite a requirement, and the first fixture format
    somebody adds - a YAML registry, a ``.feature`` file, a ``.rego`` bundle -
    is then exempt from the check without anybody choosing that.
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if IGNORED_DIRECTORY_NAMES.intersection(path.parts):
            continue
        found.append(path)
    return found


def _read(path: Path) -> str | None:
    """Text, or ``None`` for a file that is not text at all."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _defined_identifiers() -> frozenset[str]:
    """Everything the specification package defines or discusses."""
    defined: set[str] = set()
    for path in sorted(SDD_ROOT.rglob("*.md")):
        defined.update(ANY_ID.findall(path.read_text(encoding="utf-8")))
    return frozenset(defined)


def _referenced_identifiers() -> tuple[dict[str, set[str]], list[Path]]:
    """Identifier to the files citing it, plus the files that were not text."""
    referenced: dict[str, set[str]] = {}
    unreadable: list[Path] = []
    for root in SCANNED_ROOTS:
        for path in _authored_files(root):
            text = _read(path)
            if text is None:
                unreadable.append(path)
                continue
            for identifier in ANY_ID.findall(text):
                referenced.setdefault(identifier, set()).add(
                    str(path.relative_to(REPO_ROOT))
                )
    return referenced, unreadable


DEFINED: Final[frozenset[str]] = _defined_identifiers()
REFERENCED, UNREADABLE = _referenced_identifiers()


def _family_of(identifier: str) -> str:
    for name, pattern in FAMILY_PATTERNS.items():
        if pattern.fullmatch(identifier):
            return name
    raise AssertionError(f"{identifier!r} matched the combined pattern but no family")


# --- guards on the guard -----------------------------------------------------


def test_the_specification_package_was_read() -> None:
    """A moved or renamed docs tree would make every identifier an orphan - or none.

    Checked as a floor on the count rather than as "not empty": a parser that
    reads one file out of ten fails this, and the failure mode of a partially
    read corpus is a flood of false orphans that gets the check disabled.
    """
    assert len(DEFINED) > len(ID_FAMILIES) * 10, (
        f"only {len(DEFINED)} identifiers parsed out of {SDD_ROOT.relative_to(REPO_ROOT)}; "
        "the package moved or the patterns stopped matching, and this check is now blind"
    )


def test_the_code_tree_was_read() -> None:
    """The other half of the same guard, for the side the check is about."""
    assert REFERENCED, (
        f"no identifier found anywhere under {[str(p.relative_to(REPO_ROOT)) for p in SCANNED_ROOTS]}; "
        "either nothing cites a requirement any more, or the scan is not reaching the files"
    )


def test_every_scanned_file_is_readable_as_text() -> None:
    """A file the scanner cannot read is a file whose citations are unchecked.

    Silently skipping it is how a check acquires a blind spot that nobody chose:
    the skip leaves no trace, so the first person to notice is the one who finds
    a stale requirement id in a fixture nobody validated.
    """
    assert not UNREADABLE, (
        "these files are not UTF-8 text and were not scanned:\n  "
        + "\n  ".join(str(path.relative_to(REPO_ROOT)) for path in sorted(UNREADABLE))
    )


# --- the check ---------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(ID_FAMILIES))
def test_every_referenced_identifier_is_defined(family: str) -> None:
    """The stage 11 check, one family at a time so a failure names the family.

    An orphan is one of three things and all three are defects: a typo, a
    citation of something that was renamed, or a citation of something that was
    never written. The first is embarrassing, the second and the third are a
    test claiming to enforce a rule that does not exist - which is the exact
    inversion Article VII is written against.
    """
    orphans = sorted(
        f"{identifier} (cited by {', '.join(sorted(files))})"
        for identifier, files in REFERENCED.items()
        if _family_of(identifier) == family and identifier not in DEFINED
    )
    assert not orphans, (
        f"these {family} identifiers are cited in the tree and defined nowhere under "
        f"{SDD_ROOT.relative_to(REPO_ROOT)}:\n  " + "\n  ".join(orphans)
    )


# --- the checker's own reading of a near-miss --------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The defect this anchoring exists to prevent, kept as a row rather than
        # as a sentence in a docstring: an unanchored `A-[0-9][0-9]` reads the
        # `A-25` out of `SHA-256`, and a published scenario-coverage count was
        # wrong by exactly one scenario because of it.
        ("SHA-256", []),
        ("the SHA-256 digest of the envelope", []),
        # ... but a real scenario in the same sentence is still found, so the
        # fix is an anchor and not a blanket exclusion of the letter A.
        ("the SHA-256 digest named by A-25", ["A-25"]),
        # A longer prefix wins over a shorter one it contains.
        ("NFR-01", ["NFR-01"]),
        ("MUT-17", ["MUT-17"]),
        # Lettered variants are identifiers in their own right.
        ("A-12b", ["A-12b"]),
        ("MUT-12b", ["MUT-12b"]),
        ("WF-06a", ["WF-06a"]),
        ("P1-06a", ["P1-06a"]),
        # Shapes that are not identifiers of these families at all.
        ("ADR-001", []),
        ("R2-S3", []),
        ("FR-2", []),
        ("sha-256", []),
        # Markup around an identifier must not hide it.
        ("``FR-02``", ["FR-02"]),
    ],
)
def test_the_anchored_pattern_reads_only_real_identifiers(
    text: str, expected: list[str]
) -> None:
    """The regression table for the anchoring.

    Every row here is a string that a plausible version of this pattern gets
    wrong, and the one that matters is the first: the wrong answer is not an
    error but a *number*, and a number is exactly what gets published.
    """
    assert ANY_ID.findall(text) == expected


def test_every_family_pattern_is_anchored() -> None:
    """The anchoring is applied centrally; this is what stops it being bypassed.

    A family added with its own hand-written pattern would not go through
    :func:`_anchored`, and the combined pattern would keep working well enough
    that nobody would look. Reading the compiled pattern back is the only check
    that survives that refactor.
    """
    for name, compiled in FAMILY_PATTERNS.items():
        assert compiled.pattern.startswith("\\b") and compiled.pattern.endswith("\\b"), (
            f"the {name} pattern is not anchored on both sides: {compiled.pattern}"
        )
