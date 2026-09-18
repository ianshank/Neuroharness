"""Tests for the proposal and envelope digests (``FR-04``, ADR-0015, ADR-0020).

The pinned vectors are the interoperability contract. A digest is only useful
because two components that never talk to each other compute the same one: the
gateway that decides, the broker that recomputes before executing (``FR-21``),
and the approval service that checks whether a day-old approval still matches.
Pinning the exact hexadecimal here means a change to the canonicaliser, to the
projection or to the field set cannot slip through as "the tests still pass" --
it shows up as the specific decision it is.

The scope tests are the security contract, and they are asymmetric on purpose.
Every case that says "does NOT change the proposal digest" defends a human
approval against being voided by unrelated churn -- an agent redeploy, a model
upgrade, a registry tuning change, a fact refresh. Every case that says "DOES
change" defends against the opposite failure, where an approval granted for one
request silently authorises another. Both directions have to hold, so both are
enumerated rather than sampled.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from typing import Any

import pytest

from neuroharness.canonical.digest import (
    PROPOSAL_DIGEST_PROJECTION,
    digest_bytes,
    digest_value,
    envelope_digest,
    proposal_digest,
)
from neuroharness.canonical.jcs import canonicalize
from neuroharness.errors import CanonicalizationError
from neuroharness.models.common import Digest

#: Synthetic digests for the fixture. Repeated nibbles so that a value which
#: leaked into a log or an assertion is obviously fixture material.
_CREDENTIAL_DIGEST = "sha256:" + "11" * 32
_FACT_DIGEST = "sha256:" + "22" * 32
_BUNDLE_DIGEST = "sha256:" + "33" * 32
_REGISTRY_DIGEST = "sha256:" + "44" * 32
_OTHER_BUNDLE_DIGEST = "sha256:" + "55" * 32

#: Pinned vectors. Computed from this implementation and frozen deliberately:
#: any change to them is a wire-format change and needs an ADR, not an edit.
EMPTY_BYTES_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EMPTY_OBJECT_DIGEST = "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
SAMPLE_OBJECT_DIGEST = "sha256:ce74053d7a9997b7a9ffa28fba2f736e47c7ebb226ab093038bc0fba3a1554ee"
FIXTURE_PROPOSAL_DIGEST = "sha256:b1b9e1df100c7152f66910c150c11800b1c9fa2f47604634d831409f4d85d134"
FIXTURE_ENVELOPE_DIGEST = "sha256:6c52e212e351db730ca6a55d31f5cd28e6fcfd9f26ab9a0cab623304368cb56f"


def _envelope() -> dict[str, Any]:
    """A schema-shaped action envelope, rebuilt fresh for every test.

    Returned by a function rather than held as a module constant so that a test
    which mutates it cannot leak that mutation into the next one -- which, for a
    digest test, would show up as an unrelated failure three tests later.
    """
    return {
        "schema_version": "1.1",
        "action_id": "018f3f7e-0000-7000-8000-00000000abcd",
        "proposal": {
            "tool": "deploy.service",
            "intent": "release",
            "arguments": {
                "service": "example-api",
                "version": "1.4.2",
                "target": "production",
                "replicas": 4,
            },
            "claims": [{"name": "change_ticket", "value": "CHG-1042"}],
        },
        "context": {
            "tenant_id": "tenant-a",
            "session_id": "session-9f2",
            "session_root_id": "session-9f2",
            "trace_id": "trace-71c",
            "proposed_at": "2026-09-18T12:00:00Z",
            "repair_iteration": 0,
            "actor": {
                "agent_id": "agent.release-bot",
                "agent_version": "3.1.0",
                "principal": "user:123",
                "delegation_chain": [
                    {
                        "principal": "user:123",
                        "credential_status": "verified",
                        "credential_digest": _CREDENTIAL_DIGEST,
                    }
                ],
                "environment": "production",
                "model_identity": {"model_id": "governed-model", "model_version": "2026-05"},
            },
            "action_class": {
                "registered": True,
                "mode": "enforce",
                "effect_class": "write",
                "resource_key": "service:example-api/env:production",
                "repair_budget": 3,
                "approvable": True,
                "escalate_on": ["FACT_STALE"],
                "connector_kind": "sync",
            },
            "facts": [
                {
                    "name": "deployment_state",
                    "key": {"service": "example-api", "environment": "production"},
                    "value": {"current_version": "1.4.1", "replicas": 4},
                    "status": "fresh",
                    "source": "cluster-api",
                    "provider_version": "1.2.0",
                    "observed_at": "2026-09-18T11:59:30Z",
                    "fetched_at": "2026-09-18T11:59:59Z",
                    "ttl_seconds": 60,
                    "digest": _FACT_DIGEST,
                }
            ],
            "policy_bundle": {"version": "2026.09.1", "digest": _BUNDLE_DIGEST},
            "registry": {"version": "2026.09.1", "digest": _REGISTRY_DIGEST},
            "critic_versions": {"z3": "4.13.0", "monitor": "0.4.0"},
        },
    }


def _with(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Return the fixture envelope with ``path`` set to ``value``."""
    envelope = _envelope()
    cursor: Any = envelope
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return envelope


