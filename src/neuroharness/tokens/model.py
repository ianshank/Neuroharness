"""The decision token payload and its signed envelope (``FR-20``).

The token is the only artefact that can authorise execution, so its shape is
fixed by the specification rather than by convenience: ``FR-20`` names the
fourteen fields below and calls that list authoritative. Two of those fields
exist purely to close attacks that a digest alone cannot:

* ``verdict`` and ``mode`` stop a shadow token being cryptographically
  indistinguishable from an allow token, which is what lets the broker refuse a
  token minted before a class was promoted to ``enforce`` (``ADR-0016``).
* ``record_hash`` binds the token to the evaluation record it was issued
  against, so a token cannot exist without durable evidence (``INV-05``).

The token deliberately has **no signature field**. A payload that can carry its
own signature is a payload that can be verified against itself; keeping the
signature in :class:`SignedToken` means every verification path must go through
the signer, and it means a ``token_issued`` record built from this model cannot
accidentally persist key material (``NFR-18``, decision-record schema).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ConfigDict, Field, field_validator, model_validator

from neuroharness.config import SigningAlgorithm
from neuroharness.models.common import Digest, Mode, RevalidatingModel, Verdict

__all__ = [
    "DecisionToken",
    "SignedToken",
    "TOKEN_SIGNING_DOMAIN",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_TENANT_ID_LENGTH",
    "MAX_KEY_ID_LENGTH",
    "MAX_SIGNATURE_LENGTH",
]

#: Prepended to the canonical JSON before signing. Domain separation means a
#: signature produced over some other harness structure (a bundle manifest, a
#: checkpoint) can never be replayed as a token signature, even if a future
#: structure happens to canonicalise to the same bytes. The version suffix makes
#: a payload-format change a verification failure rather than a silent
#: reinterpretation of old signatures.
TOKEN_SIGNING_DOMAIN: Final[bytes] = b"neuroharness/decision-token/v1\n"

#: Bounds mirror ``docs/sdd/schemas/decision-record.schema.json``. They are here
#: so an oversized identifier is refused at construction rather than at the
#: audit boundary, where refusing it is too late to prevent the decision.
MAX_IDENTIFIER_LENGTH: Final[int] = 128
MAX_TENANT_ID_LENGTH: Final[int] = 64
MAX_KEY_ID_LENGTH: Final[int] = 64
MAX_SIGNATURE_LENGTH: Final[int] = 4096


def _utc(value: datetime) -> datetime:
    """Normalise to aware UTC, refusing naive datetimes.

    A naive timestamp in a token is an expiry nobody can evaluate, and an expiry
    nobody can evaluate is an unbounded time-of-check/time-of-use window.
    """
    if value.tzinfo is None:
        raise ValueError("token timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _isoformat(value: datetime) -> str:
    """RFC 3339 in UTC with a ``Z`` suffix, so the byte encoding is stable."""
    return _utc(value).isoformat().replace("+00:00", "Z")


class DecisionToken(RevalidatingModel):
    """A signed authorisation to execute exactly one evaluated envelope.

    Frozen because a token is evidence: the object the broker verifies must be
    the object the token service signed, and a mutable model makes that a
    convention rather than a guarantee.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    token_id: str = Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH)
    decision_id: str = Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH)
    envelope_digest: Digest
    proposal_digest: Digest
    policy_bundle_digest: Digest
    record_hash: Digest
    tenant_id: str = Field(min_length=1, max_length=MAX_TENANT_ID_LENGTH)
    mode: Mode
    verdict: Verdict
    issued_at: datetime
    expires_at: datetime
    key_id: str = Field(min_length=1, max_length=MAX_KEY_ID_LENGTH)
    key_alg: SigningAlgorithm
    shadow: bool

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _expiry_after_issue(self) -> DecisionToken:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """The exact ``FR-20`` payload as JSON-ready primitives.

        Enum members are unwrapped to their values explicitly rather than
        relying on ``str`` subclassing, so the encoding does not change if the
        enum base ever does. This is also the payload of the ``token_issued``
        evidence record (``FR-23``): note that it contains no signature.
        """
        return {
            "token_id": self.token_id,
            "decision_id": self.decision_id,
            "envelope_digest": str(self.envelope_digest),
            "proposal_digest": str(self.proposal_digest),
            "policy_bundle_digest": str(self.policy_bundle_digest),
            "record_hash": str(self.record_hash),
            "tenant_id": self.tenant_id,
            "mode": self.mode.value,
            "verdict": self.verdict.value,
            "issued_at": _isoformat(self.issued_at),
            "expires_at": _isoformat(self.expires_at),
            "key_id": self.key_id,
            "key_alg": self.key_alg.value,
            "shadow": self.shadow,
        }

    def signing_payload(self) -> bytes:
        """Return the bytes that are signed and verified.

        Sorted keys and compact separators give one encoding per token, so the
        broker reconstructs the signed bytes from the fields it received rather
        than trusting a serialisation the presenter chose. Every field is
        covered: a token whose ``mode`` could be edited without breaking the
        signature would defeat ``ADR-0016`` entirely.
        """
        body = json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return TOKEN_SIGNING_DOMAIN + body.encode("utf-8")

    def is_expired(self, now: datetime) -> bool:
        """True once ``now`` has reached ``expires_at``.

        The boundary is inclusive: at exactly ``expires_at`` the token is spent.
        Fail closed at the edge rather than granting one more instant.
        """
        return _utc(now) >= self.expires_at

    @property
    def authorises_execution(self) -> bool:
        """Whether this token's own verdict permits execution.

        Not sufficient on its own: the broker also compares the token's mode and
        verdict against the class's *current* registry mode (``FR-21``).
        """
        return self.verdict.permits_execution


class SignedToken(RevalidatingModel):
    """A token together with the signature that travels beside it.

    The split exists so that :class:`DecisionToken` can be recorded, logged and
    compared without ever carrying key material, while the wire form stays one
    object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: DecisionToken
    signature: str = Field(min_length=1, max_length=MAX_SIGNATURE_LENGTH)

    @property
    def token_id(self) -> str:
        return self.token.token_id

    @property
    def key_id(self) -> str:
        return self.token.key_id
