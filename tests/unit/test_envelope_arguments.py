"""``FR-02`` and ``FR-03``: the two checks the registry defended and nobody ran.

Since increment 1 the registry has refused an action class whose
``argument_schema`` is open (``MUT-30``, ``A-35``) and has stored every schema
faithfully. Nothing evaluated one. ``Context.stripped_proposal_keys`` has been a
field nobody populates. So ``FR-02``'s "an undeclared argument is
``SCHEMA_INVALID``" and ``FR-03``'s "context-shaped keys are removed and listed"
were both held by documents.

The tests below are organised around the two ways this can be got wrong:

* **too permissive** - an argument slips through unchecked, which is the
  smuggling channel ``FR-02`` closes;
* **too quiet** - a schema the evaluator does not fully understand is applied
  partially and reported as clean, which is worse than the first because the
  decision record then shows a validation that did not happen.

Every negative case has its control, so an evaluator that rejected everything
would fail here rather than look rigorous.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from neuroharness.envelope.arguments import (
    _JSON_TYPES,
    SUPPORTED_KEYWORDS,
    ArgumentValidator,
    BoundedSchemaValidator,
    ViolationKind,
)
from neuroharness.envelope.proposal import PROPOSAL_FIELDS, strip_context_keys
from neuroharness.models.envelope import Proposal

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
REFERENCE_REGISTRY: Final[Path] = (
    REPO_ROOT / "tests" / "fixtures" / "registry" / "reference_deploy_registry.json"
)


@pytest.fixture(scope="module")
def deploy_schema() -> dict[str, Any]:
    """The real schema from the signed reference registry, not an invention.

    A hand-written fixture would drift from what the registry actually declares,
    and then these tests would prove the evaluator handles a schema no action
    class has.
    """
    registry = json.loads(REFERENCE_REGISTRY.read_text(encoding="utf-8"))
    for action_class in registry["action_classes"]:
        if action_class["tool"] == "deployment.apply":
            schema: dict[str, Any] = action_class["argument_schema"]
            return schema
    raise AssertionError("deployment.apply is no longer in the reference registry")


@pytest.fixture
def validator() -> BoundedSchemaValidator:
    return BoundedSchemaValidator()


def valid_arguments() -> dict[str, Any]:
    return {"service": "checkout", "version": "1.2.3", "target": "production", "replicas": 3}


# --- the control --------------------------------------------------------------


def test_valid_arguments_pass(
    validator: BoundedSchemaValidator, deploy_schema: dict[str, Any]
) -> None:
    """Without this, an evaluator that refused everything would look perfect."""
    assert validator.validate(valid_arguments(), deploy_schema) == ()


def test_the_validator_satisfies_its_own_protocol(validator: BoundedSchemaValidator) -> None:
    """The seam is real: a replacement can be substituted without editing callers."""
    assert isinstance(validator, ArgumentValidator)


# --- FR-02: the undeclared argument ------------------------------------------


@pytest.mark.mutation
def test_an_undeclared_argument_is_refused(
    validator: BoundedSchemaValidator, deploy_schema: dict[str, Any]
) -> None:
    """``FR-02``'s headline clause, and ``MUT-30``'s gate at the layer it names.

    ``MUT-30`` is killed today at the resource-key layer - a bad ``target`` never
    renders a key - which is a real defence and a different one. This is the
    clause ``FR-02`` actually states: an argument nobody declared travels to the
    tool unvalidated while the record shows a clean validation.
    """
    arguments = valid_arguments() | {"dry_run": False}
    violations = validator.validate(arguments, deploy_schema)

    assert [v.kind for v in violations] == [ViolationKind.UNDECLARED_ARGUMENT]
    assert violations[0].pointer == "/dry_run"


def test_a_missing_required_argument_is_refused(
    validator: BoundedSchemaValidator, deploy_schema: dict[str, Any]
) -> None:
    arguments = valid_arguments()
    del arguments["replicas"]
    violations = validator.validate(arguments, deploy_schema)
    assert [v.kind for v in violations] == [ViolationKind.MISSING_ARGUMENT]
    assert violations[0].pointer == "/replicas"


@pytest.mark.parametrize(
    ("argument", "value", "kind"),
    [
        ("target", "Production", ViolationKind.NOT_IN_ENUM),
        ("target", "prod-eu", ViolationKind.NOT_IN_ENUM),
        ("service", "billing", ViolationKind.NOT_IN_ENUM),
        ("version", "1.2", ViolationKind.PATTERN_MISMATCH),
        ("version", "v1.2.3", ViolationKind.PATTERN_MISMATCH),
        ("replicas", 0, ViolationKind.BELOW_MINIMUM),
        ("replicas", 51, ViolationKind.ABOVE_MAXIMUM),
        ("replicas", "3", ViolationKind.WRONG_TYPE),
        # `service` declares an enum and no `type`, so a non-string is out of the
        # enumeration rather than the wrong type. Both refuse; the distinction is
        # what a repair reads, so it is asserted rather than glossed.
        ("service", 1, ViolationKind.NOT_IN_ENUM),
    ],
    ids=lambda value: str(value),
)
@pytest.mark.mutation
def test_each_declared_constraint_refuses_what_it_exists_to_refuse(
    validator: BoundedSchemaValidator,
    deploy_schema: dict[str, Any],
    argument: str,
    value: Any,
    kind: ViolationKind,
) -> None:
    """``target: "Production"`` is ``MUT-30``'s own mutation, verbatim.

    The casing case matters beyond this table: two spellings of one production
    target are two policies and only one was reviewed (``FR-34``, ``A-35``).
    """
    violations = validator.validate(valid_arguments() | {argument: value}, deploy_schema)
    assert [v.kind for v in violations] == [kind]
    assert violations[0].pointer == f"/{argument}"


def test_a_boolean_is_not_an_integer(
    validator: BoundedSchemaValidator, deploy_schema: dict[str, Any]
) -> None:
    """Python says ``True == 1``; an action class asking for replicas does not.

    ``isinstance(True, int)`` is ``True``, so the obvious implementation accepts
    ``replicas: true`` and deploys one replica. It would also digest as a boolean
    and render into a resource key as ``True``.
    """
    violations = validator.validate(valid_arguments() | {"replicas": True}, deploy_schema)
    assert [v.kind for v in violations] == [ViolationKind.WRONG_TYPE]


def test_every_violation_is_reported_not_just_the_first(
    validator: BoundedSchemaValidator, deploy_schema: dict[str, Any]
) -> None:
    """A repair loop that fixed one argument per round would exhaust its budget.

    ``FR-90``'s repair budget is small by design. An evaluator that stopped at
    the first violation would turn a payload with three fixable arguments into
    three round trips, and the third would be refused for budget rather than for
    anything about the action.
    """
    arguments = {"service": "billing", "version": "nope", "target": "Production", "replicas": 0}
    violations = validator.validate(arguments, deploy_schema)
    assert len(violations) == 4
    assert {v.pointer for v in violations} == {"/service", "/version", "/target", "/replicas"}


# --- the fail-closed half: a schema the evaluator cannot fully apply ----------


@pytest.mark.parametrize(
    "unsupported",
    ["format", "allOf", "oneOf", "$ref", "multipleOf", "minLength", "items", "not"],
)
def test_a_schema_using_an_unsupported_keyword_is_refused_not_ignored(
    validator: BoundedSchemaValidator, unsupported: str
) -> None:
    """The design property, and the reason this is not a dependency.

    A validator that skipped what it did not understand would report a clean
    validation over an argument nobody checked. That is strictly worse than
    refusing: the decision record would assert that ``FR-02`` was applied.

    Every keyword here is one a real schema author would reach for, which is the
    point - the refusal has to be loud enough that the vocabulary gets extended
    deliberately rather than worked around.
    """
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string", unsupported: "anything"}},
    }
    violations = validator.validate({"name": "checkout"}, schema)

    assert [v.kind for v in violations] == [ViolationKind.UNSUPPORTED_SCHEMA]
    assert violations[0].expectation == unsupported
    assert violations[0].pointer == "/name"


def test_an_unsupported_keyword_suppresses_the_argument_level_result(
    validator: BoundedSchemaValidator,
) -> None:
    """Refused *instead of*, not *alongside*.

    Reporting "and by the way these two arguments were fine" would be a claim
    the evaluator has no basis for: it did not apply the whole schema, so it
    does not know.
    """
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string", "format": "hostname"}},
    }
    violations = validator.validate({"name": 42, "undeclared": True}, schema)
    assert {v.kind for v in violations} == {ViolationKind.UNSUPPORTED_SCHEMA}


def test_the_reference_registrys_schemas_are_all_within_the_vocabulary() -> None:
    """The bet this design makes, checked rather than assumed.

    The bounded vocabulary is only safe if real action classes stay inside it.
    If a registry in the tree ever needs a ninth keyword, this fails and the
    choice becomes explicit: extend the vocabulary deliberately, or take the
    dependency and say so in an ADR.
    """
    registry = json.loads(REFERENCE_REGISTRY.read_text(encoding="utf-8"))
    used: set[str] = set()

    def walk(node: Any, *, inside_properties: bool) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if not inside_properties:
                used.add(key)
            walk(value, inside_properties=key == "properties")

    for action_class in registry["action_classes"]:
        walk(action_class["argument_schema"], inside_properties=False)

    assert used <= SUPPORTED_KEYWORDS, (
        f"the reference registry now uses {sorted(used - SUPPORTED_KEYWORDS)}, which the "
        "bounded evaluator refuses. Extend SUPPORTED_KEYWORDS deliberately or revisit D-3."
    )


# --- FR-03: the strip ---------------------------------------------------------


def test_the_strip_list_comes_from_the_model() -> None:
    """Derived, not written down.

    A hard-coded deny-list of context-shaped keys goes wrong in both directions:
    a new ``Proposal`` field starts being stripped, or a new envelope field
    silently passes through.
    """
    assert frozenset(Proposal.model_fields) == PROPOSAL_FIELDS
    assert "tool" in PROPOSAL_FIELDS
    assert "actor" not in PROPOSAL_FIELDS


@pytest.mark.parametrize(
    "smuggled",
    ["actor", "context", "action_class", "facts", "session_id", "policy_bundle", "schema_version"],
)
def test_a_context_shaped_key_is_removed_and_recorded(smuggled: str) -> None:
    """Constitution Article I, as a function.

    Each of these is a way for the model to nominate something the harness is
    supposed to determine - most sharply ``actor``, which is the model telling
    the harness who it is.
    """
    payload = {"tool": "deployment.apply", "intent": "deploy", "arguments": {}, smuggled: {}}
    result = strip_context_keys(payload)

    assert smuggled not in result.payload
    assert result.stripped == (smuggled,)
    assert result.payload["tool"] == "deployment.apply"


def test_a_clean_payload_is_untouched_and_records_nothing() -> None:
    """The control, and the ordinary case.

    ``stripped_proposal_keys`` must stay ``None``-equivalent for a clean payload:
    an empty tuple that means "nothing was stripped" and a tuple that means
    "something was" have to be distinguishable in the record.
    """
    payload = {"tool": "deployment.apply", "intent": "deploy", "arguments": {"service": "a"}}
    result = strip_context_keys(payload)
    assert result.stripped == ()
    assert result.payload == payload


def test_stripped_keys_are_sorted_so_one_proposal_has_one_identity() -> None:
    """Order-dependence here would give one proposal two envelope digests.

    ``stripped_proposal_keys`` is inside the envelope, so it is inside the
    envelope digest (``FR-04``). Two payloads carrying the same stray keys in
    different orders are the same proposal and must digest the same.
    """
    base = {"tool": "t", "intent": "i", "arguments": {}}
    forwards = strip_context_keys(base | {"actor": 1, "context": 2, "facts": 3})
    backwards = strip_context_keys({"facts": 3, "context": 2, "actor": 1} | base)
    assert forwards.stripped == backwards.stripped == ("actor", "context", "facts")


def test_the_stripped_payload_validates_where_the_raw_one_would_not() -> None:
    """The ordering constraint, stated as a test.

    ``Proposal`` sets ``extra="forbid"``, so an unstripped payload *raises*
    rather than being cleaned. The strip therefore has to happen before
    validation - and this is what proves the two steps are in the right order,
    which no test asserted because nothing performed the first one.
    """
    raw = {
        "tool": "deployment.apply",
        "intent": "deploy_service",
        "arguments": {"service": "checkout"},
        "actor": {"agent_id": "i-say-who-i-am"},
    }
    with pytest.raises(ValueError):
        Proposal.model_validate(raw)

    cleaned = strip_context_keys(raw)
    proposal = Proposal.model_validate(cleaned.payload)
    assert proposal.tool == "deployment.apply"
    assert cleaned.stripped == ("actor",)


# --- A `type` the evaluator cannot apply is a schema it cannot apply ----------
#
# The keyword survey checked keyword *names* and not their values, so `type`
# passed it and then `_check`'s `_JSON_TYPES.get()` returned `None` and skipped
# the type check for that subschema without saying so. The schema then accepted
# arguments of every shape and reported no violations - the exact inversion of
# ADR-0024's contract, in the module that contract is about.


#: Values of `type` no JSON Schema author should get away with here. `int` and
#: `str` are the typos (the JSON names are `integer` and `string`); `["string",
#: "null"]` is *legal* JSON Schema this evaluator does not implement, which is
#: the more dangerous case because nothing about it looks like a mistake.
UNEVALUABLE_TYPES: Final[tuple[Any, ...]] = (
    "int",
    "str",
    "float",
    "dict",
    "Integer",
    "",
    ["string", "null"],
    123,
    None,
    {"$ref": "#/$defs/Thing"},
)


@pytest.mark.parametrize("declared", UNEVALUABLE_TYPES, ids=repr)
def test_a_type_the_evaluator_cannot_apply_is_refused(declared: Any) -> None:
    violations = BoundedSchemaValidator().validate({"anything": "at all"}, {"type": declared})
    assert [v.kind for v in violations] == [ViolationKind.UNSUPPORTED_SCHEMA], (
        f"schema {{'type': {declared!r}}} was evaluated rather than refused; "
        f"got {violations}"
    )


@pytest.mark.parametrize("declared", UNEVALUABLE_TYPES, ids=repr)
def test_a_nested_unevaluable_type_is_refused(declared: Any) -> None:
    """The realistic shape: one property of an action class's argument schema.

    A registry declaring ``{"replicas": {"type": "int"}}`` used to accept a
    string where it had asked for a number, silently. That is a signed policy
    document whose constraint does nothing.
    """
    schema = {"type": "object", "properties": {"replicas": {"type": declared}}}
    violations = BoundedSchemaValidator().validate({"replicas": "not a number"}, schema)
    kinds = [v.kind for v in violations]
    assert kinds == [ViolationKind.UNSUPPORTED_SCHEMA], (
        f"nested {{'type': {declared!r}}} was evaluated rather than refused; got {violations}"
    )
    assert violations[0].pointer == "/replicas", (
        f"the refusal must name the offending subschema; got {violations[0].pointer!r}"
    )


@pytest.mark.parametrize("declared", sorted(_JSON_TYPES))
def test_every_supported_type_name_still_evaluates(declared: str) -> None:
    """The other direction, so the fix cannot be "refuse everything".

    A guard that refuses the whole vocabulary passes every test above and makes
    the evaluator useless, which is the failure mode of tightening a check only
    against its negative cases.
    """
    violations = BoundedSchemaValidator().validate({}, {"type": declared})
    assert all(v.kind is not ViolationKind.UNSUPPORTED_SCHEMA for v in violations), (
        f"the supported JSON type {declared!r} was refused as unsupported: {violations}"
    )


# --- A supported keyword carrying a value the evaluator cannot act on --------
#
# The first fix closed this for `type` alone, and the ADR line it added -- "a
# closed vocabulary is not closed until the values inside it are closed too" --
# was written while seven other keywords still carried the hole. Each guarded
# its value with an `isinstance` in `_check` and, on a miss, applied no
# constraint and reported nothing. A signed registry saying `service` is
# required, called with `{}`, produced no violation at all.


#: Malformed values for each supported keyword, with the argument that would
#: have slipped through. Every one returned no violations before this fix.
MALFORMED_SCHEMAS: Final[tuple[tuple[str, dict[str, Any], Any], ...]] = (
    ("properties", {"type": "object", "properties": "not-a-map"}, {"anything": 1}),
    ("properties", {"type": "object", "properties": {"a": "not-a-subschema"}}, {"a": 1}),
    ("required", {"required": "service"}, {}),
    (
        "required",
        {"type": "object", "required": "service", "properties": {"service": {"type": "string"}}},
        {},
    ),
    ("required", {"required": ["service", 7]}, {}),
    ("enum", {"type": "string", "enum": "abc"}, "z"),
    ("enum", {"type": "string", "enum": []}, "z"),
    ("additionalProperties", {"type": "object", "additionalProperties": "false"}, {}),
    ("pattern", {"type": "string", "pattern": 12345}, "anything at all"),
    ("pattern", {"type": "string", "pattern": "([unclosed"}, "anything at all"),
    ("minimum", {"type": "integer", "minimum": "not-a-number"}, -999),
    ("minimum", {"type": "integer", "minimum": True}, 0),
    ("maximum", {"type": "integer", "maximum": [1]}, 999),
)


@pytest.mark.parametrize(
    ("keyword", "schema", "arguments"),
    MALFORMED_SCHEMAS,
    ids=[f"{kw}-{i}" for i, (kw, _, _) in enumerate(MALFORMED_SCHEMAS)],
)
def test_a_malformed_keyword_value_refuses_the_schema(
    keyword: str, schema: dict[str, Any], arguments: Any
) -> None:
    violations = BoundedSchemaValidator().validate(arguments, schema)
    kinds = {v.kind for v in violations}
    assert kinds == {ViolationKind.UNSUPPORTED_SCHEMA}, (
        f"{keyword} carrying {schema[keyword]!r} was evaluated rather than refused; "
        f"got {violations}"
    )
    assert any(v.expectation == keyword for v in violations), (
        f"the refusal must name the offending keyword {keyword!r} so an author can "
        f"find it; got {[v.expectation for v in violations]}"
    )


#: The other direction. A guard that refuses everything passes every case above
#: and makes the evaluator useless, which is the failure mode of tightening a
#: check only against its negative cases.
WELL_FORMED_SCHEMAS: Final[tuple[tuple[dict[str, Any], Any, ViolationKind | None], ...]] = (
    (
        {"type": "object", "required": ["service"], "properties": {"service": {"type": "string"}}},
        {},
        ViolationKind.MISSING_ARGUMENT,
    ),
    (
        {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}},
        {"n": 0},
        ViolationKind.BELOW_MINIMUM,
    ),
    (
        {"type": "object", "properties": {"n": {"type": "integer", "maximum": 1}}},
        {"n": 9},
        ViolationKind.ABOVE_MAXIMUM,
    ),
    ({"type": "string", "pattern": "^a+$"}, "bbb", ViolationKind.PATTERN_MISMATCH),
    ({"type": "string", "enum": ["a", "b"]}, "z", ViolationKind.NOT_IN_ENUM),
    ({"type": "object", "additionalProperties": False}, {"x": 1}, ViolationKind.UNDECLARED_ARGUMENT),
    ({"type": "object", "properties": {"s": {"type": "string"}}}, {"s": "fine"}, None),
)


@pytest.mark.parametrize(("schema", "arguments", "expected"), WELL_FORMED_SCHEMAS)
def test_a_well_formed_keyword_still_does_its_job(
    schema: dict[str, Any], arguments: Any, expected: ViolationKind | None
) -> None:
    violations = BoundedSchemaValidator().validate(arguments, schema)
    kinds = [v.kind for v in violations]
    assert ViolationKind.UNSUPPORTED_SCHEMA not in kinds, (
        f"a well-formed schema was refused as unsupported: {violations}"
    )
    if expected is None:
        assert not violations, f"valid arguments produced {violations}"
    else:
        assert expected in kinds, f"expected {expected}, got {kinds}"


def test_every_supported_keyword_has_a_declared_value_shape() -> None:
    """The two tables are one vocabulary, and a keyword in only one is the hole.

    Import already asserts this, so a disagreement is a startup failure rather
    than a wrong answer. Restated as a test because an import-time check that
    nobody has ever seen fail is indistinguishable from one that cannot.
    """
    from neuroharness.envelope.arguments import (
        _KEYWORD_SHAPES,
        _assert_every_supported_keyword_has_a_shape,
    )

    assert set(_KEYWORD_SHAPES) == set(SUPPORTED_KEYWORDS)
    _assert_every_supported_keyword_has_a_shape()


def test_the_vocabulary_check_fails_when_the_tables_disagree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard the guard: prove the import-time assertion can actually fire."""
    from neuroharness.envelope import arguments as module

    monkeypatch.setattr(
        module, "SUPPORTED_KEYWORDS", SUPPORTED_KEYWORDS | {"minLength"}, raising=True
    )
    with pytest.raises(module.UnsupportedSchemaVocabularyError, match="minLength"):
        module._assert_every_supported_keyword_has_a_shape()
