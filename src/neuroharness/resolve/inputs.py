"""The resolver's input and output contract (specification sections 5.1-5.6).

The resolver is the one module the whole harness exists to run, so it is also
the module that must be testable without the harness. Everything it needs
arrives through the frozen dataclasses below, and it imports nothing from the
registry, the gateway, the PDP client or the critic broker.

That is dependency inversion, and here it is load-bearing rather than tidy:

* The registry entry satisfies :class:`ClassPolicy` *structurally*. The registry
  can grow fields, change its loader or move to a different schema version
  without the decision procedure being recompiled against it.
* Anything that can reach the resolver is data. A critic cannot hand the
  resolver a callable, an object with behaviour, or a string of prose; it hands
  it a :class:`CriticOutcome` whose only expressive field is a typed
  :class:`~neuroharness.reason.ReasonCode` (``SEC-07``, threat T-11).
* The truth table can enumerate inputs directly. A resolver that could only be
  driven through a gateway would be tested through a gateway, and ``FR-05``
  requires a table-driven test over all input combinations.

Nothing here reads a clock, an environment variable or a file. The same
``ResolutionRequest`` replayed in a year must produce the same
:class:`Resolution` (``INV-09``, ``FR-71``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from neuroharness.defaults import DEFAULT_REPAIR_BUDGET
from neuroharness.errors import ConfigurationError
from neuroharness.models.common import FactStatus, Mode, Verdict, VerifierResult
from neuroharness.reason import ReasonCode, ReasonName

__all__ = [
    "CriticOutcome",
    "FactState",
    "ClassPolicy",
    "SimpleClassPolicy",
    "ResolutionRequest",
    "Resolution",
]


# --- Result-to-reason mappings (sections 5.1 and 5.6) ------------------------

#: Default reason name for a critic outcome that did not supply its own.
#:
#: A critic that knows better says so in :attr:`CriticOutcome.reason` - a
#: monitor emits ``MONITOR_VIOLATION:<property_id>``, an effect verifier emits
#: ``EFFECT_MISMATCH:<resource_key>``, a PDP rule emits ``RULE_FAILED:<rule_id>``
#: naming the rule rather than the engine. This table is the fallback, and it is
#: total over every result that can influence a verdict, so an outcome can never
#: reach a record without a machine-readable reason.
_CRITIC_REASON_BY_RESULT: Final[Mapping[VerifierResult, ReasonName]] = MappingProxyType(
    {
        VerifierResult.FAIL: ReasonName.RULE_FAILED,
        VerifierResult.UNKNOWN: ReasonName.SOLVER_UNKNOWN,
        VerifierResult.TIMEOUT: ReasonName.SOLVER_TIMEOUT,
        VerifierResult.ERROR: ReasonName.CRITIC_ERROR,
    }
)

#: Reason name for each unusable fact status (section 5.6).
_FACT_REASON_BY_STATUS: Final[Mapping[FactStatus, ReasonName]] = MappingProxyType(
    {
        FactStatus.MISSING: ReasonName.FACT_MISSING,
        FactStatus.STALE: ReasonName.FACT_STALE,
        FactStatus.PROVIDER_ERROR: ReasonName.FACT_PROVIDER_ERROR,
    }
)


def _check_reason_subject(value: str, *, field_name: str, example: ReasonName) -> None:
    """Reject an identifier that could not be rendered into a reason code.

    Validated on construction rather than at render time. A critic id that only
    fails when the critic fails is a latent crash on the exact path that must
    stay alive, and an unrecorded decision was not made (Constitution III).
    """
    try:
        ReasonCode(example, value)
    except ValueError as exc:  # pragma: no cover - message assembly only
        raise ValueError(f"{field_name} {value!r} is not a usable reason subject: {exc}") from exc


# --- Critic outcomes ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CriticOutcome:
    """One critic's or one policy rule's contribution to a decision.

    ``hard`` is the critic's declared blocking status in the registry;
    ``effective_mode`` is the critic's own declared rollout mode. The class mode
    does not narrow it: a class in shadow still resolves with its enforcing
    critics blocking, and the broker - not the resolver - decides whether that
    verdict stops anything (section 5.3 step 0, ``ADR-0016``).

    Both defaults are the strict ones: an outcome that forgets to say what it is
    counts as a blocking critic in enforce, because the failure mode of guessing
    "soft" is an unguarded execution.
    """

    critic_id: str
    result: VerifierResult
    hard: bool = True
    effective_mode: Mode = Mode.ENFORCE
    repairable: bool | None = None
    reason: ReasonCode | None = None

    def __post_init__(self) -> None:
        _check_reason_subject(
            self.critic_id, field_name="critic_id", example=ReasonName.CRITIC_ERROR
        )

    @property
    def counts_as_hard(self) -> bool:
        """True when this outcome may block (section 5.3 step 0).

        A hard critic whose own declared mode is ``shadow`` or ``advisory`` is
        treated as *soft* for resolution and its would-be effect is recorded
        instead (``ADR-0014``, ``ADR-0016``). This is the only place rollout
        mode touches the verdict: a demoted critic still runs, still records and
        still appears in the shadow verdict, so shadow data stays representative
        of what enforcement would have done.

        ``halted`` never reaches here: a halted *class* denies at step 0 before
        any critic is consulted.
        """
        return self.hard and self.effective_mode is Mode.ENFORCE

    @property
    def is_failure(self) -> bool:
        return self.result is VerifierResult.FAIL

    @property
    def is_repairable_failure(self) -> bool:
        """A failure the proposer may be asked to fix.

        ``repairable`` is ``None`` when the critic did not say. That is treated
        as *not* repairable: repair is the more permissive branch, so an unstated
        flag must not buy an extra attempt.
        """
        return self.result is VerifierResult.FAIL and self.repairable is True

    @property
    def is_non_repairable_failure(self) -> bool:
        return self.result is VerifierResult.FAIL and self.repairable is not True

    @property
    def is_indeterminate(self) -> bool:
        """``UNKNOWN``, ``TIMEOUT`` or ``ERROR``: the critic could not decide."""
        return self.result.is_indeterminate

    @property
    def reason_code(self) -> ReasonCode | None:
        """The typed reason this outcome contributes, or ``None`` if it passed."""
        if self.reason is not None:
            return self.reason
        name = _CRITIC_REASON_BY_RESULT.get(self.result)
        return None if name is None else ReasonCode(name, self.critic_id)

    @property
    def indeterminate_reason_code(self) -> ReasonCode:
        """The reason an indeterminate outcome abstains. Never ``None``.

        Total over :attr:`is_indeterminate` by the import-time check at the foot
        of this module, so it subscripts the table rather than calling ``.get``.
        A :class:`KeyError` here would mean that check did not run.

        This exists because the resolver's alternative was a ``None`` guard that
        could never be taken, and whose untaken arm *silently dropped the
        abstention reason from the record* - the quietest possible failure in a
        system whose constitution says a decision that is not recorded was not
        made. The guard looked like defence and was a hole waiting for a sixth
        ``VerifierResult``. See ``resolve/resolver.py``.
        """
        if self.reason is not None:
            return self.reason
        return ReasonCode(_CRITIC_REASON_BY_RESULT[self.result], self.critic_id)


# --- Facts -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactState:
    """The freshness of one fact as policy saw it (``FR-11``).

    Only the *status* travels. A stale or missing fact arrives here with no
    value at all, so no resolution path can read one by accident.
    """

    name: str
    status: FactStatus
    required: bool = True
    escalatable: bool = False

    def __post_init__(self) -> None:
        _check_reason_subject(self.name, field_name="name", example=ReasonName.FACT_MISSING)

    @property
    def blocks(self) -> bool:
        """True when this fact forces an abstention (section 5.3 step 3).

        An optional fact that is missing is not an inability to evaluate: policy
        declared it may be absent, so rules that need it simply do not fire.
        """
        return self.required and not self.status.is_usable

    @property
    def reason_code(self) -> ReasonCode | None:
        name = _FACT_REASON_BY_STATUS.get(self.status)
        return None if name is None else ReasonCode(name, self.name)

    @property
    def blocking_reason_code(self) -> ReasonCode:
        """The reason a blocking fact abstains. Never ``None``.

        Total over :attr:`blocks` by the import-time check below, for the same
        reason as :attr:`CriticOutcome.indeterminate_reason_code`.
        """
        return ReasonCode(_FACT_REASON_BY_STATUS[self.status], self.name)


# --- Action-class policy -----------------------------------------------------


@runtime_checkable
class ClassPolicy(Protocol):
    """The registry's view of an action class, reduced to what a verdict needs.

    A :class:`~typing.Protocol` rather than a class: the signed action-class
    registry entry satisfies this structurally, so the registry depends on the
    resolver's contract and never the other way round. Anything with these four
    attributes - a registry entry, a test double, a replay record - can be
    resolved against.
    """

    @property
    def mode(self) -> Mode:
        """Rollout mode of the class (``FR-80``)."""

    @property
    def approvable(self) -> bool:
        """Whether a human may ever authorise this class (``FR-45``)."""

    @property
    def escalate_on(self) -> frozenset[ReasonName]:
        """Abstention reasons this class may escalate to approval (section 5.5)."""

    @property
    def repair_budget(self) -> int:
        """Repair iterations allowed per action (section 5.4)."""


@dataclass(frozen=True, slots=True)
class SimpleClassPolicy:
    """A concrete :class:`ClassPolicy` for tests and registry-free callers.

    Deliberately *not* validating, beyond what the field types give. The
    registry loader is the component that rejects an inconsistent entry -
    ``escalate_on`` on a non-approvable class, a reason outside the escalatable
    set, a budget above :data:`~neuroharness.defaults.MAX_REPAIR_BUDGET`. The
    resolver re-checks every one of those at resolution time anyway, and that
    defence has to be reachable by a test, which means it must be possible to
    build a policy that lies.

    The defaults are the strict ones: enforce, not approvable, nothing
    escalatable.
    """

    mode: Mode = Mode.ENFORCE
    approvable: bool = False
    escalate_on: frozenset[ReasonName] = frozenset()
    repair_budget: int = DEFAULT_REPAIR_BUDGET


# --- Request and result ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolutionRequest:
    """Everything section 5.3 consults, and nothing else.

    **Approval is signalled explicitly.** ``approval_required`` is set by the
    caller when the PDP returned a ``requires_approval`` rule outcome (section
    5.1), and ``approval_rule_id`` names the rule that asked. The alternative -
    inferring it from a ``PASS`` critic outcome that happens to carry an
    ``APPROVAL_REQUIRED`` reason - was rejected: it overloads "this check
    passed" with "a human must sign", and a channel that means two things is a
    channel a compromised critic can equivocate on. An escalated abstention
    (section 5.5) does *not* use this field; the resolver derives that from the
    abstention reasons themselves.

    ``infrastructure_reasons`` carries what the harness itself could not do:
    engine unreachable, bundle digest mismatch, clock down, health check failed.
    Only members of
    :data:`~neuroharness.reason.INFRASTRUCTURE_REASONS` are accepted, so a
    caller cannot smuggle an escalatable reason into the terminal path or an
    infrastructure reason into the escalatable one.
    """

    policy: ClassPolicy
    critic_outcomes: tuple[CriticOutcome, ...] = ()
    fact_states: tuple[FactState, ...] = ()
    infrastructure_reasons: tuple[ReasonCode, ...] = ()
    repair_iteration: int = 0
    approval_required: bool = False
    approval_rule_id: str | None = None
    approval_satisfied: bool = False
    rate_limited: bool = False

    def __post_init__(self) -> None:
        if self.repair_iteration < 0:
            raise ValueError(f"repair_iteration must not be negative: {self.repair_iteration}")
        for code in self.infrastructure_reasons:
            if not code.is_infrastructure:
                raise ValueError(
                    f"{code.render()} is not an infrastructure reason; "
                    "section 5.5 fixes that set and the resolver treats it as terminal"
                )
        if self.approval_required and self.approval_rule_id is None:
            raise ValueError(
                "approval_required needs approval_rule_id: APPROVAL_REQUIRED and "
                "APPROVAL_NOT_PERMITTED are parameterised by the rule that asked"
            )
        if self.approval_rule_id is not None:
            _check_reason_subject(
                self.approval_rule_id,
                field_name="approval_rule_id",
                example=ReasonName.APPROVAL_REQUIRED,
            )


@dataclass(frozen=True, slots=True)
class Resolution:
    """The decision, its typed reasons, and how it was reached.

    ``explain`` is an ordered, human-readable trace of which rule fired at each
    step. It exists for ``NFR-20``: a verdict must be explainable from its
    record without re-running the model or the critics. It is *not* an input to
    anything - no code branches on it - and it contains only identifiers,
    enum values and counts, never model-generated text (``SEC-07``).

    ``shadow_verdict`` is the verdict that would have applied had every hard
    critic been honoured as hard: the ``would_be_verdict`` of ``ADR-0014`` step
    0. It is populated whenever the two can differ - a class outside enforce, or
    any hard critic demoted by its own mode - and is ``None`` when they cannot,
    so a non-``None`` value always means "this differs from the enforced path in
    a way worth measuring" (``ADR-0016``).
    """

    verdict: Verdict
    reason_codes: tuple[ReasonCode, ...] = ()
    shadow_verdict: Verdict | None = None
    explain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict is not Verdict.ALLOW and not self.reason_codes:
            raise ValueError(
                f"verdict {self.verdict.value} must carry at least one reason code "
                "(section 5.6); an unexplained refusal cannot be recorded or appealed"
            )

    @property
    def primary_reason(self) -> ReasonCode | None:
        """The reason that decided this verdict, or ``None`` for a bare ``ALLOW``.

        Reason codes are ordered decisive-first by the resolver, so the head of
        the tuple is the rule that fired. Contributing reasons follow it in
        input order.
        """
        return self.reason_codes[0] if self.reason_codes else None

    @property
    def permits_execution(self) -> bool:
        return self.verdict.permits_execution


# --- The nesting that held the resolver up, asserted ---------------------------


def _assert_reason_tables_are_total() -> None:
    """Every blocking input has a reason code, checked at import.

    ``resolver.py`` used to narrow twice - ``if code is not None`` around the
    critic abstention and around the fact abstention - and neither ``None`` arm
    was ever taken. Coverage recorded them as unreachable partial branches and
    the increment-1 review filed them under "provably unreachable, exclude from
    the gate". They were not provable, and the proof they lacked is the point:

    * :attr:`CriticOutcome.reason_code` returns ``None`` only when
      ``_CRITIC_REASON_BY_RESULT`` misses. That table covers ``FAIL, UNKNOWN,
      TIMEOUT, ERROR``; :attr:`VerifierResult.is_indeterminate` is ``UNKNOWN,
      TIMEOUT, ERROR`` - a *subset*, by coincidence.
    * :attr:`FactState.reason_code` returns ``None`` only when
      ``_FACT_REASON_BY_STATUS`` misses. That table covers ``MISSING, STALE,
      PROVIDER_ERROR``; the non-usable statuses are exactly those - again by
      coincidence.

    Two independently maintained tables happened to nest inside two enums, and
    nothing said so. Add one ``VerifierResult`` whose ``is_indeterminate`` is
    true, or one non-``FRESH`` ``FactStatus``, and the untaken arm goes live -
    and its behaviour is to drop the abstention reason silently, so the record
    would say the harness abstained and not why.

    Asserting it here converts two untestable narrowings into one testable
    invariant, and lets the accessors above be total. Import-time, like
    :mod:`neuroharness.resolve.safety`'s verdict-rank guard, because the place
    to discover an unranked verdict or an unnamed abstention is process start,
    not the decision that needed it.
    """
    missing_results = sorted(
        result.name for result in VerifierResult
        if result.is_indeterminate and result not in _CRITIC_REASON_BY_RESULT
    )
    if missing_results:
        raise ConfigurationError(
            f"VerifierResult {missing_results} are indeterminate and have no entry in "
            "_CRITIC_REASON_BY_RESULT, so a critic could abstain without a reason code "
            "(section 5.6, INV-09)"
        )
    missing_statuses = sorted(
        status.name for status in FactStatus
        if not status.is_usable and status not in _FACT_REASON_BY_STATUS
    )
    if missing_statuses:
        raise ConfigurationError(
            f"FactStatus {missing_statuses} are not usable and have no entry in "
            "_FACT_REASON_BY_STATUS, so a required fact could block without a reason "
            "code (section 5.6, INV-09)"
        )


_assert_reason_tables_are_total()
