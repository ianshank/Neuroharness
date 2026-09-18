"""The pipeline is where ``INV-05`` and ``INV-11`` stop being test properties.

Each subsystem below is already correct on its own. What these tests exercise is
the *order*: that a token cannot exist before its record does, that an
infrastructure failure withholds a token in every rollout mode, and that a
shadow class runs the identical path rather than a cheaper one.
"""

from __future__ import annotations

import pytest

from neuroharness.evidence.chain import FIELD_KIND, RecordKind
from neuroharness.evidence.store import EvidenceWriter, InMemoryEvidenceStore
from neuroharness.evidence.wal import InMemoryWriteAheadLog
from neuroharness.models.common import Digest, Mode, Verdict, VerifierResult
from neuroharness.pipeline import DecisionContext, DecisionPipeline
from neuroharness.reason import ReasonCode, ReasonName
from neuroharness.resolve.inputs import CriticOutcome, ResolutionRequest, SimpleClassPolicy
from neuroharness.tokens.nonce import InMemoryNonceStore, InMemoryRevocationList
from neuroharness.tokens.service import TokenService
from neuroharness.tokens.signer import HmacSigner

ENVELOPE_DIGEST = Digest.from_hex("a" * 64)
PROPOSAL_DIGEST = Digest.from_hex("b" * 64)
BUNDLE_DIGEST = Digest.from_hex("c" * 64)


@pytest.fixture
def store(clock, ids) -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(clock=clock, id_generator=ids)


@pytest.fixture
def wal(clock) -> InMemoryWriteAheadLog:
    return InMemoryWriteAheadLog(clock=clock)


@pytest.fixture
def pipeline(store, wal, clock, ids) -> DecisionPipeline:
    return DecisionPipeline(
        writer=EvidenceWriter(store, clock=clock, id_generator=ids, wal=wal),
        tokens=TokenService(
            signer=HmacSigner(key_id="k1", secret=b"unit-test-secret-32-bytes-longxx"),
            nonce_store=InMemoryNonceStore(),
            revocation_list=InMemoryRevocationList(),
            clock=clock,
            id_generator=ids,
        ),
    )


def context(mode: Mode = Mode.ENFORCE) -> DecisionContext:
    return DecisionContext(
        tenant_id="t1",
        trace_id="0" * 32,
        action_id="act-1",
        decision_id="dec-1",
        envelope_digest=ENVELOPE_DIGEST,
        proposal_digest=PROPOSAL_DIGEST,
        policy_bundle_digest=BUNDLE_DIGEST,
        mode=mode,
        action_class="deployment.apply/deploy_service",
    )


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


# --- the happy path, so the negative tests mean something --------------------


def test_allow_writes_a_record_then_issues_a_token(pipeline, store) -> None:
    outcome = pipeline.evaluate(allowing_request(), context())

    assert outcome.verdict is Verdict.ALLOW
    assert outcome.permits_execution
    assert outcome.record is not None

    kinds = [record[FIELD_KIND] for record in store.read("t1")]
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
    assert [r[FIELD_KIND] for r in store.read("t1")] == [RecordKind.EVALUATION.value]


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
    evaluation = store.read("t1")[0]["evaluation"]
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
    assert [r[FIELD_KIND] for r in store.read("t1")] == [RecordKind.EVALUATION.value]


# --- the record is the explanation -------------------------------------------


def test_every_decision_is_recorded_whatever_the_verdict(pipeline, store) -> None:
    for request, ctx in (
        (allowing_request(), context()),
        (denying_request(), context()),
        (infrastructure_request(), context()),
    ):
        pipeline.evaluate(request, ctx)
    kinds = [r[FIELD_KIND] for r in store.read("t1")]
    assert kinds.count(RecordKind.EVALUATION.value) == 3


def test_the_record_carries_the_reasons_and_the_trace(pipeline, store) -> None:
    """NFR-20: a verdict must be explainable from its record alone."""
    pipeline.evaluate(denying_request(), context())
    evaluation = store.read("t1")[0]["evaluation"]
    assert tuple(evaluation["reason_codes"]) == ("RULE_FAILED:WF-01",)
    assert evaluation["explain"], "the resolution trace must reach the record"
    assert evaluation["proposal_digest"] == str(PROPOSAL_DIGEST)


def test_chain_verifies_across_mixed_decisions(pipeline, store) -> None:
    pipeline.evaluate(allowing_request(), context())
    pipeline.evaluate(denying_request(), context())
    assert store.verify("t1"), "the hash chain must hold across evaluation and token records"
