"""No response ever carries a string outside a closed vocabulary (``SEC-07``).

The unit tests enumerate the smuggling routes somebody thought of. This one
stops depending on that: it generates resolutions with the real resolver, hands
the projection whatever text hypothesis invents in the two scalar positions a
counterexample leaves open, and then reads back *every string in the serialised
response*, holding each one against the vocabulary its position is allowed to
speak.

The classification table is the substance of the file. A predicate per key
means a new string-bearing field cannot be added to the response without
somebody saying, in this file, which grammar it belongs to - and until they do,
this property fails. A single "everything matches the identifier charset" check
would have been shorter and would have accepted a new free-text field the day
its charset happened to be narrow.

``SEC-07``: *"the gateway rejects any string field not enumerated or
pattern-bound in the output schema"*. That sentence is a property, not an
example, which is why it is tested here as well as in
``tests/unit/test_agent_response.py``.
"""

from __future__ import annotations

import re
from typing import Any, Final
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from neuroharness import grammar
from neuroharness.defaults import MAX_REPAIR_BUDGET
from neuroharness.models.common import (
    Digest,
    FactStatus,
    Mode,
    ReceiptStatus,
    Verdict,
    VerifierResult,
)
from neuroharness.models.record import (
    Counterexample,
    CounterexampleField,
    ExpectedDomain,
    ExpectedRelation,
    Scalar,
)
from neuroharness.pipeline import EvaluationOutcome, TokenWithheld
from neuroharness.reason import (
    ESCALATABLE_REASONS,
    INFRASTRUCTURE_REASONS,
    PARAMETERISED_REASONS,
    ReasonCode,
    ReasonName,
)
from neuroharness.resolve import (
    CriticOutcome,
    FactState,
    ResolutionRequest,
    SimpleClassPolicy,
    resolve,
)
from neuroharness.response import (
    VERDICTS_CARRYING_COUNTEREXAMPLES,
    AgentResponse,
    ExecutionResultRef,
    ResponseNotProjectableError,
    discloses_counterexamples,
    project_evaluation_outcome,
)

pytestmark = pytest.mark.property

# --- The closed vocabularies, one per position -------------------------------

#: The record model owns this shape; it is restated here because a test that
#: imported the annotation would be asserting the response against itself. If
#: the two ever disagree, a pointer this table rejects will fail the property,
#: which is the direction that fails safely.
_JSON_POINTER: Final[re.Pattern[str]] = re.compile(r"^(/[A-Za-z0-9_.~-]+)+$")

_VERDICT_VALUES: Final[frozenset[str]] = frozenset(verdict.value for verdict in Verdict)
_RECEIPT_VALUES: Final[frozenset[str]] = frozenset(status.value for status in ReceiptStatus)
_RELATION_VALUES: Final[frozenset[str]] = frozenset(
    relation.value for relation in ExpectedRelation
)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _is_digest(value: str) -> bool:
    try:
        Digest(value)
    except ValueError:
        return False
    return True


def _is_reason_code(value: str) -> bool:
    """A rendered member of the closed catalogue, and nothing else.

    Round-tripped rather than pattern-matched: parsing proves the name is in the
    catalogue and the subject satisfied its grammar, and re-rendering proves
    nothing was dropped on the way - a trailing newline, for instance, which
    would forge a line in a JSONL evidence export.
    """
    try:
        return ReasonCode.parse(value).render() == value
    except ValueError:
        return False


def _is_identifier(value: str) -> bool:
    """The disclosure charset: a reason-code subject (``SEC-07``)."""
    return grammar.SUBJECT_PATTERN.fullmatch(value) is not None


def _is_property_id(value: str) -> bool:
    return grammar.anchored(grammar.PROPERTY_ID_SOURCE).fullmatch(value) is not None


#: Every key a string may appear under in a serialised response, and the
#: vocabulary that key may speak. A string under any other key fails the
#: property, which is how a new field gets classified rather than shipped.
VOCABULARY: Final[dict[str, Any]] = {
    "verdict": lambda value: value in _VERDICT_VALUES,
    "action_id": _is_uuid,
    "approval_ref": _is_uuid,
    "reason_codes": _is_reason_code,
    "message_code": _is_reason_code,
    "property_id": _is_property_id,
    "path": lambda value: _JSON_POINTER.fullmatch(value) is not None,
    "reference_path": lambda value: _JSON_POINTER.fullmatch(value) is not None,
    "value": _is_identifier,
    "enum": _is_identifier,
    "value_digest": _is_digest,
    "digest": _is_digest,
    "relation": lambda value: value in _RELATION_VALUES,
    "status": lambda value: value in _RECEIPT_VALUES,
}


