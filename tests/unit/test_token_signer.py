"""Signing, verification and rotation (``NFR-17``, ``ADR-0019``).

The properties under test are the ones a forged or misdirected token would
violate: a signature covers every byte of the payload, a wrong key never
verifies, an unknown key is an error rather than a quiet ``False``, and a
rotation keeps yesterday's tokens verifiable while today's are minted under the
new key.
"""

from __future__ import annotations

import pytest

from neuroharness.config import SigningAlgorithm
from neuroharness.errors import ConfigurationError, TokenSignatureError
from neuroharness.tokens.signer import (
    MIN_HMAC_SECRET_BYTES,
    HmacSigner,
    KeyedVerifier,
    KeyMaterial,
    MultiKeySigner,
    Signer,
    SignerRegistry,
    default_signer_registry,
    verify_signature,
)

PAYLOAD = b"neuroharness/decision-token/v1\n{}"
SECRET_A = b"a" * MIN_HMAC_SECRET_BYTES
SECRET_B = b"b" * MIN_HMAC_SECRET_BYTES


def signer_a() -> HmacSigner:
    return HmacSigner(key_id="key-a", secret=SECRET_A)


def signer_b() -> HmacSigner:
    return HmacSigner(key_id="key-b", secret=SECRET_B)


class TestHmacSigner:
    def test_satisfies_the_signer_protocol(self) -> None:
        assert isinstance(signer_a(), Signer)

    def test_round_trip(self) -> None:
        signer = signer_a()
        assert signer.verify(PAYLOAD, signer.sign(PAYLOAD))

    def test_reports_its_algorithm_and_key(self) -> None:
        signer = signer_a()
        assert signer.algorithm is SigningAlgorithm.HMAC_SHA256
        assert signer.key_id == "key-a"

    def test_signature_is_deterministic(self) -> None:
        # Determinism is what lets a replayed decision reproduce byte for byte.
        assert signer_a().sign(PAYLOAD) == signer_a().sign(PAYLOAD)

    def test_tampered_payload_does_not_verify(self) -> None:
        signature = signer_a().sign(PAYLOAD)
        assert not signer_a().verify(PAYLOAD + b" ", signature)

    def test_wrong_key_does_not_verify(self) -> None:
        signature = signer_a().sign(PAYLOAD)
        assert not HmacSigner(key_id="key-a", secret=SECRET_B).verify(PAYLOAD, signature)

    def test_garbage_signature_does_not_verify(self) -> None:
        assert not signer_a().verify(PAYLOAD, "not-a-signature")

    def test_non_ascii_signature_does_not_raise(self) -> None:
        # A constant-time comparison refuses non-ASCII operands; that must be a
        # refusal, not an unhandled TypeError on the decision path.
        assert not signer_a().verify(PAYLOAD, "sigé")

    def test_short_secret_is_a_startup_failure(self) -> None:
        with pytest.raises(ConfigurationError):
            HmacSigner(key_id="key-a", secret=b"tooshort")

    def test_empty_key_id_is_a_startup_failure(self) -> None:
        with pytest.raises(ConfigurationError):
            HmacSigner(key_id="", secret=SECRET_A)

    def test_from_material_requires_a_secret(self) -> None:
        with pytest.raises(ConfigurationError):
            HmacSigner.from_material(KeyMaterial(key_id="key-a", reference="kms://x"))


class TestSignerRegistry:
    def test_default_registry_offers_hmac(self) -> None:
        registry = default_signer_registry()
        assert registry.supports(SigningAlgorithm.HMAC_SHA256)
        signer = registry.create(
            SigningAlgorithm.HMAC_SHA256, KeyMaterial(key_id="key-a", secret=SECRET_A)
        )
        assert signer.key_id == "key-a"
        assert signer.algorithm is SigningAlgorithm.HMAC_SHA256

    def test_unregistered_algorithm_refuses_to_start(self) -> None:
        # ADR-0019 makes ECDSA the production default; a build that has not
        # registered it must refuse rather than fall back to a shared secret.
        with pytest.raises(ConfigurationError):
            default_signer_registry().create(
                SigningAlgorithm.ECDSA_P256, KeyMaterial(key_id="key-a")
            )

    def test_a_deployment_can_add_an_algorithm_without_touching_the_service(self) -> None:
        class FakeEcdsaSigner:
            """Stands in for the P-256 signer a KMS-backed deployment supplies."""

            def __init__(self, key_id: str) -> None:
                self._key_id = key_id

            @property
            def algorithm(self) -> SigningAlgorithm:
                return SigningAlgorithm.ECDSA_P256

            @property
            def key_id(self) -> str:
                return self._key_id

            def sign(self, payload: bytes) -> str:
                return f"ecdsa:{len(payload)}"

            def verify(self, payload: bytes, signature: str) -> bool:
                return signature == self.sign(payload)

        registry = default_signer_registry()
        registry.register(
            SigningAlgorithm.ECDSA_P256, lambda material: FakeEcdsaSigner(material.key_id)
        )
        signer = registry.create(SigningAlgorithm.ECDSA_P256, KeyMaterial(key_id="kms-1"))
        assert isinstance(signer, Signer)
        assert signer.verify(PAYLOAD, signer.sign(PAYLOAD))
        assert registry.algorithms >= {
            SigningAlgorithm.ECDSA_P256,
            SigningAlgorithm.HMAC_SHA256,
        }

    def test_silent_replacement_is_refused(self) -> None:
        registry = default_signer_registry()
        with pytest.raises(ConfigurationError):
            registry.register(SigningAlgorithm.HMAC_SHA256, HmacSigner.from_material)

    def test_explicit_replacement_is_allowed(self) -> None:
        registry = SignerRegistry()
        registry.register(SigningAlgorithm.HMAC_SHA256, HmacSigner.from_material)
        registry.register(
            SigningAlgorithm.HMAC_SHA256, HmacSigner.from_material, replace=True
        )
        assert registry.supports(SigningAlgorithm.HMAC_SHA256)


