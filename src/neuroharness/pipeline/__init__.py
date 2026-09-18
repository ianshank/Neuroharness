"""The component that sequences a decision.

Resolution, evidence and tokens are each independently correct and independently
testable. What binds them is an *order*, and an order that lives only in tests is
not enforced by anything: the test does the sequencing, so no production
component holds it, and the acceptance criterion passes while the requirement is
unmet. That is the gap this package closes.
"""

from neuroharness.pipeline.decision import (
    DecisionContext,
    DecisionPipeline,
    EvaluationOutcome,
)

__all__ = ["DecisionContext", "DecisionPipeline", "EvaluationOutcome"]
