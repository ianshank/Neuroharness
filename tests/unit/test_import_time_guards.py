"""The guards that only fire at process start have been observed to fire.

Six checks across the package refuse at import rather than at call time, for the
reason they state: an unranked verdict, an unnamed abstention or a build that
cannot read its own records should be discovered at process start, not inside the
decision that needed it. This module covers the four that nothing else reached --
the two reason tables in ``resolve.inputs``, the verdict rank in
``resolve.safety``, and the schema-version tables in ``version``.

That design is right and it has a cost. A guard whose failure arm has never
executed is indistinguishable from a guard that cannot fail, and
``CONTRIBUTING.md`` names this as the failure mode this project is most exposed
to: *"a checker that passes by finding nothing is the failure mode this project
is most exposed to. If a test walks a directory or a set, assert the walk found
something."* Each of these walks a set. Until this module existed, nothing
asserted any of them had ever found anything -- and ``version``'s was not a guard
at all, only a comment claiming the invariant held.

They were also the only uncovered branches in ``resolve/`` - 97% for the package
against a specification that asks for 100% on the resolver (``06-delivery-and-
governance.md`` section 3, stage 2). Covering them by exercising the failure they
exist to catch is the version of that gate worth having; a ``# pragma: no cover``
would have moved the number without learning anything.

Each test constructs the condition the guard exists to catch and asserts the
refusal names it. The tables are patched rather than the enums where the guard
reads a table, because that is the narrower lie: it leaves the enum alone and
asks the guard the question it was written to answer.
"""

from __future__ import annotations

import importlib

import pytest

from neuroharness.errors import ConfigurationError
from neuroharness.models.common import FactStatus, VerifierResult
from neuroharness.resolve import inputs as inputs_module

# --- resolve/inputs.py: the two reason tables are total ----------------------


def test_an_indeterminate_result_with_no_reason_code_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``INV-09``: a critic may not abstain without naming why.

    ``CriticOutcome.indeterminate_reason_code`` subscripts
    ``_CRITIC_REASON_BY_RESULT`` rather than calling ``.get``, which is only
    safe while the table covers every indeterminate result. Empty the table and
    the guard must say so, naming the results it can no longer explain.
    """
    monkeypatch.setattr(inputs_module, "_CRITIC_REASON_BY_RESULT", {})

    with pytest.raises(ConfigurationError) as refusal:
        inputs_module._assert_reason_tables_are_total()

    message = str(refusal.value)
    assert "_CRITIC_REASON_BY_RESULT" in message
    indeterminate = sorted(r.name for r in VerifierResult if r.is_indeterminate)
    assert indeterminate, "no VerifierResult is indeterminate; this guard guards nothing"
    for name in indeterminate:
        assert name in message, f"{name} is indeterminate and the refusal does not name it"


def test_a_blocking_fact_status_with_no_reason_code_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``INV-09``, the fact half: a required fact may not block silently.

    ``FactState.blocking_reason_code`` is total over ``blocks`` only while every
    non-usable status has an entry.
    """
    monkeypatch.setattr(inputs_module, "_FACT_REASON_BY_STATUS", {})

    with pytest.raises(ConfigurationError) as refusal:
        inputs_module._assert_reason_tables_are_total()

    message = str(refusal.value)
    assert "_FACT_REASON_BY_STATUS" in message
    blocking = sorted(s.name for s in FactStatus if not s.is_usable)
    assert blocking, "no FactStatus blocks; this guard guards nothing"
    for name in blocking:
        assert name in message, f"{name} blocks and the refusal does not name it"


def test_the_tables_are_total_on_the_tree_as_merged() -> None:
    """The control. Without it the two tests above pass against a guard that
    refuses everything, which would be a different defect with the same colour.
    """
    inputs_module._assert_reason_tables_are_total()


# --- resolve/safety.py: every verdict is ranked ------------------------------


