"""At-most-once issuance, single use, and revocation (``FR-21``, ``FR-22``).

"Exactly one action" has two halves, and this module holds both ledgers.

The :class:`IssuanceLedger` is the minting half (``ADR-0008``): one evaluated
decision may be minted for at most once. Without it, ``TokenService.issue``
called twice with identical arguments yields two distinct ``token_id`` values
that both verify and both spend as a first use against the same envelope.

The :class:`NonceStore` is the spending half. A short-lived, digest-bound token
still authorises one execution *repeatedly* unless something remembers that it
was spent, and ``FR-22`` fixes two properties of that memory:

1. Consumption is **atomic**: the check and the record are one step, or two
   brokers racing on the same token both win.
2. Duplicate delivery is **distinguished from replay**. A network retry that
   redelivers the identical envelope to the same broker inside the token's
   lifetime is benign and must not page anyone; the same token arriving with a
   different envelope, or at a different broker, is an attack signal and must.
   Collapsing the two either drowns the signal or trains operators to ignore it.

Revocation (``FR-21``, ``FR-48``) is the operator's lever between issuance and
expiry. It covers ``key_id`` as well as ``token_id`` because key compromise
invalidates every token minted under that key, and enumerating them is neither
possible nor fast enough.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final, Protocol, runtime_checkable

from neuroharness import defaults
from neuroharness.errors import FailClosedError
from neuroharness.models.common import Digest
from neuroharness.reason import ReasonName

__all__ = [
    "ConsumeOutcome",
    "NonceEntry",
    "NonceStore",
    "InMemoryNonceStore",
    "DuplicateIssuanceError",
    "IssuanceEntry",
    "IssuanceLedger",
    "InMemoryIssuanceLedger",
    "RevocationScope",
    "RevocationList",
    "InMemoryRevocationList",
    "DEFAULT_RETENTION_TTL_MULTIPLE",
]

#: Consumed nonces are remembered for this multiple of the token lifetime.
#: One lifetime would already be sound - the token service verifies expiry
#: before it consumes, so an entry older than the lifetime can only be reached
#: by a token that is already refused - but the store must stay safe when used
#: on its own, and forgetting a nonce is indistinguishable from never having
#: seen it. The multiple is the margin; the constructor parameter is the knob.
DEFAULT_RETENTION_TTL_MULTIPLE: Final[int] = 4


class ConsumeOutcome(str, Enum):
    """What the store saw when a token was presented for consumption."""

    #: The token had not been consumed. This is the only outcome that dispatches.
    FIRST_USE = "first_use"
    #: The identical delivery arrived again, in time, at the same broker
    #: (``FR-22``). Recorded, not alerted; it authorises nothing further.
    DUPLICATE_DELIVERY = "duplicate_delivery"
    #: Anything else: a different envelope, a different broker, or too late.
    REPLAY = "replay"

    @property
    def permits_dispatch(self) -> bool:
        """Only a first use may execute. A duplicate delivery is answered from
        the prior outcome, never by executing a second time."""
        return self is ConsumeOutcome.FIRST_USE

    @property
    def is_replay(self) -> bool:
        """Replay is the alerting condition (``MUT-09``, evaluation plan §4)."""
        return self is ConsumeOutcome.REPLAY


@dataclass(frozen=True, slots=True)
class NonceEntry:
    """What the store remembers about a consumed token.

    The envelope digest and broker identity are kept because they are precisely
    what distinguishes a retry from a replay; the delivery count is kept because
    "how many times" is the first question asked during an incident.
    """

    token_id: str
    envelope_digest: Digest
    broker_id: str
    first_consumed_at: datetime
    delivery_count: int = 1


@runtime_checkable
class NonceStore(Protocol):
    """Atomic single-use ledger for token identifiers."""

    def consume(
        self,
        token_id: str,
        *,
        envelope_digest: Digest,
        broker_id: str,
        now: datetime,
    ) -> ConsumeOutcome:
        """Atomically record ``token_id`` as spent and classify the attempt.

        ``now`` is supplied by the caller's injected clock: the store never
        reads the wall clock, so a replayed decision classifies identically to
        the original (``FR-71``).
        """
        ...

    def entry_for(self, token_id: str) -> NonceEntry | None:
        """Return what is known about a consumed token, for records and alerts."""
        ...


class InMemoryNonceStore:
    """Process-local nonce store guarded by a lock.

    The lock is the whole point: ``FR-22`` calls for an atomic conditional
    insert, and in one process that is a mutex around the read-decide-write. The
    PostgreSQL implementation replaces it with a unique constraint on
    ``token_id``, with the same contract and the same outcomes, so the tests
    written against this class are the tests that describe that one too.
    """

    __slots__ = ("_entries", "_lock", "_ttl_seconds", "_retention_seconds")

    def __init__(
        self,
        *,
        ttl_seconds: int = defaults.DEFAULT_TOKEN_TTL_SECONDS,
        retention_seconds: int | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("nonce store ttl_seconds must be positive")
        resolved_retention = (
            ttl_seconds * DEFAULT_RETENTION_TTL_MULTIPLE
            if retention_seconds is None
            else retention_seconds
        )
        if resolved_retention < ttl_seconds:
            raise ValueError("retention_seconds must be at least ttl_seconds")
        self._entries: dict[str, NonceEntry] = {}
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        self._retention_seconds = resolved_retention

    @property
    def ttl_seconds(self) -> int:
        """The window inside which a redelivery can still be benign."""
        return self._ttl_seconds

    def consume(
        self,
        token_id: str,
        *,
        envelope_digest: Digest,
        broker_id: str,
        now: datetime,
    ) -> ConsumeOutcome:
        moment = now.astimezone(timezone.utc)
        with self._lock:
            self._purge(moment)
            existing = self._entries.get(token_id)
            if existing is None:
                self._entries[token_id] = NonceEntry(
                    token_id=token_id,
                    envelope_digest=envelope_digest,
                    broker_id=broker_id,
                    first_consumed_at=moment,
                )
                return ConsumeOutcome.FIRST_USE

            # Every field must match for the attempt to be a retry of the same
            # delivery. A same-token/different-envelope presentation is the
            # substitution attack MUT-10 describes, arriving one step later.
            identical = (
                existing.envelope_digest == envelope_digest and existing.broker_id == broker_id
            )
            in_window = moment - existing.first_consumed_at <= timedelta(
                seconds=self._ttl_seconds
            )
            self._entries[token_id] = replace(
                existing, delivery_count=existing.delivery_count + 1
            )
            if identical and in_window:
                return ConsumeOutcome.DUPLICATE_DELIVERY
            return ConsumeOutcome.REPLAY

    def entry_for(self, token_id: str) -> NonceEntry | None:
        with self._lock:
            return self._entries.get(token_id)

    def _purge(self, now: datetime) -> None:
        """Drop entries too old to be reachable by any unexpired token.

        Called under the lock. Bounded memory matters, but it is bounded by a
        retention window strictly longer than a token can live, never by a count
        limit: evicting the oldest entries under pressure would make replay
        succeed exactly when the system is busiest.
        """
        horizon = now - timedelta(seconds=self._retention_seconds)
        stale = [k for k, v in self._entries.items() if v.first_consumed_at < horizon]
        for key in stale:
            del self._entries[key]


class RevocationScope(str, Enum):
    """Whether a single token or an entire key was revoked."""

    TOKEN = "token"
    KEY = "key"


@runtime_checkable
class RevocationList(Protocol):
    """Operator revocation of tokens and keys (``FR-21``, ``FR-48``)."""

    def is_revoked(self, *, token_id: str, key_id: str) -> bool:
        """The gate the broker consults before accepting a token."""
        ...

    def revocation_scope(self, *, token_id: str, key_id: str) -> RevocationScope | None:
        """Why it was refused, for the record. ``None`` when not revoked.

        The boolean answers "may this execute"; the scope answers "what do we
        tell the operator", and a refusal nobody can explain is a refusal nobody
        can act on (``NFR-20``).
        """
        ...


class InMemoryRevocationList:
    """Process-local revocation list guarded by a lock.

    Revocations are append-only here. Un-revoking is deliberately absent: it
    would be a loosening operation, and ``FR-48`` puts loosening behind
    two-principal override authority rather than behind a method call.
    """

    __slots__ = ("_tokens", "_keys", "_lock")

    def __init__(self) -> None:
        self._tokens: dict[str, str | None] = {}
        self._keys: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def revoke_token(self, token_id: str, *, reason: str | None = None) -> None:
        """Revoke one token, by identifier."""
        with self._lock:
            self._tokens[token_id] = reason

    def revoke_key(self, key_id: str, *, reason: str | None = None) -> None:
        """Revoke every token minted under ``key_id``.

        Key compromise cannot be handled token by token: the set of affected
        tokens is unbounded and partly unknown, so the key is the unit.
        """
        with self._lock:
            self._keys[key_id] = reason

    def is_revoked(self, *, token_id: str, key_id: str) -> bool:
        return self.revocation_scope(token_id=token_id, key_id=key_id) is not None

    def revocation_scope(self, *, token_id: str, key_id: str) -> RevocationScope | None:
        with self._lock:
            if token_id in self._tokens:
                return RevocationScope.TOKEN
            if key_id in self._keys:
                return RevocationScope.KEY
            return None

    def reason_for(self, *, token_id: str, key_id: str) -> str | None:
        """The operator's recorded reason, when one was given."""
        with self._lock:
            if token_id in self._tokens:
                return self._tokens[token_id]
            if key_id in self._keys:
                return self._keys[key_id]
            return None

    @property
    def revoked_token_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._tokens)

    @property
    def revoked_key_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._keys)


