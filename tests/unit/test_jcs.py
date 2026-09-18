"""Tests for RFC 8785 canonicalisation (``FR-04``).

Two halves, and the second is the one that matters.

The first half pins the canonical form against the vectors of RFC 8785 itself,
derived from the rules the RFC states: the code-unit ordering example of section
3.2.3, the ECMAScript number grammar of section 3.2.2.3 and the escape set of
section 3.2.2.2. These are interoperability tests. If they drift, a non-Python
component recomputing an envelope digest stops agreeing with this one, and every
honest decision turns into ``TOKEN_INVALID:digest_mismatch``.

The second half pins the refusals. A canonicaliser that quietly does something
reasonable with a ``set``, a duplicate key or a thousand-deep nest is worse than
one that raises, because the something-reasonable becomes a digest and the
digest becomes an authorisation. Each of those tests exists to prove that a
specific input gets no identity at all (Constitution Article II).

Characters and escape sequences are written with ``chr`` and by concatenation
rather than with source escapes. A test for an escaping rule that is itself
expressed in escapes is a test that can pass for the wrong reason.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator, Mapping
from decimal import Decimal
from typing import Any

import pytest

from neuroharness.canonical.jcs import (
    DEFAULT_MAX_DEPTH,
    canonical_string,
    canonicalize,
    parse_json,
    reject_duplicate_keys,
)
from neuroharness.errors import CanonicalizationError
from neuroharness.models.common import Digest, Verdict

QUOTE = chr(0x22)
BACKSLASH = chr(0x5C)

BACKSPACE = chr(0x08)
TAB = chr(0x09)
LINE_FEED = chr(0x0A)
FORM_FEED = chr(0x0C)
CARRIAGE_RETURN = chr(0x0D)
UNIT_SEPARATOR = chr(0x1F)
DELETE = chr(0x7F)
C1_CONTROL_FIRST = chr(0x80)
C1_CONTROL_LAST = chr(0x9F)
O_DIAERESIS = chr(0xF6)
E_ACUTE = chr(0xE9)
EURO_SIGN = chr(0x20AC)
DALET_WITH_DAGESH = chr(0xFB33)
GRINNING_FACE = chr(0x1F600)
HIGH_SURROGATE = chr(0xD800)


def _quoted(body: str) -> str:
    """Wrap ``body`` in the output's double quotes."""
    return QUOTE + body + QUOTE


def _escape(sequence: str) -> str:
    """Build the two-character escape ``\\<sequence>`` as it appears in output."""
    return BACKSLASH + sequence


def _nest(depth: int) -> Any:
    """Return ``depth`` nested arrays around a scalar."""
    value: Any = 0
    for _ in range(depth):
        value = [value]
    return value