def _without(path: tuple[str, ...]) -> dict[str, Any]:
    """Return the fixture envelope with ``path`` removed."""
    envelope = _envelope()
    cursor: Any = envelope
    for key in path[:-1]:
        cursor = cursor[key]
    del cursor[path[-1]]
    return envelope


class TestPinnedVectors:
    def test_empty_bytes(self) -> None:
        assert digest_bytes(b"") == EMPTY_BYTES_DIGEST

    def test_empty_object(self) -> None:
        assert digest_value({}) == EMPTY_OBJECT_DIGEST
        assert digest_value({}) == digest_bytes(b"{}")

    def test_sample_object(self) -> None:
        assert digest_value({"b": [1, 2.5, None], "a": "x"}) == SAMPLE_OBJECT_DIGEST

    def test_fixture_envelope(self) -> None:
        assert envelope_digest(_envelope()) == FIXTURE_ENVELOPE_DIGEST

    def test_fixture_proposal(self) -> None:
        assert proposal_digest(_envelope()) == FIXTURE_PROPOSAL_DIGEST

    def test_results_are_validated_digest_values(self) -> None:
        # ``Digest`` validates on construction, so a malformed digest cannot be
        # handed to the token service or written into a record at all.
        assert isinstance(digest_value({}), Digest)
        assert digest_value({}).hex == hashlib.sha256(b"{}").hexdigest()

    def test_digest_value_is_digest_bytes_of_the_canonical_form(self) -> None:
        envelope = _envelope()
        assert digest_value(envelope) == digest_bytes(canonicalize(envelope))

    def test_the_two_digests_differ(self) -> None:
        envelope = _envelope()
        assert proposal_digest(envelope) != envelope_digest(envelope)


class TestProposalDigestIgnoresEvaluationScope:
    """Changes that must NOT void a pending human approval (``FR-40``)."""

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            # Evaluation-scoped: this is what the envelope digest is for.
            (("context", "facts"), []),
            (
                ("context", "facts", 0, "value"),
                {"current_version": "1.4.0", "replicas": 2},
            ),
            (("context", "facts", 0, "fetched_at"), "2026-09-18T12:04:59Z"),
            (("context", "proposed_at"), "2026-09-18T18:30:00Z"),
            (("context", "trace_id"), "trace-000"),
            (("context", "session_id"), "session-000"),
            (("context", "repair_iteration"), 2),
            (("action_id",), "018f3f7e-0000-7000-8000-00000000ffff"),
            # Deployment churn between request and approval (ADR-0020). A human
            # approved an ask; an agent redeploy is not a different ask.
            (("context", "actor", "agent_version"), "3.2.0"),
            (
                ("context", "actor", "model_identity"),
                {"model_id": "governed-model", "model_version": "2026-09"},
            ),
            # Mutable action-class knobs. Promoting a class from shadow to
            # enforce must not void the approvals that made it safe to promote.
            (("context", "action_class", "mode"), "shadow"),
            (("context", "action_class", "repair_budget"), 1),
            (("context", "action_class", "approvable"), False),
            (("context", "action_class", "escalate_on"), []),
            # Agent-controlled and never evaluated (``INV-08``): including it
            # would let the agent void its own approval by editing free text.
            (("proposal", "claims"), [{"name": "change_ticket", "value": "CHG-9999"}]),
            # The bundle is bound by digest; ``version`` is a label for the same
            # bytes, so relabelling a bundle is not a change of rules.
            (("context", "policy_bundle", "version"), "2026.09.1-hotfix"),
        ],
    )
    def test_proposal_digest_is_stable_while_envelope_digest_moves(
        self, path: tuple[Any, ...], value: Any
    ) -> None:
        mutated = _with(path, value)
        assert mutated != _envelope(), "the mutation must actually change the envelope"
        assert proposal_digest(mutated) == proposal_digest(_envelope())
        assert envelope_digest(mutated) != envelope_digest(_envelope())

    def test_agent_redeploy_does_not_supersede_an_approval(self) -> None:
        # The case ADR-0020 was written for, spelled out rather than buried in
        # a parametrisation: a release bot redeploys while a human is deciding.
        at_request = _envelope()
        at_approval = _with(("context", "actor", "agent_version"), "3.2.0")
        assert proposal_digest(at_approval) == proposal_digest(at_request)
        assert envelope_digest(at_approval) != envelope_digest(at_request)

    def test_class_promotion_does_not_supersede_an_approval(self) -> None:
        at_request = _with(("context", "action_class", "mode"), "advisory")
        at_approval = _with(("context", "action_class", "mode"), "enforce")
        assert proposal_digest(at_approval) == proposal_digest(at_request)
        assert envelope_digest(at_approval) != envelope_digest(at_request)

    def test_key_order_does_not_change_either_digest(self) -> None:
        envelope = _envelope()
        reordered = dict(reversed(list(envelope.items())))
        reordered["context"] = dict(reversed(list(envelope["context"].items())))
        assert proposal_digest(reordered) == proposal_digest(envelope)
        assert envelope_digest(reordered) == envelope_digest(envelope)


