"""Verdict resolution: the decision procedure (``FR-05``, specification section 5.3).

The public surface is deliberately small. Callers build a
:class:`~neuroharness.resolve.inputs.ResolutionRequest`, call
:func:`~neuroharness.resolve.resolver.resolve`, and record the
:class:`~neuroharness.resolve.inputs.Resolution`. The safety order is exported
because other components need to compare verdicts - the broker, the batch
evaluator and the property tests - and there must be exactly one definition of
what "safer" means.
"""

from neuroharness.resolve.inputs import (
    ClassPolicy,
    CriticOutcome,
    FactState,
    Resolution,
    ResolutionRequest,
    SimpleClassPolicy,
)
from neuroharness.resolve.resolver import resolve
from neuroharness.resolve.safety import (
    SAFETY_ORDER,
    SAFETY_RANK,
    is_at_least_as_safe,
    safer_of,
    safest_of,
)

__all__ = [
    "resolve",
    "ResolutionRequest",
    "Resolution",
    "CriticOutcome",
    "FactState",
    "ClassPolicy",
    "SimpleClassPolicy",
    "SAFETY_ORDER",
    "SAFETY_RANK",
    "safer_of",
    "safest_of",
    "is_at_least_as_safe",
]
