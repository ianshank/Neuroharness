"""Constitutional constants must not be reachable from configuration.

Round-two review of the increment plan caught a real regression in the phrase
"every default value is overridable". Some values are policy knobs and belong in
the signed registry. Others are constitutional: the safety order (Article VIII),
the closed reason-code catalogue (``SEC-07``), the fixed set of non-escalating
infrastructure reasons (specification 5.5), and the hard ceiling on the repair
budget. If an operator can weaken any of those with an environment variable,
they have reduced enforcement with no signature, no two-person review and no
record, which is precisely what ``FR-48`` and ``SEC-14`` forbid.

So the rule is split:

* policy knobs  -> signed registry, with ``defaults`` filling optional omissions
* constitutional constants -> module constants, provably not settable here
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from neuroharness import defaults
from neuroharness.config import Settings
from neuroharness.reason import (
    ESCALATABLE_REASONS,
    INFRASTRUCTURE_REASONS,
    ReasonName,
)

#: Settings fields that deliberately expose policy knobs an operator may set.
_PERMITTED_SETTING_FIELDS = {
    "tenant_id",
    "unregistered_class_policy",
    "signing_algorithm",
    "token_ttl_seconds",
    "bundle_grace_seconds",
    "approval_ttl_seconds",
    "lease_timeout_seconds",
    "repair_budget",
    "new_actions_per_hour",
    "record_retention_days",
    "log_level",
    "log_json",
}


def test_settings_expose_no_constitutional_constant() -> None:
    """No settings field may name the safety order, the catalogues or the ceiling."""
    forbidden_substrings = (
        "safety",
        "verdict_order",
        "reason_catalogue",
        "reason_catalog",
        "infrastructure_reason",
        "escalatable",
        "max_repair",
    )
    for field in Settings.model_fields:
        lowered = field.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), (
            f"settings field {field!r} exposes a constitutional constant; those "
            "change only by amending the specification, never by configuration"
        )


def test_settings_surface_is_the_declared_one() -> None:
    """A new settings field is a deliberate act, reviewed here."""
    assert set(Settings.model_fields) == _PERMITTED_SETTING_FIELDS


def test_repair_budget_ceiling_is_not_settable() -> None:
    """Settings may lower the budget, never raise it past the constitutional max.

    The exception type and message are named rather than caught as
    ``Exception``: a bare catch passes on a ``TypeError`` from an unrelated
    signature change, so deleting the ceiling itself would leave this test green
    while an operator could set a repair budget of any size from the
    environment - a loosening with no signature, no two-person review and no
    record (``FR-48``, ``SEC-14``).
    """
    with pytest.raises(ValidationError, match="less than or equal to"):
        Settings(repair_budget=defaults.MAX_REPAIR_BUDGET + 1)


def test_the_repair_budget_ceiling_itself_is_settable_up_to_the_maximum() -> None:
    """The ceiling is a ceiling, not an off-by-one: the maximum is allowed.

    A limit that refused its own documented maximum would push deployments to
    raise ``MAX_REPAIR_BUDGET`` to get the value the specification already
    permits, which is how a constitutional constant gets edited for an
    operational reason.
    """
    assert Settings(repair_budget=defaults.MAX_REPAIR_BUDGET).repair_budget == (
        defaults.MAX_REPAIR_BUDGET
    )


def test_infrastructure_reason_set_is_fixed_and_disjoint_from_escalatable() -> None:
    """Section 5.5: infrastructure abstentions may never escalate to a human."""
    assert INFRASTRUCTURE_REASONS, "the infrastructure reason set must not be empty"
    assert not (INFRASTRUCTURE_REASONS & ESCALATABLE_REASONS), (
        "a reason cannot be both non-escalating infrastructure and escalatable"
    )
    # The exact membership is constitutional; changing it is a specification
    # amendment, so it is pinned here rather than derived.
    assert INFRASTRUCTURE_REASONS == frozenset(
        {
            ReasonName.POLICY_ENGINE_UNAVAILABLE,
            ReasonName.BUNDLE_INTEGRITY_FAILED,
            ReasonName.REGISTRY_INTEGRITY_FAILED,
            ReasonName.EVIDENCE_UNAVAILABLE,
            ReasonName.CLOCK_UNAVAILABLE,
            ReasonName.MONITOR_STATE_LOST,
            ReasonName.HARNESS_UNHEALTHY,
            ReasonName.CRITIC_ERROR,
            ReasonName.SCHEMA_INVALID,
            ReasonName.ACTION_CLASS_UNREGISTERED,
        }
    )


def test_escalatable_reason_set_is_fixed() -> None:
    assert ESCALATABLE_REASONS == frozenset(
        {
            ReasonName.SOLVER_UNKNOWN,
            ReasonName.SOLVER_TIMEOUT,
            ReasonName.FACT_MISSING,
            ReasonName.FACT_STALE,
        }
    )
