"""The ADR index and the ADR files are one list, checked against each other.

Governance section 3 stage 11 requires an ADR index consistency check. Article
VII says an architectural change is recorded as a decision record *before* it is
built, and the index carries the rule that makes a record trustworthy: accepted
records are immutable, and changing a decision means a new record that supersedes
the old one rather than an edit.

Both of those depend on the index and the directory agreeing, and neither is
self-enforcing:

* a **row with no file** reads as a decision that exists. Somebody looking for
  the rationale finds a title and stops looking, which is worse than finding
  nothing - two such rows are in the index today, and they are legitimate
  because they say ``Planned``;
* a **file with no row** is a decision nobody can find, which is the same as a
  decision nobody made;
* a **dangling supersession** is the dangerous one. ``Superseded by ADR-0014``
  is the whole mechanism for changing a decision safely: it says the old record
  is history and points at what replaced it. If the replacement does not exist,
  the reader is left with a record that disclaims itself and names nothing, so
  the decision in force is whatever the code happens to do;
* an **unparseable status** removes the distinction between proposed and
  accepted, which is the distinction the immutability rule is keyed on.

``Planned`` is tolerated for rows with no file - that is what the state is for.
It is tolerated *only* there: a ``Planned`` file would be a record of a decision
that has not been taken.

One thing this module checks by identifier rather than by path: the ``Superseded
by`` links inside the ADR files point at ``ADR-0014-*.md``, a glob rather than a
filename, so they do not resolve as relative links. The index's own row links do
resolve and are checked as paths. Resolving the in-file links is a documentation
link check - the other half of stage 11 - and belongs with that tool rather than
here, where narrowing it to a guess at the intended path would be inventing data.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, NamedTuple

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
ADR_DIR: Final[Path] = REPO_ROOT / "docs" / "sdd" / "adr"
INDEX: Final[Path] = ADR_DIR / "README.md"

#: The statuses a decision record may be in. Closed on purpose: the index's
#: immutability rule applies to exactly one of them, so a status outside this set
#: is a record whose handling nobody has decided. MADR's vocabulary, plus
#: ``Planned``, which this project uses for a reserved identifier with no file.
STATUSES: Final[frozenset[str]] = frozenset(
    {"Proposed", "Accepted", "Rejected", "Deprecated", "Superseded", "Planned"}
)

#: The one status that may appear on a row with no file behind it.
FILELESS_STATUS: Final[str] = "Planned"

_ADR_ID: Final[str] = r"ADR-[0-9]{4}"

#: ``| [ADR-0001](ADR-0001-....md) | Title | Status | Origin |`` and the
#: fileless variant ``| ADR-0011 | Title | Planned (`P0-02`) | - |``.
_ROW_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\|\s*(?:\[({_ADR_ID})\]\(([^)]+)\)|({_ADR_ID}))\s*\|([^|]*)\|([^|]*)\|"
)

#: ``**Status:** Proposed · **Date:** ...`` - the first field of an ADR's
#: metadata line.
_STATUS_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*(?:·|$)")

#: ``Superseded by [ADR-0014](...)`` in a file, ``**Superseded by ADR-0014**``
#: in the index. One pattern reads both.
_SUPERSEDED_BY_RE: Final[re.Pattern[str]] = re.compile(rf"Superseded by \[?({_ADR_ID})")

#: An ADR file is named for its identifier: ``ADR-0014-verdict-resolution-order.md``.
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(rf"^({_ADR_ID})-[a-z0-9-]+\.md$")


def _status_keyword(status: str) -> str | None:
    """The status itself, with the qualifiers and the markup stripped.

    ``Proposed (supersedes 0007; **amended** ...)`` is ``Proposed``; ``**Superseded
    by ADR-0014**`` is ``Superseded``. The qualifier carries real information and
    is deliberately free text - what must be closed is the state itself, because
    that is what the immutability rule reads.
    """
    match = re.match(r"^\**([A-Za-z]+)", status.strip())
    return match.group(1) if match else None


class _Row(NamedTuple):
    """One line of the index table."""

    identifier: str
    link: str | None
    title: str
    status: str

    @property
    def keyword(self) -> str | None:
        return _status_keyword(self.status)


def _index_rows() -> dict[str, _Row]:
    rows: dict[str, _Row] = {}
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        identifier = match.group(1) or match.group(3)
        rows[identifier] = _Row(
            identifier=identifier,
            link=match.group(2),
            title=match.group(4).strip(),
            status=match.group(5).strip(),
        )
    return rows


def _adr_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        match = _FILENAME_RE.match(path.name)
        if match:
            files[match.group(1)] = path
    return files


def _file_status(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _STATUS_LINE_RE.match(line)
        if match:
            return match.group(1)
    return None


ROWS: Final[dict[str, _Row]] = _index_rows()
FILES: Final[dict[str, Path]] = _adr_files()


# --- guards on the guard -----------------------------------------------------


def test_the_index_and_the_directory_were_both_read() -> None:
    """A table-format change would make every check below vacuous.

    Both sides, because reading one and not the other turns a consistency check
    into an assertion that a list is empty - which passes.
    """
    assert len(ROWS) > 1, f"only {len(ROWS)} rows parsed out of {INDEX.relative_to(REPO_ROOT)}"
    assert len(FILES) > 1, f"only {len(FILES)} decision records found in {ADR_DIR.relative_to(REPO_ROOT)}"


def test_no_markdown_file_in_the_directory_is_skipped_by_the_filename_rule() -> None:
    """A record whose filename does not follow the convention is invisible here.

    Silently, and in the direction that matters: it has no row to dangle and no
    identifier to check, so every other test in this module passes while the
    record sits unindexed.
    """
    unmatched = sorted(
        path.name
        for path in ADR_DIR.glob("*.md")
        if path.name != INDEX.name and not _FILENAME_RE.match(path.name)
    )
    assert not unmatched, (
        "these files are in the decision-record directory and are not named "
        "``ADR-NNNN-slug.md``, so nothing checks them:\n  " + "\n  ".join(unmatched)
    )


# --- the index and the directory name the same records -----------------------


def test_every_record_file_has_an_index_row() -> None:
    """A decision nobody can find from the index is a decision nobody made."""
    unlisted = sorted(
        f"{identifier} ({path.name})" for identifier, path in FILES.items() if identifier not in ROWS
    )
    assert not unlisted, (
        f"these decision records have no row in {INDEX.relative_to(REPO_ROOT)}:\n  "
        + "\n  ".join(unlisted)
    )


def test_every_index_row_has_a_file_unless_it_is_planned() -> None:
    """The dangling-row check, with the one state that is allowed to dangle.

    A row with a title and no record reads, to anybody scanning the index, as a
    decision that has been taken. ``Planned`` is the honest form of the same
    row: the identifier is reserved, the decision is not made, and the status
    says so.
    """
    dangling = sorted(
        f"{row.identifier} ({row.title!r}, status {row.status!r})"
        for row in ROWS.values()
        if row.identifier not in FILES and row.keyword != FILELESS_STATUS
    )
    assert not dangling, (
        "these index rows name no decision record and are not marked "
        f"{FILELESS_STATUS!r}:\n  " + "\n  ".join(dangling)
    )


def test_a_planned_row_has_no_file() -> None:
    """The other direction of the same tolerance.

    ``Planned`` says the decision has not been taken. A file behind it is a
    decision that has been taken and whose index row still says otherwise, so
    readers of the index and readers of the directory disagree about what is in
    force.
    """
    contradictions = sorted(
        row.identifier
        for row in ROWS.values()
        if row.keyword == FILELESS_STATUS and row.identifier in FILES
    )
    assert not contradictions, (
        f"these rows are marked {FILELESS_STATUS!r} and have a record file: "
        f"{', '.join(contradictions)}"
    )


@pytest.mark.parametrize("identifier", sorted(FILES))
def test_the_index_link_resolves_to_the_record(identifier: str) -> None:
    """The link is how a reader gets from the index to the rationale."""
    row = ROWS[identifier]
    assert row.link is not None, f"{identifier} has a file but its index row does not link to it"
    target = (ADR_DIR / row.link).resolve()
    assert target == FILES[identifier].resolve(), (
        f"{identifier} links to {row.link}, which is not {FILES[identifier].name}"
    )


# --- statuses ----------------------------------------------------------------


@pytest.mark.parametrize("identifier", sorted(FILES))
def test_every_record_has_a_parseable_status_from_the_closed_set(identifier: str) -> None:
    """The immutability rule is keyed on the status, so the status must be readable.

    "Accepted records are immutable; to change a decision, write a new record
    that supersedes it." A record whose status cannot be parsed is a record that
    rule cannot be applied to - and the rule is the only thing that makes a
    decision record different from a document somebody may quietly rewrite.
    """
    status = _file_status(FILES[identifier])
    assert status is not None, (
        f"{FILES[identifier].name} has no ``**Status:**`` line; the index's immutability "
        "rule has nothing to read"
    )
    keyword = _status_keyword(status)
    assert keyword in STATUSES, (
        f"{FILES[identifier].name} declares status {status!r}, whose state {keyword!r} is not "
        f"one of {sorted(STATUSES)}"
    )


@pytest.mark.parametrize("identifier", sorted(FILES))
def test_the_index_and_the_record_agree_on_the_status(identifier: str) -> None:
    """Two places holding one fact, so one of them can be wrong.

    The qualifier may differ - the index compresses "rejected for v1; R&D track
    only" to "reject for v1" - but the state cannot, because a record the index
    calls superseded and the file calls proposed is a record whose standing
    depends on which document the reader opened.
    """
    file_status = _file_status(FILES[identifier])
    assert file_status is not None
    assert _status_keyword(file_status) == ROWS[identifier].keyword, (
        f"{identifier}: the index says {ROWS[identifier].status!r} and the record says "
        f"{file_status!r}"
    )


@pytest.mark.parametrize("identifier", sorted(ROWS))
def test_every_supersession_names_a_record_that_exists(identifier: str) -> None:
    """A supersession pointing at nothing leaves no decision in force.

    Read from both the index row and the record's own status line, because the
    two are written at different times by different changes and either one can
    be the stale copy.
    """
    sources = {"the index row": ROWS[identifier].status}
    path = FILES.get(identifier)
    if path is not None:
        sources["the record"] = _file_status(path) or ""
    for where, text in sources.items():
        for target in _SUPERSEDED_BY_RE.findall(text):
            assert target in FILES, (
                f"{identifier} says in {where} that it is superseded by {target}, which has no "
                f"record file in {ADR_DIR.relative_to(REPO_ROOT)}; the decision in force is "
                "then whatever the code happens to do"
            )


def test_a_superseded_record_is_superseded_by_exactly_one_successor() -> None:
    """Two successors is two decisions in force, which is none.

    Cheap to check and impossible to see by eye once the index is twenty rows
    long, which is exactly when it starts to matter.
    """
    ambiguous = sorted(
        f"{identifier} -> {', '.join(targets)}"
        for identifier, row in ROWS.items()
        if len(targets := _SUPERSEDED_BY_RE.findall(row.status)) > 1
    )
    assert not ambiguous, "these records name more than one successor:\n  " + "\n  ".join(ambiguous)
