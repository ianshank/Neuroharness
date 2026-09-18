# ADR-0021: Exact integers in canonicalisation, and a rule that makes the deviation unreachable

**Status:** Proposed · **Date:** 2026-09-18 · **Deciders:** Tech lead · **Relates to:** ADR-0015, ADR-0020 · **Origin:** raised by the canonicalisation implementation

## Context
`FR-04` says the two digests are computed over the RFC 8785 canonical form. RFC 8785 defines number serialisation through ECMAScript, which means every number is an IEEE-754 double. Python distinguishes `int` from `float` and carries arbitrary-precision integers.

The implementation hit the conflict immediately. A tool argument may legitimately be an identifier larger than 2^53 — an account number, a row id, a snowflake id. Strict RFC 8785 would serialise it through a double and lose precision, so the harness would compute a digest over a *rounded* value and then issue a token authorising action on the original. Rounding a value while computing the digest that authorises acting on it is the worst possible place to lose precision.

The implementation therefore emits exact digits for Python integers and uses the ECMAScript path for floats. That is a deviation from RFC 8785, and deviations that live only in a docstring are how two implementations of the same protocol quietly stop agreeing.

## Decision
1. **Integers are serialised exactly.** A Python `int` emits its full decimal expansion. Floats follow the ECMAScript number grammar exactly, including the boundaries where Python's `repr` disagrees with it.
2. **The deviation is made unreachable rather than merely documented.** The two implementations diverge on exactly one class of value: an integer outside the IEEE-754 safe range (±(2^53 − 1)). Floats are not affected — both follow the ECMAScript grammar and agree byte for byte — so they are permitted. The rule is therefore narrow: **a digested document may not contain an integer outside the safe range**; values needing more range travel as strings. The digest functions reject such a value, naming its JSON Pointer path. `canonicalize` itself stays a faithful serialiser and does not impose the restriction, because the restriction is a property of what the harness agrees to *identify*, not of the encoding.
3. Separately, and for a different reason, an action class's `argument_schema` may not type a **policy-compared** argument as a float (`FR-02`, `FR-30`). That is not about canonicalisation: a gate that rests on floating-point equality is a defect regardless of how the value is serialised.
4. Consequently a conforming RFC 8785 implementation and this one produce identical bytes for every document the harness will actually digest, because the only documents where they could differ are refused before they are digested.

## Alternatives considered
- **Strict RFC 8785, accepting precision loss.** Rejected: the harness would authorise an action whose identity it computed from a value different to the one it executes.
- **Deviate and document, without the restriction.** Rejected: the divergence is silent and only appears when a large identifier meets a second implementation, which is exactly when a mismatched digest is most expensive to diagnose. The plan's own acceptance criterion asks for a cross-implementation check; that check would be meaningless if the two are permitted to differ.
- **Serialise all numbers as strings.** Rejected: it changes the wire shape of every envelope for a case the restriction already handles.

## Consequences
- Positive: no rounding in the identity path; byte-compatibility with standard implementations for every document that can actually occur; a cross-implementation conformance check is meaningful.
- Negative: an identifier larger than 2^53 must travel as a string, which is a real constraint on tool authors and will occasionally surprise one. It surfaces as a named rejection at the boundary rather than as a digest mismatch later, which is the cheaper place to learn it. Separately, a policy-compared argument may not be a float.
- Follow-ups: the digest-layer rejection rule (this increment) and the registry's `argument_schema` float rule (with the registry work) are both required before the digest test vectors are published as stable. Until then the vectors are provisional.
