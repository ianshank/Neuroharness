"""The token service: issue, verify, consume (``FR-20``-``FR-23``).

This is the only component permitted to mint an authorisation, and the ordering
of its checks is part of the design rather than an implementation detail.

*Issue* refuses more often than it succeeds, on purpose. A token is the harness
saying "this exact envelope may run now", so every precondition that cannot be
re-checked later is checked here: the evaluation record must already be durable
(``INV-05``, ``FR-23``), a blocking mode must have produced an ``ALLOW``
(``FR-20``), and a halted class mints nothing at all (``ADR-0016``).

*Verify* reproduces the broker's obligations in ``FR-21`` and raises a distinct
typed error per failure, because the reason code is the record and
``TOKEN_INVALID`` on its own tells an operator nothing. Signature comes first:
until the signature verifies, no field in the token means anything, so checking
expiry or mode on an unverified payload would be reading attacker-supplied data
and reporting it as a harness finding.

*Consume* verifies and only then consumes, so a token that would have been
refused never burns its nonce - otherwise an attacker could spend a victim's
token by presenting it against the wrong envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, NoReturn

from neuroharness import defaults
from neuroharness.config import SigningAlgorithm
from neuroharness.errors import (
    ClassHaltedError,
    ConfigurationError,
    EvidenceUnavailableError,
    FailClosedError,
    TokenBundleStaleError,
    TokenConsumedError,
    TokenDigestMismatchError,
    TokenExpiredError,
    TokenModeMismatchError,
    TokenRevokedError,
    TokenSignatureError,
    TokenVerdictMismatchError,
)
from neuroharness.models.common import Digest, Mode, Verdict
from neuroharness.observability.logging import get_logger
from neuroharness.seams import Clock, IdGenerator
from neuroharness.tokens.model import DecisionToken, SignedToken
from neuroharness.tokens.nonce import ConsumeOutcome, NonceStore, RevocationList
from neuroharness.tokens.signer import Signer, verify_signature

__all__ = ["ConsumeResult", "TokenService", "MIN_TOKEN_TTL_SECONDS"]

#: A non-positive lifetime would mint a token that is expired on arrival, which
#: is an outage disguised as a security control. Refused at construction.
MIN_TOKEN_TTL_SECONDS: Final[int] = 1

_LOGGER = get_logger("neuroharness.tokens")

_EVENT_ISSUED = "token_issued"
_EVENT_ISSUE_REFUSED = "token_issue_refused"
_EVENT_VERIFY_REFUSED = "token_verify_refused"
_EVENT_CONSUME_REFUSED = "token_consume_refused"
_EVENT_CONSUMED = "token_consumed"
_EVENT_DUPLICATE = "token_duplicate_delivery"


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """The outcome of a consumption attempt that did not fail closed.

    A duplicate delivery is *not* an error, so it is returned rather than
    raised: the broker answers the retry from the original outcome. It is also
    not permission to execute again, which is why the caller is made to read
    :attr:`permits_dispatch` instead of assuming success (``FR-22``).
    """

    token: DecisionToken
    outcome: ConsumeOutcome

    @property
    def permits_dispatch(self) -> bool:
        return self.outcome.permits_dispatch

    @property
    def duplicate_delivery(self) -> bool:
        return self.outcome is ConsumeOutcome.DUPLICATE_DELIVERY


class TokenService:
    """Mints, verifies and spends decision tokens.

    Every dependency is injected, including time and identifiers: replay
    (``FR-71``) re-evaluates a recorded decision using the record's own
    timestamps, and a service that reached for :func:`datetime.now` could not be
    replayed at all (:mod:`neuroharness.seams`).
    """

    __slots__ = (
        "_signer",
        "_nonce_store",
        "_revocation_list",
        "_clock",
        "_id_generator",
        "_ttl_seconds",
        "_bundle_grace_seconds",
        "_logger",
    )

    def __init__(
        self,
        *,
        signer: Signer,
        nonce_store: NonceStore,
        revocation_list: RevocationList,
        clock: Clock,
        id_generator: IdGenerator,
        ttl_seconds: int = defaults.DEFAULT_TOKEN_TTL_SECONDS,
        bundle_grace_seconds: int = defaults.DEFAULT_BUNDLE_GRACE_SECONDS,
        logger: Any = None,
    ) -> None:
        if ttl_seconds < MIN_TOKEN_TTL_SECONDS:
            raise ConfigurationError(
                f"token ttl_seconds must be at least {MIN_TOKEN_TTL_SECONDS}"
            )
        if bundle_grace_seconds < 0:
            raise ConfigurationError("bundle_grace_seconds must not be negative")
        self._signer = signer
        self._nonce_store = nonce_store
        self._revocation_list = revocation_list
        self._clock = clock
        self._id_generator = id_generator
        self._ttl_seconds = ttl_seconds
        self._bundle_grace_seconds = bundle_grace_seconds
        self._logger = logger if logger is not None else _LOGGER

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @property
    def bundle_grace_seconds(self) -> int:
        return self._bundle_grace_seconds

    @property
    def signing_key_id(self) -> str:
        """The key new tokens are minted under. Changes on rotation."""
        return self._signer.key_id

    @property
    def signing_algorithm(self) -> SigningAlgorithm:
        return self._signer.algorithm

    # -- issue ---------------------------------------------------------------

    def issue(
        self,
        *,
        decision_id: str,
        envelope_digest: Digest,
        proposal_digest: Digest,
        policy_bundle_digest: Digest,
        record_hash: Digest | None,
        tenant_id: str,
        mode: Mode,
        verdict: Verdict,
        ttl_seconds: int | None = None,
    ) -> SignedToken:
        """Mint a token for one evaluated envelope.

        ``record_hash`` is typed optional so that a caller with no durable
        record can still call this and be refused, loudly and in one place,
        rather than being trusted to remember the ordering (``MUT-13``).
        """
        now = self._clock.now()
        context: dict[str, Any] = {
            "decision_id": decision_id,
            "tenant_id": tenant_id,
            "mode": mode.value,
            "verdict": verdict.value,
            "envelope_digest": str(envelope_digest),
        }

        if record_hash is None:
            # INV-05: the evidence is the decision. No record, no authority.
            self._refuse(
                _EVENT_ISSUE_REFUSED,
                EvidenceUnavailableError,
                "no evaluation record hash: a token may not precede its record",
                **context,
            )

        if mode is Mode.HALTED:
            # ADR-0016 §4: halted denies everything and issues no token.
            self._refuse(
                _EVENT_ISSUE_REFUSED,
                ClassHaltedError,
                "action class is halted; no token is issued in halted mode",
                **context,
            )

        # ADR-0016 §1: shadow and advisory issue a token for every verdict so
        # that the enforcing code path is the path shadow exercises. Only a
        # blocking mode requires ALLOW.
        shadow = not mode.blocks_on_verdict
        if not shadow and not verdict.permits_execution:
            self._refuse(
                _EVENT_ISSUE_REFUSED,
                TokenVerdictMismatchError,
                f"refusing to issue a token for verdict {verdict.value} in mode {mode.value}",
                **context,
            )

        lifetime = self._ttl_seconds if ttl_seconds is None else ttl_seconds
        if lifetime < MIN_TOKEN_TTL_SECONDS:
            raise ConfigurationError(
                f"token ttl_seconds must be at least {MIN_TOKEN_TTL_SECONDS}"
            )

        token = DecisionToken(
            token_id=self._id_generator.new_id(),
            decision_id=decision_id,
            envelope_digest=envelope_digest,
            proposal_digest=proposal_digest,
            policy_bundle_digest=policy_bundle_digest,
            record_hash=record_hash,
            tenant_id=tenant_id,
            mode=mode,
            verdict=verdict,
            issued_at=now,
            expires_at=now + timedelta(seconds=lifetime),
            key_id=self._signer.key_id,
            key_alg=self._signer.algorithm,
            shadow=shadow,
        )
        signature = self._signer.sign(token.signing_payload())
        self._logger.info(
            _EVENT_ISSUED,
            token_id=token.token_id,
            key_id=token.key_id,
            key_alg=token.key_alg.value,
            shadow=token.shadow,
            expires_at=token.expires_at.isoformat(),
            **context,
        )
        return SignedToken(token=token, signature=signature)

    # -- verify --------------------------------------------------------------

    def verify(
        self,
        signed: SignedToken,
        *,
        envelope_digest: Digest,
        tenant_id: str,
        current_mode: Mode,
        current_bundle_digest: Digest,
        now: datetime | None = None,
        grace_seconds: int | None = None,
    ) -> DecisionToken:
        """Re-check every binding the token claims, or fail closed.

        ``envelope_digest`` is the digest the broker recomputed from what it is
        about to execute, never the one the token carries: comparing a token to
        itself proves nothing (``FR-21``).
        """
        token = signed.token
        moment = self._clock.now() if now is None else now
        context: dict[str, Any] = {
            "token_id": token.token_id,
            "decision_id": token.decision_id,
            "tenant_id": token.tenant_id,
            "key_id": token.key_id,
            "mode": token.mode.value,
            "verdict": token.verdict.value,
            "current_mode": current_mode.value,
        }

        # 1. Signature. Nothing below may be trusted before this passes. An
        #    unknown key id raises from the signer rather than returning False;
        #    it is re-raised through the same recording path so that every
        #    refusal leaves evidence, whichever layer detected it.
        try:
            signature_valid = verify_signature(
                self._signer,
                key_id=token.key_id,
                key_alg=token.key_alg,
                payload=token.signing_payload(),
                signature=signed.signature,
            )
        except TokenSignatureError as exc:
            self._refuse(_EVENT_VERIFY_REFUSED, TokenSignatureError, str(exc), **context)
        if not signature_valid:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenSignatureError,
                "token signature does not verify",
                **context,
            )

        # 2. Revocation. An operator's refusal outranks the token's own claims,
        #    including its lifetime (FR-48).
        scope = self._revocation_list.revocation_scope(
            token_id=token.token_id, key_id=token.key_id
        )
        if scope is not None:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenRevokedError,
                f"token revoked by {scope.value}",
                revocation_scope=scope.value,
                **context,
            )

        # 3. Lifetime. Bounds the time-of-check/time-of-use gap.
        if token.is_expired(moment):
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenExpiredError,
                f"token expired at {token.expires_at.isoformat()}",
                **context,
            )

        # 4. Tenant. The closed reason catalogue has no tenant reason, so a
        #    cross-tenant presentation is recorded as the binding failure it is:
        #    this token does not authorise this execution.
        if token.tenant_id != tenant_id:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenDigestMismatchError,
                f"token is bound to tenant {token.tenant_id!r}, not {tenant_id!r}",
                presented_tenant_id=tenant_id,
                **context,
            )

        # 5. Envelope binding (MUT-10).
        if token.envelope_digest != envelope_digest:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenDigestMismatchError,
                "token envelope digest does not match the recomputed digest",
                token_envelope_digest=str(token.envelope_digest),
                presented_envelope_digest=str(envelope_digest),
                **context,
            )

        # 6. Mode (MUT-20). A token minted in shadow cannot straddle a promotion
        #    to enforce, and an enforce token cannot be spent after a demotion.
        if token.mode is not current_mode:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenModeMismatchError,
                f"token mode {token.mode.value} is not the class's current mode "
                f"{current_mode.value}",
                **context,
            )
        if token.shadow and current_mode.blocks_on_verdict:
            # Unreachable while modes agree; kept because the shadow flag is
            # what an operator reads, and a flag that is never independently
            # checked is a flag that can drift.
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenModeMismatchError,
                "shadow token presented for a class that now blocks on the verdict",
                **context,
            )

        # 7. Verdict, judged against the class's *current* mode (FR-21).
        if current_mode is Mode.ENFORCE and not token.verdict.permits_execution:
            self._refuse(
                _EVENT_VERIFY_REFUSED,
                TokenVerdictMismatchError,
                f"verdict {token.verdict.value} does not authorise execution under enforce",
                **context,
            )

        # 8. Policy bundle currency (FR-84). The service is not told when the
        #    transition happened, so the grace window is measured from issuance:
        #    a token issued under the superseded bundle gets at most
        #    ``grace_seconds`` of life, never more. Default grace is zero.
        grace = self._bundle_grace_seconds if grace_seconds is None else grace_seconds
        if token.policy_bundle_digest != current_bundle_digest:
            within_grace = grace > 0 and moment <= token.issued_at + timedelta(seconds=grace)
            if not within_grace:
                self._refuse(
                    _EVENT_VERIFY_REFUSED,
                    TokenBundleStaleError,
                    "token carries a superseded policy bundle digest",
                    token_bundle_digest=str(token.policy_bundle_digest),
                    current_bundle_digest=str(current_bundle_digest),
                    grace_seconds=grace,
                    **context,
                )

        return token

    # -- consume -------------------------------------------------------------

    def consume(
        self,
        signed: SignedToken,
        *,
        envelope_digest: Digest,
        broker_id: str,
        tenant_id: str,
        current_mode: Mode,
        current_bundle_digest: Digest,
        now: datetime | None = None,
        grace_seconds: int | None = None,
    ) -> ConsumeResult:
        """Verify, then atomically spend the token's nonce (``FR-22``).

        Verification precedes consumption so that a token which would be refused
        does not burn: otherwise presenting a stolen token against the wrong
        envelope would destroy a legitimate authorisation, turning a failed
        attack into a successful denial of service.
        """
        moment = self._clock.now() if now is None else now
        token = self.verify(
            signed,
            envelope_digest=envelope_digest,
            tenant_id=tenant_id,
            current_mode=current_mode,
            current_bundle_digest=current_bundle_digest,
            now=moment,
            grace_seconds=grace_seconds,
        )

        outcome = self._nonce_store.consume(
            token.token_id,
            envelope_digest=envelope_digest,
            broker_id=broker_id,
            now=moment,
        )
        context: dict[str, Any] = {
            "token_id": token.token_id,
            "decision_id": token.decision_id,
            "tenant_id": token.tenant_id,
            "broker_id": broker_id,
            "outcome": outcome.value,
        }
        if outcome.is_replay:
            self._refuse(
                _EVENT_CONSUME_REFUSED,
                TokenConsumedError,
                "token has already been consumed",
                **context,
            )
        if outcome is ConsumeOutcome.DUPLICATE_DELIVERY:
            # Benign retry: recorded so the timeline is complete, at info level
            # so it does not page anyone (FR-22).
            self._logger.info(_EVENT_DUPLICATE, **context)
        else:
            self._logger.info(_EVENT_CONSUMED, **context)
        return ConsumeResult(token=token, outcome=outcome)

    # -- internals -----------------------------------------------------------

    def _refuse(
        self,
        event: str,
        error_type: type[FailClosedError],
        message: str,
        **fields: Any,
    ) -> NoReturn:
        """Record the refusal with its reason code, then fail closed.

        Refusals are logged here and nowhere else, so no path can raise without
        leaving evidence (Constitution, Article IV). Only identifiers, digests
        and reasons are emitted; signature material is never passed in.
        """
        error = error_type(message)
        self._logger.warning(event, reason=error.reason_code.render(), detail=message, **fields)
        raise error
