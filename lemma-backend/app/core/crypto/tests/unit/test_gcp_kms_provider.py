"""Unit tests for the Cloud KMS envelope provider, using a fake KMS client.

Exercises the ``kms+fernet`` envelope path end-to-end through the real cipher,
plus the read-path unwrap cache, without needing a GCP project.
"""

from __future__ import annotations

import types

import pytest

from app.core.crypto.cipher import EnvelopeSecretCipher
from app.core.crypto.providers.gcp_kms import GcpKmsKeyProvider

pytestmark = pytest.mark.unit

KEY_NAME = "projects/p/locations/global/keyRings/r/cryptoKeys/k"
PRIMARY_VERSION = f"{KEY_NAME}/cryptoKeyVersions/2"


class _FakeKms:
    """Reversible stand-in: wrap == b'WRAP:' + dek."""

    def __init__(self) -> None:
        self.encrypt_calls = 0
        self.decrypt_calls = 0

    def encrypt(self, request):
        self.encrypt_calls += 1
        return types.SimpleNamespace(
            name=PRIMARY_VERSION, ciphertext=b"WRAP:" + request["plaintext"]
        )

    def decrypt(self, request):
        self.decrypt_calls += 1
        ct = request["ciphertext"]
        assert ct.startswith(b"WRAP:")
        return types.SimpleNamespace(plaintext=ct[len(b"WRAP:") :])

    def get_crypto_key(self, name):
        assert name == KEY_NAME
        return types.SimpleNamespace(
            primary=types.SimpleNamespace(name=PRIMARY_VERSION)
        )


def _provider() -> tuple[GcpKmsKeyProvider, _FakeKms]:
    provider = GcpKmsKeyProvider(key_name=KEY_NAME)
    fake = _FakeKms()
    provider._client = fake  # inject, bypassing lazy google import
    return provider, fake


def test_envelope_round_trip():
    provider, fake = _provider()
    cipher = EnvelopeSecretCipher(provider)

    enc = cipher.encrypt_json({"token": "xyz", "n": 3})
    assert enc["_encrypted"] == "lemma-secret-v2"
    assert enc["alg"] == "kms+fernet"
    assert enc["kid"] == "v2"  # KEK version from encrypt response
    assert "dek" in enc and "ct" in enc
    assert "xyz" not in str(enc)
    assert fake.encrypt_calls == 1  # one wrap per write

    assert cipher.decrypt_json(enc) == {"token": "xyz", "n": 3}


def test_unwrap_cache_avoids_repeat_kms_calls():
    # A fresh provider has nothing cached: first decrypt calls KMS, then caches.
    writer, _ = _provider()
    enc = EnvelopeSecretCipher(writer).encrypt_json({"a": "b"})

    reader, fake = _provider()
    cipher = EnvelopeSecretCipher(reader)
    assert cipher.decrypt_json(enc) == {"a": "b"}
    assert fake.decrypt_calls == 1
    cipher.decrypt_json(enc)
    cipher.decrypt_json(enc)
    assert fake.decrypt_calls == 1  # served from unwrap cache


def test_is_under_primary_key_tracks_kek_version():
    provider, _ = _provider()
    cipher = EnvelopeSecretCipher(provider)
    enc = cipher.encrypt_json({"a": "b"})
    assert cipher.is_under_primary_key(enc) is True  # kid v2 == primary v2

    enc_old = dict(enc, kid="v1")  # simulate a blob wrapped by an older version
    assert cipher.is_under_primary_key(enc_old) is False