def test_an_unranked_verdict_refuses_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ADR-0014``: the safety order must rank every ``Verdict`` exactly once.

    This guard is written inline at module scope rather than in a function, so
    the only way to ask it the question is to re-execute the module against a
    ``Verdict`` that carries a member nobody placed in the order. Reloading is
    the mechanism, not the subject: what is asserted is that the guard fires and
    names both sides of the disagreement, so an operator reading the traceback
    learns which member is unranked without opening the source.
    """
    import enum

    from neuroharness.models import common

    extended = enum.Enum(  # type: ignore[misc]
        "Verdict",
        [(member.name, member.value) for member in common.Verdict] + [("ESCALATE", "ESCALATE")],
        type=str,
    )
    monkeypatch.setattr(common, "Verdict", extended)

    from neuroharness.resolve import safety as safety_module

    # `finally`, not a trailing pair of statements: if the refusal does not
    # arrive, or a message assertion fails, execution never reaches the restore
    # and the module is left holding whatever the failed reload wrote. A
    # half-initialised safety order is the one piece of state the rest of this
    # suite cannot tolerate, so a failure here must not become a failure
    # everywhere else as well -- the second symptom would bury the first.
    try:
        with pytest.raises(ConfigurationError) as refusal:
            importlib.reload(safety_module)

        message = str(refusal.value)
        assert "rank every Verdict member exactly once" in message
        assert "ESCALATE" in message, "the refusal does not name the member that is unranked"
    finally:
        monkeypatch.undo()
        importlib.reload(safety_module)


def test_the_order_ranks_every_verdict_on_the_tree_as_merged() -> None:
    """The control for the reload test, and a guard against it leaving damage."""
    from neuroharness.models.common import Verdict
    from neuroharness.resolve.safety import SAFETY_ORDER, SAFETY_RANK

    assert set(SAFETY_RANK) == set(Verdict)
    assert len(SAFETY_ORDER) == len(Verdict)


# --- version.py: this build can read back what it writes ---------------------


def test_writing_a_version_it_cannot_read_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``FR-71``: evidence this build wrote must replay through this build.

    The sixth guard, and the one that was a comment until round five. A written
    version outside the readable set means records are produced and then refused
    on the way back in, which Constitution Art. III makes the one failure the
    evidence layer may not have.
    """
    from neuroharness import version as version_module

    monkeypatch.setattr(
        version_module,
        "_WRITTEN",
        {**version_module._WRITTEN, version_module.SchemaKind.RECORD: "9.9"},
    )

    with pytest.raises(ValueError) as refusal:
        version_module._assert_written_versions_are_readable()

    message = str(refusal.value)
    assert "cannot read back" in message
    assert "9.9" in message, "the refusal does not name the version that would not replay"
    assert version_module.SchemaKind.RECORD in message


def test_a_kind_in_one_table_and_not_the_other_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two tables describe the same kinds, in both directions."""
    from neuroharness import version as version_module

    monkeypatch.setattr(
        version_module, "_WRITTEN", {**version_module._WRITTEN, "action-class-bundle": "1.0"}
    )

    with pytest.raises(ValueError) as refusal:
        version_module._assert_written_versions_are_readable()

    assert "different kinds" in str(refusal.value)
    assert "action-class-bundle" in str(refusal.value)


def test_the_version_tables_agree_on_the_tree_as_merged() -> None:
    """The control, and the assertion the import-time call already makes."""
    from neuroharness import version as version_module

    version_module._assert_written_versions_are_readable()
    for kind, written in version_module._WRITTEN.items():
        assert written in version_module._READABLE[kind]


# --- resolve/inputs.py: the accessor that returns no reason ------------------


def test_a_usable_fact_contributes_no_reason_code() -> None:
    """``FactState.reason_code`` is ``None`` for a fact that is not a problem.

    The distinction matters because ``blocking_reason_code`` is total and this
    one is not: a fresh fact has nothing to say, and saying nothing is different
    from having no name for what it would have said.
    """
    fresh = inputs_module.FactState(name="ci_result", status=FactStatus.FRESH)

    assert fresh.reason_code is None
    assert not fresh.blocks

    for status in FactStatus:
        state = inputs_module.FactState(name="ci_result", status=status)
        if status.is_usable:
            assert state.reason_code is None, f"{status.name} is usable and named a reason"
        else:
            assert state.reason_code is not None, f"{status.name} blocks and named no reason"