def string_leaves(document: Any, key: str | None = None) -> list[tuple[str, str]]:
    """Every string in ``document``, paired with the key that introduced it.

    List members inherit their list's key, which is what makes ``reason_codes``
    and ``expected.enum`` classifiable at all: the vocabulary belongs to the
    field, not to the position within it.
    """
    if isinstance(document, str):
        return [(key or "", document)]
    if isinstance(document, dict):
        return [
            leaf
            for inner_key, value in document.items()
            for leaf in string_leaves(value, str(inner_key))
        ]
    if isinstance(document, list):
        return [leaf for value in document for leaf in string_leaves(value, key)]
    return []


# --- Generators ---------------------------------------------------------------

_CRITIC_IDS: Final[tuple[str, ...]] = ("critic.a", "critic.b")
_FACT_NAMES: Final[tuple[str, ...]] = ("fact_a", "fact_b")
_RULE_ID: Final[str] = "WF-01"
_PROPERTY_IDS: Final[tuple[str, ...]] = ("WF-06a", "INV-09", "FR-25.window")
_POINTERS: Final[tuple[str, ...]] = (
    "/proposal/arguments/target",
    "/proposal/arguments/replicas",
    "/context/facts/deploy_state",
)

#: Identifier-shaped values a real critic would report, mixed below with text
#: hypothesis invents. Both matter: the invented text must be refused, and these
#: must not be, or the disclosure charset would be a charset nothing fits.
_LEGITIMATE_SCALARS: Final[tuple[Scalar, ...]] = (
    "cluster-prod",
    "svc-a",
    "v1.2.3",
    "sha256:" + "a" * 64,
    True,
    3,
    1.5,
    None,
)


#: A subject shaped for the *name* that carries it. ``ReasonCode`` itself only
#: checks the wide reason-subject charset, while the record catalogue fixes a
#: grammar per name - ``CRITIC_ERROR`` takes a critic id, not a requirement id -
#: so a reason built with the wrong shape constructs happily and is then refused
#: at the record writer and here. Generating the right shape keeps this file
#: testing the response rather than that disagreement.
_SUBJECT_BY_NAME: Final[dict[ReasonName, str]] = {ReasonName.CRITIC_ERROR: _CRITIC_IDS[0]}


def _reason(name: ReasonName) -> ReasonCode:
    if name not in PARAMETERISED_REASONS:
        return ReasonCode(name)
    return ReasonCode(name, _SUBJECT_BY_NAME.get(name, _RULE_ID))


legitimate_scalars = st.sampled_from(_LEGITIMATE_SCALARS)

#: Anything at all, bounded by what the record model would accept in the field,
#: so that the property tests the response layer rather than the record layer.
any_scalars = st.one_of(
    legitimate_scalars,
    st.text(max_size=grammar.MAX_SUBJECT_LENGTH),
    st.integers(),
    st.booleans(),
    st.none(),
)


def counterexamples(scalars: st.SearchStrategy[Scalar]) -> st.SearchStrategy[Counterexample]:
    return st.builds(
        Counterexample,
        property_id=st.sampled_from(_PROPERTY_IDS),
        fields=st.lists(
            st.builds(
                CounterexampleField,
                path=st.sampled_from(_POINTERS),
                value=scalars,
                expected=st.builds(
                    ExpectedDomain,
                    enum=st.lists(scalars, max_size=3).map(tuple),
                    relation=st.sampled_from(tuple(ExpectedRelation)),
                    reference_path=st.sampled_from(_POINTERS),
                ),
            ),
            min_size=1,
            max_size=2,
        ).map(tuple),
    )


#: Reasons shaped as the record catalogue requires: ``RULE_FAILED`` names a
#: *rule*, ``MONITOR_VIOLATION`` names a property.
#:
#: Every generated critic states its own reason, which is not incidental. A
#: critic that states none falls back to
#: ``resolve.inputs._CRITIC_REASON_BY_RESULT``, which renders a ``FAIL`` as
#: ``RULE_FAILED:<critic_id>`` - and the catalogue in ``models/record.py``
#: requires a rule id there, so that reason cannot be recorded and cannot be
#: returned. That is a defect in the resolver's fallback rather than in this
#: file, and it is reported rather than worked around: pinning the reasons here
#: keeps the property aimed at the response layer instead of failing on every
#: example for a reason that has nothing to do with it.
well_shaped_reasons = st.sampled_from(
    (
        ReasonCode(ReasonName.RULE_FAILED, _RULE_ID),
        ReasonCode(ReasonName.MONITOR_VIOLATION, _PROPERTY_IDS[0]),
    )
)

critic_outcomes = st.builds(
    CriticOutcome,
    critic_id=st.sampled_from(_CRITIC_IDS),
    result=st.sampled_from(tuple(VerifierResult)),
    hard=st.booleans(),
    effective_mode=st.sampled_from((Mode.SHADOW, Mode.ADVISORY, Mode.ENFORCE)),
    repairable=st.sampled_from((True, False, None)),
    reason=well_shaped_reasons,
)

