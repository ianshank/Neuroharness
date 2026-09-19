"""Deployment settings.

Settings sit between :mod:`neuroharness.defaults` (documented defaults) and the
signed action-class registry (per-class authority). They carry deployment-wide
choices an operator makes once: strict or permissive handling of unregistered
action classes, retention, the signing algorithm, rate limits.

Values are read from the environment with a configurable prefix so that a single
host can run several tenants' harnesses without them sharing configuration by
accident. Unknown or malformed values raise :class:`ConfigurationError` at start
rather than degrading at runtime.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator

from neuroharness import defaults
from neuroharness.errors import ConfigurationError
from neuroharness.models.common import RevalidatingModel

__all__ = ["UnregisteredClassPolicy", "SigningAlgorithm", "Settings"]

_ENV_PREFIX = "NEUROHARNESS_"


class UnregisteredClassPolicy(str, Enum):
    """What to do with a ``(tool, intent)`` pair the registry does not name.

    ``STRICT`` is the default and abstains (``FR-31``). ``PERMISSIVE`` evaluates
    policy only, and exists for deployments mid-migration; it is a deliberate,
    recorded reduction in coverage, not a convenience.
    """

    STRICT = "strict"
    PERMISSIVE = "permissive"


class SigningAlgorithm(str, Enum):
    """Token signing algorithm (``ADR-0019``)."""

    ECDSA_P256 = "ecdsa-p256"
    ED25519 = "ed25519"
    HMAC_SHA256 = "hmac-sha256"


class Settings(RevalidatingModel):
    """Immutable, validated deployment settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    unregistered_class_policy: UnregisteredClassPolicy = UnregisteredClassPolicy.STRICT

    #: ``ADR-0019`` decision 1: ECDSA P-256 by default, Ed25519 where the key
    #: service supports it, HMAC-SHA256 *only* for single-process deployments.
    #: The default is the deployment shape the harness is designed for, in which
    #: the token service and the broker are separate processes. Defaulting to
    #: HMAC there would hand the broker the key that mints the tokens it is
    #: supposed to only verify, which dissolves the separation the token exists
    #: to create -- and it would do so silently, on a deployment nobody
    #: configured wrongly. A build with no P-256 signer registered refuses to
    #: start (:func:`neuroharness.tokens.signer.signer_for_settings`); it does
    #: not fall back.
    signing_algorithm: SigningAlgorithm = SigningAlgorithm.ECDSA_P256

    token_ttl_seconds: int = Field(default=defaults.DEFAULT_TOKEN_TTL_SECONDS, ge=1, le=3600)
    bundle_grace_seconds: int = Field(default=defaults.DEFAULT_BUNDLE_GRACE_SECONDS, ge=0, le=3600)
    approval_ttl_seconds: int = Field(default=defaults.DEFAULT_APPROVAL_TTL_SECONDS, ge=60)
    lease_timeout_seconds: int = Field(default=defaults.DEFAULT_LEASE_TIMEOUT_SECONDS, ge=1)
    repair_budget: int = Field(
        default=defaults.DEFAULT_REPAIR_BUDGET, ge=0, le=defaults.MAX_REPAIR_BUDGET
    )
    new_actions_per_hour: int = Field(default=defaults.DEFAULT_NEW_ACTIONS_PER_HOUR, ge=1)
    record_retention_days: int = Field(default=defaults.DEFAULT_RECORD_RETENTION_DAYS, ge=1)

    log_level: str = Field(default="INFO")
    log_json: bool = True

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level: {value!r}")
        return level

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, prefix: str = _ENV_PREFIX
    ) -> Settings:
        """Build settings from environment variables named ``<prefix><FIELD>``.

        Unset fields fall back to the documented defaults. A malformed value is
        a startup failure, never a silent fallback: a harness running with
        settings the operator did not intend is a harness nobody can reason
        about.

        So is a *misspelled* name. Reading only the known fields would let
        ``NEUROHARNESS_REPAIR_BUDGT=1`` sit in a deployment manifest, pass review
        and do nothing, while the default quietly stayed in force -- an operator
        would believe a control is configured that is not, which is the one
        belief a fail-closed harness cannot afford. ``extra="forbid"`` on the
        model cannot catch it, because a name nobody recognises never reaches
        the model at all. So the whole ``prefix`` namespace belongs to settings:
        every variable in it must name a field this build reads, and any other
        is a startup failure naming both the offending variable and the settings
        that do exist.

        There are no reserved names inside the namespace today. A prefixed
        variable that is deliberately *not* a setting would have to be declared
        here, which is the point: the exemption would be reviewable instead of
        being an environment nobody audited.

        ``prefix`` may not be empty, for the same reason stated the other way
        round: claiming a namespace is only safe when there is one. An empty
        prefix would make every variable in the process a candidate setting and
        refuse the whole environment.
        """
        if not prefix:
            raise ConfigurationError(
                "from_env needs a non-empty prefix; an empty one would claim the "
                "whole environment as settings and refuse every variable in it"
            )
        env = os.environ if environ is None else environ
        known = {f"{prefix}{name.upper()}": name for name in cls.model_fields}

        unknown = sorted(key for key in env if key.startswith(prefix) and key not in known)
        if unknown:
            raise ConfigurationError(
                f"unknown setting(s) in the {prefix} namespace: {', '.join(unknown)}; "
                f"this build reads: {', '.join(sorted(known))}"
            )

        raw: dict[str, Any] = {
            name: _coerce(cls, name, env[key], key)
            for key, name in known.items()
            if key in env
        }
        try:
            return cls(**raw)
        except ValidationError as exc:
            raise ConfigurationError(f"invalid settings from environment: {exc}") from exc


def _coerce(model: type[Settings], field: str, value: str, key: str) -> Any:
    annotation = model.model_fields[field].annotation
    if annotation is bool:
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ConfigurationError(f"{key} is not a boolean: {value!r}")
    if annotation is int:
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigurationError(f"{key} is not an integer: {value!r}") from exc
    return value
