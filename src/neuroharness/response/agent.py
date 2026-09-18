"""The agent-facing response, and the closed vocabulary it may speak.

``02-technical-plan.md`` §4.1 fixes the contract: *"the gateway returns one of
``{verdict, reason_codes[], counterexamples[], approval_ref?, result?}``; free
text is never emitted"*. Until this module existed, that sentence was the entire
implementation of it. The nearest type in the package,
:class:`~neuroharness.pipeline.decision.EvaluationOutcome`, is not it: it carries
a :class:`~neuroharness.tokens.model.SignedToken` and an
:class:`~neuroharness.evidence.store.AppendResult`, neither of which an agent may
ever see, and it carries neither counterexamples nor an approval reference.

Four rules shape what is written below.

**Free text is unrepresentable, not filtered.** Every field is a closed
enumeration (:class:`~neuroharness.models.common.Verdict`,
:class:`~neuroharness.models.common.ReceiptStatus`), a typed
:class:`~neuroharness.reason.ReasonCode`, a UUID, a
:class:`~neuroharness.models.common.Digest`, a bounded number, or a
counterexample whose every string is pattern-bound. There is no field a sentence
fits in, and ``extra="forbid"`` means there is no field to add one to. That is
``SEC-07`` read literally - *"the gateway rejects any string field not enumerated
or pattern-bound in the output schema"* - and threat ``T-11``: a compromised
critic or a hostile fact provider must not be able to deliver instruction-shaped
text to the governed model through the channel that exists to help it repair.

**One narrowing over the record vocabulary, and it is deliberate.**
``models/record.py``'s ``Scalar`` admits any string up to 128 characters, so
``Counterexample.fields[].value`` and ``ExpectedDomain.enum`` are, in a *record*,
places a short sentence fits. That is right for a record - an auditor must see
the offending value exactly as it was proposed - and wrong here, because this
structure is handed back to the model. :data:`AgentCounterexample` is the same
type with the disclosure charset applied: every scalar string it carries must be
a reason-code subject (:data:`neuroharness.grammar.SUBJECT_PATTERN`). Nothing is
copied and no second counterexample model exists, because a second spelling of a
shared vocabulary is how the resource-key grammar drifted (``ADR-0023``).

**The result is a reference, not a payload.** ``FR-63`` requires a tool result
returned to the governed runtime to pass through the broker, be typed per the
tool's output schema, size-bounded and tagged untrusted. The broker does not
exist, and the per-tool output schema is its contract, not this module's. So the
verdict channel carries :class:`ExecutionResultRef` - what happened, over what
digest, how large, and whether it was truncated - and no unbounded content. Were
the payload itself ever added here, it would have to arrive as a typed document
validated against the registered output schema and tagged untrusted; it must
never arrive as a string.

**``action_id`` is present although §4.1's list omits it.** ``FR-90`` requires
the counterexamples of an iteration to be returned *"together, in typed form,
with the ``action_id``"*, and ``FR-91`` links iterations by it. A repair channel
that cannot say which action is being repaired is not a repair channel.

Bounds are not invented here: :data:`MAX_REASON_CODES` and
:data:`MAX_COUNTEREXAMPLES` are the decision record's own, so the response can
never say more than the evidence says (``NFR-20``: the record is what explains a
verdict, and a response longer than its record would be unexplainable).

**What this does not do, stated plainly (Constitution Art. X).** A charset bounds
the *shape* of a string, not its meaning: ``deploy-to-production-now`` is a legal
identifier. What closes that is the other half of ``SEC-07`` - *"suffixes are
validated by the gateway against registry rule, critic, fact and property IDs"* -
which resolves every identifier against the signed registries and refuses one
that names nothing. That check needs the registry at request time and belongs to
the gateway, which does not exist yet. This module delivers the structural half:
no sentence, no newline, no unbounded field, nothing an auditor would have to
read to know what was returned.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from pydantic import AfterValidator, Field, field_validator, model_validator

from neuroharness import grammar
from neuroharness.models.common import Digest, ReceiptStatus, Verdict
from neuroharness.models.envelope import WireModel
from neuroharness.models.record import (
    MAX_COUNTEREXAMPLES,
    MAX_REASON_CODES,
    Counterexample,
    ReasonCodeField,
    Scalar,
)

__all__ = [
    "AgentCounterexample",
    "ExecutionResultRef",
    "AgentResponse",
    "VERDICTS_CARRYING_COUNTEREXAMPLES",
    "VERDICTS_CARRYING_APPROVAL_REF",
    "MAX_REASON_CODES",
    "MAX_COUNTEREXAMPLES",
]


# --- The disclosure charset --------------------------------------------------


def _assert_disclosable(value: Scalar, *, where: str) -> None:
    """Refuse a scalar string that is not an identifier (``SEC-07``).

    Booleans, numbers and ``None`` pass untouched: they have no charset to
    smuggle anything through. A string must be a reason-code subject, which is
    the widest identifier shape the harness records anywhere, and still narrow
    enough that no sentence - anything with a space, a comma or a quote - fits.
    """
    if isinstance(value, str) and not grammar.SUBJECT_PATTERN.fullmatch(value):
        raise ValueError(
            f"{where} is not an identifier; a counterexample returned to the "
            "governed model carries typed values, never text (SEC-07, FR-56)"
        )


def _refuse_undisclosable_text(counterexample: Counterexample) -> Counterexample:
    """Hold a record counterexample to the agent channel's charset.

    ``property_id``, the JSON pointers and ``message_code`` are already
    pattern-bound by the record model. The two holes are the scalar positions -
    the offending ``value`` and the ``expected.enum`` members - and the second is
    the larger one: an enum may carry up to ``MAX_EXPECTED_ENUM`` scalars, so a
    single counterexample field could otherwise hand the model several thousand
    characters of whatever a critic chose to put there.
    """
    for index, field in enumerate(counterexample.fields):
        _assert_disclosable(field.value, where=f"counterexample field {index} value")
        expected = field.expected
        if expected is None or expected.enum is None:
            continue
        for position, member in enumerate(expected.enum):
            _assert_disclosable(
                member, where=f"counterexample field {index} expected enum member {position}"
            )
    return counterexample


#: A :class:`~neuroharness.models.record.Counterexample` that may be shown to the
#: governed model. Same type, same wire form, narrower charset.
AgentCounterexample = Annotated[Counterexample, AfterValidator(_refuse_undisclosable_text)]


# --- What may accompany which verdict ----------------------------------------

#: Verdicts a counterexample can honestly accompany. Both arise from a hard
#: ``FAIL``, which is what ``FR-56`` says a counterexample explains: ``REPAIR``
#: when the failure is repairable and budget remains, ``DENY`` when it is not.
#: On ``ALLOW`` nothing failed, and on ``ABSTAIN`` the harness could not decide -
#: a counterexample there would assert a definite finding the evaluation never
#: made, and would be a disclosure channel with no purpose.
VERDICTS_CARRYING_COUNTEREXAMPLES: Final[frozenset[Verdict]] = frozenset(
    {Verdict.REPAIR, Verdict.DENY}
)

#: The only verdict an approval reference belongs to. An approval handle attached
#: to any other verdict is a handle on a decision that verdict did not make
#: (Constitution Art. IX: who decided, on exactly what, until when).
VERDICTS_CARRYING_APPROVAL_REF: Final[frozenset[Verdict]] = frozenset(
    {Verdict.REQUIRES_APPROVAL}
)


class ExecutionResultRef(WireModel):
    """What the broker did, as a reference rather than as content (``FR-63``).

    ``untrusted`` is pinned to ``True`` by a validator rather than left a flag,
    for the same reason
    :attr:`~neuroharness.models.record.ExecutionReceipt.result_tagged_untrusted`
    is: the day it can be ``False`` is the day some path treats a tool result -
    attacker-influenced content returning into the model's context (``SEC-08``,
    threat ``T-12``) - as trusted input.
    """

    status: ReceiptStatus
    untrusted: bool = True
    digest: Digest | None = None
    size_bytes: Annotated[int, Field(ge=0)] | None = None
    truncated: bool | None = None

    @field_validator("untrusted")
    @classmethod
    def _results_are_always_untrusted(cls, value: bool) -> bool:
        """A result handed back to the governed runtime is never trusted."""
        if value is not True:
            raise ValueError(
                "a tool result returned to the governed runtime is always tagged "
                "untrusted; untrusted cannot be false (FR-63, SEC-08)"
            )
        return value


class AgentResponse(WireModel):
    """Everything the governed runtime is ever told about a decision.

    Frozen and ``extra="forbid"`` by :class:`~neuroharness.models.envelope.WireModel`.
    Both matter here more than elsewhere: ``extra="forbid"`` is what makes an
    ``explain``, a ``message``, a ``detail`` or a ``token`` field unspellable, and
    frozen is what stops a later layer editing a response after it has been
    decided.

    Deliberately absent, and each absence is a rule rather than an omission:

    ``token`` / ``record``
        The decision token authorises execution and is presented by the gateway
        to the broker; the agent never holds it. The evidence record is the
        harness's own chain position. Neither is a field of this type, so
        returning this object cannot leak either one.
    ``mode``
        The rollout mode tells the model whether the class currently enforces.
        An agent that can read it can find out when it is unguarded.
    ``explain`` / ``shadow_verdict``
        :attr:`~neuroharness.resolve.inputs.Resolution.explain` is an operator's
        trace and ``shadow_verdict`` is rollout measurement. Both are for the
        log and the record (``NFR-20``), and neither is for the model.
    """

    verdict: Verdict
    action_id: UUID
    reason_codes: Annotated[
        tuple[ReasonCodeField, ...], Field(max_length=MAX_REASON_CODES)
    ] = ()
    counterexamples: Annotated[
        tuple[AgentCounterexample, ...], Field(max_length=MAX_COUNTEREXAMPLES)
    ] = ()
    approval_ref: UUID | None = None
    result: ExecutionResultRef | None = None

    @model_validator(mode="after")
    def _every_refusal_is_explained(self) -> AgentResponse:
        """A non-``ALLOW`` verdict carries at least one reason code (§5.6).

        The same rule :class:`~neuroharness.resolve.inputs.Resolution` enforces,
        restated at the boundary because the two can be built independently. An
        unexplained refusal cannot be appealed, cannot be repaired, and gives the
        proposer nothing to do except retry - which is the behaviour the repair
        channel exists to replace.
        """
        if self.verdict is not Verdict.ALLOW and not self.reason_codes:
            raise ValueError(
                f"verdict {self.verdict.value} must carry at least one reason code "
                "(section 5.6); an unexplained refusal cannot be repaired or appealed"
            )
        return self

    @model_validator(mode="after")
    def _counterexamples_explain_a_failure(self) -> AgentResponse:
        """Counterexamples accompany only the verdicts a hard ``FAIL`` produces."""
        if self.counterexamples and self.verdict not in VERDICTS_CARRYING_COUNTEREXAMPLES:
            permitted = sorted(verdict.value for verdict in VERDICTS_CARRYING_COUNTEREXAMPLES)
            raise ValueError(
                f"verdict {self.verdict.value} carries {len(self.counterexamples)} "
                f"counterexample(s); a counterexample explains a hard FAIL and belongs "
                f"to {', '.join(permitted)} (FR-56, FR-90)"
            )
        return self

    @model_validator(mode="after")
    def _approval_ref_belongs_to_the_request_for_one(self) -> AgentResponse:
        """An approval reference accompanies only ``REQUIRES_APPROVAL``."""
        if self.approval_ref is not None and self.verdict not in VERDICTS_CARRYING_APPROVAL_REF:
            permitted = sorted(verdict.value for verdict in VERDICTS_CARRYING_APPROVAL_REF)
            raise ValueError(
                f"verdict {self.verdict.value} carries an approval_ref; an approval "
                f"reference belongs to {', '.join(permitted)} (FR-40, Art. IX)"
            )
        return self

    @property
    def is_repairable(self) -> bool:
        """True when the proposer may resubmit against this ``action_id``.

        ``REPAIR`` is the only non-terminal verdict (§5.2). Exposed as a property
        so a caller branches on the verdict rather than on the presence of
        counterexamples, which are supplied by critics that may not have run.
        """
        return not self.verdict.is_terminal
