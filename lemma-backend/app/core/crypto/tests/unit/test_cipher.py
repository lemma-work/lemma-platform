"""Unit tests for the system-wide secret cipher.

Covers: v2 round-trips (json + string), legacy v1 read, key rotation
(read-old/write-new, drop-old), and the rotation `is_under_primary_key` aid.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from app.core.crypto.cipher import EnvelopeSecretCipher
from app.core.crypto.keys import local_fallback_secret
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.providers.static import StaticKeyProvider

pytestmark = pytest.mark.unit


def _keyring(*entries: tuple[str, bytes], primary: str) -> Keyring:
    return Keyring(
        primary_kid=primary,
        keys={kid: KeyMaterial(kid, key) for kid, key in entries},
    )


def _cipher(keyring: Keyring, legacy: list[bytes] | None = None) -> EnvelopeSecretCipher:
    return EnvelopeSecretCipher(StaticKeyProvider(keyring), legacy_secrets=legacy)


def test_encrypt_json_round_trip_emits_v2_envelope():
    key = Fernet.generate_key()
    cipher = _cipher(_keyring(("k1", key), primary="k1"))

    # Long unique sentinel so a substring check can't collide with base64.
    secret = "ACCESS-TOKEN-PLAINTEXT-SENTINEL-9f1c2"
    enc = cipher.encrypt_json({"access_token": secret, "n": 1})

    assert enc is not None
    assert enc["_encrypted"] == "lemma-secret-v2"
    assert enc["alg"] == "fernet"
    assert enc["kid"] == "k1"
    assert secret not in json.dumps(enc)  # plaintext never present
    assert cipher.decrypt_json(enc) == {"access_token": secret, "n": 1}


def test_encrypt_json_is_idempotent_on_already_encrypted():
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    enc = cipher.encrypt_json({"a": "b"})
    assert cipher.encrypt_json(enc) == enc  # not double-wrapped


@pytest.mark.asyncio
async def test_encrypt_json_async_round_trip_off_loop():
    # The connector credential repos call encrypt_json_async/decrypt_json_async on
    # the REAL cipher (get_secret_cipher() → EnvelopeSecretCipher), so those async
    # methods must exist here and round-trip — not just on the deprecated adapter.
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    secret = "ASYNC-ACCESS-TOKEN-SENTINEL-7b3d1"
    enc = await cipher.encrypt_json_async({"access_token": secret})
    assert enc is not None and secret not in json.dumps(enc)
    assert await cipher.decrypt_json_async(enc) == {"access_token": secret}
    assert await cipher.encrypt_json_async(None) is None


def test_none_passthrough():
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    assert cipher.encrypt_json(None) is None
    assert cipher.decrypt_json(None) is None
    assert cipher.encrypt_str(None) is None
    assert cipher.decrypt_str(None) is None


def test_unencrypted_values_pass_through_decrypt():
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    plain = {"not": "encrypted"}
    assert cipher.decrypt_json(plain) == plain


def test_string_round_trip():
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    enc = cipher.encrypt_str("webhook-secret-token")
    assert enc is not None and enc.startswith("lsenc1:")
    assert "webhook-secret-token" not in enc
    assert cipher.decrypt_str(enc) == "webhook-secret-token"
    assert cipher.encrypt_str(enc) == enc  # idempotent


def test_reads_legacy_v1_envelope():
    legacy_key = local_fallback_secret()
    payload = json.dumps({"a": "legacy"}, sort_keys=True, separators=(",", ":")).encode()
    v1 = {
        "_encrypted": "fernet-json-v1",
        "ciphertext": Fernet(legacy_key).encrypt(payload).decode("ascii"),
    }
    cipher = _cipher(
        _keyring(("k1", Fernet.generate_key()), primary="k1"),
        legacy=[legacy_key],
    )
    assert cipher.decrypt_json(v1) == {"a": "legacy"}
    # legacy blobs are not "under primary" → the rotation walker upgrades them.
    assert cipher.is_under_primary_key(v1) is False


def test_v1_without_legacy_key_raises():
    cipher = _cipher(_keyring(("k1", Fernet.generate_key()), primary="k1"))
    v1 = {"_encrypted": "fernet-json-v1", "ciphertext": "x"}
    with pytest.raises(RuntimeError):
        cipher.decrypt_json(v1)


def test_rotation_read_old_write_new_then_drop_old():
    key_a, key_b = Fernet.generate_key(), Fernet.generate_key()

    # Encrypt under A.
    cipher_a = _cipher(_keyring(("A", key_a), primary="A"))
    blob = cipher_a.encrypt_json({"v": "x"})
    assert blob["kid"] == "A"

    # Rotate: B primary, A retained. Old blob still readable; not under primary.
    cipher_ab = _cipher(_keyring(("A", key_a), ("B", key_b), primary="B"))
    assert cipher_ab.decrypt_json(blob) == {"v": "x"}
    assert cipher_ab.is_under_primary_key(blob) is False

    # Re-encrypt forward → now under B.
    reblob = cipher_ab.encrypt_json(cipher_ab.decrypt_json(blob))
    assert reblob["kid"] == "B"
    assert cipher_ab.is_under_primary_key(reblob) is True

    # Drop A entirely: B-blob ok, A-blob unreadable.
    cipher_b = _cipher(_keyring(("B", key_b), primary="B"))
    assert cipher_b.decrypt_json(reblob) == {"v": "x"}
    with pytest.raises(Exception):
        cipher_b.decrypt_json(blob)


def test_is_under_primary_key_for_string_envelope():
    key_a, key_b = Fernet.generate_key(), Fernet.generate_key()
    cipher_a = _cipher(_keyring(("A", key_a), primary="A"))
    enc = cipher_a.encrypt_str("tok")

    cipher_b = _cipher(_keyring(("A", key_a), ("B", key_b), primary="B"))
    assert cipher_b.is_under_primary_key(enc) is False
    assert cipher_b.is_under_primary_key("plain-not-encrypted") is False
