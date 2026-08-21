"""Unit tests for the re-encryption decision logic (DB-free).

The full DB walker (`reencrypt_all`) is covered by the rotation smoke against a
real Postgres; here we pin the per-value decisions that make rotation correct
and idempotent.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from app.core.crypto.cipher import EnvelopeSecretCipher
from app.core.crypto.keys import local_fallback_secret
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.providers.static import StaticKeyProvider
from app.core.crypto.rotation import EncryptedColumn, _reencrypt_value

pytestmark = pytest.mark.unit

JSON_COL = EncryptedColumn("t", "c", "json", "t.c")
STR_COL = EncryptedColumn("t", "c", "str", "t.c")


def _cipher(
    keyring: Keyring, legacy: list[bytes] | None = None
) -> EnvelopeSecretCipher:
    return EnvelopeSecretCipher(StaticKeyProvider(keyring), legacy_secrets=legacy)


def _ring(*entries: tuple[str, bytes], primary: str) -> Keyring:
    return Keyring(primary, {kid: KeyMaterial(kid, key) for kid, key in entries})


def test_json_under_primary_is_skipped():
    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"))
    blob = cipher.encrypt_json({"x": 1})
    assert _reencrypt_value(cipher, JSON_COL, json.dumps(blob), force=False) is None


def test_json_under_retired_key_is_reencrypted():
    key_a, key_b = Fernet.generate_key(), Fernet.generate_key()
    old = _cipher(_ring(("A", key_a), primary="A")).encrypt_json({"x": 1})

    cipher = _cipher(_ring(("A", key_a), ("B", key_b), primary="B"))
    out = _reencrypt_value(cipher, JSON_COL, json.dumps(old), force=False)
    assert out is not None
    new = json.loads(out)
    assert new["kid"] == "B"
    assert cipher.decrypt_json(new) == {"x": 1}


def test_json_legacy_v1_is_reencrypted_to_v2():
    legacy = local_fallback_secret()
    payload = json.dumps({"x": 1}, sort_keys=True, separators=(",", ":")).encode()
    v1 = {
        "_encrypted": "fernet-json-v1",
        "ciphertext": Fernet(legacy).encrypt(payload).decode(),
    }

    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"), legacy=[legacy])
    out = _reencrypt_value(cipher, JSON_COL, json.dumps(v1), force=False)
    assert out is not None
    assert json.loads(out)["_encrypted"] == "lemma-secret-v2"


def test_json_null_is_skipped():
    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"))
    assert _reencrypt_value(cipher, JSON_COL, "null", force=False) is None


def test_force_reencrypts_even_under_primary():
    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"))
    blob = cipher.encrypt_json({"x": 1})
    out = _reencrypt_value(cipher, JSON_COL, json.dumps(blob), force=True)
    assert out is not None  # rewritten despite already being under primary


def test_str_plaintext_is_encrypted_then_idempotent():
    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"))
    out = _reencrypt_value(cipher, STR_COL, "plain-webhook-secret", force=False)
    assert out is not None and out.startswith("lsenc1:")
    assert cipher.decrypt_str(out) == "plain-webhook-secret"
    # second pass: already under primary → skipped
    assert _reencrypt_value(cipher, STR_COL, out, force=False) is None


def test_str_empty_is_skipped():
    cipher = _cipher(_ring(("A", Fernet.generate_key()), primary="A"))
    assert _reencrypt_value(cipher, STR_COL, "", force=False) is None
