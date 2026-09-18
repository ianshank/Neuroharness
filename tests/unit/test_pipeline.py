"""The pipeline is where ``INV-05`` and ``INV-11`` stop being test properties.

Each subsystem below is already correct on its own. What these tests exercise is
the *order*: that a token cannot exist before its record does, that an
infrastructure failure withholds a token in every rollout mode, and that a
shadow class runs the identical path rather than a cheaper one.

They also exercise the thing the pipeline writes. Every record it appends is
validated here against ``docs/sdd/schemas/decision-record.schema.json`` - the
same authoritative document :mod:`tests.unit.test_schema_conformance` holds the
models to - because a hash chain makes a record permanent, and a record that an
auditor's validator rejects is evidence that cannot be read back
(Constitution Art. IV).

Identifiers come from :class:`~neuroharness.seams.DeterministicUuidGenerator`
rather than :class:`~neuroharness.seams.SequenceIdGenerator`: the schema types
``record_id``, ``decision_id``, ``action_id`` and ``token_id`` as UUIDs, so a
test driven by sequence strings would prove the pipeline correct on a record
shape production rejects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from jsonschema import Draft202012Validator

from neuroharness.errors import (
    EvidenceUnavailableError,
    FailClosedError,
    TokenVerdictMismatchError,
)
from neuroharness.evidence.chain import FIELD_KIND, RecordKind, plain_value
from neuroharness.evidence.store import AppendResult, EvidenceWriter, InMemoryEvidenceStore
from neuroharness.evidence.wal import InMemoryWriteAheadLog
from neuroharness.models.common import Digest, EffectClass, Mode, Verdict, VerifierResult
from neuroharness.models.record import DecisionTokenRecord, Evaluation
from neuroharness.pipeline import DecisionContext, DecisionPipeline
from neuroharness.pipeline.decision import (
    PIPELINE_OWNED_EVALUATION_FIELDS,
    RecordNotConstructibleError,
)
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve.inputs import CriticOutcome, ResolutionRequest, SimpleClassPolicy
from neuroharness.seams import DeterministicUuidGenerator
from neuroharness.tokens.nonce import InMemoryNonceStore, InMemoryRevocationList
from neuroharness.tokens.service import TokenService
from neuroharness.tokens.signer import HmacSigner

ENVELOPE_DIGEST = Digest.from_hex("a" * 64)
PROPOSAL_DIGEST = Digest.from_hex("b" * 64)
BUNDLE_DIGEST = Digest.from_hex("c" * 64)
REGISTRY_DIGEST = Digest.from_hex("d" * 64)
OTHER_DIGEST = Digest.from_hex("e" * 64)

TENANT_ID: Final[str] = "t1"
TRACE_ID: Final[str] = "4bf92f3577b34da6a3ce929d0e0e4736"
DECISION_ID: Final[str] = "00000000-0000-0000-0000-0000000000d1"
ACTION_ID: Final[str] = "00000000-0000-0000-0000-0000000000a1"
SESSION_ID: Final[str] = "session-root"
HMAC_SECRET: Final[bytes] = b"unit-test-secret-32-bytes-longxx"

#: Latency figures the gateway would measure. Present and non-empty so the
#: record carries the ``NFR-01`` budget evidence rather than an empty map.
LATENCY_MS: Final[dict[str, float]] = {"resolve": 1.5}
END_TO_END_MS: Final[float] = 4.25


# --- schema conformance ------------------------------------------------------

SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "docs" / "sdd" / "schemas" / "decision-record.schema.json"
)


def _payload_validator(definition: str) -> Draft202012Validator:
    """A validator for one ``$defs`` block of the record schema.

    The pipeline writes a payload, not a whole record: ``seq``,
    ``prev_record_hash`` and ``record_hash`` belong to the store. Validating the
    payload against its own definition keeps the assertion aimed at the thing
    under test, and the ``$defs`` root is carried along so internal ``$ref``s
    still resolve.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{definition}",
        },
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


EVALUATION_VALIDATOR: Final[Draft202012Validator] = _payload_validator("Evaluation")
TOKEN_ISSUED_VALIDATOR: Final[Draft202012Validator] = _payload_validator("DecisionToken")


