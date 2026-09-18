"""Fail-closed paths that existed, were correct, and had never been executed.

Coverage found these; the increment-1 review listed them and deferred them. Each
is a refusal on the loosening or the recording side, which is the category the
``T-`` series review named as "logic verified thoroughly, data verified poorly".
A refusal nobody has run is a refusal nobody has seen work.

Three of them, and they are not arbitrary:

* ``pipeline/decision.py``'s ``ValidationError`` handler on the **token_issued**
  record. Its twin on the *evaluation* record is well covered. ``C-01`` was
  precisely "the pipeline handed an unvalidated payload to the hash chain"; the
  fix was applied to both records and executed on one.
* ``models/record.py``'s ``FR-48`` override window floor. ``demote_mode`` is the
  one *loosening* override, the one requiring two principals, and the check
  that it cannot be backdated into an already-expired window had no test - while
  the ceiling beside it did.
* ``tokens/nonce.py``'s ``reason_for``. Diagnostic only, which is why it was
  skipped, and it is the text an operator reads during a key-compromise
  incident (``NFR-20``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import pytest

from neuroharness.models.common import OverrideKind
from neuroharness.models.record import Override
from neuroharness.pipeline.decision import RecordNotConstructibleError
from neuroharness.tokens.nonce import InMemoryRevocationList

ANCHOR: Final[datetime] = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
TOKEN_ID: Final[str] = str(UUID(int=0x7043))
KEY_ID: Final[str] = "signing-key-2026-09"
OTHER_KEY_ID: Final[str] = "signing-key-2026-08"


# --- FR-48: the loosening override cannot be backdated ------------------------


def _demote_mode_fields(**overrides: Any) -> dict[str, Any]:
    """A valid ``demote_mode`` override, so each test varies exactly one thing.

    Built as a dict rather than a model so the invalid cases can be constructed
    at all: the model is what is under test.
    """
    fields: dict[str, Any] = {
        "override_id": str(UUID(int=0x0FF)),
        "kind": OverrideKind.DEMOTE_MODE.value,
        "target": "deploy.service/rollout",
        "authorized_by": ("user:sre.oncall", "user:sre.secondary"),
        "reason_code": "HARNESS_UNHEALTHY",
        "ticket_ref": "INC-4471",
        "effective_at": ANCHOR.isoformat(),
        "expires_at": (ANCHOR + timedelta(hours=1)).isoformat(),
    }
    fields.update(overrides)
    return fields


def test_a_valid_demote_mode_override_is_accepted() -> None:
    """The control. Without it, a check that rejected everything would pass."""
    override = Override.model_validate(_demote_mode_fields())
    assert override.kind is OverrideKind.DEMOTE_MODE


@pytest.mark.parametrize(
    ("window", "why"),
    [
        (timedelta(0), "expires exactly when it takes effect: a zero-length window"),
        (timedelta(seconds=-1), "expires one second before it takes effect"),
        (timedelta(days=-30), "backdated into a window that closed a month ago"),
    ],
    ids=["zero", "one-second-backwards", "a-month-backwards"],
)
def test_a_demote_mode_override_cannot_expire_before_it_starts(
    window: timedelta, why: str
) -> None:
    """``FR-48``: a loosening override must auto-expire, forwards.

    The ceiling beside this check - ``window > MAX_DEMOTE_MODE_WINDOW_SECONDS``
    - was tested from the start. The floor was not, and the floor is what stops
    an override whose window has already closed being recorded as though it were
    live. An already-expired demotion is not a harmless no-op: it is a signed,
    two-principal record asserting that enforcement was lowered over a period
    nobody can now observe.
    """
    fields = _demote_mode_fields(expires_at=(ANCHOR + window).isoformat())
    with pytest.raises(ValueError, match="expires_at must be after effective_at"):
        Override.model_validate(fields)


def test_the_window_floor_and_ceiling_refuse_for_different_reasons() -> None:
    """Two bounds, two messages: an operator must be able to tell them apart.

    If both said the same thing, the runbook could not distinguish "you
    backdated this" from "you asked for too long a demotion", and those have
    opposite remedies.
    """
    backwards = _demote_mode_fields(expires_at=(ANCHOR - timedelta(hours=1)).isoformat())
    too_long = _demote_mode_fields(expires_at=(ANCHOR + timedelta(days=365)).isoformat())

    with pytest.raises(ValueError) as backwards_error:
        Override.model_validate(backwards)
    with pytest.raises(ValueError) as long_error:
        Override.model_validate(too_long)

    assert "after effective_at" in str(backwards_error.value)
    assert "may not exceed" in str(long_error.value)


# --- NFR-20: the reason an operator reads during a key compromise -------------


def test_a_token_scoped_revocation_reports_its_reason() -> None:
    """The ordinary case, and the one the gate itself already covered."""
    revocations = InMemoryRevocationList()
    revocations.revoke_token(TOKEN_ID, reason="operator revoked after a bad deploy")

    assert revocations.reason_for(token_id=TOKEN_ID, key_id=KEY_ID) == (
        "operator revoked after a bad deploy"
    )


def test_a_key_scoped_revocation_reports_its_reason() -> None:
    """The incident case. This arm had no test.

    Revoking a *key* invalidates every token it signed, which during a
    compromise is thousands of them. ``is_revoked`` already said yes; nothing
    had ever asked *why*, and "why" is the line an operator reads when deciding
    whether the blast radius is one bad deploy or a stolen key.
    """
    revocations = InMemoryRevocationList()
    revocations.revoke_key(KEY_ID, reason="key material disclosed in INC-4471")

    assert revocations.reason_for(token_id=TOKEN_ID, key_id=KEY_ID) == (
        "key material disclosed in INC-4471"
    )


def test_an_unrevoked_token_has_no_reason() -> None:
    """``None``, not an empty string and not a placeholder.

    A diagnostic that invented text for the healthy case would put a revocation
    reason in front of an operator looking at a token that was never revoked.
    """
    revocations = InMemoryRevocationList()
    revocations.revoke_key(OTHER_KEY_ID, reason="unrelated rotation")

    assert revocations.reason_for(token_id=TOKEN_ID, key_id=KEY_ID) is None


def test_a_token_reason_wins_over_the_key_it_was_signed_with() -> None:
    """Both can be revoked at once, and the narrower reason is the useful one.

    Order is asserted because it is a choice: the token-specific reason names
    what this decision did, the key-wide one names an incident. An operator
    triaging one refusal wants the former.
    """
    revocations = InMemoryRevocationList()
    revocations.revoke_key(KEY_ID, reason="key material disclosed in INC-4471")
    revocations.revoke_token(TOKEN_ID, reason="this deploy was rolled back")

    assert revocations.reason_for(token_id=TOKEN_ID, key_id=KEY_ID) == (
        "this deploy was rolled back"
    )


# --- The reason-code coercion's third arm -------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, 42, 1.5, True, ["RULE_FAILED:FR-02"], {"name": "RULE_FAILED"}, b"RULE_FAILED:FR-02"],
    ids=["none", "int", "float", "bool", "list", "dict", "bytes"],
)
def test_a_reason_code_that_is_neither_a_code_nor_its_rendering_is_refused(
    value: object,
) -> None:
    """``SEC-07``'s structural half, on the input the catalogue does not describe.

    ``bytes`` is the interesting one: it is the shape a reason code arrives in
    off a socket, and ``isinstance(value, str)`` is ``False`` for it, so a
    coercion that fell through to ``str(value)`` would record ``b'RULE_FAILED:
    FR-02'`` - a string no catalogue member matches, discovered at the record
    boundary rather than here.
    """
    from neuroharness.models.record import _coerce_reason_code

    with pytest.raises(ValueError, match="must be a ReasonCode or its rendered form"):
        _coerce_reason_code(value)


# --- C-01, on the second record ----------------------------------------------


def test_an_invalid_token_issued_payload_raises_rather_than_reaching_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler that was written for ``C-01`` and never executed.

    ``C-01`` was "the pipeline handed an unvalidated payload to the hash chain".
    The fix validates both records before they are written. The evaluation
    record's handler has two tests; this one had none, so the second half of a
    fix made in response to a real defect had never run.

    Driven through ``DecisionTokenRecord.model_validate`` directly rather than
    through a doctored signer: the handler's contract is "a payload the schema
    rejects becomes ``RecordNotConstructibleError``, not a chain entry", and
    ``key_id`` is the field a real signer could plausibly get wrong -
    ``KeyId`` bounds it at 64 characters and a KMS key ARN is longer than that.
    """
    from neuroharness.models.record import DecisionTokenRecord

    payload = {
        "token_id": str(UUID(int=0x7043)),
        "decision_id": str(UUID(int=0xDEC)),
        "envelope_digest": f"sha256:{'a' * 64}",
        "proposal_digest": f"sha256:{'b' * 64}",
        "policy_bundle_digest": f"sha256:{'c' * 64}",
        "record_hash": f"sha256:{'d' * 64}",
        "tenant_id": "acme",
        "mode": "enforce",
        "verdict": "ALLOW",
        "issued_at": ANCHOR.isoformat(),
        "expires_at": (ANCHOR + timedelta(minutes=5)).isoformat(),
        # 64 is the bound; a KMS ARN is routinely longer.
        "key_id": "arn:aws:kms:eu-west-1:000000000000:key/" + "0" * 40,
        "key_alg": "ecdsa-p256",
        "shadow": False,
    }
    assert len(payload["key_id"]) > 64, "the fixture no longer exercises the bound"

    with pytest.raises(Exception) as error:
        DecisionTokenRecord.model_validate(payload)
    assert "key_id" in str(error.value)

    # And the same payload minus the over-long key is accepted, so the test is
    # about `key_id` and not about some other field drifting.
    payload["key_id"] = KEY_ID
    assert DecisionTokenRecord.model_validate(payload).key_id == KEY_ID


