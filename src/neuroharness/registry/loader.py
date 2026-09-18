"""Loading, verifying and validating a registry document (``FR-30``--``FR-34``).

Three checks happen here, in this order, and each one fails closed:

1. **Schema version.** ``SchemaCompatibility.assert_readable`` rejects a
   document this build does not fully understand. "Understood the parts I
   recognised" is how a policy field goes missing without anyone noticing.
2. **Integrity.** ``SEC-05``/``FR-33`` require the registry to be versioned and
   signed like a policy bundle. An unverified registry is an attacker-supplied
   policy: it decides which critics run, which class is approvable and which is
   halted. Verification is injected through :class:`SignatureVerifier` so a
   deployment can use its own signing scheme without this module holding keys.
3. **Content.** Every rule in :mod:`neuroharness.registry.models` runs, and a
   failure is re-raised as :class:`RegistryValidationError` naming the offending
   class and the rule it broke, because a rejection an operator cannot act on is
   an outage.

The default verifier is :class:`DigestVerifier`, not :class:`NullVerifier`.
Skipping verification is possible, but only by passing ``NullVerifier()``
explicitly -- an auditable, greppable choice rather than a default nobody
noticed (Constitution Art. II).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Mapping, Protocol, runtime_checkable

from pydantic import ValidationError

from neuroharness.canonical import canonicalize, digest_value, parse_json
from neuroharness.errors import (
    CanonicalizationError,
    RegistryIntegrityError,
    RegistryValidationError,
)
from neuroharness.models.common import Digest
from neuroharness.observability.logging import get_logger
from neuroharness.registry.models import ActionClass, ActionClassRegistry
from neuroharness.registry.resource_keys import ResourceKeyRegistry
from neuroharness.version import SchemaCompatibility, SchemaKind

__all__ = [
    "SignatureVerifier",
    "NullVerifier",
    "DigestVerifier",
    "canonical_bytes",
    "compute_registry_digest",
    "load_registry",
    "load_registry_file",
    "UNSIGNED_FIELDS",
]

_LOG = get_logger(__name__)

#: File suffixes routed to the YAML parser rather than the JSON one.
_YAML_SUFFIXES: Final[frozenset[str]] = frozenset({".yaml", ".yml"})

#: Fields excluded from the canonical form before digesting, because they carry
#: the integrity evidence itself and cannot cover themselves.
UNSIGNED_FIELDS: Final[frozenset[str]] = frozenset({"digest", "signature", "signatures"})

#: Document keys the loader consumes. Anything else is rejected rather than
#: ignored: an unknown top-level key is a policy the operator believes is in
#: force and is not.
_KNOWN_DOCUMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "registry_version",
        "unregistered_class_policy",
        "resource_keys",
        "action_classes",
    }
) | UNSIGNED_FIELDS


# --- canonicalization --------------------------------------------------------


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    """Return the RFC 8785 canonical byte form of ``document``.

    Delegates to :func:`neuroharness.canonical.canonicalize` rather than
    configuring ``json.dumps`` here. The byte-level identity of a document has
    exactly one definition in this harness; a second one in the registry loader
    would mean the registry an operator signed and the registry this module
    verifies could differ by a whitespace convention and nobody would find out
    until a digest mismatch with no cause.
    """
    return canonicalize(document)


def compute_registry_digest(document: Mapping[str, Any]) -> Digest:
    """Return the SHA-256 digest of ``document`` excluding :data:`UNSIGNED_FIELDS`.

    The digest covers everything an operator wrote and nothing the signing step
    added, so a document can carry its own digest without the self-reference
    problem, and tampering with any policy field changes the result.

    Raises :class:`~neuroharness.errors.CanonicalizationError` when the document
    has no canonical form, rather than digesting a guess: a registry with no
    stable identity cannot be the thing an operator signed.
    """
    payload = {k: v for k, v in document.items() if k not in UNSIGNED_FIELDS}
    return digest_value(payload)


# --- verification ------------------------------------------------------------


@runtime_checkable
class SignatureVerifier(Protocol):
    """Proves a registry document is the one an operator signed (``SEC-05``).

    Implementations raise :class:`RegistryIntegrityError` and return nothing on
    success. A boolean return would make "forgot to check the result" a silent
    bypass of the registry's entire authority.
    """

    def verify(self, document: Mapping[str, Any]) -> None:
        """Raise :class:`RegistryIntegrityError` unless ``document`` is authentic."""
        ...


class NullVerifier:
    """Verifies nothing. For tests and for assembling a registry in memory.

    Deliberately never the default. Named so that its presence in production
    wiring is obvious to a reviewer and greppable in an audit.
    """

    __slots__ = ()

    def verify(self, document: Mapping[str, Any]) -> None:  # noqa: D102 - see class docstring
        return None


class DigestVerifier:
    """Recomputes the document digest and compares it (``FR-33``, ``SEC-05``).

    This is integrity, not authenticity: it proves the document was not altered
    since its digest was computed, and -- when ``expected`` is supplied from a
    trusted channel -- that it is the exact document the operator approved. A
    deployment that needs authenticity wires a signature-checking verifier in
    its place; this is the floor, not the ceiling.

    Absence of a digest is a failure, not a pass. "No evidence of tampering"
    and "no evidence" are the same thing to a fail-closed harness.
    """

    __slots__ = ("_expected",)

    def __init__(self, expected: Digest | str | None = None) -> None:
        self._expected = Digest(expected) if expected is not None else None

    @property
    def expected(self) -> Digest | None:
        """The out-of-band digest this verifier pins to, if any."""
        return self._expected

    def verify(self, document: Mapping[str, Any]) -> None:
        declared_raw = document.get("digest")
        if declared_raw is None:
            raise RegistryIntegrityError(
                "registry document carries no digest; an unverified registry decides "
                "which classes are enforced and which are approvable (SEC-05, FR-33)"
            )
        try:
            declared = Digest(declared_raw)
        except ValueError as exc:
            raise RegistryIntegrityError(f"registry digest is malformed: {exc}") from exc

        actual = compute_registry_digest(document)
        if actual != declared:
            raise RegistryIntegrityError(
                f"registry digest mismatch: document declares {declared}, content hashes "
                f"to {actual}; the document was altered after signing (SEC-05)"
            )
        if self._expected is not None and declared != self._expected:
            raise RegistryIntegrityError(
                f"registry digest {declared} is not the pinned digest {self._expected}; "
                "this is a different registry than the one approved (FR-33)"
            )


# --- loading -----------------------------------------------------------------


def load_registry(
    document: Mapping[str, Any],
    *,
    verifier: SignatureVerifier | None = None,
) -> ActionClassRegistry:
    """Load, verify and validate a registry document (``FR-30``--``FR-34``).

    ``verifier`` defaults to :class:`DigestVerifier`, so a document with no or
    a wrong digest is refused unless the caller opts out explicitly with
    :class:`NullVerifier`.

    Raises
    ------
    neuroharness.errors.SchemaVersionError
        The document declares a ``schema_version`` this build cannot read.
    neuroharness.errors.RegistryIntegrityError
        The document failed verification.
    neuroharness.errors.RegistryValidationError
        The document is internally inconsistent; the message names the
        offending action class and the rule it broke.
    """
    if not isinstance(document, Mapping):
        raise RegistryValidationError(
            f"registry document must be a mapping, got {type(document).__name__}"
        )

    # 1. Schema version, before anything else reads a field by name.
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise RegistryValidationError(
            "registry document declares no schema_version; a document whose shape is "
            "unstated cannot be read safely (FR-30)"
        )
    SchemaCompatibility.assert_readable(SchemaKind.REGISTRY, schema_version)

    unknown = sorted(set(document) - _KNOWN_DOCUMENT_FIELDS)
    if unknown:
        raise RegistryValidationError(
            f"registry document has unknown top-level field(s) {unknown}; an unrecognised "
            "key is a policy the operator believes is in force and is not"
        )

    # 2. Integrity, before any of its content is trusted.
    effective_verifier: SignatureVerifier = DigestVerifier() if verifier is None else verifier
    effective_verifier.verify(document)

    # 3. Content, one class at a time so a failure can name the offender.
    resource_keys = _load_resource_keys(document.get("resource_keys"))
    action_classes = _load_action_classes(document.get("action_classes"))

    registry_version = document.get("registry_version")
    if not isinstance(registry_version, str) or not registry_version:
        raise RegistryValidationError(
            "registry document declares no registry_version; a registry with no version "
            "cannot be cited by a decision record (FR-33, Art. III)"
        )

    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "registry_version": registry_version,
        "action_classes": action_classes,
        "resource_keys": resource_keys,
    }
    if "digest" in document:
        payload["digest"] = document["digest"]
    if "unregistered_class_policy" in document:
        payload["unregistered_class_policy"] = document["unregistered_class_policy"]

    try:
        registry = ActionClassRegistry(**payload)
    except ValidationError as exc:
        raise RegistryValidationError(f"registry document rejected: {_first_error(exc)}") from exc

    _LOG.info(
        "registry_loaded",
        registry_version=registry.registry_version,
        schema_version=registry.schema_version,
        digest=str(registry.digest) if registry.digest else None,
        action_class_count=len(registry),
        unregistered_class_policy=registry.unregistered_class_policy.value,
        verifier=type(effective_verifier).__name__,
    )
    return registry


def load_registry_file(
    path: str | Path,
    *,
    verifier: SignatureVerifier | None = None,
) -> ActionClassRegistry:
    """Load a registry from a JSON (or, when PyYAML is installed, YAML) file.

    The file is read as text and parsed here rather than streamed, because the
    digest must be computed over the document the loader actually validates.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryValidationError(f"cannot read registry file {file_path}: {exc}") from exc

    document = _parse_document(text, file_path)
    return load_registry(document, verifier=verifier)


