"""A digested document may not carry a number two encoders would read differently.

``ADR-0021``. This implementation emits integers exactly, because rounding an
identifier while computing the digest that authorises acting on it is the worst
possible place to lose precision. RFC 8785 defines numbers through ECMAScript,
which has only doubles. The two agree everywhere except integers outside the
IEEE-754 safe range, so documents carrying one are refused before they are
digested and the divergence becomes unreachable.

The rule is stated over the *canonical form*, never over the Python type, and
the difference is not academic. ``1e20`` is a ``float`` in Python and the digits
``100000000000000000000`` once RFC 8785 has serialised it; the broker re-parses
those bytes and recomputes the digest before executing (``FR-21``), so a
type-based rule accepts the envelope at the gateway and refuses it at the
broker. That is a fail-closed refusal of an envelope nobody tampered with, on
the far side of the decision, which is the worst place to discover it.
"""

from __future__ import annotations

import json

import pytest

from neuroharness.canonical.digest import (
    SAFE_INTEGER_BOUND,
    digest_value,
    envelope_digest,
    proposal_digest,
)
from neuroharness.canonical.jcs import DEFAULT_MAX_DEPTH, canonical_string
from neuroharness.errors import CanonicalizationError

#: The first float above the safe range that ECMAScript still renders as plain
#: digits. Below ``1e21`` there is no exponent in the output, so the value
#: re-parses as an ``int``; at and above it there is, so it re-parses as a float.
FIRST_EXPONENT_FLOAT = 1e21


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


# --- the rule is about the canonical form, not the Python type ---------------


@pytest.mark.parametrize(
    "value",
    [1e20, -1e20, float(2**53), float(2**60), FIRST_EXPONENT_FLOAT / 10],
)
def test_floats_that_canonicalise_to_a_large_integer_are_refused(value: float) -> None:
    """The defect: a ``float`` the broker would read back as an out-of-range ``int``.

    Each of these serialises to plain digits with no fraction and no exponent,
    so ``json.loads`` of the canonical form returns an ``int`` beyond the safe
    range. Refusing at the gateway is what keeps the two sides symmetric.
    """
    rendered = canonical_string(value)
    assert isinstance(json.loads(rendered), int), (
        f"{value!r} canonicalises to {rendered!r}, which is not an integer literal; "
        "this case does not exercise what it claims to"
    )
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        digest_value({"x": value})


@pytest.mark.parametrize(
    "value", [2.5, -0.0, 0.0, 1e-7, 1.0, FIRST_EXPONENT_FLOAT, 1e300, -1e300]
)
def test_floats_that_survive_a_round_trip_are_digested(value: float) -> None:
    """Over-rejecting is not safe either: it refuses envelopes nobody tampered with.

    Every value here either carries a fraction or is rendered with an exponent,
    so it re-parses as the same ``float`` and the two encoders agree byte for
    byte. ``1e21`` is the interesting one: it is far above the safe integer
    range and perfectly digestible, because ECMAScript stops writing plain
    digits there.
    """
    assert digest_value({"x": value}).startswith("sha256:")


@pytest.mark.parametrize(
    "value",
    [2.5, -0.0, 1e-7, 1.0, FIRST_EXPONENT_FLOAT, 1e300, 0, 1, -1, SAFE_INTEGER_BOUND],
)
def test_the_broker_recomputes_the_same_digest_from_the_canonical_bytes(
    value: float | int,
) -> None:
    """``FR-21``: gateway and broker must agree on every document either accepts.

    The gateway digests the value it holds; the broker digests what it parsed
    from the canonical bytes. If those two ever disagree the token fails to
    verify for an envelope nobody touched, and this is the assertion that says
    they never do inside the accepted domain.
    """
    document = {"x": value}
    reparsed = json.loads(canonical_string(document))
    assert digest_value(reparsed) == digest_value(document)


def test_the_two_sides_of_the_boundary_are_adjacent() -> None:
    """No gap and no overlap between what is accepted and what is refused."""
    assert digest_value(float(SAFE_INTEGER_BOUND)).startswith("sha256:")
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        digest_value(float(SAFE_INTEGER_BOUND + 1))


# --- depth: a fail-closed error, never a bare RecursionError -----------------


def _nest(depth: int) -> dict[str, object]:
    document: dict[str, object] = {}
    cursor = document
    for _ in range(depth):
        child: dict[str, object] = {}
        cursor["a"] = child
        cursor = child
    return document


def test_an_overdeep_document_fails_closed_rather_than_blowing_the_stack() -> None:
    """The number check walks the document too, so it needs the same depth bound.

    Unbounded, it ran first and raised ``RecursionError`` - an error outside the
    :class:`~neuroharness.errors.FailClosedError` hierarchy, which no caller
    catches and no reason code names. Article II says an inability to evaluate
    ends in no execution, and it can only do that if the failure is typed.
    """
    with pytest.raises(CanonicalizationError, match="nesting depth"):
        digest_value(_nest(DEFAULT_MAX_DEPTH + 5))


def test_a_document_deeper_than_the_interpreter_stack_still_fails_closed() -> None:
    with pytest.raises(CanonicalizationError, match="nesting depth"):
        digest_value(_nest(5000))


def test_a_document_within_the_depth_bound_is_digested() -> None:
    assert digest_value(_nest(DEFAULT_MAX_DEPTH - 2)).startswith("sha256:")


# --- both digests enforce the same domain ------------------------------------


def _envelope(row_id: object) -> dict[str, object]:
    return {
        "proposal": {
            "tool": "deployment.apply",
            "intent": "deploy_service",
            "arguments": {"row_id": row_id},
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


@pytest.mark.parametrize("row_id", [SAFE_INTEGER_BOUND + 1, 1e20])
def test_the_restriction_applies_to_both_digests(row_id: object) -> None:
    envelope = _envelope(row_id)
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        proposal_digest(envelope)
    with pytest.raises(CanonicalizationError, match="IEEE-754"):
        envelope_digest(envelope)


def test_a_large_identifier_is_fine_as_a_string() -> None:
    """The documented way out, so the rejection is actionable rather than a wall."""
    assert digest_value({"row_id": str(SAFE_INTEGER_BOUND + 1)}).startswith("sha256:")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_float_is_refused_by_the_canonicaliser_not_by_this_rule(
    value: float,
) -> None:
    """Whoever can name the path should be the one to raise.

    A non-finite float is not a JSON value at all, so it is not an ADR-0021
    question. The number check steps over it deliberately and lets
    :func:`~neuroharness.canonical.jcs.canonicalize` refuse it, because that is
    the function that knows where in the document it was.
    """
    with pytest.raises(CanonicalizationError) as raised:
        digest_value({"context": {"facts": [value]}})
    message = str(raised.value)
    assert "IEEE-754" not in message
    assert "/context/facts/0" in message
