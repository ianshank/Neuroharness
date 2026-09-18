"""Hash-chain primitives for decision records (``FR-70``, ``SEC-10``).

Evidence is a byproduct of enforcement (Constitution Article III): a decision
that is not recorded was not made. That only holds if a recorded decision can be
shown to be *the* decision that was made, so every record carries a digest over
its own content and over the record before it in its tenant's chain.

Two bindings are load bearing and deserve their reasons written down.

**Why the previous hash is bound into the record hash.** A digest over a record
alone proves the record was not edited. It does not prove *where* the record
sits. Without the predecessor bound in, records can be reordered, a record can
be dropped, or a record can be lifted out of one chain and dropped into another
- and each record still hashes correctly, so the tamper is invisible. Binding
the predecessor makes the digest a statement about position as well as content,
so any reordering, deletion or splice breaks the link at the first disturbed
record.

**Why the tenant is bound in.** ``tenant_id`` is part of the hashed body, so a
record produced for tenant A cannot be presented as evidence in tenant B's chain
even if an attacker rewrites the visible ``tenant_id`` field: rewriting it
changes the body and therefore the digest. Tenants are separate chains
(``NFR-15``); binding the tenant is what makes that separation checkable rather
than merely declared.

Verification never raises on bad input. A verifier that raises on a malformed
record tempts its callers into a bare ``except`` that swallows the finding;
instead every failure is a typed, non-instructional result naming the first
index that does not verify (``FR-56``, ``SEC-07``).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Final, Mapping, Sequence

from neuroharness.canonical.digest import digest_value
from neuroharness.errors import ConfigurationError, FailClosedError
from neuroharness.models.common import Digest

# The record vocabulary lives with the record model, not here: two copies of a
# closed enumeration is two places for the schema to drift away from itself.
from neuroharness.models.record import RecordKind
from neuroharness.reason import ReasonName
from neuroharness.version import SchemaCompatibility, SchemaKind

__all__ = [
    "RecordKind",
    "ChainBreak",
    "ChainVerification",
    "MalformedRecordError",
    "Signer",
    "SCHEMA_VERSION",
    "GENESIS_SEQ",
    "MAX_TENANT_ID_LENGTH",
    "FIELD_TENANT_ID",
    "FIELD_SEQ",
    "FIELD_PREV_RECORD_HASH",
    "FIELD_RECORD_HASH",
    "FIELD_RECORD_ID",
    "FIELD_KIND",
    "FIELD_TIMESTAMP",
    "FIELD_SCHEMA_VERSION",
    "RESERVED_CHAIN_FIELDS",
    "CHECKPOINT_SIGNING_DOMAIN",
    "compute_record_hash",
    "verify_chain",
    "create_checkpoint",
    "verify_checkpoint",
    "checkpoint_signing_bytes",
    "plain_value",
]

# --- Schema-derived constants ------------------------------------------------
# These mirror ``docs/sdd/schemas/decision-record.schema.json``. They are named
# here so that no behaviour in this package turns on a bare literal and so that
# a schema bump is a one-line, reviewable change.

#: The decision-record schema version this build writes, re-exported for the
#: modules and fixtures that already name it here.
#:
#: Derived, never declared. :mod:`neuroharness.version` documents itself as the
#: single place that says which versions a build reads and writes, and a second
#: literal here would let the two disagree: widening ``_READABLE[RECORD]`` -
#: which that module documents as a backwards-compatible change - would make
#: :class:`~neuroharness.models.record.DecisionRecord` accept ``1.2`` while the
#: evidence store still refused it, which is a record the harness can build and
#: cannot write (``F4``). Reading is checked with
#: :meth:`SchemaCompatibility.assert_readable`, not against this value, because
#: a build may read more versions than it writes.
SCHEMA_VERSION: Final[str] = SchemaCompatibility.written_version(SchemaKind.RECORD)

#: Sequence number of the first record in a tenant's chain.
GENESIS_SEQ: Final[int] = 0

#: ``tenant_id`` maxLength in the record schema.
MAX_TENANT_ID_LENGTH: Final[int] = 64

FIELD_SCHEMA_VERSION: Final[str] = "schema_version"
FIELD_RECORD_ID: Final[str] = "record_id"
FIELD_TENANT_ID: Final[str] = "tenant_id"
FIELD_SEQ: Final[str] = "seq"
FIELD_PREV_RECORD_HASH: Final[str] = "prev_record_hash"
FIELD_RECORD_HASH: Final[str] = "record_hash"
FIELD_KIND: Final[str] = "kind"
FIELD_TIMESTAMP: Final[str] = "timestamp"

#: Fields that describe a record's position in the chain. The store is the only
#: authority for them: a caller that could supply them could choose where its
#: record appears, which is the whole property the chain exists to deny.
RESERVED_CHAIN_FIELDS: Final[tuple[str, ...]] = (
    FIELD_SEQ,
    FIELD_PREV_RECORD_HASH,
    FIELD_RECORD_HASH,
)

#: Fields every record must carry for its chain position to be checkable.
_REQUIRED_CHAIN_FIELDS: Final[tuple[str, ...]] = (
    FIELD_TENANT_ID,
    FIELD_SEQ,
    FIELD_PREV_RECORD_HASH,
    FIELD_RECORD_HASH,
)

#: Domain separator mixed into every checkpoint signature. Without it, a
#: signature produced over some other structure that happened to canonicalise
#: identically could be presented as a checkpoint signature (``SEC-10``).
CHECKPOINT_SIGNING_DOMAIN: Final[str] = "neuroharness/evidence-checkpoint/v1"

_CHECKPOINT_FIELD_LAST_SEQ: Final[str] = "last_seq"
_CHECKPOINT_FIELD_LAST_RECORD_HASH: Final[str] = "last_record_hash"
_CHECKPOINT_FIELD_KEY_ID: Final[str] = "key_id"
_CHECKPOINT_FIELD_SIGNATURE: Final[str] = "signature"
_CHECKPOINT_FIELD_ANCHOR_REF: Final[str] = "anchor_ref"


class MalformedRecordError(FailClosedError):
    """A record is not shaped well enough to take a position in a chain.

    Fail-closed rather than best-effort: a record whose tenant or sequence
    cannot be read cannot be chained, and an unchained record is not evidence.
    """

    reason_name = ReasonName.SCHEMA_INVALID


class ChainBreak(str, Enum):
    """Why a chain did not verify. Typed, so operators and tests can branch."""

    #: The record's stored hash does not match its content: it was edited, or
    #: it was lifted from another chain and relabelled.
    RECORD_HASH_MISMATCH = "record_hash_mismatch"
    #: The record does not link to the record presented before it.
    PREV_HASH_MISMATCH = "prev_hash_mismatch"
    #: A sequence number is missing: at least one record was removed.
    SEQUENCE_GAP = "sequence_gap"
    #: All sequence numbers are present but not in ascending order: records
    #: were reordered or repeated.
    SEQUENCE_OUT_OF_ORDER = "sequence_out_of_order"
    #: A record belongs to a different tenant's chain.
    TENANT_MISMATCH = "tenant_mismatch"
    #: A record is missing the fields that make its position checkable.
    MALFORMED_RECORD = "malformed_record"


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Result of verifying a run of records. Frozen: a verdict is not editable."""

    ok: bool
    checked: int
    first_broken_index: int | None = None
    reason: ChainBreak | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def verified(cls, checked: int) -> ChainVerification:
        return cls(ok=True, checked=checked)

    @classmethod
    def broken(cls, index: int, reason: ChainBreak, detail: str = "") -> ChainVerification:
        return cls(ok=False, checked=index, first_broken_index=index, reason=reason, detail=detail)


