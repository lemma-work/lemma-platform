"""The vault cutover migration's frozen legacy decoders.

The migration is the only code that will ever read the retired formats again,
once. These pin the one path a local database cannot exercise -- ``kms+fernet``,
through a fake KMS -- and the plaintext pass-throughs.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

pytestmark = pytest.mark.unit


# ----------------------------------------------- the migration's old decoders
def _migration():
    path = (
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-01_vault_cutover_0044.py"
    )
    spec = importlib.util.spec_from_file_location("vault_0044", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeKms:
    def __init__(self, dek: bytes) -> None:
        self.dek = dek
        self.calls: list[tuple[str, bytes, bytes]] = []

    def decrypt(self, key_name: str, ciphertext: bytes, aad: bytes) -> bytes:
        self.calls.append((key_name, ciphertext, aad))
        return self.dek


def test_the_migration_reads_kms_wrapped_secrets_through_kms():
    """The one legacy format a local database cannot produce: ``kms+fernet``."""
    legacy = _migration()._Legacy()
    dek = Fernet.generate_key()
    legacy._kms = _FakeKms(dek)
    legacy._kms_key = "projects/p/locations/l/keyRings/r/cryptoKeys/k"
    envelope = {
        "_encrypted": "lemma-secret-v2",
        "kid": "v3",
        "alg": "kms+fernet",
        "ct": base64.urlsafe_b64encode(
            Fernet(dek).encrypt(json.dumps({"a": 1}).encode())
        ).decode(),
        "dek": base64.urlsafe_b64encode(b"wrapped-dek").decode(),
    }

    assert legacy.json(envelope) == {"a": 1}
    assert legacy._kms.calls == [(legacy._kms_key, b"wrapped-dek", b"")]


def test_the_migration_passes_plaintext_and_json_null_through():
    legacy = _migration()._Legacy()
    assert legacy.json({"bot_token": "x"}) == {"bot_token": "x"}
    assert legacy.json("null") is None
    assert legacy.text("plain-secret") == "plain-secret"
    assert legacy.text("") is None
