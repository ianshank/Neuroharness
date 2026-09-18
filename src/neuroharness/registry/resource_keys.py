"""The signed resource-key registry (``FR-34``).

Policy must never compare free strings. ``FR-34`` requires one shared,
signed catalogue of canonical resource identifiers and enumerations so that
argument schemas, temporal properties, broker leases and reason-code subjects
all name the *same* things the *same* way. Two spellings of one production
target are two policies, and only one of them was reviewed (scenario ``A-35``).

A resource key is ``kind:id`` or a ``/``-joined path of such segments, for
example ``service:checkout/target:production``. The grammar is deliberately
narrow:

* it is a subset of the reason-code subject charset in
  :data:`neuroharness.reason.PARAMETERISED_REASONS` (section 5.6), because a
  resource key is emitted verbatim as the subject of
  ``RESOURCE_BUSY:<resource_key>`` and ``EFFECT_MISMATCH:<resource_key>``. A key
  that cannot be rendered as a reason code is a key whose contention or effect
  mismatch could not be recorded, and an unrecorded decision was not made
  (Constitution Art. III);
* it excludes ``_`` and any character outside ``[A-Za-z0-9.-]`` in identifiers
  for the same reason;
* it is length-bounded so the rendered reason code stays inside the limit
  :class:`neuroharness.reason.ReasonCode` enforces.

Rendering a key from arguments (``FR-25``) happens here too, so the broker's
lease identity and the registry's declared template can never drift apart.
"""

from __future__ import annotations

import re
from typing import Any, Collection, Final, Mapping

from pydantic import BaseModel, ConfigDict, model_validator

from neuroharness.errors import RegistryValidationError

__all__ = [
    "RESOURCE_KEY_PATTERN",
    "MAX_RESOURCE_KEY_LENGTH",
    "ResourceKeyRegistry",
    "is_resource_key",
    "template_placeholders",
    "render_template",
]

#: A resource-key *kind* (the part before the colon): lowercase, hyphenated.
_KIND = r"[a-z][a-z0-9-]{0,31}"

#: A resource-key *identifier* (the part after the colon). No underscore and no
#: colon: both would make the key unparseable as a reason-code subject.
_ID = r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}"

#: ``kind:id(/kind:id)*`` -- the whole grammar, anchored.
RESOURCE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^{_KIND}:{_ID}(?:/{_KIND}:{_ID})*$"
)

_KIND_PATTERN: Final[re.Pattern[str]] = re.compile(rf"^{_KIND}$")
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(rf"^{_ID}$")

#: Upper bound on a rendered resource key. Chosen to fit inside the reason-code
#: subject limit (section 5.6) so ``RESOURCE_BUSY:<resource_key>`` is always
#: constructible.
MAX_RESOURCE_KEY_LENGTH: Final[int] = 158

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
    return RESOURCE_KEY_PATTERN.match(value) is not None


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
        if not _ID_PATTERN.match(text):
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


class ResourceKeyRegistry(BaseModel):
    """The canonical enumerations every action class shares (``FR-34``).

    Signed and versioned alongside the action-class registry. Keys of
    :attr:`enumerations` are resource-key *kinds* -- the literal token before the
    colon in ``service:checkout`` -- so a policy author reading a rendered key
    can find the enumeration it came from without a naming convention to
    remember.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enumerations: dict[str, tuple[str, ...]] = {}

    @model_validator(mode="after")
    def _check_enumerations(self) -> "ResourceKeyRegistry":
        """Reject a catalogue that cannot produce well-formed keys.

        A malformed enumeration member is worse than a missing one: it would be
        accepted into an argument schema and then fail at lease time, after the
        decision was already recorded as ``ALLOW``.
        """
        for kind, members in self.enumerations.items():
            if not _KIND_PATTERN.match(kind):
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
                if not _ID_PATTERN.match(member):
                    raise ValueError(
                        f"resource kind {kind!r} member {member!r} is not a resource "
                        f"identifier (expected {_ID_PATTERN.pattern}) (FR-34)"
                    )
        return self

    # -- lookup ---------------------------------------------------------------

    def validate(self, key: str) -> bool:  # noqa: A003 - the FR-34 vocabulary word
        """Return ``True`` when ``key`` matches ``kind:id(/kind:id)*``.

        Shape only, by design: see :func:`is_resource_key`.
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
