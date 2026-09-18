"""Token signing, verification and key rotation (``NFR-17``, ``ADR-0019``).

``ADR-0019`` makes ECDSA P-256 the production default, Ed25519 where the key
service offers it, and HMAC-SHA256 acceptable only for single-process
deployments. That decision is about *operations*, not about the decision path,
so it must not reach the token service as a branch. It reaches it as a
:class:`Signer`: the service holds one, signs with it, verifies with it, and
never learns which algorithm it is. Adding ECDSA later is a registered factory
and a configuration change, not an edit to :mod:`neuroharness.tokens.service`.

Rotation is the second reason this module exists. ``NFR-17`` requires multiple
active key identifiers and rotation without downtime, which means verification
must be able to select a key by the identifier the token names while signing
uses exactly one. :class:`MultiKeySigner` is that split, and it refuses an
unknown ``key_id`` with :class:`TokenSignatureError` rather than falling back to
"try them all and hope", because a token naming a key the deployment does not
hold is a forgery attempt, not a near miss.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Callable, Final, Iterable, Mapping, Protocol, runtime_checkable

from neuroharness.config import Settings, SigningAlgorithm
from neuroharness.errors import ConfigurationError, TokenSignatureError

__all__ = [
    "Signer",
    "KeyedVerifier",
    "KeyMaterial",
    "SignerFactory",
    "HmacSigner",
    "MultiKeySigner",
    "SignerRegistry",
    "default_signer_registry",
    "signer_for_settings",
    "verify_signature",
    "MIN_HMAC_SECRET_BYTES",
]

#: A shorter shared secret than the HMAC-SHA256 block output buys nothing and
#: invites a weak deployment secret. Enforced at construction, so a misconfigured
#: deployment refuses to start rather than issuing forgeable tokens.
MIN_HMAC_SECRET_BYTES: Final[int] = 32


@runtime_checkable
class Signer(Protocol):
    """Produces and checks the signature over a token's signing payload.

    The interface is intentionally byte-in / string-out: the signer knows
    nothing about tokens, and the token service knows nothing about keys.
    """

    @property
    def algorithm(self) -> SigningAlgorithm:
        """The algorithm recorded in the token's ``key_alg`` field."""
        ...

    @property
    def key_id(self) -> str:
        """The identifier of the key this signer *signs* with."""
        ...

    def sign(self, payload: bytes) -> str:
        """Return a textual signature over ``payload``."""
        ...

    def verify(self, payload: bytes, signature: str) -> bool:
        """Return whether ``signature`` is valid for ``payload``.

        Returns a boolean rather than raising: "this signature does not verify"
        is an ordinary outcome the caller converts into a recorded refusal.
        """
        ...


@runtime_checkable
class KeyedVerifier(Protocol):
    """A signer that can verify against a *named* key.

    Separate from :class:`Signer` so that a single-key deployment does not have
    to implement rotation machinery it does not use, while a rotating deployment
    can still be asked "verify this with key ``k-2024-06``" instead of "verify
    this with anything you happen to hold".
    """

    def verify_with(self, *, key_id: str, payload: bytes, signature: str) -> bool:
        """Verify using the named key. Unknown key ids raise ``TokenSignatureError``."""
        ...

    def algorithm_for(self, key_id: str) -> SigningAlgorithm:
        """The algorithm of the named key. Unknown key ids raise ``TokenSignatureError``."""
        ...


@dataclass(frozen=True, slots=True)
class KeyMaterial:
    """What a deployment hands a signer factory.

    Deliberately opaque and union-shaped: an HMAC deployment supplies a secret,
    a KMS-backed ECDSA deployment supplies a key reference and never a secret at
    all. Neither the registry nor the token service inspects these fields, which
    is what keeps key handling out of the decision path.
    """

    key_id: str
    secret: bytes | None = None
    reference: str | None = None
    parameters: Mapping[str, str] = field(default_factory=dict)


#: How a deployment builds a signer for one algorithm from one key's material.
SignerFactory = Callable[[KeyMaterial], Signer]


