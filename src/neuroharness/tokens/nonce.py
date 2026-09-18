"""At-most-once issuance, single use, and revocation (``FR-21``, ``FR-22``).

"Exactly one action" has two halves, and this module holds both ledgers.

The :class:`IssuanceLedger` is the minting half (``ADR-0008``): one evaluated
decision may be minted for at most once. Without it, ``TokenService.issue``
called twice with identical arguments yields two distinct ``token_id`` values
that both verify and both spend as a first use against the same envelope.

A ledger that is a *second* store beside the evidence chain has a window the
chain does not: the claim is taken, the ``token_issued`` record (``FR-23``) is
written afterwards, and a failure in between leaves a claim standing for a
token that was never returned. :class:`IssuanceEvidence` and
:meth:`InMemoryIssuanceLedger.claim_recorded` close that window by making the
record the claim - one step instead of two - rather than by compensating for it
afterwards; see the :class:`InMemoryIssuanceLedger` docstring for why a
compensating release cannot be made safe.

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
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final, Protocol, runtime_checkable

from neuroharness import defaults
from neuroharness.errors import FailClosedError
from neuroharness.models.common import Digest
from neuroharness.reason import ReasonName
from neuroharness.tokens.model import DecisionToken

__all__ = [
    "ConsumeOutcome",
    "NonceEntry",
    "NonceStore",
    "InMemoryNonceStore",
    "DuplicateIssuanceError",
    "IssuanceEntry",
    "IssuanceEvidence",
    "IssuanceLedger",
    "RecordingIssuanceLedger",
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
        moment = now.astimezone(UTC)
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

    **What it means now, and the one thing it no longer means.** On the recorded
    path (:meth:`InMemoryIssuanceLedger.claim_recorded`) this error is raised
    only when a durable ``token_issued`` record for the decision already exists,
    so it always names a decision that genuinely was authorised once. It used to
    have a second, indistinguishable cause: a claim left standing by an evidence
    outage, which wedged the decision permanently and reported the wedge as an
    outage of the wrong kind. That cause is gone - an evidence failure now
    reports ``EVIDENCE_UNAVAILABLE``, which is what actually happened and what a
    retry can clear.

    Whether a *correct control working* should keep declaring
    ``HARNESS_UNHEALTHY`` is a live question and the honest answer is probably
    no: it is an infrastructure reason, so an operator dashboard and the
    ``NFR-21`` correlated-failure alert read every duplicate refusal as harness
    ill-health. The fix is one catalogue member, and it is deliberately not made
    here: the reason catalogue is outside this change's scope, and swapping the
    name is a one-line edit once that member exists.
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

        The claim is taken on this ledger's own authority, so a caller that must
        do something durable *after* minting - ``FR-23``'s ``token_issued``
        record, for one - is responsible for the window between the two. A
        caller that cannot own that window wants
        :meth:`RecordingIssuanceLedger.claim_recorded`, which has none.
        """
        ...

    def entry_for(self, *, tenant_id: str, decision_id: str) -> IssuanceEntry | None:
        """Return the issuance recorded for a decision, for records and alerts."""
        ...


@runtime_checkable
class IssuanceEvidence(Protocol):
    """The durable ``token_issued`` record, read and written as the claim itself.

    ``FR-23`` already requires that every issuance is appended as a
    ``token_issued`` record. This protocol says that the record is not merely
    written *after* the claim: it **is** the claim. An implementation reads and
    writes one identified record - the issuance record of one
    ``(tenant_id, decision_id)`` - and the ledger asks it, rather than its own
    memory, whether that decision has been minted for.

    Two obligations, and a ledger built on anything weaker is unsound:

    1. :meth:`recorded_issuance` sees **every** durable issuance for that
       decision, including ones written by another process, by a replay, or by
       an attempt whose caller was told the write had failed. Absence must be a
       proof of absence, not a cache miss.
    2. :meth:`record_issuance` raises unless the record is durable. It may raise
       when the record did in fact land - a timeout is allowed to lie in that
       direction - because obligation 1 is what resolves the ambiguity on the
       next attempt.
    """

    def recorded_issuance(self) -> IssuanceEntry | None:
        """The issuance already durably recorded for this decision.

        ``None`` means the chain holds no issuance record for it. Raising is the
        third answer and the only honest one when durability cannot be read: an
        unreadable chain is not an empty chain.
        """
        ...

    def record_issuance(self, token: DecisionToken) -> None:
        """Append the ``token_issued`` record for ``token`` (``FR-23``).

        Returning means the record is durable. Raising a
        :class:`~neuroharness.errors.FailClosedError` means it may not be, and
        the caller must behave as though the mint never happened.
        """
        ...


