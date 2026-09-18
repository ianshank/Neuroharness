"""Package version and wire-schema compatibility.

Backwards compatibility is a property the harness must *enforce*, not hope for.
Every wire model carries ``schema_version``. This module is the single place
that declares which versions a build can read and which version it writes.

An unknown version is a typed, fail-closed rejection (Constitution Art. II):
the harness never silently accepts a document it does not fully understand,
because "understood the parts I recognised" is exactly how an evidence field
goes missing without anyone noticing.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "PACKAGE_VERSION",
    "SchemaKind",
    "SchemaCompatibility",
]

PACKAGE_VERSION: Final[str] = "0.1.0"


class SchemaKind:
    """Names of the wire schemas this package versions independently."""

    ENVELOPE: Final[str] = "action-envelope"
    RECORD: Final[str] = "decision-record"
    REGISTRY: Final[str] = "action-class-registry"


# Versions this build can *read*. Widening this set is a backwards-compatible
# change; narrowing it is not and requires a migration note in the change log.
#
# These sets must never claim a version the published JSON Schema rejects. The
# schemas currently pin a single version each, so the sets are single-valued
# today; when 1.2 lands, the schema moves from `const` to `enum` and the set
# widens in the same change. Peer review caught the earlier draft claiming to
# read 1.0 while the schema forbade it, which is the kind of divergence that
# makes a compatibility matrix worse than none.
_READABLE: Final[dict[str, frozenset[str]]] = {
    SchemaKind.ENVELOPE: frozenset({"1.1"}),
    SchemaKind.RECORD: frozenset({"1.1"}),
    SchemaKind.REGISTRY: frozenset({"1.0"}),
}

# The single version this build *writes*. Always a member of the readable set.
_WRITTEN: Final[dict[str, str]] = {
    SchemaKind.ENVELOPE: "1.1",
    SchemaKind.RECORD: "1.1",
    SchemaKind.REGISTRY: "1.0",
}


class SchemaCompatibility:
    """Declares and checks wire-schema compatibility for a build."""

    @staticmethod
    def readable_versions(kind: str) -> frozenset[str]:
        try:
            return _READABLE[kind]
        except KeyError as exc:  # pragma: no cover - guards a programming error
            raise ValueError(f"unknown schema kind: {kind!r}") from exc

    @staticmethod
    def written_version(kind: str) -> str:
        try:
            return _WRITTEN[kind]
        except KeyError as exc:  # pragma: no cover - guards a programming error
            raise ValueError(f"unknown schema kind: {kind!r}") from exc

    @classmethod
    def is_readable(cls, kind: str, version: str) -> bool:
        return version in cls.readable_versions(kind)

    @classmethod
    def assert_readable(cls, kind: str, version: str) -> None:
        """Raise :class:`SchemaVersionError` if this build cannot read ``version``."""
        if not cls.is_readable(kind, version):
            from neuroharness.errors import SchemaVersionError

            raise SchemaVersionError(
                schema_kind=kind,
                found_version=version,
                supported_versions=sorted(cls.readable_versions(kind)),
            )