def assert_conforms(validator: Draft202012Validator, document: Any) -> None:
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    assert not errors, "\n".join(
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    )


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def ids() -> DeterministicUuidGenerator:
    return DeterministicUuidGenerator()


@pytest.fixture
def store(clock, ids) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=ids)


@pytest.fixture
def wal(clock) -> InMemoryWriteAheadLog:
    return InMemoryWriteAheadLog(clock=clock)


@pytest.fixture
def tokens(clock, ids) -> TokenService:
    return TokenService(
        signer=HmacSigner(key_id="k1", secret=HMAC_SECRET),
        nonce_store=InMemoryNonceStore(),
        revocation_list=InMemoryRevocationList(),
        clock=clock,
        id_generator=ids,
    )


@pytest.fixture
def pipeline(store, wal, tokens, clock, ids) -> DecisionPipeline:
    return DecisionPipeline(
        writer=EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal),
        tokens=tokens,
    )


def evaluation_payload(**overrides: Any) -> dict[str, Any]:
    """The gateway's half of an evaluation record (``FR-70``).

    Everything the pipeline cannot know: where the canonical envelope is kept,
    which class this was, the session lineage, the versions of the two signed
    artifacts, and the outcomes and timings the evaluation produced.
    """
    payload: dict[str, Any] = {
        "envelope_ref": f"evidence/envelopes/{ENVELOPE_DIGEST.hex}",
        "action_class": {
            "tool": "deploy.service",
            "intent": "deploy_service",
            "effect_class": EffectClass.WRITE.value,
        },
        "session_id": SESSION_ID,
        "session_root_id": SESSION_ID,
        "policy_bundle": {"version": "2026.09.1", "digest": str(BUNDLE_DIGEST)},
        "registry": {"version": "2026.09.1", "digest": str(REGISTRY_DIGEST)},
        "pdp_outcomes": [],
        "critic_results": [],
        "facts_used": [],
        "claims_recorded": [],
        "latency_ms": dict(LATENCY_MS),
        "end_to_end_ms": END_TO_END_MS,
    }
    payload.update(overrides)
    return payload


def context(mode: Mode = Mode.ENFORCE, **overrides: Any) -> DecisionContext:
    fields: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "trace_id": TRACE_ID,
        "action_id": ACTION_ID,
        "decision_id": DECISION_ID,
        "envelope_digest": ENVELOPE_DIGEST,
        "proposal_digest": PROPOSAL_DIGEST,
        "policy_bundle_digest": BUNDLE_DIGEST,
        "mode": mode,
        "action_class": "deploy.service/deploy_service",
        "session_id": SESSION_ID,
        "evaluation_payload": evaluation_payload(),
    }
    fields.update(overrides)
    return DecisionContext(**fields)


def allowing_request(mode: Mode = Mode.ENFORCE) -> ResolutionRequest:
    return ResolutionRequest(policy=SimpleClassPolicy(mode=mode))


def denying_request(mode: Mode = Mode.ENFORCE) -> ResolutionRequest:
    """A class in ``mode`` whose enforcing critic fails.

    The critic's ``effective_mode`` is its *own* declared mode, which stays
    ``ENFORCE`` whatever the class is doing. That distinction is the whole point
    of ``INV-11``: a shadow class must still compute the denial it exists to
    measure, and only the broker's consultation of that verdict changes.
    """
    return ResolutionRequest(
        policy=SimpleClassPolicy(mode=mode),
        critic_outcomes=(
            CriticOutcome(
                critic_id="pdp.deploy",
                result=VerifierResult.FAIL,
                hard=True,
                effective_mode=Mode.ENFORCE,
                repairable=False,
                reason=ReasonCode(ReasonName.RULE_FAILED, "WF-01"),
            ),
        ),
    )


def infrastructure_request(mode: Mode = Mode.ENFORCE) -> ResolutionRequest:
    return ResolutionRequest(
        policy=SimpleClassPolicy(mode=mode),
        infrastructure_reasons=(ReasonCode(ReasonName.POLICY_ENGINE_UNAVAILABLE),),
    )