#: Signs the canonical bytes of a checkpoint and returns the signature as text.
#: A callable rather than a key: signing material belongs to a KMS or an HSM,
#: never to this process (``SEC-03``).
Signer = Callable[[bytes], str]


def plain_value(value: Any) -> Any:
    """Return ``value`` as plain JSON-shaped data.

    Stored records are handed out as read-only views built from mapping proxies
    and tuples, and identifiers such as :class:`Digest` are ``str`` subclasses.
    Canonicalisation and JSON serialisation both want the plain shapes, and a
    digest that depended on which wrapper a caller happened to hold would not be
    a digest of the record at all.
    """
    if isinstance(value, Mapping):
        return {str(key): plain_value(item) for key, item in value.items()}
    if isinstance(value, Enum):
        return plain_value(value.value)
    if isinstance(value, (str, bytes)):
        return str(value) if isinstance(value, str) else value
    if isinstance(value, (list, tuple)):
        return [plain_value(item) for item in value]
    return value


def compute_record_hash(record: Mapping[str, Any], *, prev_hash: Digest | None) -> Digest:
    """Return the chain hash of ``record`` linked to ``prev_hash``.

    The digest is taken over the canonical form of the record with
    ``record_hash`` omitted (a field cannot commit to itself) and with
    ``prev_record_hash`` *set to the supplied predecessor*. Overriding rather
    than trusting the field is deliberate: it makes it impossible to compute a
    hash whose chain link disagrees with the link the record advertises, which
    would otherwise let a record be hashed for one position and filed in
    another.

    ``tenant_id`` is part of the hashed body, so the digest is also a
    commitment to the chain the record belongs to (``NFR-15``).
    """
    if not isinstance(record, Mapping):
        raise MalformedRecordError("a decision record must be a mapping")
    missing = [field for field in (FIELD_TENANT_ID, FIELD_SEQ) if field not in record]
    if missing:
        raise MalformedRecordError(
            "cannot hash a record without its chain identity: missing "
            + ", ".join(sorted(missing))
        )
    body = {
        str(key): plain_value(value)
        for key, value in record.items()
        if key != FIELD_RECORD_HASH
    }
    body[FIELD_PREV_RECORD_HASH] = str(prev_hash) if prev_hash is not None else None
    return digest_value(body)