class HmacSigner:
    """HMAC-SHA256 signer for single-process deployments (``ADR-0019``).

    Acceptable only where the token service and the broker are the same process:
    a shared secret across a process boundary puts the minting key in the
    component that is supposed to be unable to mint, which dissolves the
    separation the token design rests on.
    """

    __slots__ = ("_key_id", "_secret")

    def __init__(self, *, key_id: str, secret: bytes) -> None:
        if not key_id:
            raise ConfigurationError("signer key_id must not be empty")
        if len(secret) < MIN_HMAC_SECRET_BYTES:
            raise ConfigurationError(
                f"hmac secret for key {key_id!r} is shorter than "
                f"{MIN_HMAC_SECRET_BYTES} bytes"
            )
        self._key_id = key_id
        self._secret = bytes(secret)

    @classmethod
    def from_material(cls, material: KeyMaterial) -> HmacSigner:
        """Factory form, for registration in a :class:`SignerRegistry`."""
        if material.secret is None:
            raise ConfigurationError(
                f"hmac key {material.key_id!r} was configured without a secret"
            )
        return cls(key_id=material.key_id, secret=material.secret)

    @property
    def algorithm(self) -> SigningAlgorithm:
        return SigningAlgorithm.HMAC_SHA256

    @property
    def key_id(self) -> str:
        return self._key_id

    def _mac(self, payload: bytes) -> str:
        digest = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def sign(self, payload: bytes) -> str:
        return self._mac(payload)

    def verify(self, payload: bytes, signature: str) -> bool:
        """Constant-time comparison, so a failure leaks no prefix information."""
        if not signature.isascii():
            # ``compare_digest`` rejects non-ASCII str operands outright; a
            # signature that cannot be compared is simply not a valid one.
            return False
        return hmac.compare_digest(self._mac(payload), signature)


class MultiKeySigner:
    """Signs with one key, verifies against every active key (``NFR-17``).

    Rotation without downtime is exactly this asymmetry: the new key becomes the
    signing key immediately, while tokens minted seconds earlier under the
    previous key stay verifiable until they expire. Immutable, so a rotation is
    a new object published to the components that need it rather than a mutation
    racing in-flight verifications.
    """

    __slots__ = ("_by_key_id", "_active_key_id")

    def __init__(self, *, active: Signer, additional: Iterable[Signer] = ()) -> None:
        by_key_id: dict[str, Signer] = {}
        for signer in (active, *additional):
            if signer.key_id in by_key_id:
                raise ConfigurationError(f"duplicate signing key id: {signer.key_id!r}")
            by_key_id[signer.key_id] = signer
        self._by_key_id = by_key_id
        self._active_key_id = active.key_id

    @property
    def algorithm(self) -> SigningAlgorithm:
        return self._by_key_id[self._active_key_id].algorithm

    @property
    def key_id(self) -> str:
        return self._active_key_id

    @property
    def verifying_key_ids(self) -> frozenset[str]:
        """Every key this signer will still accept a token from."""
        return frozenset(self._by_key_id)

    def rotate_to(self, key_id: str) -> MultiKeySigner:
        """Return a signer that mints under ``key_id`` and still verifies the rest."""
        if key_id not in self._by_key_id:
            raise ConfigurationError(f"cannot rotate to unknown key id: {key_id!r}")
        others = [s for k, s in self._by_key_id.items() if k != key_id]
        return MultiKeySigner(active=self._by_key_id[key_id], additional=others)

    def retire(self, key_id: str) -> MultiKeySigner:
        """Return a signer that no longer verifies ``key_id``.

        Retiring the active key is refused: a signer that cannot sign is an
        outage discovered at the first mint, which is the worst moment to find
        out.
        """
        if key_id == self._active_key_id:
            raise ConfigurationError("cannot retire the active signing key")
        if key_id not in self._by_key_id:
            raise ConfigurationError(f"cannot retire unknown key id: {key_id!r}")
        others = [s for k, s in self._by_key_id.items() if k not in {key_id, self._active_key_id}]
        return MultiKeySigner(active=self._by_key_id[self._active_key_id], additional=others)

    def _signer_for(self, key_id: str) -> Signer:
        try:
            return self._by_key_id[key_id]
        except KeyError:
            raise TokenSignatureError(f"token names unknown signing key: {key_id!r}") from None

    def sign(self, payload: bytes) -> str:
        return self._by_key_id[self._active_key_id].sign(payload)

    def verify(self, payload: bytes, signature: str) -> bool:
        """Verify against any active key.

        Used only where the caller has no key identifier to go on; the token
        path always names one and goes through :meth:`verify_with`.
        """
        return any(signer.verify(payload, signature) for signer in self._by_key_id.values())

    def verify_with(self, *, key_id: str, payload: bytes, signature: str) -> bool:
        return self._signer_for(key_id).verify(payload, signature)

    def algorithm_for(self, key_id: str) -> SigningAlgorithm:
        return self._signer_for(key_id).algorithm