def records(store: InMemoryEvidenceStore) -> list[dict[str, Any]]:
    """This tenant's chain as plain JSON data.

    The store hands records out as read-only views - mapping proxies and tuples
    - so that a caller cannot edit what was recorded. ``plain_value`` is the
    same conversion the chain itself uses before hashing, and it is what a JSON
    Schema validator needs in order to see an object rather than a proxy.
    """
    return [plain_value(record) for record in store.read(TENANT_ID)]


# --- the happy path, so the negative tests mean something --------------------


def test_allow_writes_a_record_then_issues_a_token(pipeline, store) -> None:
    outcome = pipeline.evaluate(allowing_request(), context())

    assert outcome.verdict is Verdict.ALLOW
    assert outcome.permits_execution
    assert outcome.record is not None

    kinds = [record[FIELD_KIND] for record in records(store)]
    assert kinds == [RecordKind.EVALUATION.value, RecordKind.TOKEN_ISSUED.value], (
        "the evaluation record must precede the token record in the chain"
    )
    assert outcome.token.token.record_hash == outcome.record.record_hash


def test_the_token_binds_the_digests_the_decision_was_made_on(pipeline) -> None:
    token = pipeline.evaluate(allowing_request(), context()).token.token
    assert token.envelope_digest == ENVELOPE_DIGEST
    assert token.proposal_digest == PROPOSAL_DIGEST
    assert token.policy_bundle_digest == BUNDLE_DIGEST
    assert token.verdict is Verdict.ALLOW
    assert token.mode is Mode.ENFORCE


# --- FR-70: what is written is what the schema admits ------------------------


def test_the_evaluation_record_conforms_to_the_published_schema(pipeline, store) -> None:
    """A record an auditor's validator rejects is not evidence (Art. IV)."""
    pipeline.evaluate(denying_request(), context())

    evaluation = records(store)[0]["evaluation"]
    assert_conforms(EVALUATION_VALIDATOR, evaluation)
    assert Evaluation.model_validate(evaluation).verdict is Verdict.DENY


def test_the_token_issued_record_conforms_to_the_published_schema(pipeline, store) -> None:
    pipeline.evaluate(allowing_request(), context())

    issued = records(store)[1]["token_issued"]
    assert_conforms(TOKEN_ISSUED_VALIDATOR, issued)
    assert DecisionTokenRecord.model_validate(issued).decision_id.hex == DECISION_ID.replace(
        "-", ""
    )


def test_the_token_record_never_carries_the_signature(pipeline, store) -> None:
    """The record proves a token existed; it is not a place to keep credentials."""
    signed = pipeline.evaluate(allowing_request(), context()).token
    issued = records(store)[1]["token_issued"]

    assert "signature" not in issued
    assert signed.signature not in json.dumps(issued)


@pytest.mark.parametrize("missing", sorted(evaluation_payload()))
def test_an_incomplete_evaluation_payload_fails_closed(pipeline, store, missing: str) -> None:
    """Every required field is required: drop one and nothing may execute.

    Parametrised over the payload itself rather than over a hand-written list,
    so a field added to the record schema is covered the day it is added.
    """
    payload = evaluation_payload()
    payload.pop(missing)

    # ``session_id`` is the one field the pipeline can supply from the trace
    # context, so the context must withhold it too for this to be a true
    # omission rather than a backfill.
    outcome = pipeline.evaluate(
        allowing_request(), context(session_id=None, evaluation_payload=payload)
    )

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.reason_codes[0].name is ReasonName.SCHEMA_INVALID
    assert outcome.token is None
    assert outcome.evidence_failed
    assert not records(store), "a record the schema rejects must not enter the chain"


def test_a_malformed_evaluation_payload_does_not_raise_out_of_evaluate(pipeline) -> None:
    """``FR-02``: an inability to evaluate is a verdict, not a stack trace."""
    outcome = pipeline.evaluate(
        allowing_request(),
        context(evaluation_payload=evaluation_payload(end_to_end_ms=-1.0)),
    )
    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.token is None


