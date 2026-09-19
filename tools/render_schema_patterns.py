"""Write the derived patterns from Python into the published JSON Schemas.

The resource-key grammar and the reason-code catalogue are contracts, and a
contract stated twice is a contract that drifts. Before this tool the grammar
lived in four places - ``registry/resource_keys.py``, ``models/record.py``,
``reason.py`` and the two schema files - and two of them disagreed:
``cluster-prod:svc-a`` was accepted by the signed registry, used as the broker's
lease identity under ``FR-25``, and rejected by the record model and both
schemas. The ``TokenInvalidReason`` catalogue lived in three, which is why
adding a ninth member had never been worth doing.

Now Python is the source and the schemas are rendered from it. Run:

    python3 tools/render_schema_patterns.py            # write
    python3 tools/render_schema_patterns.py --check    # report drift, write nothing

``--check`` is what CI runs, via ``tests/unit/test_schema_patterns_are_generated.py``.

Deliberately *not* under ``src/``: the trusted computing base is inventoried,
and a build-time renderer with no runtime caller does not belong in it. The test
adds this directory to ``sys.path`` rather than the package importing it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
SCHEMA_ROOT: Final[Path] = REPO_ROOT / "docs" / "sdd" / "schemas"

sys.path.insert(0, str(REPO_ROOT / "src"))

from neuroharness import grammar  # noqa: E402  - after the sys.path insert above
from neuroharness.models.record import (  # noqa: E402
    MAX_REASON_CODE_LENGTH,
    REASON_CODE_ALTERNATIVES,
)

RECORD_SCHEMA: Final[str] = "decision-record.schema.json"
ENVELOPE_SCHEMA: Final[str] = "action-envelope.schema.json"

#: Where a resource key appears in each published schema. Both get the same
#: three values, because they are the same string in the same grammar; the
#: schemas had different ``maxLength`` values (256) from the Python bound (158),
#: which is a fourth way for the same constraint to disagree with itself.
_RESOURCE_KEY_SITES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (RECORD_SCHEMA, ("$defs", "ResourceKey")),
    (ENVELOPE_SCHEMA, ("$defs", "ActionClassRef", "properties", "resource_key")),
)


def _resource_key_node() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": grammar.RESOURCE_KEY_PATTERN.pattern,
        "maxLength": grammar.MAX_RESOURCE_KEY_LENGTH,
    }


def _reason_code_alternatives() -> list[dict[str, Any]]:
    """The catalogue as JSON Schema ``anyOf`` branches, in Python's order.

    The first branch is the bare names as an ``enum``; the rest are anchored
    patterns. ``REASON_CODE_ALTERNATIVES[0]`` is the ``|``-joined bare names,
    which reads better in a schema as an enumeration than as an alternation, so
    it is split back out here rather than emitted as a pattern.
    """
    bare, *parameterised = REASON_CODE_ALTERNATIVES
    branches: list[dict[str, Any]] = [{"enum": bare.split("|")}]
    branches.extend({"pattern": f"^{alternative}$"} for alternative in parameterised)
    return branches


def derived_values() -> dict[str, dict[str, Any]]:
    """Every value this tool owns, keyed by schema file.

    Returned as a plain structure so the drift check and the writer compute it
    exactly once, from the same code. A checker that recomputed the expected
    value differently from the writer would be a fifth source of truth.
    """
    per_file: dict[str, dict[str, Any]] = {RECORD_SCHEMA: {}, ENVELOPE_SCHEMA: {}}
    for schema_file, path in _RESOURCE_KEY_SITES:
        per_file[schema_file]["/".join(path)] = _resource_key_node()
    per_file[RECORD_SCHEMA]["$defs/ReasonCode/anyOf"] = _reason_code_alternatives()
    per_file[RECORD_SCHEMA]["$defs/ReasonCode/maxLength"] = MAX_REASON_CODE_LENGTH
    return per_file


def _read(schema_file: str) -> dict[str, Any]:
    text = (SCHEMA_ROOT / schema_file).read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _at(document: dict[str, Any], pointer: str) -> Any:
    node: Any = document
    for step in pointer.split("/"):
        node = node[step]
    return node


def _set(document: dict[str, Any], pointer: str, value: Any) -> None:
    steps = pointer.split("/")
    node: Any = document
    for step in steps[:-1]:
        node = node[step]
    last = steps[-1]
    if isinstance(value, dict) and isinstance(node.get(last), dict):
        # Keep whatever the schema author wrote around the derived keys - the
        # `description` on a resource_key field is documentation this tool has
        # no opinion about and must not silently delete.
        node[last] = {**node[last], **value}
    else:
        node[last] = value


def drift(read: Callable[[str], dict[str, Any]] = _read) -> list[str]:
    """Pointers whose checked-in value differs from the derived one.

    ``read`` is injectable so the comparison itself can be tested against a
    tampered document without writing one to disk. It defaults to reading the
    checked-in schemas, so ``--check`` and every existing caller are unchanged.

    The seam is here because the test that claimed to cover this never called
    this function: it built a tampered value and asserted it differed from the
    one it was built from, which is true by construction. ``drift`` could have
    returned ``[]`` unconditionally and that test would still have passed - a
    green check over a guard that had stopped guarding, which is the exact
    failure this tool exists to catch in the schemas.
    """
    stale: list[str] = []
    for schema_file, values in derived_values().items():
        document = read(schema_file)
        for pointer, expected in values.items():
            actual = _at(document, pointer)
            if isinstance(expected, dict):
                actual = {key: actual.get(key) for key in expected}
            if actual != expected:
                stale.append(f"{schema_file}#{pointer}")
    return stale


def render() -> list[str]:
    """Write the derived values in; return the files that changed."""
    changed: list[str] = []
    for schema_file, values in derived_values().items():
        path = SCHEMA_ROOT / schema_file
        before = path.read_text(encoding="utf-8")
        document = json.loads(before)
        for pointer, value in values.items():
            _set(document, pointer, value)
        # Two spaces and a trailing newline: what the files already use, so a
        # render produces no incidental diff.
        after = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(schema_file)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and write nothing; exits non-zero when the schemas are stale",
    )
    args = parser.parse_args(argv)

    if args.check:
        stale = drift()
        for pointer in stale:
            print(f"stale: {pointer}")
        if stale:
            print("\nrun: python3 tools/render_schema_patterns.py")
        return 1 if stale else 0

    changed = render()
    for schema_file in changed:
        print(f"rendered: {schema_file}")
    if not changed:
        print("no change: the schemas already match Python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
