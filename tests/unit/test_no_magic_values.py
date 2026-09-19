"""Structural enforcement of the "no hardcoded values" constraint.

The increment plan promises this is enforced structurally rather than by
discipline. This is that enforcement.

The rule it checks: inside the decision-path packages, a numeric literal may not
be an operand of a comparison, an argument default, or arithmetic that scales a
policy quantity. Those are the positions where a magic threshold hides. Numbers
that govern behaviour belong in :mod:`neuroharness.defaults` (documented, named,
traceable to a requirement) or in the signed action-class registry.

A small allowlist covers the idioms that are structure rather than policy:
``0`` and ``1`` for indexing, counting and emptiness checks, ``2`` for pairs and
halves, ``-1`` for "last". Anything else must be named.

This test is deliberately a lint with teeth rather than a perfect analysis. If it
ever blocks a legitimate change, the fix is to name the constant, which is the
outcome the rule wants anyway.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "neuroharness"

#: Packages whose code participates in producing a verdict, a token, or a record.
DECISION_PATH_PACKAGES = ("resolve", "tokens", "evidence", "registry", "canonical", "response")

#: Modules exempt because naming values is precisely their job, or because they
#: contain no decision logic.
EXEMPT_FILES = {"defaults.py", "version.py", "__init__.py"}

#: Structural numbers, not policy numbers.
ALLOWED_NUMBERS: frozenset[float] = frozenset({0, 1, 2, -1})

#: Domain strings that must never be compared as literals. These are the values
#: the resource-key registry and the action-class registry exist to supply
#: (``FR-02``, ``FR-34``): an environment name or a fact name frozen into a
#: comparison is exactly the target-aliasing defect round two found (``T-17``).
#: The empty string is allowed because it is an emptiness check, not a value.
FORBIDDEN_STRING_LITERALS: frozenset[str] = frozenset(
    {
        "production",
        "prod",
        "staging",
        "development",
        "test",
        "enforce",
        "shadow",
        "advisory",
        "halted",
        "ci_result",
        "change_approval",
        "deploy_state",
        "harness_approval",
        "ALLOW",
        "DENY",
        "ABSTAIN",
        "REPAIR",
        "REQUIRES_APPROVAL",
    }
)

#: Byte and bit sizes are structure (hash widths, encodings), not policy.
ALLOWED_IN_CALLS = frozenset({"range", "len", "round", "int", "float", "zfill", "ljust", "rjust"})


def _operand_literals(node: ast.AST) -> Iterator[ast.Constant]:
    """Yield every literal an expression contributes to the position it sits in.

    ``900``, ``900 + 1``, ``-900``, ``60 * 15`` and ``x in (900, 1800)`` are one
    threshold written five ways, and only the first is a bare ``ast.Constant``.
    Checking the operand node itself therefore left the rule enforceable only
    against the spelling nobody who wanted to avoid it would use.

    The walk descends through arithmetic, negation and literal collections,
    which are the forms that carry a number through to the enclosing comparison
    or default unchanged. It stops at calls, names and subscripts: those get
    their numbers from somewhere else, and ``visit_Call`` already governs the
    structural helpers, so widening here cannot change their verdict.
    """
    if isinstance(node, ast.Constant):
        yield node
    elif isinstance(node, ast.UnaryOp):
        yield from _operand_literals(node.operand)
    elif isinstance(node, ast.BinOp):
        yield from _operand_literals(node.left)
        yield from _operand_literals(node.right)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for element in node.elts:
            yield from _operand_literals(element)


@dataclass(frozen=True)
class Violation:
    module: str
    line: int
    value: object
    context: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} bare numeric {self.value!r} in {self.context}"


class _MagicNumberVisitor(ast.NodeVisitor):
    """Flags numeric literals in positions where a policy threshold would hide."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.violations: list[Violation] = []
        self._call_depth_allowed = 0

    # A number compared against something is a threshold.
    def visit_Compare(self, node: ast.Compare) -> None:
        for operand in [node.left, *node.comparators]:
            self._check(operand, "a comparison")
        self.generic_visit(node)

    # A default argument value is configuration with no name.
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_defaults(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in ALLOWED_IN_CALLS:
            # Structural helpers: range(2), len()-based slicing and so on.
            for arg in node.args:
                if isinstance(arg, ast.Constant):
                    continue
                self.visit(arg)
            for kw in node.keywords:
                self.visit(kw.value)
            return
        self.generic_visit(node)

    def _check_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults = [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]
        for default in defaults:
            self._check(default, f"the default of {node.name}()")

    def _check(self, node: ast.AST, context: str) -> None:
        for literal in _operand_literals(node):
            self._check_literal(literal, context)

    def _check_literal(self, node: ast.Constant, context: str) -> None:
        value = node.value
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            if value in ALLOWED_NUMBERS:
                return
            self.violations.append(Violation(self.module, node.lineno, value, context))
            return
        if isinstance(value, str) and value in FORBIDDEN_STRING_LITERALS:
            self.violations.append(
                Violation(
                    self.module,
                    node.lineno,
                    value,
                    f"{context} (use the enum member or the registry enumeration)",
                )
            )


def _modules() -> list[Path]:
    found: list[Path] = []
    for package in DECISION_PATH_PACKAGES:
        directory = SRC / package
        if not directory.is_dir():
            continue
        found.extend(p for p in sorted(directory.rglob("*.py")) if p.name not in EXEMPT_FILES)
    return found


def test_decision_path_packages_exist() -> None:
    """Guard against this test silently passing because it scanned nothing."""
    assert _modules(), (
        "no decision-path modules were scanned; either the packages are missing "
        "or DECISION_PATH_PACKAGES is stale"
    )


@pytest.mark.parametrize("module_path", _modules(), ids=lambda p: str(p.name))
def test_no_magic_values_govern_decisions(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    visitor = _MagicNumberVisitor(module_path.relative_to(SRC).as_posix())
    visitor.visit(tree)
    assert not visitor.violations, (
        "policy values must be named in neuroharness.defaults or come from the "
        "signed registry:\n  " + "\n  ".join(str(v) for v in visitor.violations)
    )


def test_visitor_detects_a_planted_threshold(tmp_path: Path) -> None:
    """The scanner must actually catch something, or it proves nothing."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "def decide(age_seconds):\n"
        "    if age_seconds > 900:\n"
        "        return 'STALE'\n"
        "    return 'FRESH'\n",
        encoding="utf-8",
    )
    visitor = _MagicNumberVisitor("planted.py")
    visitor.visit(ast.parse(planted.read_text(encoding="utf-8")))
    assert any(v.value == 900 for v in visitor.violations)


def test_visitor_detects_a_planted_domain_string(tmp_path: Path) -> None:
    """A frozen environment name is the target-aliasing defect (T-17)."""
    planted = tmp_path / "aliased.py"
    planted.write_text(
        "def needs_approval(target):\n"
        "    return target == 'production'\n",
        encoding="utf-8",
    )
    visitor = _MagicNumberVisitor("aliased.py")
    visitor.visit(ast.parse(planted.read_text(encoding="utf-8")))
    assert any(v.value == "production" for v in visitor.violations)


def test_visitor_allows_structural_numbers(tmp_path: Path) -> None:
    planted = tmp_path / "structural.py"
    planted.write_text(
        "def first(items):\n"
        "    if len(items) == 0:\n"
        "        return None\n"
        "    return items[-1]\n",
        encoding="utf-8",
    )
    visitor = _MagicNumberVisitor("structural.py")
    visitor.visit(ast.parse(planted.read_text(encoding="utf-8")))
    assert not visitor.violations


#: One threshold, written the five ways a blocked change gets rewritten: a bound
#: nudged by one, the same bound negated, a duration left in its factors, those
#: factors parenthesised, and a pair of bounds tested for membership. Each pairs
#: with a literal the scanner must name when it reports it.
REWRITTEN_THRESHOLDS = (
    ("age_seconds > 900 + 1", 900),
    ("age_seconds > -900", 900),
    ("age_seconds > 60 * 15", 15),
    ("age_seconds > ((60 * 15) + 1)", 60),
    ("age_seconds in (900, 1800)", 1800),
)


def _planted_violations(tmp_path: Path, source: str) -> list[Violation]:
    planted = tmp_path / "planted.py"
    planted.write_text(source, encoding="utf-8")
    visitor = _MagicNumberVisitor("planted.py")
    visitor.visit(ast.parse(planted.read_text(encoding="utf-8")))
    return visitor.violations


@pytest.mark.parametrize(("expression", "value"), REWRITTEN_THRESHOLDS, ids=lambda p: str(p))
def test_a_threshold_rewritten_as_arithmetic_is_still_caught(
    tmp_path: Path, expression: str, value: int
) -> None:
    """A gate that a one-character rewrite walks past is not a gate.

    This scan is the only thing enforcing that a governing value is named, and an
    unnamed threshold is a policy decision nobody reviewed (``T-17``). While only
    a bare literal was examined, adding ``+ 1`` or a minus sign in front of a
    blocked number shipped it - and the reviewer of that change would read a
    passing structural test as evidence the number had been through the rule.
    """
    source = (
        "def decide(age_seconds):\n"
        f"    if {expression}:\n"
        "        return 'STALE'\n"
        "    return 'FRESH'\n"
    )
    assert any(v.value == value for v in _planted_violations(tmp_path, source))


def test_a_threshold_rewritten_in_an_argument_default_is_still_caught(
    tmp_path: Path,
) -> None:
    """A default is configuration with no name, and hides arithmetic just as well.

    ``ttl=60 * 15`` is the same unreviewed fifteen minutes as ``ttl=900``, and it
    is worse placed: a signature is read as structure, so the number is less
    likely to be questioned there than in the comparison it ends up in.
    """
    violations = _planted_violations(tmp_path, "def issue(subject, ttl=60 * 15):\n    return ttl\n")
    assert {v.value for v in violations} == {60, 15}


def test_structural_arithmetic_is_still_allowed(tmp_path: Path) -> None:
    """Widening the scan must not make legitimate structure unwritable.

    If ``len(items) - 1`` started failing, the pressure would be to add another
    entry to the allowances rather than to name anything - and every widened
    allowance is a place a real threshold can be parked afterwards.
    """
    source = (
        "def window(items, limit):\n"
        "    if len(items) - 1 > limit:\n"
        "        return items[0:2]\n"
        "    return items[len(items) - 1 :]\n"
    )
    assert not _planted_violations(tmp_path, source)