class TestProposalDigestTracksTheAsk:
    """Changes that MUST supersede an approval (``FR-41``, ``FR-46``)."""

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            # The ask itself.
            (("proposal", "tool"), "deploy.database"),
            (("proposal", "intent"), "rollback"),
            (("proposal", "arguments", "version"), "1.4.3"),
            (("proposal", "arguments", "service"), "billing-api"),
            (("proposal", "arguments", "replicas"), 40),
            (
                ("proposal", "arguments"),
                {"service": "example-api", "version": "1.4.2", "target": "production"},
            ),
            # By whom, and through which chain of delegation.
            (("context", "actor", "agent_id"), "agent.other-bot"),
            (("context", "actor", "principal"), "user:456"),
            (
                ("context", "actor", "delegation_chain"),
                [
                    {
                        "principal": "user:123",
                        "credential_status": "verified",
                        "credential_digest": _CREDENTIAL_DIGEST,
                    },
                    {"principal": "agent:helper", "credential_status": "unverified"},
                ],
            ),
            # In which environment.
            (("context", "actor", "environment"), "staging"),
            # Under which rule set.
            (("context", "policy_bundle", "digest"), _OTHER_BUNDLE_DIGEST),
        ],
    )
    def test_both_digests_change(self, path: tuple[Any, ...], value: Any) -> None:
        mutated = _with(path, value)
        assert proposal_digest(mutated) != proposal_digest(_envelope())
        assert envelope_digest(mutated) != envelope_digest(_envelope())

    def test_argument_change_alone_supersedes(self) -> None:
        # The repair loop submits an edited proposal under the same action_id;
        # the pending approval must not carry over to it (``FR-41``).
        original = _envelope()
        repaired = _with(("proposal", "arguments", "replicas"), 40)
        assert repaired["action_id"] == original["action_id"]
        assert proposal_digest(repaired) != proposal_digest(original)


class TestEnvelopeDigestCoversEverything:
    @pytest.mark.parametrize(
        ("path", "value"),
        [
            (("context", "facts", 0, "value", "replicas"), 8),
            (("context", "facts", 0, "status"), "stale"),
            (("context", "facts"), []),
            (("context", "registry", "digest"), _OTHER_BUNDLE_DIGEST),
            (("context", "critic_versions", "z3"), "4.12.0"),
            (("context", "tenant_id"), "tenant-b"),
            (("schema_version",), "1.0"),
        ],
    )
    def test_any_field_change_moves_it(self, path: tuple[Any, ...], value: Any) -> None:
        assert envelope_digest(_with(path, value)) != envelope_digest(_envelope())

    def test_a_stale_fact_cannot_reuse_a_fresh_token(self) -> None:
        # ``FR-21``: the broker recomputes this over what it will execute, so a
        # fact that changed between decision and dispatch invalidates the token.
        at_decision = _envelope()
        at_dispatch = copy.deepcopy(at_decision)
        at_dispatch["context"]["facts"][0]["value"]["current_version"] = "1.4.2"
        assert envelope_digest(at_dispatch) != envelope_digest(at_decision)