def verify_chain(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_tenant_id: str | None = None,
) -> ChainVerification:
    """Verify a contiguous run of one tenant's records.

    Detects, in this order per record, an altered payload (the stored hash no
    longer matches the content), a foreign record (a different tenant), a
    deleted record (a missing sequence number), a reordering *or repetition*
    (no sequence number missing, but the run is not ascending) and a broken link
    (the record does not point at the record before it).

    ``records`` may start part way through a chain, as :meth:`EvidenceStore.read`
    with a ``start_seq`` returns. Only a run that starts at :data:`GENESIS_SEQ`
    has its anchoring checked, because the predecessor of a mid-chain first
    record is by definition not present to compare against.

    Pass ``expected_tenant_id`` when the caller knows which chain it asked for:
    without it, a run made entirely of another tenant's records is internally
    consistent and verifies.
    """
    if not records:
        # Nothing to contradict. An empty read is not evidence of tampering.
        return ChainVerification.verified(0)

    for index, record in enumerate(records):
        structural = _check_structure(record)
        if structural is not None:
            return ChainVerification.broken(index, ChainBreak.MALFORMED_RECORD, structural)

    tenant_id = (
        expected_tenant_id if expected_tenant_id is not None else str(records[0][FIELD_TENANT_ID])
    )

    # A missing sequence number, a swapped pair and a repeated number all look
    # identical at the first disturbed record, so the distinction is made over
    # the run as a whole. The chain fails either way; the label is what an
    # operator branches on, and "a record was deleted" starts a very different
    # investigation from "a record was presented twice".
    #
    # A repetition is checked first because it is not a gap: ``[0, 1, 1]`` is
    # not contiguous, so a contiguity test alone reports SEQUENCE_GAP for a run
    # in which nothing is missing. ``SEQUENCE_OUT_OF_ORDER`` documents itself as
    # covering "reordered or repeated", which is where a repetition belongs.
    sequence_numbers = [int(record[FIELD_SEQ]) for record in records]
    repeated = len(set(sequence_numbers)) != len(sequence_numbers)
    lowest = min(sequence_numbers)
    contiguous = sorted(sequence_numbers) == list(
        range(lowest, lowest + len(sequence_numbers))
    )
    disorder = (
        ChainBreak.SEQUENCE_OUT_OF_ORDER
        if repeated or contiguous
        else ChainBreak.SEQUENCE_GAP
    )

    previous: Mapping[str, Any] | None = None
    for index, record in enumerate(records):
        stored_prev = _parse_optional_digest(record[FIELD_PREV_RECORD_HASH])
        stored_hash = _parse_digest(record[FIELD_RECORD_HASH])
        seq = int(record[FIELD_SEQ])

        if str(record[FIELD_TENANT_ID]) != tenant_id:
            return ChainVerification.broken(
                index,
                ChainBreak.TENANT_MISMATCH,
                f"record belongs to tenant {record[FIELD_TENANT_ID]!r}, chain is {tenant_id!r}",
            )

        if compute_record_hash(record, prev_hash=stored_prev) != stored_hash:
            return ChainVerification.broken(
                index,
                ChainBreak.RECORD_HASH_MISMATCH,
                f"content at seq {seq} does not match its recorded hash",
            )

        if previous is None:
            if seq == GENESIS_SEQ and stored_prev is not None:
                return ChainVerification.broken(
                    index,
                    ChainBreak.PREV_HASH_MISMATCH,
                    "the genesis record must not link to a predecessor",
                )
            previous = record
            continue

        expected_seq = int(previous[FIELD_SEQ]) + 1
        if seq != expected_seq:
            return ChainVerification.broken(
                index,
                disorder,
                f"expected seq {expected_seq}, found {seq}",
            )

        expected_prev = _parse_digest(previous[FIELD_RECORD_HASH])
        if stored_prev != expected_prev:
            return ChainVerification.broken(
                index,
                ChainBreak.PREV_HASH_MISMATCH,
                f"record at seq {seq} does not link to the record at seq {expected_seq - 1}",
            )

        previous = record

    return ChainVerification.verified(len(records))