def test_the_pipelines_token_record_handler_refuses_rather_than_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the handler around it turns that into the typed, fail-closed refusal.

    Patches the model the pipeline validates against, because the realistic
    driver - a signer emitting an over-long ``key_id`` - cannot be built without
    a signer that is itself invalid. What matters is the handler's behaviour on
    a ``ValidationError``, not which field produced it.
    """
    from pydantic import ValidationError

    from neuroharness.pipeline import decision

    class Rejecting:
        @staticmethod
        def model_validate(payload: Any) -> Any:
            raise ValidationError.from_exception_data(
                "DecisionTokenRecord",
                [
                    {
                        "type": "string_too_long",
                        "loc": ("key_id",),
                        "input": payload.get("key_id"),
                        "ctx": {"max_length": 64},
                    }
                ],
            )

    monkeypatch.setattr(decision, "DecisionTokenRecord", Rejecting)

    pipeline = decision.DecisionPipeline.__new__(decision.DecisionPipeline)
    with pytest.raises(RecordNotConstructibleError) as error:
        # reaching past the API: the handler is the unit under test
        decision.DecisionPipeline._write_token_issued(
            pipeline, _signed_token_stub(), _context_stub()
        )
    assert "token_issued" in str(error.value)


def _signed_token_stub() -> Any:
    """The attributes ``_write_token_issued`` reads, and nothing else."""

    class _Token:
        token_id = str(UUID(int=0x7043))
        decision_id = str(UUID(int=0xDEC))
        envelope_digest = f"sha256:{'a' * 64}"
        proposal_digest = f"sha256:{'b' * 64}"
        policy_bundle_digest = f"sha256:{'c' * 64}"
        record_hash = f"sha256:{'d' * 64}"
        tenant_id = "acme"
        issued_at = ANCHOR
        expires_at = ANCHOR + timedelta(minutes=5)
        key_id = "k"
        shadow = False

        class mode:  # a stand-in for the enum member, named as the attribute is
            value = "enforce"

        class verdict:
            value = "ALLOW"

        class key_alg:
            value = "ecdsa-p256"

    class _Signed:
        token = _Token()

    return _Signed()


def _context_stub() -> Any:
    class _Context:
        trace_id = "0" * 32
        action_class = "deployment.apply"

    return _Context()