class DuplicateIssuanceError(FailClosedError):
    """A second token was requested for a decision that already has one.

    ``ADR-0008`` states that an approval means exactly one action. A token is
    the only artefact that authorises execution (``FR-20``), so "one evaluation,
    one authorisation" holds only if minting is guarded the way spending is:
    without it, two ``issue`` calls with identical arguments produce two
    different ``token_id`` values, both of which verify and both of which
    consume as a first use against the same envelope digest.

    The refusal is reported as ``HARNESS_UNHEALTHY`` rather than
    ``TOKEN_INVALID``: nothing is wrong with any token, and the closed
    ``TokenInvalidReason`` catalogue in the decision-record schema describes the
    broker's refusals, not the mint's. ``HARNESS_UNHEALTHY`` is also an
    infrastructure reason, so it can never escalate to a human approval -
    correctly, because no human can vouch for a harness that just tried to
    authorise one decision twice.
    """

    reason_name = ReasonName.HARNESS_UNHEALTHY


@dataclass(frozen=True, slots=True)
class IssuanceEntry:
    """The single issuance a decision is allowed.

    ``envelope_digest`` is kept because it answers the question an operator asks
    when a duplicate is refused: was the second request for the same action, or
    for a different one wearing the same decision id?
    """

    tenant_id: str
    decision_id: str
    token_id: str
    envelope_digest: Digest
    issued_at: datetime