def checkpoint_signing_bytes(
    *,
    tenant_id: str,
    last_seq: int,
    last_record_hash: Digest,
    key_id: str,
    anchor_ref: str | None = None,
) -> bytes:
    """Return the canonical bytes a checkpoint signature covers (``SEC-10``).

    The tenant is inside the signed payload even though the schema keeps it on
    the enclosing record: a checkpoint that did not name its chain could be
    moved to a shorter chain of another tenant and would still verify.
    """
    payload = {
        "domain": CHECKPOINT_SIGNING_DOMAIN,
        FIELD_TENANT_ID: str(tenant_id),
        _CHECKPOINT_FIELD_LAST_SEQ: int(last_seq),
        _CHECKPOINT_FIELD_LAST_RECORD_HASH: str(last_record_hash),
        _CHECKPOINT_FIELD_KEY_ID: str(key_id),
        _CHECKPOINT_FIELD_ANCHOR_REF: anchor_ref,
    }
    return digest_value(payload).encode("utf-8")


def create_checkpoint(
    *,
    tenant_id: str,
    last_seq: int,
    last_record_hash: Digest,
    key_id: str,
    signer: Signer,
    anchor_ref: str | None = None,
) -> dict[str, Any]:
    """Sign the head of a tenant's chain (``SEC-10``, technical plan 4.11).

    A hash chain proves internal consistency; it does not stop a holder of the
    whole store from rewriting every record and every link. A periodic signature
    over ``(tenant, last_seq, last_hash)`` is what bounds such a rewrite to the
    window since the last checkpoint.

    The returned mapping is the schema's ``Checkpoint`` object, ready to be
    carried by a record of kind ``checkpoint``.
    """
    digest = last_record_hash if isinstance(last_record_hash, Digest) else Digest(last_record_hash)
    if int(last_seq) < GENESIS_SEQ:
        raise ConfigurationError(f"last_seq must be at least {GENESIS_SEQ}")
    signature = signer(
        checkpoint_signing_bytes(
            tenant_id=tenant_id,
            last_seq=int(last_seq),
            last_record_hash=digest,
            key_id=key_id,
            anchor_ref=anchor_ref,
        )
    )
    checkpoint: dict[str, Any] = {
        _CHECKPOINT_FIELD_LAST_SEQ: int(last_seq),
        _CHECKPOINT_FIELD_LAST_RECORD_HASH: str(digest),
        _CHECKPOINT_FIELD_KEY_ID: str(key_id),
        _CHECKPOINT_FIELD_SIGNATURE: signature,
    }
    if anchor_ref is not None:
        checkpoint[_CHECKPOINT_FIELD_ANCHOR_REF] = anchor_ref
    return checkpoint


