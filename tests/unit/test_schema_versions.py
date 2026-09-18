"""What this build declares it can read, and how strictly it means it.

:mod:`neuroharness.version` is the single place that says which wire versions a
build understands. Everything downstream - the envelope model, the record model,
the evidence store, the registry loader - delegates to it, so a relaxation here
relaxes every one of those gates at once and none of them would report it.

Two things are therefore pinned here rather than derived. The readable set per
schema, because widening it is a backwards-compatible change and narrowing it is
not, and both need a reviewer to see them alongside the published JSON Schemas.
And the strictness of the comparison, because "same major, different minor" is
precisely the document a lenient build would accept while missing the fields the
newer minor added - the "understood the parts I recognised" failure Article II
names.
"""

from __future__ import annotations

import pytest

from neuroharness.errors import SchemaVersionError
from neuroharness.version import SchemaCompatibility, SchemaKind

#: The exact set of versions this build reads, per schema. A version appearing
#: here that the published JSON Schema rejects is the divergence peer review
#: caught once already, so this list and ``docs/sdd/schemas/`` move together.
EXPECTED_READABLE = {
    SchemaKind.ENVELOPE: frozenset({"1.1"}),
    SchemaKind.RECORD: frozenset({"1.1"}),
    SchemaKind.REGISTRY: frozenset({"1.0"}),
}

#: The single version each schema is written at. Always a member of the
#: readable set: a build that cannot read what it writes cannot read itself back.
EXPECTED_WRITTEN = {
    SchemaKind.ENVELOPE: "1.1",
    SchemaKind.RECORD: "1.1",
    SchemaKind.REGISTRY: "1.0",
}

#: A minor component no schema in this package will plausibly reach. Appended to
#: a readable major, it produces a version that is unreadable *only* if the
#: comparison looks past the major component.
UNREADABLE_MINOR = "99"

KINDS = (SchemaKind.ENVELOPE, SchemaKind.RECORD, SchemaKind.REGISTRY)


def same_major_unknown_minor(kind: str) -> str:
    """A version sharing a readable major, with a minor this build cannot read."""
    readable = sorted(SchemaCompatibility.readable_versions(kind))[0]
    return f"{readable.partition('.')[0]}.{UNREADABLE_MINOR}"


@pytest.mark.parametrize("kind", KINDS)
def test_the_readable_set_is_the_declared_one(kind: str) -> None:
    """Widening this set is how a build starts accepting documents it half-reads.

    Derived assertions elsewhere ask "does the loader delegate to this set";
    nothing else asks what the set actually contains, so a silent edit to it
    would change what every gate accepts with no test to show for it.
    """
    assert SchemaCompatibility.readable_versions(kind) == EXPECTED_READABLE[kind]


@pytest.mark.parametrize("kind", KINDS)
def test_the_written_version_is_declared_and_readable(kind: str) -> None:
    """A build that writes a version it cannot read cannot replay its own records."""
    written = SchemaCompatibility.written_version(kind)
    assert written == EXPECTED_WRITTEN[kind]
    assert SchemaCompatibility.is_readable(kind, written)


@pytest.mark.parametrize("kind", KINDS)
def test_a_known_major_with_an_unknown_minor_is_not_readable(kind: str) -> None:
    """The minor component is part of the version, not decoration.

    A build comparing only the major would accept a document written by a future
    minor revision and read it with this revision's field list: the fields the
    newer minor added - including whichever one a rule or an auditor reads -
    would simply be absent, and nothing would report it (Art. II).
    """
    version = same_major_unknown_minor(kind)
    assert version not in SchemaCompatibility.readable_versions(kind), (
        "this negative only discriminates while the version is genuinely unreadable"
    )
    assert not SchemaCompatibility.is_readable(kind, version)


@pytest.mark.parametrize("kind", KINDS)
def test_asserting_an_unknown_minor_fails_closed_with_what_it_can_read(kind: str) -> None:
    """A refusal an operator cannot act on is an outage with extra steps."""
    version = same_major_unknown_minor(kind)
    with pytest.raises(SchemaVersionError) as raised:
        SchemaCompatibility.assert_readable(kind, version)
    assert raised.value.schema_kind == kind
    assert raised.value.found_version == version
    assert raised.value.supported_versions == sorted(EXPECTED_READABLE[kind])