class TestMultiKeySigner:
    def test_signs_with_the_active_key_only(self) -> None:
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        assert multi.key_id == "key-b"
        assert multi.sign(PAYLOAD) == signer_b().sign(PAYLOAD)
        assert multi.verifying_key_ids == {"key-a", "key-b"}

    def test_verifies_tokens_from_every_active_key(self) -> None:
        old_signature = signer_a().sign(PAYLOAD)
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        assert multi.verify_with(key_id="key-a", payload=PAYLOAD, signature=old_signature)
        assert multi.verify(PAYLOAD, old_signature)

    def test_a_key_still_rejects_another_keys_signature(self) -> None:
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        assert not multi.verify_with(
            key_id="key-b", payload=PAYLOAD, signature=signer_a().sign(PAYLOAD)
        )

    def test_unknown_key_id_raises_rather_than_returning_false(self) -> None:
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        with pytest.raises(TokenSignatureError):
            multi.verify_with(key_id="key-z", payload=PAYLOAD, signature="x")
        with pytest.raises(TokenSignatureError):
            multi.algorithm_for("key-z")

    def test_duplicate_key_ids_refuse_to_start(self) -> None:
        with pytest.raises(ConfigurationError):
            MultiKeySigner(active=signer_a(), additional=[HmacSigner(key_id="key-a", secret=SECRET_B)])

    def test_rotation_moves_the_signing_key_and_keeps_verification(self) -> None:
        multi = MultiKeySigner(active=signer_a(), additional=[signer_b()])
        rotated = multi.rotate_to("key-b")
        assert rotated.key_id == "key-b"
        assert rotated.verifying_key_ids == {"key-a", "key-b"}
        # The original object is untouched: in-flight verifications cannot be
        # invalidated by someone else's rotation.
        assert multi.key_id == "key-a"

    def test_rotation_to_an_unknown_key_refuses(self) -> None:
        with pytest.raises(ConfigurationError):
            MultiKeySigner(active=signer_a()).rotate_to("key-z")

    def test_retiring_a_key_stops_verification_for_it(self) -> None:
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        retired = multi.retire("key-a")
        assert retired.verifying_key_ids == {"key-b"}
        with pytest.raises(TokenSignatureError):
            retired.verify_with(key_id="key-a", payload=PAYLOAD, signature="x")

    def test_retiring_the_active_key_refuses(self) -> None:
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        with pytest.raises(ConfigurationError):
            multi.retire("key-b")

    def test_satisfies_the_keyed_verifier_protocol(self) -> None:
        assert isinstance(MultiKeySigner(active=signer_a()), KeyedVerifier)
        assert not isinstance(signer_a(), KeyedVerifier)


class TestVerifySignatureHelper:
    def test_single_key_signer_verifies_its_own_key(self) -> None:
        signer = signer_a()
        assert verify_signature(
            signer,
            key_id="key-a",
            key_alg=SigningAlgorithm.HMAC_SHA256,
            payload=PAYLOAD,
            signature=signer.sign(PAYLOAD),
        )

    def test_single_key_signer_rejects_a_foreign_key_id(self) -> None:
        signer = signer_a()
        with pytest.raises(TokenSignatureError):
            verify_signature(
                signer,
                key_id="key-z",
                key_alg=SigningAlgorithm.HMAC_SHA256,
                payload=PAYLOAD,
                signature=signer.sign(PAYLOAD),
            )

    def test_algorithm_confusion_is_refused(self) -> None:
        # A token naming an algorithm its key does not use cannot have been
        # minted by this harness, so it is an error, not a failed comparison.
        signer = signer_a()
        with pytest.raises(TokenSignatureError):
            verify_signature(
                signer,
                key_id="key-a",
                key_alg=SigningAlgorithm.ED25519,
                payload=PAYLOAD,
                signature=signer.sign(PAYLOAD),
            )
        with pytest.raises(TokenSignatureError):
            verify_signature(
                MultiKeySigner(active=signer),
                key_id="key-a",
                key_alg=SigningAlgorithm.ED25519,
                payload=PAYLOAD,
                signature=signer.sign(PAYLOAD),
            )

    def test_multi_key_signer_verifies_the_named_key(self) -> None:
        signature = signer_a().sign(PAYLOAD)
        multi = MultiKeySigner(active=signer_b(), additional=[signer_a()])
        assert verify_signature(
            multi,
            key_id="key-a",
            key_alg=SigningAlgorithm.HMAC_SHA256,
            payload=PAYLOAD,
            signature=signature,
        )
        assert not verify_signature(
            multi,
            key_id="key-b",
            key_alg=SigningAlgorithm.HMAC_SHA256,
            payload=PAYLOAD,
            signature=signature,
        )