@runtime_checkable
class IssuanceLedger(Protocol):
    """Atomic at-most-once ledger for token *minting*, keyed per decision.

    The counterpart of :class:`NonceStore`: that one makes a token spendable
    once, this one makes a decision mintable once. Both exist because either
    alone leaves the other half of "exactly one action" to caller discipline,
    and ``ADR-0008`` does not rest on discipline.

    The key is ``(tenant_id, decision_id)``. ``decision_id`` alone would let one
    tenant's identifier collide with another's and deny a legitimate mint, which
    is a fail-closed outage caused by an unrelated tenant.
    """

    def claim(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        token_id: str,
        envelope_digest: Digest,
        now: datetime,
    ) -> IssuanceEntry | None:
        """Atomically claim ``(tenant_id, decision_id)`` for ``token_id``.

        Returns ``None`` when the claim succeeded, and the :class:`IssuanceEntry`
        that already holds the key when it did not. The conditional insert is one
        step, exactly as in :meth:`NonceStore.consume`: a read followed by a
        write would let two threads racing on one decision both observe "unheld"
        and both mint.

        ``now`` comes from the caller's injected clock so that a replayed
        decision (``FR-71``) claims with the record's own timestamp.
        """
        ...

    def entry_for(self, *, tenant_id: str, decision_id: str) -> IssuanceEntry | None:
        """Return the issuance recorded for a decision, for records and alerts."""
        ...


class InMemoryIssuanceLedger:
    """Process-local issuance ledger guarded by a lock.

    The lock makes :meth:`claim` the atomic conditional insert the protocol
    requires. The PostgreSQL implementation replaces it with a unique constraint
    on ``(tenant_id, decision_id)``, with the same contract and the same return
    convention, so the tests written against this class describe that one too.

    Entries are **never** purged, unlike :class:`InMemoryNonceStore`. That
    asymmetry is deliberate. A nonce may be forgotten once no unexpired token
    could still carry it, because the token service refuses an expired token
    before it reaches the store. An issuance has no such backstop: forgetting it
    is indistinguishable from never having minted, so a purge would re-open the
    second-mint path the moment the first token expired - and a second mint is a
    *fresh* token with a fresh lifetime, which is the whole defect.
    """

    __slots__ = ("_entries", "_lock")

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], IssuanceEntry] = {}
        self._lock = threading.Lock()

    def claim(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        token_id: str,
        envelope_digest: Digest,
        now: datetime,
    ) -> IssuanceEntry | None:
        key = (tenant_id, decision_id)
        moment = now.astimezone(timezone.utc)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                return existing
            self._entries[key] = IssuanceEntry(
                tenant_id=tenant_id,
                decision_id=decision_id,
                token_id=token_id,
                envelope_digest=envelope_digest,
                issued_at=moment,
            )
            return None

    def entry_for(self, *, tenant_id: str, decision_id: str) -> IssuanceEntry | None:
        with self._lock:
            return self._entries.get((tenant_id, decision_id))

    @property
    def issued_count(self) -> int:
        """How many distinct decisions have been minted for, for assertions."""
        with self._lock:
            return len(self._entries)
