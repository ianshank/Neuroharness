"""The agent-facing response contract (``SEC-07``, ``FR-56``, ``FR-90``).

``02-technical-plan.md`` §4.1 says the gateway returns one of
``{verdict, reason_codes[], counterexamples[], approval_ref?, result?}`` and that
free text is never emitted. This package is that sentence, made into a type.

Two things are worth knowing before reading further.

**Nothing here talks to a model, and nothing here runs a loop.**
:class:`~neuroharness.response.agent.AgentResponse` is a value. How the governed
runtime's own scaffold presents it to the model is the scaffold's business
(``02-technical-plan.md`` §4.12: *"the harness supplies no natural-language
text"*). The ``FR-90``-``FR-92`` repair *loop* - holding an action open across
iterations, linking a resubmission to it, spending the budget, rate-limiting new
actions, measuring iterations-to-allow - needs an orchestrator that does not
exist yet, because it needs the gateway. What this package delivers is the
channel the loop will speak over: a response that can carry typed
counterexamples and the ``action_id`` they belong to, with no path for prose.

**The projection is the point.** A gateway author holding an
:class:`~neuroharness.pipeline.decision.EvaluationOutcome` has the decision token
and the evidence record in their hands. Returning that object is the whole
failure. :func:`~neuroharness.response.projection.project_evaluation_outcome` is
the only route from one to the other, and the response type has no field either
artifact could occupy, so the mistake is unspellable rather than merely
discouraged.
"""

from neuroharness.response.agent import (
    MAX_COUNTEREXAMPLES,
    MAX_REASON_CODES,
    VERDICTS_CARRYING_APPROVAL_REF,
    VERDICTS_CARRYING_COUNTEREXAMPLES,
    AgentCounterexample,
    AgentResponse,
    ExecutionResultRef,
)
from neuroharness.response.projection import (
    ResponseNotProjectableError,
    discloses_counterexamples,
    project_evaluation_outcome,
)

__all__ = [
    # the contract
    "AgentResponse",
    "AgentCounterexample",
    "ExecutionResultRef",
    # the one route into it
    "project_evaluation_outcome",
    "ResponseNotProjectableError",
    "discloses_counterexamples",
    # what may accompany which verdict, and how much of it
    "VERDICTS_CARRYING_COUNTEREXAMPLES",
    "VERDICTS_CARRYING_APPROVAL_REF",
    "MAX_REASON_CODES",
    "MAX_COUNTEREXAMPLES",
]