class TestProjectionIsData:
    def test_projection_matches_adr_0020(self) -> None:
        assert PROPOSAL_DIGEST_PROJECTION == (
            ("proposal", "tool"),
            ("proposal", "intent"),
            ("proposal", "arguments"),
            ("context", "actor", "agent_id"),
            ("context", "actor", "principal"),
            ("context", "actor", "delegation_chain"),
            ("context", "actor", "environment"),
            ("context", "policy_bundle", "digest"),
        )

    @pytest.mark.parametrize("path", PROPOSAL_DIGEST_PROJECTION)
    def test_a_missing_projected_field_is_refused(self, path: tuple[str, ...]) -> None:
        # Not "projected as absent". A partial projection is a valid digest of
        # the wrong document, and an approval would then bind to a proposal
        # whose principal or policy bundle nobody recorded.
        with pytest.raises(CanonicalizationError, match="/" + "/".join(path)):
            proposal_digest(_without(path))

    def test_a_missing_parent_is_refused_at_the_parent(self) -> None:
        with pytest.raises(CanonicalizationError, match="/context/actor"):
            proposal_digest(_without(("context", "actor")))

    def test_an_empty_mapping_is_refused(self) -> None:
        with pytest.raises(CanonicalizationError, match="/proposal/tool"):
            proposal_digest({})

    def test_the_projection_is_exactly_what_is_hashed(self) -> None:
        # Guards against the projection drifting away from the digest: the
        # digest must equal one taken over the projected document alone.
        projected = {
            "proposal": {
                "tool": "deploy.service",
                "intent": "release",
                "arguments": {
                    "service": "example-api",
                    "version": "1.4.2",
                    "target": "production",
                    "replicas": 4,
                },
            },
            "context": {
                "actor": {
                    "agent_id": "agent.release-bot",
                    "principal": "user:123",
                    "delegation_chain": [
                        {
                            "principal": "user:123",
                            "credential_status": "verified",
                            "credential_digest": _CREDENTIAL_DIGEST,
                        }
                    ],
                    "environment": "production",
                },
                "policy_bundle": {"digest": _BUNDLE_DIGEST},
            },
        }
        assert proposal_digest(_envelope()) == digest_value(projected)


class TestUncanonicalisableValues:
    def test_error_propagates_rather_than_yielding_a_sentinel(self) -> None:
        # Article II: a value with no identity cannot be evaluated, so the
        # failure has to reach the caller that will record a refusal.
        with pytest.raises(CanonicalizationError):
            envelope_digest(_with(("proposal", "arguments"), {"seen": {1, 2}}))

    def test_a_bad_field_outside_the_projection_still_blocks_the_proposal_digest_only_if_projected(
        self,
    ) -> None:
        # The proposal digest never reads facts, so an uncanonicalisable fact
        # value cannot stop an approval from being matched -- but it does stop
        # the evaluation, through the envelope digest.
        broken = _with(("context", "facts", 0, "value"), {"seen": {1, 2}})
        assert proposal_digest(broken) == proposal_digest(_envelope())
        with pytest.raises(CanonicalizationError):
            envelope_digest(broken)


class TestLogging:
    def test_debug_log_carries_the_digest_and_length_but_not_the_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        envelope = _envelope()
        with caplog.at_level(logging.DEBUG, logger="neuroharness.canonical.digest"):
            digest = envelope_digest(envelope)

        records = [r for r in caplog.records if r.getMessage() == "canonical.digest_computed"]
        assert len(records) == 1
        fields = records[0].fields  # type: ignore[attr-defined]
        assert fields["digest"] == str(digest)
        assert fields["byte_length"] == len(canonicalize(envelope))
        assert fields["kind"] == "envelope"

        # ``NFR-18``: no arguments, no fact values, no principal in the log.
        rendered = str(fields)
        for secret in ("example-api", "user:123", "1.4.2", "cluster-api"):
            assert secret not in rendered

    def test_each_digest_kind_is_labelled(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG, logger="neuroharness.canonical.digest"):
            digest_bytes(b"x")
            digest_value({})
            proposal_digest(_envelope())
            envelope_digest(_envelope())
        kinds = [
            r.fields["kind"]  # type: ignore[attr-defined]
            for r in caplog.records
            if r.getMessage() == "canonical.digest_computed"
        ]
        assert kinds == ["bytes", "value", "proposal", "envelope"]
