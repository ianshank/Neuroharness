"""The secret scanner's allowlist is held to a shape, because nothing else holds it.

CI stage 9 runs ``gitleaks`` over the whole history. When it reports a finding
there are exactly two honest responses -- the value is a secret and must be
rotated and removed, or it is not a secret and the scanner is told so -- and one
dishonest one, which is to widen the configuration until the scan is quiet. The
dishonest response is also the cheapest, it leaves a green check behind, and
nobody reviewing a later pull request will re-derive why an allowlist entry is
there. That is the same shape as a mutation fixture quietly demoted to make a
build pass: the gate still has a name and a green tick and no longer refuses
anything.

So the allowlist is checked by a test rather than by convention:

* **the default rule set is extended, not replaced.** ``useDefault = false`` and
  a hand-written rule list is the single change that would disable most of the
  scanner while leaving a configuration that looks deliberate and detailed;
* **every entry is a literal, anchored at both ends.** An unanchored entry
  matches a substring, so ``signing`` would exempt every value containing it.
  This is the difference between exempting two known strings and exempting a
  family nobody enumerated -- and the widened entry is indistinguishable from
  the narrow one at review, which is why it is checked mechanically;
* **the allowlist uses no mechanism broader than a value.** ``paths`` drops
  whole files from the scan, ``commits`` drops whole commits, ``stopwords``
  suppresses by substring, and ``regexTarget`` re-points the regexes at the
  surrounding line. Each is legitimate somewhere and none is legitimate as an
  unremarked addition, so adding one fails here first and has to be argued for;
* **every entry still matches something in the tree.** A stale entry is an
  exemption nobody can evaluate: the reader cannot tell whether the value went
  away or moved, and the safe-looking move is to leave it. Entries that must be
  justified to survive do not accumulate.

What this module deliberately does not check is the *number* of entries. A cap
would make the third false positive an obstacle to route around rather than
something to look at, which inverts the intent.

This test does not run ``gitleaks``; it is a static check of the configuration
that governs it, and needs no binary. The scan itself is CI stage 9.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CONFIG: Final[Path] = REPO_ROOT / ".gitleaks.toml"

#: The only keys the ``[allowlist]`` table may carry. Everything else gitleaks
#: accepts there suppresses by a unit larger than one value -- a path, a commit,
#: a substring, or the whole matched line -- and is a decision rather than a
#: maintenance detail. Closed on purpose: a key outside this set fails, and the
#: repair is to argue for it here.
ALLOWED_KEYS: Final[frozenset[str]] = frozenset({"description", "regexes"})

#: ``^literal$``, where the literal contains no regular-expression metacharacter
#: at all. Not merely "anchored": ``^.*$`` is anchored and exempts everything,
#: and so, less obviously, is ``^fpr-signing-2026q3|x$``, because alternation
#: binds looser than the anchors. Restricting the body to characters that are
#: literal in a regex removes the whole question.
_LITERAL_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^\^([A-Za-z0-9_-]+)\$$")

#: Where an allowlisted value may live. The tree, not the history: an entry
#: whose value survives only in a commit that no longer has a file is one nobody
#: can check, and gitleaks scans history precisely because such a value is still
#: published. Extensions rather than a full walk, so a build artefact or a cache
#: cannot satisfy an entry.
_SEARCH_GLOBS: Final[tuple[str, ...]] = ("*.py", "*.md", "*.json", "*.toml", "*.yml", "*.yaml")

#: Directories a static walk must not descend into: they are generated, and a
#: value found in one of them proves nothing about the checked-in tree.
_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
     ".hypothesis", "htmlcov", "node_modules", ".tox", "dist", "build"}
)


def _load() -> dict[str, Any]:
    """The parsed configuration.

    A parse failure is a real finding rather than a fixture problem: gitleaks
    would fail to start, and a scanner that does not start is a stage 9 that
    reports nothing.
    """
    if not CONFIG.is_file():
        pytest.fail(f"{CONFIG.name} is missing; CI stage 9 runs with it and expects it present")
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def _allowlist() -> dict[str, Any]:
    allowlist = _load().get("allowlist")
    if allowlist is None:
        # Legitimate: no false positives to exempt. Every entry-shaped check
        # below is then vacuous, which is the correct result rather than a skip.
        return {}
    assert isinstance(allowlist, dict), "[allowlist] must be a table"
    return allowlist


def _entries() -> list[str]:
    regexes = _allowlist().get("regexes", [])
    assert isinstance(regexes, list), "allowlist.regexes must be an array"
    return [str(entry) for entry in regexes]


def _tracked_text() -> str:
    """Every checked-in text file this module searches, concatenated once."""
    chunks: list[str] = []
    for glob in _SEARCH_GLOBS:
        for path in sorted(REPO_ROOT.rglob(glob)):
            if _SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts):
                continue
            if path == CONFIG:
                # The configuration names its own entries in comments, so
                # searching it would make every entry self-satisfying.
                continue
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def test_the_default_rule_set_is_extended_rather_than_replaced() -> None:
    """``useDefault = false`` disables most of the scanner and looks deliberate."""
    extend = _load().get("extend", {})
    assert extend.get("useDefault") is True, (
        "[extend].useDefault must be true. Without it gitleaks runs only the rules "
        "written in this file, so the configuration that looks most thorough is the "
        "one that scans for least."
    )


def test_the_allowlist_suppresses_by_value_and_by_nothing_broader() -> None:
    """``paths``, ``commits``, ``stopwords`` and ``regexTarget`` are not maintenance."""
    unexpected = sorted(set(_allowlist()) - ALLOWED_KEYS)
    assert not unexpected, (
        f"[allowlist] carries {unexpected}, which suppresses by a unit larger than one "
        f"value. If that is what is wanted, say why in the file and add the key to "
        f"ALLOWED_KEYS in the same change, so the widening is reviewed rather than "
        f"inherited."
    )


@pytest.mark.parametrize("entry", _entries())
def test_every_allowlist_entry_is_an_anchored_literal(entry: str) -> None:
    """Anchored is not enough: ``^.*$`` and ``^a|b$`` are both anchored."""
    assert _LITERAL_ENTRY_RE.match(entry), (
        f"allowlist entry {entry!r} is not an anchored literal. Expected ^<value>$ with "
        f"no regular-expression metacharacter in <value>, so that the entry exempts the "
        f"one string it names and not a family nobody enumerated."
    )


@pytest.mark.parametrize("entry", _entries())
def test_every_allowlist_entry_still_matches_something_in_the_tree(entry: str) -> None:
    """A stale exemption is one no reader can evaluate, so it survives forever."""
    match = _LITERAL_ENTRY_RE.match(entry)
    if match is None:  # pragma: no cover - reported by the test above
        pytest.skip("not an anchored literal; reported separately")
    value = match.group(1)
    assert value in _tracked_text(), (
        f"allowlist entry {entry!r} matches nothing in the checked-in tree. Either the "
        f"value moved and the entry should follow it, or it is gone and the entry "
        f"should go with it."
    )