@runtime_checkable
class RecordingIssuanceLedger(IssuanceLedger, Protocol):
    """An issuance ledger that can take the claim and the record as one step.

    Separate from :class:`IssuanceLedger` rather than folded into it, because
    widening a protocol every implementation must satisfy is a breaking change
    for implementations this package does not own. ``runtime_checkable`` is what
    lets :class:`~neuroharness.tokens.service.TokenService` detect the capability
    and refuse to pretend, rather than silently falling back to the two-step
    path a caller asked it not to use.
    """

    def claim_recorded(
        self,
        *,
        token: DecisionToken,
        evidence: IssuanceEvidence,
        now: datetime,
    ) -> IssuanceEntry | None:
        """Claim ``token``'s decision *by* recording its issuance.

        Same return convention as :meth:`IssuanceLedger.claim`: ``None`` when
        the claim succeeded and the holding :class:`IssuanceEntry` when it did
        not. The difference is what a failure leaves behind - nothing, because
        an issuance that could not be recorded did not happen.
        """
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

    **The wedge that argument used to imply, and why it no longer does.**
    :meth:`claim` takes the claim on its own authority, so a caller that records
    the issuance afterwards - which ``FR-23`` requires it to - has a window: the
    record write fails, the token is correctly withheld, and the claim stands
    for a token nobody holds. Never purging then means that decision is
    un-mintable forever. The two obvious repairs both fail against the paragraph
    above. Releasing the claim on failure is a purge by another name, and
    "provably never returned" is exactly what a crash between the two steps and
    a store timeout cannot establish. Bounding the entries by age is the same
    purge with a timer.

    :meth:`claim_recorded` removes the window instead of compensating for it.
    The claim and the ``token_issued`` record are one step: the entry is created
    only once :class:`IssuanceEvidence` has made the record durable, and the
    evidence - not this dictionary - is what a later attempt is checked against.
    So there is nothing to release on failure, because nothing was claimed; and
    forgetting stays impossible, because the entries here are a *cache of the
    chain* rather than the record of the mint. The never-purge argument survives
    intact: what changed is that the thing that must never be forgotten is now
    the append-only evidence record, which cannot be forgotten by construction.

    **Retention (``D-5``).** These entries are still never purged, and the
    reason is now narrower than "we dare not". On the :meth:`claim` path a purge
    is unsound for the reason above. On the :meth:`claim_recorded` path eviction
    would be *safe* - a miss falls through to :meth:`IssuanceEvidence.recorded_issuance`
    - but it is not implemented, because one class serves both paths and the
    unsound reading is the one that costs a token. The durable answer is
    therefore that the ledger has no retention policy of its own: it holds one
    small entry per decision for the life of the process, and the retention that
    actually governs at-most-once minting is the decision-record retention
    (:data:`~neuroharness.defaults.DEFAULT_RECORD_RETENTION_DAYS`, ``NFR-16``),
    because the issuance record is what the claim is derived from. That is
    orders of magnitude longer than
    :data:`~neuroharness.defaults.DEFAULT_TOKEN_TTL_SECONDS`, which is the
    interval the purge argument is actually about.
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
        moment = now.astimezone(UTC)
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

    def claim_recorded(
        self,
        *,
        token: DecisionToken,
        evidence: IssuanceEvidence,
        now: datetime,
    ) -> IssuanceEntry | None:
        """Claim ``token``'s decision by making its issuance record durable.

        Three answers, in the order the lock sees them:

        * this process already recorded an issuance for the key - refuse from
          the cache, without touching the store;
        * the chain holds one this process did not write (another gateway, a
          replay, or an attempt whose caller was told the write had failed) -
          adopt it and refuse. This is the branch that keeps a second mint
          impossible across the ambiguous timeout, and the reason
          :meth:`IssuanceEvidence.recorded_issuance` may not answer from a
          cache;
        * neither - write the record, and only then remember the claim.

        The lock spans the write. That serialises minting within the process,
        which the in-memory evidence store does anyway, and it is what makes the
        read-then-write one step. A durable ledger replaces the whole method
        with a unique constraint on ``(tenant_id, decision_id)`` applied in the
        same transaction as the record insert, which is the same single step
        with the same outcomes.

        Nothing is written back on failure: an exception from either call leaves
        the ledger exactly as it was, so the next attempt for this decision is a
        first attempt again - checked, before it mints, against the record.
        """
        key = (token.tenant_id, token.decision_id)
        moment = now.astimezone(UTC)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                return existing

            recorded = evidence.recorded_issuance()
            if recorded is not None:
                self._entries[key] = recorded
                return recorded

            evidence.record_issuance(token)
            self._entries[key] = IssuanceEntry(
                tenant_id=token.tenant_id,
                decision_id=token.decision_id,
                token_id=token.token_id,
                envelope_digest=token.envelope_digest,
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
