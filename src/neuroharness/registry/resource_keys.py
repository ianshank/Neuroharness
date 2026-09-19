"""The signed resource-key registry (``FR-34``).

Policy must never compare free strings. ``FR-34`` requires one shared,
signed catalogue of canonical resource identifiers and enumerations so that
argument schemas, temporal properties, broker leases and reason-code subjects
all name the *same* things the *same* way. Two spellings of one production
target are two policies, and only one of them was reviewed (scenario ``A-35``).

A resource key is ``kind:id`` or a ``/``-joined path of such segments, for
example ``service:checkout/target:production``. The grammar is deliberately
narrow:

* it is a subset of the reason-code subject charset (section 5.6), because a
  resource key is emitted verbatim as the subject of
  ``RESOURCE_BUSY:<resource_key>`` and ``EFFECT_MISMATCH:<resource_key>``. A key
  that cannot be rendered as a reason code is a key whose contention or effect
  mismatch could not be recorded, and an unrecorded decision was not made
  (Constitution Art. III). That claim used to be written here and was false in
  both directions; it is now asserted at import in :mod:`neuroharness.grammar`;
* it excludes ``_`` and any character outside ``[A-Za-z0-9.-]`` in identifiers
  for the same reason;
* it is length-bounded so the rendered reason code stays inside the limit
  :class:`neuroharness.reason.ReasonCode` enforces.

Rendering a key from arguments (``FR-25``) happens here too, so the broker's
lease identity and the registry's declared template can never drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Annotated, Any, Final

from pydantic import ConfigDict, Field, model_validator

from neuroharness.errors import RegistryValidationError
from neuroharness.grammar import (
    MAX_RESOURCE_KEY_LENGTH,
    RESOURCE_ID_SOURCE,
    RESOURCE_KEY_PATTERN,
    RESOURCE_KIND_SOURCE,
    anchored,
)
from neuroharness.models.common import (
    FrozenMappingSerializer,
    FrozenMappingValidator,
    RevalidatingModel,
)

__all__ = [
    "RESOURCE_KEY_PATTERN",
    "MAX_RESOURCE_KEY_LENGTH",
    "ResourceKeyRegistry",
    "is_resource_key",
    "template_placeholders",
    "render_template",
]

# The grammar itself lives in :mod:`neuroharness.grammar`, alongside the
# reason-code subject grammar it has to fit inside. It was spelled here, in
# ``models/record.py``, in ``reason.py`` and in both published JSON Schemas, and
# the four had drifted: this module admitted ``cluster-prod:svc-a`` and the
# record model refused it, so a lease on a hyphenated resource kind could be
# taken and never recorded. Re-exported under the names this module has always
# used, so no caller moves.
_KIND: Final[str] = RESOURCE_KIND_SOURCE
_ID: Final[str] = RESOURCE_ID_SOURCE

_KIND_PATTERN: Final[re.Pattern[str]] = anchored(_KIND)
_ID_PATTERN: Final[re.Pattern[str]] = anchored(_ID)

#: ``{argument_name}`` placeholders in a ``resource_key_template``. Argument
#: names follow Python identifier rules because they are envelope field names,
#: not resource identifiers, so ``_`` is allowed here.
_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Argument values that may be substituted into a template. ``bool`` is excluded
#: on purpose: ``True`` is not a resource identifier, and Python would happily
#: render it as one.
_RENDERABLE_TYPES: Final[tuple[type, ...]] = (str, int)


def is_resource_key(value: str) -> bool:
    """Return ``True`` when ``value`` is a well-formed resource key.

    Shape only. Membership of a canonical enumeration is a separate question,
    answered by :meth:`ResourceKeyRegistry.is_known`, because a deployment may
    legitimately key a lease on a resource kind it does not enumerate.
    """
    if not isinstance(value, str) or len(value) > MAX_RESOURCE_KEY_LENGTH:
        return False
    # ``fullmatch``, not ``match``: ``$`` also matches before a final newline,
    # and the rendered key is the broker's lease identity, so a key with one
    # appended is a second lease on one resource (``FR-25``).
    return RESOURCE_KEY_PATTERN.fullmatch(value) is not None


def split_resource_key(key: str) -> tuple[tuple[str, str], ...]:
    """Split a resource key into its ``(kind, id)`` segments.

    Raises :class:`RegistryValidationError` when the key is malformed, rather
    than returning a partial parse: a half-understood key is how one resource
    ends up with two lease identities (``FR-25``).
    """
    if not is_resource_key(key):
        raise RegistryValidationError(
            f"malformed resource key {key!r}; expected kind:id(/kind:id)* "
            f"of at most {MAX_RESOURCE_KEY_LENGTH} characters (FR-34)"
        )
    segments: list[tuple[str, str]] = []
    for segment in key.split("/"):
        kind, _, identifier = segment.partition(":")
        segments.append((kind, identifier))
    return tuple(segments)


def template_placeholders(template: str) -> tuple[str, ...]:
    """Return the argument names a ``resource_key_template`` substitutes.

    Order of first appearance, duplicates collapsed. Used by the action-class
    loader to prove every placeholder is a declared argument (``FR-34``).
    """
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER_PATTERN.finditer(template):
        seen.setdefault(match.group(1), None)
    return tuple(seen)


def render_template(
    template: str,
    arguments: Mapping[str, Any],
    enumerations: Mapping[str, Collection[str]] | None = None,
) -> str:
    """Render ``template`` against ``arguments`` and validate the result.

    ``FR-25`` makes the rendered key the broker's lease identity, so this
    function fails closed at every step rather than producing a key that merely
    looks plausible:

    * a placeholder with no matching argument is an error, never an empty
      string -- ``service:/target:production`` would silently collide with every
      other service;
    * a value that is not a string or integer is an error, so a nested object
      or a boolean cannot be stringified into an identifier;
    * the rendered key must match :data:`RESOURCE_KEY_PATTERN`;
    * when ``enumerations`` are supplied, every segment whose *kind* is
      enumerated must carry an enumerated *id* (``FR-34``, scenario ``A-35``).

    ``enumerations`` is keyed by resource-key *kind* (``"service"``), which is
    the same token that appears in the key itself.
    """
    if not isinstance(template, str) or not template:
        raise RegistryValidationError("resource_key_template must be a non-empty string")

    rendered = template
    for name in template_placeholders(template):
        if name not in arguments:
            raise RegistryValidationError(
                f"resource_key_template {template!r} references argument {name!r}, "
                "which the call does not supply (FR-34)"
            )
        value = arguments[name]
        if isinstance(value, bool) or not isinstance(value, _RENDERABLE_TYPES):
            raise RegistryValidationError(
                f"argument {name!r} has type {type(value).__name__}, which is not a "
                "resource identifier; only strings and integers may be substituted"
            )
        text = str(value)
        if not _ID_PATTERN.fullmatch(text):
            raise RegistryValidationError(
                f"argument {name!r} value {text!r} is not a resource identifier "
                f"(expected {_ID_PATTERN.pattern})"
            )
        rendered = rendered.replace("{" + name + "}", text)

    if not is_resource_key(rendered):
        raise RegistryValidationError(
            f"resource_key_template {template!r} rendered {rendered!r}, which is not a "
            "resource key of the form kind:id(/kind:id)* (FR-34)"
        )

    if enumerations is not None:
        for kind, identifier in split_resource_key(rendered):
            allowed = enumerations.get(kind)
            if allowed is not None and identifier not in allowed:
                raise RegistryValidationError(
                    f"resource key {rendered!r} names {kind}:{identifier!r}, which is not "
                    f"in the canonical {kind} enumeration (FR-34)"
                )
    return rendered


class ResourceKeyRegistry(RevalidatingModel):
    """The canonical enumerations every action class shares (``FR-34``).

    Signed and versioned alongside the action-class registry. Keys of
    :attr:`enumerations` are resource-key *kinds* -- the literal token before the
    colon in ``service:checkout`` -- so a policy author reading a rendered key
    can find the enumeration it came from without a naming convention to
    remember.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Read-only, and read-only all the way down. ``frozen=True`` stops
    #: attribute *assignment* only, so a plain ``dict`` here would let any code
    #: in the process widen a signed enumeration in place --
    #: ``registry.enumerations["target"] += ("Production",)`` -- without
    #: changing the registry digest and without leaving a record. That moves the
    #: control in the permissive direction, which is the ``MUT-30``/``T-17``
    #: target-aliasing defect reached from inside the runtime instead of from
    #: the document. The freeze must be deep because pydantic copies the outer
    #: mapping and passes its values through; it reuses the same machinery that
    #: closed the hole on ``Proposal.arguments`` and
    #: ``ActionClass.argument_schema``. ``validate_default`` is on so the empty
    #: default is frozen too: the shared ``ResourceKeyRegistry()`` that
    #: ``ActionClassRegistry`` falls back to would otherwise be one mutable
    #: dictionary behind every registry in the process.
    enumerations: Annotated[
        Mapping[str, tuple[str, ...]],
        FrozenMappingValidator,
        FrozenMappingSerializer,
    ] = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="after")
    def _check_enumerations(self) -> ResourceKeyRegistry:
        """Reject a catalogue that cannot produce well-formed keys.

        A malformed enumeration member is worse than a missing one: it would be
        accepted into an argument schema and then fail at lease time, after the
        decision was already recorded as ``ALLOW``.
        """
        for kind, members in self.enumerations.items():
            if not _KIND_PATTERN.fullmatch(kind):
                raise ValueError(
                    f"resource kind {kind!r} is malformed; expected {_KIND_PATTERN.pattern} (FR-34)"
                )
            if not members:
                raise ValueError(
                    f"resource kind {kind!r} enumerates nothing; an empty enumeration "
                    "matches no value and would deny every call that uses it (FR-34)"
                )
            if len(set(members)) != len(members):
                raise ValueError(f"resource kind {kind!r} has duplicate members (FR-34)")
            for member in members:
                if not _ID_PATTERN.fullmatch(member):
                    raise ValueError(
                        f"resource kind {kind!r} member {member!r} is not a resource "
                        f"identifier (expected {_ID_PATTERN.pattern}) (FR-34)"
                    )
        return self

    # -- lookup ---------------------------------------------------------------

    def is_well_formed(self, key: str) -> bool:
        """Return ``True`` when ``key`` matches ``kind:id(/kind:id)*``.

        Shape only, by design: see :func:`is_resource_key`.

        Named ``is_well_formed`` rather than ``validate`` because
        :class:`pydantic.BaseModel` already carries a ``validate`` classmethod
        (deprecated, v1-compatible, returning an *instance*). Shadowing it with
        an instance method returning ``bool`` meant a caller reaching for
        pydantic's parsing got a shape check instead, with no error at either
        end. No alias is kept: an alias would keep the shadow, which was the
        defect.
        """
        return is_resource_key(key)

    def has_kind(self, kind: str) -> bool:
        """Return ``True`` when this registry enumerates ``kind``."""
        return kind in self.enumerations

    def enumeration(self, kind: str) -> tuple[str, ...]:
        """Return the canonical members of ``kind``.

        Raises rather than returning an empty tuple for an unknown kind: an
        empty answer reads as "nothing is allowed" at one call site and "no
        constraint" at another, and the difference is a gate.
        """
        try:
            return self.enumerations[kind]
        except KeyError as exc:
            raise RegistryValidationError(
                f"no canonical enumeration for resource kind {kind!r} (FR-34)"
            ) from exc

    def contains(self, kind: str, value: str) -> bool:
        """Return ``True`` when ``value`` is an enumerated member of ``kind``."""
        return value in self.enumerations.get(kind, ())

    def is_known(self, key: str) -> bool:
        """Return ``True`` when ``key`` is well-formed *and* fully enumerated.

        Segments whose kind this registry does not enumerate are accepted: the
        registry constrains what it knows, and a kind it never declared is the
        action class's business, not a silent allow.
        """
        if not is_resource_key(key):
            return False
        for kind, identifier in split_resource_key(key):
            if self.has_kind(kind) and not self.contains(kind, identifier):
                return False
        return True

    def assert_known(self, key: str) -> None:
        """Raise :class:`RegistryValidationError` unless :meth:`is_known`."""
        if not self.is_known(key):
            raise RegistryValidationError(
                f"resource key {key!r} is not canonical for this registry (FR-34)"
            )

    def render(self, template: str, arguments: Mapping[str, Any]) -> str:
        """Render ``template`` and check it against these enumerations."""
        return render_template(template, arguments, self.enumerations)
