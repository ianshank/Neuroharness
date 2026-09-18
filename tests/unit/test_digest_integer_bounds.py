"""A digested document may not carry an integer two encoders would read differently.

``ADR-0021``. This implementation emits integers exactly, because rounding an
identifier while computing the digest that authorises acting on it is the worst
possible place to lose precision. RFC 8785 defines numbers through ECMAScript,
which has only doubles. The two agree everywhere except integers outside the
IEEE-754 safe range, so documents carrying one are refused before they are
digested and the divergence becomes unreachable.

Floats are deliberately *not* restricted here: both encoders follow the same
ECMAScript grammar for them and agree byte for byte.
"""

from __future__ import annotations

import pytest

from neuroharness.canonical.digest import (
    SAFE_INTEGER_BOUND,
    digest_value,
    envelope_digest,
    proposal_digest,
)
from neuroharness.errors import CanonicalizationError


@pytest.mark.parametrize(
    "value",
    [0, 1, -1, SAFE_INTEGER_BOUND, -SAFE_INTEGER_BOUND, 2**32, -(2**32)],
)
def test_integers_inside_the_safe_range_are_digested(value: int) -> None:
    assert digest_value({"id": value}).startswith("sha256:")


@pytest.mark.parametrize(
    "value", [SAFE_INTEGER_BOUND + 1, -(SAFE_INTEGER_BOUND + 1), 2**64, -(2**70)]
)
def test_integers_outside_the_safe_range_are_refused(value: int) -> None:
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        digest_value({"id": value})


def test_the_rejection_names_the_path_that_caused_it() -> None:
    """A named path is the difference between a fix and an investigation."""
    with pytest.raises(CanonicalizationError, match=r"/context/arguments/1"):
        digest_value({"context": {"arguments": [1, SAFE_INTEGER_BOUND + 1]}})


def test_booleans_are_not_mistaken_for_integers() -> None:
    """``bool`` subclasses ``int`` in Python; a naive range check rejects True."""
    assert digest_value({"flag": True, "other": False}).startswith("sha256:")


def test_floats_are_unaffected() -> None:
    """Both encoders follow the same grammar for floats, so nothing to restrict."""
    for value in (2.5, -0.0, 1e30, 1e-7, 1.0):
        assert digest_value({"x": value}).startswith("sha256:")


def test_the_restriction_applies_to_both_digests() -> None:
    envelope = {
        "proposal": {
            "tool": "deployment.apply",
            "intent": "deploy_service",
            "arguments": {"row_id": SAFE_INTEGER_BOUND + 1},
        },
        "context": {
            "actor": {
                "agent_id": "a",
                "principal": "user:1",
                "delegation_chain": [],
                "environment": "staging",
            },
            "policy_bundle": {"digest": "sha256:" + "a" * 64},
        },
    }
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        proposal_digest(envelope)
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        envelope_digest(envelope)


def test_a_large_identifier_is_fine_as_a_string() -> None:
    """The documented way out, so the rejection is actionable rather than a wall."""
    assert digest_value({"row_id": str(SAFE_INTEGER_BOUND + 1)}).startswith("sha256:")