def verify_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    tenant_id: str,
    signer: Signer | None = None,
    verifier: Callable[[bytes, str], bool] | None = None,
) -> bool:
    """Verify a checkpoint signature. Returns ``False``; never raises on tamper.

    ``signer`` re-signs and compares in constant time, which suits a symmetric
    key; ``verifier`` suits a public-key scheme where this process holds no
    signing material. Exactly one must be given, because defaulting either way
    would make an unverified checkpoint look verified.
    """
    if (signer is None) == (verifier is None):
        raise ConfigurationError("verify_checkpoint needs exactly one of signer or verifier")
    required = (
        _CHECKPOINT_FIELD_LAST_SEQ,
        _CHECKPOINT_FIELD_LAST_RECORD_HASH,
        _CHECKPOINT_FIELD_KEY_ID,
        _CHECKPOINT_FIELD_SIGNATURE,
    )
    if any(field not in checkpoint for field in required):
        return False
    try:
        payload = checkpoint_signing_bytes(
            tenant_id=tenant_id,
            last_seq=int(checkpoint[_CHECKPOINT_FIELD_LAST_SEQ]),
            last_record_hash=Digest(str(checkpoint[_CHECKPOINT_FIELD_LAST_RECORD_HASH])),
            key_id=str(checkpoint[_CHECKPOINT_FIELD_KEY_ID]),
            anchor_ref=checkpoint.get(_CHECKPOINT_FIELD_ANCHOR_REF),
        )
    except (ValueError, TypeError):
        # A tampered field that is not even well formed is a failed
        # verification, not an exception for the caller to interpret.
        return False
    signature = str(checkpoint[_CHECKPOINT_FIELD_SIGNATURE])
    if verifier is not None:
        return bool(verifier(payload, signature))
    assert signer is not None  # narrowed by the exactly-one check above
    return hmac.compare_digest(signer(payload), signature)


def _check_structure(record: Mapping[str, Any]) -> str | None:
    """Return a description of what makes ``record`` unchainable, or ``None``."""
    if not isinstance(record, Mapping):
        return "record is not a mapping"
    missing = [field for field in _REQUIRED_CHAIN_FIELDS if field not in record]
    if missing:
        return "missing chain fields: " + ", ".join(sorted(missing))
    seq = record[FIELD_SEQ]
    # ``bool`` is an ``int`` in Python; a boolean sequence number is a bug, not
    # a position, and it must not be allowed to compare equal to 0 or 1.
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < GENESIS_SEQ:
        return f"seq is not a sequence number: {seq!r}"
    tenant_id = record[FIELD_TENANT_ID]
    if not isinstance(tenant_id, str) or not tenant_id:
        return f"tenant_id is not an identifier: {tenant_id!r}"
    try:
        _parse_digest(record[FIELD_RECORD_HASH])
        _parse_optional_digest(record[FIELD_PREV_RECORD_HASH])
    except (ValueError, TypeError) as exc:
        return str(exc)
    return None


def _parse_digest(value: Any) -> Digest:
    return value if isinstance(value, Digest) else Digest(str(value))


def _parse_optional_digest(value: Any) -> Digest | None:
    return None if value is None else _parse_digest(value)