class _DuplicateKeyMapping(Mapping[str, Any]):
    """A ``Mapping`` whose ``items()`` repeats a key.

    Not a contrivance: a mapping-like object assembled from a merge, a
    multi-dict or a protobuf ``map`` field can present one key twice, and
    ``dict(...)`` would silently pick a winner on the way in.
    """

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        self._pairs = pairs

    def __getitem__(self, key: str) -> Any:
        for candidate, value in self._pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(key for key, _ in self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def items(self) -> Any:  # type: ignore[override]
        return list(self._pairs)


class TestKeyOrdering:
    """Section 3.2.3: keys sort by UTF-16 code unit, not by code point."""

    def test_rfc_8785_sorting_example(self) -> None:
        # The document from RFC 8785 section 3.2.3, in its published order.
        document = {
            EURO_SIGN: "Euro Sign",
            CARRIAGE_RETURN: "Carriage Return",
            DALET_WITH_DAGESH: "Hebrew Letter Dalet With Dagesh",
            "1": "One",
            GRINNING_FACE: "Emoji: Grinning Face",
            C1_CONTROL_FIRST: "Control",
            O_DIAERESIS: "Latin Small Letter O With Diaeresis",
        }
        members = [
            (_escape("r"), "Carriage Return"),
            ("1", "One"),
            (C1_CONTROL_FIRST, "Control"),
            (O_DIAERESIS, "Latin Small Letter O With Diaeresis"),
            (EURO_SIGN, "Euro Sign"),
            (GRINNING_FACE, "Emoji: Grinning Face"),
            (DALET_WITH_DAGESH, "Hebrew Letter Dalet With Dagesh"),
        ]
        expected = "{" + ",".join(
            _quoted(key) + ":" + _quoted(value) for key, value in members
        ) + "}"
        assert canonical_string(document) == expected

    def test_astral_key_sorts_before_high_bmp_key(self) -> None:
        # The whole reason the RFC says "UTF-16 code unit". U+1F600 is the
        # larger code point, but its leading surrogate D83D is below FB33, so
        # code-point ordering -- Python's default -- gives the wrong answer.
        assert DALET_WITH_DAGESH < GRINNING_FACE
        output = canonical_string({DALET_WITH_DAGESH: 1, GRINNING_FACE: 2})
        assert output.index(GRINNING_FACE) < output.index(DALET_WITH_DAGESH)

    def test_shorter_key_sorts_before_its_own_extension(self) -> None:
        assert canonical_string({"ab": 1, "a": 2, "": 3}) == '{"":3,"a":2,"ab":1}'

    def test_ordering_is_recursive(self) -> None:
        assert canonical_string({"b": {"d": 1, "c": 2}, "a": 3}) == '{"a":3,"b":{"c":2,"d":1}}'


class TestNumbers:
    """Section 3.2.2.3: ECMAScript ``Number::toString``."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # Zero, and the negative zero JSON cannot distinguish from it.
            (0.0, "0"),
            (-0.0, "0"),
            # Integral floats lose their fractional part entirely.
            (1.0, "1"),
            (100.0, "100"),
            (-1.5, "-1.5"),
            # The plain/exponential boundary at 10**21, which is exactly where
            # ECMAScript and Python's own repr disagree.
            (1e20, "100000000000000000000"),
            (1e21, "1e+21"),
            (1e30, "1e+30"),
            # The same boundary at the small end, at 10**-7.
            (1e-6, "0.000001"),
            (1e-7, "1e-7"),
            (1e-100, "1e-100"),
            # Shortest round-trip, not a fixed precision.
            (0.1, "0.1"),
            (1 / 3, "0.3333333333333333"),
            (1.5e300, "1.5e+300"),
            # Doubles at their limits.
            (9007199254740992.0, "9007199254740992"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (-1.7976931348623157e308, "-1.7976931348623157e+308"),
        ],
    )
    def test_float_vectors(self, value: float, expected: str) -> None:
        assert canonical_string(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0"),
            (1, "1"),
            (-1, "-1"),
            (9007199254740992, "9007199254740992"),
            # Beyond 2**53 a double cannot hold the value, so the RFC's grammar
            # would have the harness digest a rounded identifier. Exact digits
            # instead; see the module docstring for the reasoning.
            (9007199254740993, "9007199254740993"),
            (10**30, "1" + "0" * 30),
            (-(10**30), "-1" + "0" * 30),
        ],
    )
    def test_integer_vectors(self, value: int, expected: str) -> None:
        assert canonical_string(value) == expected

    def test_int_and_float_agree_where_the_float_is_exact(self) -> None:
        assert canonical_string(1) == canonical_string(1.0)

    def test_booleans_are_not_integers(self) -> None:
        # ``True`` is an ``int`` in Python; canonicalising it as 1 would let a
        # boolean argument be compared against a numeric policy constant.
        assert canonical_string({"a": True, "b": False, "c": 1, "d": 0}) == (
            '{"a":true,"b":false,"c":1,"d":0}'
        )

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_numbers_are_refused(self, value: float) -> None:
        with pytest.raises(CanonicalizationError, match="NaN or Infinity"):
            canonicalize({"limit": value})


class TestStrings:
    """Section 3.2.2.2: the escape set, and nothing beyond it."""

    def test_required_escapes(self) -> None:
        source = QUOTE + BACKSLASH + BACKSPACE + FORM_FEED + LINE_FEED + CARRIAGE_RETURN + TAB
        expected = _quoted(
            _escape(QUOTE)
            + _escape(BACKSLASH)
            + _escape("b")
            + _escape("f")
            + _escape("n")
            + _escape("r")
            + _escape("t")
        )
        assert canonical_string(source) == expected

    def test_other_c0_controls_use_lowercase_four_digit_hex(self) -> None:
        source = chr(0x00) + chr(0x01) + UNIT_SEPARATOR
        expected = _quoted(_escape("u0000") + _escape("u0001") + _escape("u001f"))
        assert canonical_string(source) == expected

    def test_escaping_stops_at_u001f(self) -> None:
        # DEL and the C1 controls are emitted literally. Escaping them
        # "defensively" would change the bytes and therefore every digest.
        source = DELETE + C1_CONTROL_FIRST + C1_CONTROL_LAST
        assert canonical_string(source) == _quoted(source)

    def test_non_ascii_is_never_escaped(self) -> None:
        source = E_ACUTE + EURO_SIGN + GRINNING_FACE
        assert canonical_string(source) == _quoted(source)

    def test_forward_slash_is_not_escaped(self) -> None:
        assert canonical_string("a/b") == '"a/b"'

    def test_output_is_utf_8(self) -> None:
        assert canonicalize(EURO_SIGN) == bytes([0x22, 0xE2, 0x82, 0xAC, 0x22])

    def test_str_subclasses_canonicalise_as_their_content(self) -> None:
        # ``Digest`` and the str-valued enums travel inside envelopes, and a
        # str-valued enum's ``str()`` is "Verdict.ALLOW" -- which would make the
        # digest depend on whether the value had been through a parse.
        digest = Digest.from_hex("0" * 64)
        assert canonical_string({"verdict": Verdict.ALLOW, "digest": digest}) == (
            '{"digest":"sha256:' + "0" * 64 + '","verdict":"ALLOW"}'
        )

    def test_unpaired_surrogate_is_refused(self) -> None:
        with pytest.raises(CanonicalizationError, match="unpaired surrogate"):
            canonicalize({"name": HIGH_SURROGATE})


class TestStructure:
    def test_no_insignificant_whitespace(self) -> None:
        assert canonical_string({"a": [1, 2], "b": {"c": None}}) == '{"a":[1,2],"b":{"c":null}}'

    def test_array_order_is_preserved(self) -> None:
        # Arrays are ordered data; sorting one would change its meaning.
        assert canonical_string(["c", "a", "b"]) == '["c","a","b"]'

    def test_empty_containers(self) -> None:
        assert canonical_string({"a": {}, "b": []}) == '{"a":{},"b":[]}'

    def test_tuple_denotes_an_array(self) -> None:
        assert canonical_string((1, 2)) == canonical_string([1, 2])

    def test_scalars_are_valid_documents(self) -> None:
        assert canonical_string(None) == "null"
        assert canonical_string("x") == '"x"'


class TestDuplicateAndInvalidKeys:
    def test_mapping_with_repeated_key_is_refused(self) -> None:
        document = _DuplicateKeyMapping([("a", 1), ("a", 2)])
        with pytest.raises(CanonicalizationError, match="repeats the key 'a'"):
            canonicalize(document)

    def test_json_text_with_repeated_key_is_refused(self) -> None:
        # ``json.loads`` keeps the last value and says nothing; the harness must
        # not digest a document that two parsers would read differently.
        assert json.loads('{"a":1,"a":2}') == {"a": 2}
        with pytest.raises(CanonicalizationError, match="repeats the object key 'a'"):
            parse_json('{"a":1,"a":2}')

    def test_reject_duplicate_keys_is_usable_as_an_object_pairs_hook(self) -> None:
        assert json.loads('{"a":1}', object_pairs_hook=reject_duplicate_keys) == {"a": 1}
        with pytest.raises(CanonicalizationError):
            json.loads('{"a":1,"a":2}', object_pairs_hook=reject_duplicate_keys)

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_parse_json_refuses_non_standard_literals(self, literal: str) -> None:
        with pytest.raises(CanonicalizationError, match="non-standard literal"):
            parse_json('{"limit":' + literal + "}")

    @pytest.mark.parametrize("key", [1, None, True, 2.5])
    def test_non_string_keys_are_refused(self, key: Any) -> None:
        with pytest.raises(CanonicalizationError, match="non-string key"):
            canonicalize({key: "value"})

    def test_key_error_names_the_offending_path(self) -> None:
        with pytest.raises(CanonicalizationError, match="/context/facts/0"):
            canonicalize({"context": {"facts": [{7: "x"}]}})


class TestUnsupportedTypes:
    @pytest.mark.parametrize(
        ("value", "type_name"),
        [
            ({1, 2}, "set"),
            (frozenset({1}), "frozenset"),
            (b"bytes", "bytes"),
            (bytearray(b"b"), "bytearray"),
            (dt.date(2026, 9, 18), "date"),
            (dt.datetime(2026, 9, 18, 12), "datetime"),
            (dt.timedelta(seconds=1), "timedelta"),
            (Decimal("1.5"), "Decimal"),
            (complex(1, 2), "complex"),
            (object(), "object"),
        ],
    )
    def test_refused_with_its_type_named(self, value: Any, type_name: str) -> None:
        with pytest.raises(CanonicalizationError, match=type_name):
            canonicalize(value)

    def test_error_names_the_offending_path(self) -> None:
        document = {"context": {"facts": [{"value": {"seen": {1, 2}}}]}}
        with pytest.raises(CanonicalizationError, match="/context/facts/0/value/seen"):
            canonicalize(document)

    def test_root_path_is_named_readably(self) -> None:
        with pytest.raises(CanonicalizationError, match="<document root>"):
            canonicalize({1, 2})

    def test_path_segments_are_json_pointer_escaped(self) -> None:
        with pytest.raises(CanonicalizationError, match="/a~1b/c~0d"):
            canonicalize({"a/b": {"c~d": {1, 2}}})


class TestDepthBound:
    def test_default_depth_is_enforced(self) -> None:
        assert canonicalize(_nest(DEFAULT_MAX_DEPTH))
        with pytest.raises(CanonicalizationError, match="maximum nesting depth"):
            canonicalize(_nest(DEFAULT_MAX_DEPTH + 1))

    def test_depth_is_configurable(self) -> None:
        assert canonical_string(_nest(3), max_depth=3) == "[[[0]]]"
        with pytest.raises(CanonicalizationError, match="maximum nesting depth of 3"):
            canonicalize(_nest(4), max_depth=3)

    def test_objects_count_towards_the_same_bound(self) -> None:
        with pytest.raises(CanonicalizationError, match="object at /a/b"):
            canonicalize({"a": {"b": {"c": 1}}}, max_depth=2)

    def test_self_referential_document_fails_closed(self) -> None:
        # A cycle has no canonical form at all. The depth bound is what turns it
        # into a recorded refusal instead of a RecursionError in the gateway.
        document: dict[str, Any] = {}
        document["self"] = document
        with pytest.raises(CanonicalizationError, match="maximum nesting depth"):
            canonicalize(document)