fact_states = st.builds(
    FactState,
    name=st.sampled_from(_FACT_NAMES),
    status=st.sampled_from(tuple(FactStatus)),
    required=st.booleans(),
    escalatable=st.booleans(),
)

class_policies = st.builds(
    SimpleClassPolicy,
    mode=st.sampled_from(tuple(Mode)),
    approvable=st.booleans(),
    escalate_on=st.frozensets(
        st.sampled_from(sorted(ESCALATABLE_REASONS, key=lambda name: name.value)),
        max_size=len(ESCALATABLE_REASONS),
    ),
    repair_budget=st.integers(min_value=0, max_value=MAX_REPAIR_BUDGET),
)

requests = st.builds(
    ResolutionRequest,
    policy=class_policies,
    critic_outcomes=st.lists(critic_outcomes, max_size=3).map(tuple),
    fact_states=st.lists(fact_states, max_size=2).map(tuple),
    infrastructure_reasons=st.lists(
        st.sampled_from(sorted(INFRASTRUCTURE_REASONS, key=lambda name: name.value)).map(_reason),
        max_size=2,
    ).map(tuple),
    repair_iteration=st.integers(min_value=0, max_value=MAX_REPAIR_BUDGET),
    approval_required=st.booleans(),
    approval_rule_id=st.just(_RULE_ID),
    approval_satisfied=st.booleans(),
    rate_limited=st.booleans(),
)


def outcome_of(request: ResolutionRequest) -> EvaluationOutcome:
    """Resolve ``request`` and wrap it as the pipeline would, minus the artifacts.

    ``token`` and ``record`` are ``None`` here: what this file is about is the
    text that reaches the model, and a real token would only slow the generator
    down. The token-drop itself is asserted against a real signed token in
    ``tests/unit/test_agent_response.py``.
    """
    resolution = resolve(request)
    return EvaluationOutcome(
        verdict=resolution.verdict,
        reason_codes=resolution.reason_codes,
        resolution=resolution,
        record=None,
        token=None,
        # ``D-4``: an outcome with no token names why. Derived from the
        # resolution rather than fixed, so a generated ``ALLOW`` is described
        # as an issuance that did not land rather than as a verdict that
        # refused - which would be a fiction the generator quietly produced.
        withheld=(
            TokenWithheld.VERDICT
            if not resolution.permits_execution
            else TokenWithheld.ISSUANCE_UNRECORDED
        ),
    )


#: Examples per property. The vocabulary property walks the cross product of
#: every verdict the resolver can reach and every scalar hypothesis can invent,
#: so it gets the larger budget; the control only has to show the legitimate
#: path survives. Derandomised for the reason ``test_resolver_properties``
#: gives: a property failure must be reproducible from a checkout.
_VOCABULARY_EXAMPLES: Final[int] = 400
_CONTROL_EXAMPLES: Final[int] = 200

_THOROUGH = settings(deadline=None, max_examples=_VOCABULARY_EXAMPLES, derandomize=True)
_STANDARD = settings(deadline=None, max_examples=_CONTROL_EXAMPLES, derandomize=True)


# --- The properties -----------------------------------------------------------


@_THOROUGH
@given(
    request=requests,
    examples=st.lists(counterexamples(any_scalars), max_size=2).map(tuple),
    mode=st.sampled_from(tuple(Mode)),
    action_id=st.uuids(),
    approval_ref=st.one_of(st.none(), st.uuids()),
)
def test_no_response_speaks_outside_its_vocabulary(
    request: ResolutionRequest,
    examples: tuple[Counterexample, ...],
    mode: Mode,
    action_id: UUID,
    approval_ref: UUID | None,
) -> None:
    """Every string in a projected response belongs to the grammar of its field.

    This is the property the whole package is for. A counterexample is assembled
    from critic output, and a critic is the component an attacker most wants:
    ``SEC-07``'s threat ``T-11`` is a compromised verifier delivering
    instruction-shaped text to the governed model through the repair channel. If
    a single string could reach the model without passing a grammar, every
    structural claim made in ``response/agent.py`` would be decoration.

    Either the projection refuses the input or the response is clean. A refusal
    is an acceptable answer - fail-closed is the harness's whole posture - and
    the control below is what stops "always refuse" passing as an implementation.
    """
    try:
        response = project_evaluation_outcome(
            outcome_of(request),
            action_id=action_id,
            mode=mode,
            counterexamples=examples,
            approval_ref=approval_ref,
        )
    except ResponseNotProjectableError:
        return

    document = response.model_dump(mode="json")
    assert set(document) <= set(AgentResponse.model_fields)
    for key, value in string_leaves(document):
        assert key in VOCABULARY, f"string field {key!r} has no declared vocabulary (SEC-07)"
        assert VOCABULARY[key](value), f"{key}={value!r} is outside its vocabulary (SEC-07)"


