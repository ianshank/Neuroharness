"""The published schemas say what Python says (governance section 3, stage 10).

``docs/sdd/schemas/*.json`` is called "the authoritative wire contract" by the
schema-conformance CI job, and until now the resource-key grammar and the
reason-code catalogue were *transcribed* into it by hand. Transcription drifts:
the schemas carried ``maxLength: 256`` where Python bounded a key at 158, and a
kind-segment charset that refused the hyphen the signed registry admits.

So the values Python owns are rendered into the schemas by
``tools/render_schema_patterns.py`` and this module is the drift check. It is
the cheap half of a generated contract: the tool writes, the test refuses to let
anyone hand-edit what the tool writes.

Scope, stated because a checker whose scope nobody states grows a blind spot:
this module owns the *derived* values only - the resource-key nodes, the
reason-code ``anyOf`` branches and its ``maxLength``. Everything else in those
files is written by hand and reviewed by hand, and
``tests/unit/test_schema_conformance.py`` checks the models against them in both
directions.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from render_schema_patterns import (  # noqa: E402  - after the sys.path insert
    SCHEMA_ROOT,
    _at,
    _read,
    _set,
    derived_values,
    drift,
)


def test_the_checked_in_schemas_match_python() -> None:
    """The headline check.

    On failure the fix is one command, named in the message rather than left to
    be guessed - a check whose remedy is unclear is a check people work around.
    """
    stale = drift()
    assert not stale, (
        "these schema values no longer match their Python source:\n  "
        + "\n  ".join(stale)
        + "\n\nrun: python3 tools/render_schema_patterns.py"
    )


def test_the_tool_owns_something_in_every_schema_it_names() -> None:
    """Guard the guard.

    ``drift()`` reports nothing when it is given nothing to compare. A typo in a
    JSON pointer, a renamed ``$defs`` entry, or a schema split into two files
    would all empty the comparison silently and leave this module green while
    checking nothing - which is the failure mode the whole increment is about.
    """
    values = derived_values()
    assert values, "the tool derives no values at all"
    for schema_file, pointers in values.items():
        assert pointers, f"the tool claims {schema_file} and owns nothing in it"
        assert (SCHEMA_ROOT / schema_file).is_file(), f"{schema_file} does not exist"


@pytest.mark.parametrize("schema_file", sorted(derived_values()))
def test_every_rendered_pattern_is_a_valid_regex(schema_file: str) -> None:
    """A pattern is only a contract if both sides can compile it.

    The length bound travels as an ECMA-262 lookahead so that it reaches the
    schemas rather than living only in ``is_resource_key``'s separate ``len()``
    check. Lookaheads are valid in ECMA-262 and in Python, but they are the one
    construct in these patterns where the two dialects could part company, so
    the compile is asserted rather than assumed.
    """
    document: dict[str, Any] = json.loads(
        (SCHEMA_ROOT / schema_file).read_text(encoding="utf-8")
    )
    patterns: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pattern = node.get("pattern")
            if isinstance(pattern, str):
                patterns.append(pattern)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    assert patterns, f"{schema_file} declares no patterns; the walk found nothing"
    for pattern in patterns:
        re.compile(pattern)


def _tampered_reader(schema_file: str, pointer: str, mutate: Callable[[Any], Any]):
    """A ``drift`` reader that serves one schema with one derived value altered.

    Reads the real file and edits the parsed copy, so the tampering is applied
    to exactly the document ``drift`` would otherwise have read, and nothing is
    written to disk.
    """

    def read(requested: str) -> dict[str, Any]:
        document = _read(requested)
        if requested == schema_file:
            _set(document, pointer, mutate(_at(document, pointer)))
        return document

    return read


@pytest.mark.parametrize(
    ("pointer", "mutate", "what"),
    [
        ("$defs/ResourceKey", lambda node: {**node, "maxLength": node["maxLength"] + 1}, "a widened bound"),
        ("$defs/ResourceKey", lambda node: {**node, "pattern": "^.*$"}, "a loosened pattern"),
        ("$defs/ReasonCode/maxLength", lambda value: value + 1, "a widened scalar"),
        ("$defs/ReasonCode/anyOf", lambda branches: branches[:-1], "a dropped alternative"),
    ],
)
def test_a_hand_edit_to_a_generated_value_is_caught(
    pointer: str, mutate: Callable[[Any], Any], what: str
) -> None:
    """The drift check must *report* a hand edit, not merely be capable of it.

    What this replaced asserted ``tampered != expected`` on a value it had
    just built by changing one key - true by construction, and true whatever
    ``drift`` does. ``drift`` could have been ``return []`` and it stayed green:
    a green check over a guard that had stopped guarding, which is the failure
    this tool exists to catch in the schemas.

    Each case is a hand edit somebody would plausibly make - widening a bound,
    loosening a pattern, dropping a reason-code alternative - and each is a
    loosening, because that is the direction that matters: a schema quietly
    wider than the Python grammar accepts documents the harness will refuse.
    """
    record = "decision-record.schema.json"
    reported = drift(_tampered_reader(record, pointer, mutate))
    assert f"{record}#{pointer}" in reported, (
        f"drift() did not report {what} at {pointer}; it returned {reported}. "
        f"The check cannot distinguish a clean tree from a broken comparison."
    )


def test_the_checked_in_schemas_are_the_control_for_that() -> None:
    """The same reader, untampered, reports nothing - so the cases above are the edit.

    Without this, every case above could pass because ``drift`` reports
    everything always, which is as useless as reporting nothing.
    """
    assert drift(_read) == [], (
        "the checked-in schemas are already stale, so the tampering tests above "
        "prove nothing; run python3 tools/render_schema_patterns.py"
    )