@pytest.mark.parametrize("field", sorted(PIPELINE_OWNED_EVALUATION_FIELDS))
def test_a_caller_may_not_state_what_was_decided(pipeline, store, field: str) -> None:
    """The record says what the resolver decided, not what the caller preferred.

    A caller that could supply ``verdict`` could record an ALLOW for a decision
    that denied, and the token would then be the only honest artifact in the
    chain.
    """
    outcome = pipeline.evaluate(
        denying_request(),
        context(evaluation_payload=evaluation_payload(**{field: "usurped"})),
    )

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.reason_codes[0].name is ReasonName.SCHEMA_INVALID
    assert not records(store)


def test_the_recorded_bundle_must_be_the_bundle_the_token_binds(pipeline, store) -> None:
    """``FR-84``: one decision, one rule set, and the two artifacts must agree."""
    payload = evaluation_payload(
        policy_bundle={"version": "2026.09.1", "digest": str(OTHER_DIGEST)}
    )

    outcome = pipeline.evaluate(allowing_request(), context(evaluation_payload=payload))

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.token is None
    assert not records(store)


def test_the_recorded_session_must_be_the_traced_session(pipeline) -> None:
    payload = evaluation_payload(session_id="a-different-session")

    outcome = pipeline.evaluate(allowing_request(), context(evaluation_payload=payload))

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.token is None


def test_the_session_is_taken_from_the_context_when_the_payload_omits_it(
    pipeline, store
) -> None:
    payload = evaluation_payload()
    payload.pop("session_id")

    pipeline.evaluate(allowing_request(), context(evaluation_payload=payload))

    assert records(store)[0]["evaluation"]["session_id"] == SESSION_ID


def test_the_record_carries_the_repair_iteration_that_was_resolved(pipeline, store) -> None:
    request = ResolutionRequest(policy=SimpleClassPolicy(), repair_iteration=2)

    pipeline.evaluate(request, context())

    assert records(store)[0]["evaluation"]["repair_iteration"] == 2


def test_the_record_failure_is_a_typed_fail_closed_error() -> None:
    """The reason code is the record; an untyped error could not be one.

    Pinned as a value rather than asserted as a type: an error remapped to
    ``HARNESS_UNHEALTHY`` would still be a ``FailClosedError`` and would still
    withhold the token, but the chain would then blame the harness's health for
    a record the harness simply could not build.
    """
    error = RecordNotConstructibleError("the record does not satisfy the schema")
    assert isinstance(error, FailClosedError)
    assert error.reason_code.name is ReasonName.SCHEMA_INVALID


# --- FR-21/INV-11: one class has one rollout mode ----------------------------


def test_a_context_mode_that_contradicts_the_class_fails_closed(pipeline, store) -> None:
    """A token must not bind a mode the decision was not taken under."""
    outcome = pipeline.evaluate(allowing_request(Mode.SHADOW), context(Mode.ENFORCE))

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.reason_codes[0].name is ReasonName.SCHEMA_INVALID
    assert outcome.token is None
    assert not records(store), "the contradiction is caught before anything is written"


# --- FR-30: the registry owns the token lifetime -----------------------------


def test_the_class_token_ttl_is_honoured(pipeline, tokens) -> None:
    """``token_ttl_seconds`` is a per-class registry knob, not a global default."""
    class_ttl = tokens.ttl_seconds + 17

    token = pipeline.evaluate(
        allowing_request(), context(token_ttl_seconds=class_ttl)
    ).token.token

    assert (token.expires_at - token.issued_at).total_seconds() == class_ttl


def test_without_a_class_ttl_the_service_default_applies(pipeline, tokens) -> None:
    token = pipeline.evaluate(allowing_request(), context()).token.token
    assert (token.expires_at - token.issued_at).total_seconds() == tokens.ttl_seconds


# --- INV-05: no token without a durable record -------------------------------


@pytest.mark.mutation
def test_mut_13_evidence_failure_blocks_token_issuance(pipeline, store, wal) -> None:
    """MUT-13 (partial): the pipeline owns the refusal; real durability is P1-06a."""
    store.set_available(False)

    outcome = pipeline.evaluate(allowing_request(), context())

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.reason_codes[0].name is ReasonName.EVIDENCE_UNAVAILABLE
    assert outcome.token is None, "a token was minted for a decision with no record"
    assert not outcome.permits_execution
    assert outcome.evidence_failed
    assert len(wal.pending()) == 1, "the unrecorded decision must be staged for replay"


