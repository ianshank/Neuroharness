"""Validating a proposal's arguments against its action class (``FR-02``).

``FR-02`` requires that ``proposal.arguments`` is checked against the action
class's ``argument_schema`` and that an undeclared argument is ``SCHEMA_INVALID``.
The registry has stored and defended that schema since increment 1 - it refuses
an action class whose schema is open (``MUT-30``, ``A-35``) - and **nothing has
ever evaluated one**. ``jsonschema`` is a dev-only dependency and is imported
nowhere in ``src/``.

``ADR-0024`` records the decision (increment-2 ``D-3``). The short version, and
the reason this is a hand-written evaluator rather than a dependency:

* **The trusted computing base is one runtime package.** Adding a second is a
  supply-chain decision under governance §4, not a convenience.
* **Replay determinism (`NFR-07`, `R-13`).** A general validator's behaviour
  depends on its draft resolution and its version. A decision replayed a year
  later must reach the same verdict; "the same, modulo a minor release of a
  transitive dependency" is not that.
* **The vocabulary is small and closed.** Across every registry in the tree the
  schemas use eight keywords: ``type``, ``properties``, ``required``, ``enum``,
  ``additionalProperties``, ``pattern``, ``minimum``, ``maximum``. That is not a
  coincidence - the registry's own rules push authors towards enumerations for
  anything policy compares.

**The property that makes this safe, and it is the whole design:** this
evaluator *refuses a schema it cannot fully evaluate*. An unsupported keyword is
not ignored, and it is not best-effort - it is a violation, reported against the
schema rather than against the arguments. A validator that silently skipped what
it did not understand would report a clean validation over an argument nobody
checked, which is exactly the smuggling channel ``FR-02`` exists to close.

The :class:`ArgumentValidator` protocol is the seam. Swapping in a full
JSON Schema implementation later means registering one, not editing callers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Protocol, runtime_checkable

from neuroharness.observability.logging import get_logger

__all__ = [
    "SUPPORTED_KEYWORDS",
    "ArgumentValidator",
    "ArgumentViolation",
    "BoundedSchemaValidator",
    "ViolationKind",
]

_LOG: Final = get_logger("neuroharness.envelope")


class UnsupportedSchemaVocabularyError(RuntimeError):
    """The keyword vocabulary and the value-shape table disagree.

    Raised at import, not at evaluation. A ``RuntimeError`` rather than a
    ``FailClosedError`` for the same reason ``grammar.py`` uses one: this module
    is imported by the envelope layer, and reaching for ``errors`` here would
    close an import cycle. There is nothing to fail closed *about* yet - the
    process has not started.
    """

_EVENT_UNSUPPORTED: Final[str] = "envelope.schema_unsupported"

#: Every JSON Schema keyword this evaluator understands. A schema using anything
#: else is refused rather than partially applied - see the module docstring.
#:
#: Derived from what the registries in the tree actually declare, and deliberately
#: not grown speculatively: each addition is a keyword whose semantics somebody
#: has to get exactly right, and a keyword implemented *almost* right is worse
#: than one that refuses.
SUPPORTED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "type",
        "properties",
        "required",
        "enum",
        "additionalProperties",
        "pattern",
        "minimum",
        "maximum",
    }
)

#: JSON type names this evaluator can check, mapped to the Python types that
#: satisfy them. ``bool`` is excluded from ``integer`` on purpose: Python makes
#: ``True`` an ``int``, and an action class expecting a replica count should not
#: accept a boolean.
_JSON_TYPES: Final[Mapping[str, tuple[type, ...]]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _is_json_type(value: Any) -> bool:
    return isinstance(value, str) and value in _JSON_TYPES


def _is_subschema_map(value: Any) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(name, str) and isinstance(sub, Mapping) for name, sub in value.items()
    )


def _is_name_list(value: Any) -> bool:
    # `str` is a sequence of characters, so `{"required": "service"}` would
    # iterate as `s`, `e`, `r`... A check that reads a string as a list of
    # names is worse than one that refuses it.
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def _is_enum(value: Any) -> bool:
    # Non-empty, because `{"enum": []}` accepts nothing and is a schema whose
    # author meant something else.
    return isinstance(value, (list, tuple)) and len(value) > 0


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_regex(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        re.compile(value)
    except re.error:
        # A pattern that does not compile is a constraint that cannot be
        # applied, which is this module's definition of a refusal - not an
        # exception to raise out of a survey.
        return False
    return True


def _is_number(value: Any) -> bool:
    # `bool` is an `int` in Python; a bound of `True` is a schema mistake.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


#: The value shape each supported keyword must carry for this evaluator to act
#: on it. Keyed by the same frozenset above, and checked to be total at import,
#: because a keyword added to ``SUPPORTED_KEYWORDS`` without an entry here would
#: be a keyword whose value nobody validates - the exact hole this table closes.
#:
#: Every one of these was a silent skip. `_check` guarded each keyword with an
#: `isinstance` and, on a miss, simply did not apply that constraint and
#: reported nothing: `{"required": "service"}` skipped the required check,
#: `{"pattern": 12345}` skipped the pattern, `{"minimum": "x"}` skipped the
#: bound. A signed registry's constraint that silently does nothing is worse
#: than no constraint, because the document says it is enforced.
_KEYWORD_SHAPES: Final[Mapping[str, Callable[[Any], bool]]] = {
    "type": _is_json_type,
    "properties": _is_subschema_map,
    "required": _is_name_list,
    "enum": _is_enum,
    "additionalProperties": _is_bool,
    "pattern": _is_regex,
    "minimum": _is_number,
    "maximum": _is_number,
}


def _assert_every_supported_keyword_has_a_shape() -> None:
    """A keyword with no shape entry is a keyword whose value nobody checks."""
    missing = SUPPORTED_KEYWORDS - set(_KEYWORD_SHAPES)
    extra = set(_KEYWORD_SHAPES) - SUPPORTED_KEYWORDS
    if missing or extra:
        raise UnsupportedSchemaVocabularyError(
            f"SUPPORTED_KEYWORDS and _KEYWORD_SHAPES disagree: "
            f"{sorted(missing)} have no shape, {sorted(extra)} shape nothing supported"
        )


class ViolationKind(str, Enum):
    """Why an argument or a schema was refused.

    A closed vocabulary, because a violation becomes the subject of a
    ``SCHEMA_INVALID`` refusal and reason codes never carry free text
    (``SEC-07``). The message an operator reads is built from these plus a JSON
    Pointer, never from an exception's ``str()``.
    """

    UNDECLARED_ARGUMENT = "undeclared_argument"
    MISSING_ARGUMENT = "missing_argument"
    WRONG_TYPE = "wrong_type"
    NOT_IN_ENUM = "not_in_enum"
    PATTERN_MISMATCH = "pattern_mismatch"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    #: The schema, not the arguments. Fail-closed: see the module docstring.
    UNSUPPORTED_SCHEMA = "unsupported_schema"


@dataclass(frozen=True, slots=True)
class ArgumentViolation:
    """One refusal, addressed by JSON Pointer.

    ``value`` is deliberately absent. A violation travels towards a decision
    record and, through the repair channel, towards the governed model; echoing
    the offending value back would hand the model a way to put chosen text into
    its own context (``SEC-07``, ``T-11``). The pointer says *where*, the kind
    says *what rule*, and the expectation says *what would have been accepted* -
    which is what a repair needs and no more.
    """

    pointer: str
    kind: ViolationKind
    expectation: str


@runtime_checkable
class ArgumentValidator(Protocol):
    """The seam. A full JSON Schema implementation can land behind this."""

    def validate(
        self, arguments: Mapping[str, Any], schema: Mapping[str, Any]
    ) -> tuple[ArgumentViolation, ...]:
        """Return every violation. Empty means the arguments satisfy the schema.

        Returns rather than raises: a caller wants all of them, because a repair
        round-trip that fixed one argument per iteration would burn the repair
        budget (``FR-90``) on a schema the agent could have satisfied at once.
        """
        ...


class BoundedSchemaValidator:
    """A closed-vocabulary evaluator that refuses what it cannot evaluate."""

    __slots__ = ()

    def validate(
        self, arguments: Mapping[str, Any], schema: Mapping[str, Any]
    ) -> tuple[ArgumentViolation, ...]:
        unsupported = self._unsupported_keywords(schema, pointer="")
        if unsupported:
            # Reported *instead of* any argument-level result, not alongside it.
            # A partial result here would read as "these three arguments are
            # fine", and this evaluator has no basis for saying that about a
            # schema it did not fully apply.
            _LOG.error(
                _EVENT_UNSUPPORTED,
                keywords=sorted({violation.expectation for violation in unsupported}),
            )
            return unsupported
        return tuple(self._check(arguments, schema, pointer=""))

    # -- fail-closed schema survey ------------------------------------------

    def _unsupported_keywords(
        self, schema: Mapping[str, Any], *, pointer: str
    ) -> tuple[ArgumentViolation, ...]:
        """Walk the schema and report every keyword outside the vocabulary."""
        found: list[ArgumentViolation] = []
        for keyword, value in schema.items():
            if keyword not in SUPPORTED_KEYWORDS:
                found.append(
                    ArgumentViolation(
                        pointer=pointer or "/",
                        kind=ViolationKind.UNSUPPORTED_SCHEMA,
                        expectation=keyword,
                    )
                )
                continue
            if not _KEYWORD_SHAPES[keyword](value):
                # A supported keyword carrying a value this evaluator cannot
                # act on, which the keyword survey alone does not catch. ``int``
                # is not a JSON type name - it is the typo a schema author
                # makes for ``integer`` - and the list form ``["string",
                # "null"]`` is legal JSON Schema this evaluator does not
                # implement. Both used to fall through ``_check``'s
                # ``_JSON_TYPES.get()`` as ``None`` and disable the type check
                # for that subschema *silently*, so the schema accepted
                # arguments of every shape while reporting no violations.
                #
                # Refused here rather than repaired in ``_check`` because this
                # is the same question the rest of this survey asks: can the
                # whole schema be applied? A value it cannot act on means no,
                # and ``ADR-0024`` says that answer is a refusal (``FR-02``).
                found.append(
                    ArgumentViolation(
                        pointer=pointer or "/",
                        kind=ViolationKind.UNSUPPORTED_SCHEMA,
                        # The keyword whose value is wrong. Not the value
                        # itself: a malformed one can be an arbitrary
                        # structure, and the keyword names the site precisely
                        # enough for a schema author to find it. ``type`` is
                        # the exception, because "you wrote `int`" is the whole
                        # diagnostic there and the name comes from the signed
                        # registry rather than from the agent (``SEC-07``,
                        # ``T-11``).
                        expectation=(
                            value
                            if keyword == "type" and isinstance(value, str)
                            else keyword
                        ),
                    )
                )
                continue
            if keyword == "properties" and isinstance(value, Mapping):
                for name, subschema in value.items():
                    if isinstance(subschema, Mapping):
                        found.extend(
                            self._unsupported_keywords(
                                subschema, pointer=f"{pointer}/{name}"
                            )
                        )
        return tuple(found)

    # -- argument evaluation -------------------------------------------------

    def _check(
        self, value: Any, schema: Mapping[str, Any], *, pointer: str
    ) -> list[ArgumentViolation]:
        violations: list[ArgumentViolation] = []
        declared_type = schema.get("type")
        if isinstance(declared_type, str):
            expected = _JSON_TYPES.get(declared_type)
            if expected is not None and not self._is_type(value, declared_type, expected):
                return [
                    ArgumentViolation(
                        pointer=pointer or "/",
                        kind=ViolationKind.WRONG_TYPE,
                        expectation=declared_type,
                    )
                ]

        choices = schema.get("enum")
        if (
            isinstance(choices, Sequence)
            and not isinstance(choices, (str, bytes))
            and value not in choices
        ):
            violations.append(
                ArgumentViolation(
                    pointer=pointer or "/",
                    kind=ViolationKind.NOT_IN_ENUM,
                    # The enumeration is the registry's, not the agent's, so
                    # naming its members is safe and is what a repair needs.
                    expectation=", ".join(str(choice) for choice in choices),
                )
            )

        pattern = schema.get("pattern")
        if (
            isinstance(pattern, str)
            and isinstance(value, str)
            and not re.compile(pattern).search(value)
        ):
            violations.append(
                ArgumentViolation(
                    pointer=pointer or "/",
                    kind=ViolationKind.PATTERN_MISMATCH,
                    expectation=pattern,
                )
            )

        violations.extend(self._check_bounds(value, schema, pointer=pointer))
        if isinstance(value, Mapping):
            violations.extend(self._check_object(value, schema, pointer=pointer))
        return violations

    @staticmethod
    def _is_type(value: Any, declared: str, expected: tuple[type, ...]) -> bool:
        if declared in {"integer", "number"} and isinstance(value, bool):
            # True is an int in Python. A replica count is not a boolean.
            return False
        return isinstance(value, expected)

    @staticmethod
    def _check_bounds(
        value: Any, schema: Mapping[str, Any], *, pointer: str
    ) -> list[ArgumentViolation]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return []
        found: list[ArgumentViolation] = []
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            found.append(
                ArgumentViolation(
                    pointer=pointer or "/",
                    kind=ViolationKind.BELOW_MINIMUM,
                    expectation=str(minimum),
                )
            )
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            found.append(
                ArgumentViolation(
                    pointer=pointer or "/",
                    kind=ViolationKind.ABOVE_MAXIMUM,
                    expectation=str(maximum),
                )
            )
        return found

    def _check_object(
        self, value: Mapping[str, Any], schema: Mapping[str, Any], *, pointer: str
    ) -> list[ArgumentViolation]:
        found: list[ArgumentViolation] = []
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}

        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
            for name in required:
                if name not in value:
                    found.append(
                        ArgumentViolation(
                            pointer=f"{pointer}/{name}",
                            kind=ViolationKind.MISSING_ARGUMENT,
                            expectation=str(name),
                        )
                    )

        # ``FR-02``'s whole point. The registry already guarantees
        # ``additionalProperties: false``; this is where that guarantee is
        # applied to an actual payload.
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in declared:
                    found.append(
                        ArgumentViolation(
                            pointer=f"{pointer}/{name}",
                            kind=ViolationKind.UNDECLARED_ARGUMENT,
                            expectation=", ".join(sorted(declared)) or "no arguments",
                        )
                    )

        for name, subschema in declared.items():
            if name in value and isinstance(subschema, Mapping):
                found.extend(self._check(value[name], subschema, pointer=f"{pointer}/{name}"))
        return found


_assert_every_supported_keyword_has_a_shape()
