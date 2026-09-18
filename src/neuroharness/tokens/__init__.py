"""Decision tokens: the only artefact that can authorise execution.

``FR-20``-``FR-23`` and ``ADR-0015``/``ADR-0016``/``ADR-0019`` split this into
four pieces, each replaceable without touching the others:

* :mod:`~neuroharness.tokens.model` - the fixed ``FR-20`` payload and its stable
  signing encoding. Carries no signature, by construction.
* :mod:`~neuroharness.tokens.signer` - algorithms and key rotation. The service
  never names an algorithm.
* :mod:`~neuroharness.tokens.nonce` - atomic at-most-once issuance, atomic
  single use, and revocation.
* :mod:`~neuroharness.tokens.service` - issuance preconditions and the broker's
  verification obligations, each failing with its own typed reason.
"""

from neuroharness.tokens.model import DecisionToken, SignedToken
from neuroharness.tokens.nonce import (
    ConsumeOutcome,
    DuplicateIssuanceError,
    InMemoryIssuanceLedger,
    InMemoryNonceStore,
    InMemoryRevocationList,
    IssuanceEntry,
    IssuanceEvidence,
    IssuanceLedger,
    NonceEntry,
    NonceStore,
    RecordingIssuanceLedger,
    RevocationList,
    RevocationScope,
)
from neuroharness.tokens.service import ConsumeResult, TokenService
from neuroharness.tokens.signer import (
    HmacSigner,
    KeyedVerifier,
    KeyMaterial,
    MultiKeySigner,
    Signer,
    SignerFactory,
    SignerRegistry,
    default_signer_registry,
    signer_for_settings,
    verify_signature,
)

__all__ = [
    "ConsumeOutcome",
    "ConsumeResult",
    "DecisionToken",
    "DuplicateIssuanceError",
    "HmacSigner",
    "InMemoryIssuanceLedger",
    "InMemoryNonceStore",
    "InMemoryRevocationList",
    "IssuanceEntry",
    "IssuanceEvidence",
    "IssuanceLedger",
    "KeyMaterial",
    "KeyedVerifier",
    "MultiKeySigner",
    "NonceEntry",
    "NonceStore",
    "RecordingIssuanceLedger",
    "RevocationList",
    "RevocationScope",
    "SignedToken",
    "Signer",
    "SignerFactory",
    "SignerRegistry",
    "TokenService",
    "default_signer_registry",
    "signer_for_settings",
    "verify_signature",
]