def test_the_decision_survives_the_outage_and_replays(pipeline, store, wal) -> None:
    store.set_available(False)
    pipeline.evaluate(allowing_request(), context())
    store.set_available(True)

    report = wal.replay(store)

    assert len(report.appended) == 1
    assert not wal.pending()
    assert [r[FIELD_KIND] for r in records(store)] == [RecordKind.EVALUATION.value]


def test_an_outage_between_the_two_records_withholds_the_token(
    store, wal, tokens, clock, ids
) -> None:
    """``FR-23``: an issuance nobody could record authorises nothing.

    The store goes down after the evaluation record and before the token record.
    The token has been minted by then, so the only safe move is to not hand it
    back - and to return an outcome rather than let the store's failure escape a
    method whose contract is to produce one.
    """

    class FailsOnTheSecondWrite(EvidenceWriter):
        """An evidence writer that survives one append and then does not."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.writes = 0

        def write(self, record: Any) -> AppendResult:
            self.writes += 1
            if self.writes > 1:
                raise EvidenceUnavailableError("the store went down mid-decision")
            return super().write(record)

    writer = FailsOnTheSecondWrite(store, clock=clock, id_generator=ids, wal=wal)
    pipeline = DecisionPipeline(writer=writer, tokens=tokens)

    outcome = pipeline.evaluate(allowing_request(), context())

    assert writer.writes == 2, "the token record must have been attempted"
    assert outcome.token is None, "a token whose issuance is unrecorded must not be returned"
    assert not outcome.permits_execution
    assert outcome.record is not None, "the evaluation record still stands"
    assert outcome.verdict is Verdict.ALLOW, "the resolver's verdict is unchanged"
    assert [r[FIELD_KIND] for r in records(store)] == [RecordKind.EVALUATION.value]


def test_a_decision_id_the_schema_rejects_stops_before_the_chain(pipeline, store) -> None:
    """``FR-72``: an identifier the record schema rejects never reaches the chain.

    ``DecisionContext.decision_id`` is a free string -- it comes from whatever
    identifier seam the deployment injects -- while the record schema types it
    as a UUID. A deployment wired to a non-UUID generator used to hash-chain an
    evaluation record no auditor could read, and find out from the auditor;
    the record was permanent by then, because that is what a hash chain is for.

    The store now refuses the record outright, so the pipeline abstains, nothing
    is written, and no token exists. All three matter: writing the record and
    withholding only the token would still leave the unreadable record in the
    chain.
    """
    outcome = pipeline.evaluate(allowing_request(), context(decision_id="dec-1"))

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.reason_codes[0].name is ReasonName.SCHEMA_INVALID
    assert outcome.token is None
    assert not outcome.permits_execution
    assert outcome.evidence_failed
    assert not records(store), "an unreadable record must not become permanent"


def test_a_token_service_refusal_withholds_rather_than_raises(store, wal, clock, ids) -> None:
    """The token service is the second opinion, and its refusal wins quietly.

    The pipeline's own ``_withholding_reason`` waves this decision through; the
    service refuses anyway. Nothing may execute, and ``evaluate`` must still
    return an outcome - a raised exception here would be an unhandled failure on
    the decision path.
    """

    class RefusingTokenService(TokenService):
        def issue(self, **kwargs: Any) -> Any:
            raise TokenVerdictMismatchError("refused by the token service")

    pipeline = DecisionPipeline(
        writer=EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal),
        tokens=RefusingTokenService(
            signer=HmacSigner(key_id="k1", secret=HMAC_SECRET),
            nonce_store=InMemoryNonceStore(),
            revocation_list=InMemoryRevocationList(),
            clock=clock,
            id_generator=ids,
        ),
    )

    outcome = pipeline.evaluate(allowing_request(), context())

    assert outcome.verdict is Verdict.ALLOW
    assert outcome.token is None
    assert not outcome.permits_execution
    assert outcome.record is not None
    assert [r[FIELD_KIND] for r in records(store)] == [RecordKind.EVALUATION.value]


# --- INV-11 and ADR-0016: modes never weaken the fail-closed path -------------


@pytest.mark.mutation
def test_mut_21_infrastructure_failure_blocks_in_shadow_mode(pipeline, store) -> None:
    """MUT-21: a shadow class is not an unguarded class."""
    outcome = pipeline.evaluate(
        infrastructure_request(Mode.SHADOW), context(Mode.SHADOW)
    )

    assert outcome.verdict is Verdict.ABSTAIN
    assert outcome.token is None, "an infrastructure failure must withhold a token in every mode"
    assert outcome.record is not None, "but it must still be recorded"


@pytest.mark.parametrize("mode", list(Mode))
def test_infrastructure_failure_withholds_a_token_in_every_mode(pipeline, mode: Mode) -> None:
    outcome = pipeline.evaluate(infrastructure_request(mode), context(mode))
    assert outcome.token is None
    assert outcome.verdict in {Verdict.ABSTAIN, Verdict.DENY}


def test_shadow_mode_records_a_deny_and_still_issues_a_shadow_token(pipeline, store) -> None:
    """The code path under observation must be the code path that will enforce."""
    outcome = pipeline.evaluate(denying_request(Mode.SHADOW), context(Mode.SHADOW))

    assert outcome.token is not None and outcome.shadow
    evaluation = records(store)[0]["evaluation"]
    assert evaluation["verdict"] == Verdict.DENY.value
    assert evaluation["mode"] == Mode.SHADOW.value


def test_enforce_mode_withholds_a_token_on_deny(pipeline) -> None:
    outcome = pipeline.evaluate(denying_request(Mode.ENFORCE), context(Mode.ENFORCE))
    assert outcome.verdict is Verdict.DENY
    assert outcome.token is None


@pytest.mark.mutation
def test_mut_19_a_halted_class_denies_and_issues_nothing(pipeline, store) -> None:
    """MUT-19: halt is the incident lever, and it withholds every token."""
    outcome = pipeline.evaluate(allowing_request(Mode.HALTED), context(Mode.HALTED))

    assert outcome.verdict is Verdict.DENY
    assert outcome.reason_codes[0].name is ReasonName.CLASS_HALTED
    assert outcome.token is None
    assert [r[FIELD_KIND] for r in records(store)] == [RecordKind.EVALUATION.value]


# --- the record is the explanation -------------------------------------------


def test_every_decision_is_recorded_whatever_the_verdict(pipeline, store) -> None:
    for request, ctx in (
        (allowing_request(), context()),
        (denying_request(), context()),
        (infrastructure_request(), context()),
    ):
        pipeline.evaluate(request, ctx)
    kinds = [r[FIELD_KIND] for r in records(store)]
    assert kinds.count(RecordKind.EVALUATION.value) == 3


def test_the_record_carries_the_reasons_and_the_digests(pipeline, store) -> None:
    """``NFR-20``: a verdict must be explainable from its record alone.

    Through typed fields, not prose. ``Resolution.explain`` is a trace for a
    human reading a log; the record explains itself with reason codes, rule
    outcomes and critic results, which are the parts a replay can compare.
    """
    outcome = pipeline.evaluate(denying_request(), context())

    evaluation = records(store)[0]["evaluation"]
    assert tuple(evaluation["reason_codes"]) == ("RULE_FAILED:WF-01",)
    assert evaluation["proposal_digest"] == str(PROPOSAL_DIGEST)
    assert evaluation["envelope_digest"] == str(ENVELOPE_DIGEST)
    assert "explain" not in evaluation, "prose has no place in the record (SEC-07)"
    assert outcome.resolution.explain, "the trace is still returned to the caller"


def test_chain_verifies_across_mixed_decisions(pipeline, store) -> None:
    pipeline.evaluate(allowing_request(), context())
    pipeline.evaluate(denying_request(), context())
    assert store.verify(TENANT_ID), (
        "the hash chain must hold across evaluation and token records"
    )