# --- internals ---------------------------------------------------------------


def _parse_document(text: str, file_path: Path) -> Mapping[str, Any]:
    """Parse a registry document, choosing the format from the suffix."""
    suffix = file_path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        parsed = _parse_yaml(text, file_path)
    else:
        # parse_json, not json.loads: it refuses a repeated object key instead of
        # silently keeping the last one. A registry with two `mode` keys is a
        # document whose meaning depends on the parser, and the signer's parser
        # is not necessarily this one.
        try:
            parsed = parse_json(text)
        except json.JSONDecodeError as exc:
            raise RegistryValidationError(f"malformed JSON in {file_path}: {exc}") from exc
        except CanonicalizationError as exc:
            raise RegistryValidationError(f"ambiguous JSON in {file_path}: {exc}") from exc

    if not isinstance(parsed, Mapping):
        raise RegistryValidationError(
            f"{file_path} does not contain a registry object (got {type(parsed).__name__})"
        )
    return parsed


def _parse_yaml(text: str, file_path: Path) -> Any:
    """Parse a YAML registry under the same strictness as the JSON path.

    ``yaml.safe_load`` keeps the last of a repeated mapping key and says
    nothing, which is exactly what :func:`neuroharness.canonical.parse_json`
    refuses on the JSON side: a registry with two ``mode`` keys is a document
    whose meaning depends on the parser, and the signer's parser is not
    necessarily this one. One format being strict and the other lax means the
    strictness is decorative - an attacker picks the lax one - and this document
    decides which action classes are enforced (``FR-30``, ``FR-33``, ``SEC-05``).

    Non-string keys are refused for the same reason: the digest is taken over
    the JSON form, where ``1`` and ``"1"`` are one key, so a YAML document that
    distinguishes them has two meanings and one digest.

    The rejection happens here, before the digest is computed, because a
    digest over a document the loader had to reinterpret proves nothing.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RegistryValidationError(
            f"cannot read {file_path}: YAML registries need PyYAML installed"
        ) from exc

    class _StrictLoader(yaml.SafeLoader):  # type: ignore[misc, name-defined]
        """A safe loader whose mappings have exactly one value per key."""

        def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
            # Deliberately does not call ``flatten_mapping``: a YAML merge key
            # would splice one mapping into another after this check, which is
            # another way for a document to mean two things.
            mapping: dict[str, Any] = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str):
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"registry mapping key {key!r} is not a string; the digest "
                        "is taken over the JSON form, where it would collide",
                        key_node.start_mark,
                    )
                if key in mapping:
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"registry repeats the mapping key {key!r}; parsers disagree "
                        "on which value wins, so the document has no single meaning",
                        key_node.start_mark,
                    )
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        return yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise RegistryValidationError(f"malformed YAML in {file_path}: {exc}") from exc


def _load_resource_keys(raw: Any) -> ResourceKeyRegistry:
    """Build the shared resource-key registry (``FR-34``)."""
    if raw is None:
        return ResourceKeyRegistry()
    if not isinstance(raw, Mapping):
        raise RegistryValidationError(
            f"resource_keys must be an object, got {type(raw).__name__} (FR-34)"
        )
    try:
        return ResourceKeyRegistry(**raw)
    except ValidationError as exc:
        raise RegistryValidationError(f"resource_keys rejected: {_first_error(exc)}") from exc


def _load_action_classes(raw: Any) -> tuple[ActionClass, ...]:
    """Build each action class, naming the offender when one is rejected."""
    if raw is None:
        raise RegistryValidationError("registry document declares no action_classes (FR-30)")
    if not isinstance(raw, (list, tuple)):
        raise RegistryValidationError(
            f"action_classes must be a list, got {type(raw).__name__} (FR-30)"
        )

    classes: list[ActionClass] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise RegistryValidationError(
                f"action_classes[{index}] must be an object, got {type(entry).__name__}"
            )
        name = _describe(entry, index)
        try:
            classes.append(ActionClass(**entry))
        except ValidationError as exc:
            raise RegistryValidationError(
                f"action class {name} rejected: {_first_error(exc)}"
            ) from exc
    return tuple(classes)


def _describe(entry: Mapping[str, Any], index: int) -> str:
    """Name an entry for an error message, even when its identity is malformed."""
    tool = entry.get("tool")
    intent = entry.get("intent")
    if isinstance(tool, str) and isinstance(intent, str):
        return f"{tool!r}/{intent!r}"
    return f"at index {index}"


def _first_error(exc: ValidationError) -> str:
    """Render a pydantic failure as one operator-readable line.

    The rule docstrings in :mod:`neuroharness.registry.models` raise messages
    that already name the requirement id, so surfacing the first error verbatim
    tells an operator both what is wrong and which requirement says so.
    """
    errors = exc.errors()
    if not errors:  # pragma: no cover - pydantic always reports at least one
        return str(exc)
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "<document>"
    message = str(first.get("msg", "")).removeprefix("Value error, ")
    return f"{location}: {message}"