class SignerRegistry:
    """Maps a :class:`SigningAlgorithm` to the factory that builds its signer.

    This is the seam ``ADR-0019`` needs: a deployment that moves from HMAC to
    ECDSA P-256 registers a factory and changes a setting. No module in the
    decision path names an algorithm, so no module in the decision path has to
    be re-reviewed when the algorithm changes.
    """

    __slots__ = ("_factories",)

    def __init__(self, factories: Mapping[SigningAlgorithm, SignerFactory] | None = None) -> None:
        self._factories: dict[SigningAlgorithm, SignerFactory] = dict(factories or {})

    def register(
        self,
        algorithm: SigningAlgorithm,
        factory: SignerFactory,
        *,
        replace: bool = False,
    ) -> None:
        """Register ``factory`` for ``algorithm``.

        Re-registration must be explicit: silently replacing a signer factory is
        how a deployment ends up minting under an algorithm nobody chose.
        """
        if algorithm in self._factories and not replace:
            raise ConfigurationError(
                f"a signer factory for {algorithm.value} is already registered"
            )
        self._factories[algorithm] = factory

    def supports(self, algorithm: SigningAlgorithm) -> bool:
        return algorithm in self._factories

    @property
    def algorithms(self) -> frozenset[SigningAlgorithm]:
        return frozenset(self._factories)

    def create(self, algorithm: SigningAlgorithm, material: KeyMaterial) -> Signer:
        """Build a signer, or refuse to start.

        An unregistered algorithm is operator error, not a runtime verdict:
        there is no safe degraded behaviour between "sign with the configured
        algorithm" and "do not run".
        """
        try:
            factory = self._factories[algorithm]
        except KeyError:
            supported = ", ".join(sorted(a.value for a in self._factories)) or "none"
            raise ConfigurationError(
                f"no signer factory registered for {algorithm.value}; this build supports: "
                f"{supported}"
            ) from None
        return factory(material)


def default_signer_registry() -> SignerRegistry:
    """A registry with the algorithms this build implements.

    Returned fresh rather than shared as a module global: a mutable process-wide
    registry is a place where one tenant's configuration can change another's.
    """
    return SignerRegistry({SigningAlgorithm.HMAC_SHA256: HmacSigner.from_material})


def signer_for_settings(
    settings: Settings,
    material: KeyMaterial,
    *,
    registry: SignerRegistry | None = None,
) -> Signer:
    """Build the signer the deployment's settings ask for, or refuse to start.

    This is the seam between ``ADR-0019`` as a *setting* and ``ADR-0019`` as a
    *signer*, and it exists so that the two cannot disagree quietly. The ADR
    makes ECDSA P-256 the default and HMAC-SHA256 acceptable only where the
    token service and the broker are one process; a build that has not
    registered a P-256 factory therefore has nothing it may legitimately
    substitute. Falling back to the algorithm it does happen to have would put
    the minting key inside the component whose whole job is to be unable to
    mint, and it would do it on a deployment running defaults, with no operator
    decision to point at afterwards.

    So the refusal is a startup failure rather than a runtime verdict:
    :meth:`SignerRegistry.create` raises :class:`ConfigurationError` naming the
    configured algorithm and the ones this build implements. A single-process
    deployment answers it by setting ``NEUROHARNESS_SIGNING_ALGORITHM`` to
    ``hmac-sha256`` -- a recorded, reviewable choice, which is the difference
    that matters.
    """
    return (registry or default_signer_registry()).create(settings.signing_algorithm, material)


def verify_signature(
    signer: Signer,
    *,
    key_id: str,
    key_alg: SigningAlgorithm,
    payload: bytes,
    signature: str,
) -> bool:
    """Verify ``signature`` against the key the token *names*.

    Checking the named key rather than "whatever key we hold" is what makes
    rotation safe and forgery detectable. A token naming a key the deployment
    does not hold, or naming an algorithm that key does not use, raises
    :class:`TokenSignatureError`: both are impossible for a token this harness
    minted, so neither can be treated as an ordinary verification failure.
    """
    if isinstance(signer, KeyedVerifier):
        if signer.algorithm_for(key_id) is not key_alg:
            raise TokenSignatureError(
                f"token claims algorithm {key_alg.value} for key {key_id!r}"
            )
        return signer.verify_with(key_id=key_id, payload=payload, signature=signature)
    if key_id != signer.key_id:
        raise TokenSignatureError(f"token names unknown signing key: {key_id!r}")
    if key_alg is not signer.algorithm:
        raise TokenSignatureError(f"token claims algorithm {key_alg.value} for key {key_id!r}")
    return signer.verify(payload, signature)