@_STANDARD
@given(
    request=requests,
    examples=st.lists(counterexamples(legitimate_scalars), min_size=1, max_size=2).map(tuple),
    action_id=st.uuids(),
)
def test_a_counterexample_a_critic_would_really_report_gets_through(
    request: ResolutionRequest,
    examples: tuple[Counterexample, ...],
    action_id: UUID,
) -> None:
    """The control: the vocabulary must be wide enough for real critic output.

    Resource keys, versions, digests, booleans and numbers are what a typed
    counterexample carries (``FR-56``). If any of them were refused, the property
    above would still pass - every refusal satisfies it - while the repair
    channel quietly returned nothing on every real failure, and the pressure
    would be to widen the charset rather than to find out which value broke.

    Only an enforcing class is checked here: withholding in ``shadow`` is
    ``FR-80`` working, and is asserted as a unit test rather than inferred from
    an empty tuple here. Counterexamples are offered only where the verdict can
    carry them, because attaching one to an ``ALLOW`` is a separate refusal with
    its own test - and a control that tripped over it would stop controlling
    anything.
    """
    outcome = outcome_of(request)
    offered = examples if outcome.verdict in VERDICTS_CARRYING_COUNTEREXAMPLES else ()
    response = project_evaluation_outcome(
        outcome, action_id=action_id, mode=Mode.ENFORCE, counterexamples=offered
    )
    assert response.verdict is outcome.verdict
    assert len(response.counterexamples) == len(offered)


@_STANDARD
@given(request=requests, mode=st.sampled_from(tuple(Mode)), action_id=st.uuids())
def test_a_response_never_carries_what_the_agent_may_not_learn(
    request: ResolutionRequest, mode: Mode, action_id: UUID
) -> None:
    """The operator's half of an outcome never crosses into the agent's half.

    ``Resolution`` carries ``explain`` - which names the rule that fired at each
    step - and ``shadow_verdict``, which says what the verdict would have been
    had every demoted critic been honoured. The first is guidance shaped like
    instructions; the second tells the agent that the class it just called is
    being observed rather than enforced, which is the same as telling it when it
    is unguarded. Neither has a field in the response, and this asserts it over
    every resolution the resolver can produce rather than over one.
    """
    outcome = outcome_of(request)
    response = project_evaluation_outcome(outcome, action_id=action_id, mode=mode)
    document = response.model_dump(mode="json")
    strings = {value for _, value in string_leaves(document)}
    assert not strings & set(outcome.resolution.explain)
    if outcome.resolution.shadow_verdict is not None:
        assert document["verdict"] == outcome.verdict.value


@_STANDARD
@given(
    request=requests,
    mode=st.sampled_from(tuple(Mode)),
    action_id=st.uuids(),
    status=st.sampled_from(tuple(ReceiptStatus)),
)
def test_a_result_needs_a_token_whatever_the_verdict_or_mode(
    request: ResolutionRequest, mode: Mode, action_id: UUID, status: ReceiptStatus
) -> None:
    """``INV-05``: no token, no execution, so nothing to report.

    Stated as a property because the tempting implementation is to test the
    *verdict* instead, which is wrong in both directions: a shadow class holds a
    token on a ``DENY`` and really did execute, while an ``ALLOW`` whose evidence
    write failed holds none and did not. The outcomes generated here carry no
    token, so every one of them must refuse.
    """
    with pytest.raises(ResponseNotProjectableError):
        project_evaluation_outcome(
            outcome_of(request),
            action_id=action_id,
            mode=mode,
            result=ExecutionResultRef(status=status),
        )


@_STANDARD
@given(
    request=requests,
    examples=st.lists(counterexamples(legitimate_scalars), min_size=1, max_size=2).map(tuple),
    mode=st.sampled_from(tuple(Mode)),
    action_id=st.uuids(),
)
def test_disclosure_follows_the_mode_and_nothing_else(
    request: ResolutionRequest,
    examples: tuple[Counterexample, ...],
    mode: Mode,
    action_id: UUID,
) -> None:
    """``FR-80``: a non-enforcing class returns no counterexamples, ever.

    Property rather than example because the risk is a conditional somebody adds
    later - "unless the verdict is DENY", "unless there is only one" - and a
    handful of cases would not find it. Here the class mode is the only input
    that may change what is disclosed.
    """
    outcome = outcome_of(request)
    offered = examples if outcome.verdict in VERDICTS_CARRYING_COUNTEREXAMPLES else ()
    response = project_evaluation_outcome(
        outcome, action_id=action_id, mode=mode, counterexamples=offered
    )
    if discloses_counterexamples(mode):
        assert len(response.counterexamples) == len(offered)
    else:
        assert response.counterexamples == ()
