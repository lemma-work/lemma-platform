"""Unit tests for the HKDF-based secret signer."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.signer import HkdfSecretSigner

pytestmark = pytest.mark.unit


def _signer(*entries: tuple[str, bytes], primary: str) -> HkdfSecretSigner:
    keyring = Keyring(
        primary_kid=primary,
        keys={kid: KeyMaterial(kid, key) for kid, key in entries},
    )
    return HkdfSecretSigner(lambda: keyring)


def test_sign_verify_round_trip_carries_kid():
    signer = _signer(("A", Fernet.generate_key()), primary="A")
    sig = signer.sign("widget-url", b"message")
    assert sig.startswith("A.")
    assert signer.verify("widget-url", b"message", sig) is True


def test_tampered_payload_fails():
    signer = _signer(("A", Fernet.generate_key()), primary="A")
    sig = signer.sign("widget-url", b"message")
    assert signer.verify("widget-url", b"tampered", sig) is False


def test_purpose_isolation():
    signer = _signer(("A", Fernet.generate_key()), primary="A")
    sig = signer.sign("widget-url", b"message")
    # Same key, different purpose → different subkey → must not verify.
    assert signer.verify("datastore-file-url", b"message", sig) is False


def test_rotation_grace_old_kid_still_verifies():
    key_a, key_b = Fernet.generate_key(), Fernet.generate_key()
    signer_a = _signer(("A", key_a), primary="A")
    old_sig = signer_a.sign("widget-url", b"m")

    # Rotate: B primary, A retained for the grace window.
    signer_ab = _signer(("A", key_a), ("B", key_b), primary="B")
    assert signer_ab.verify("widget-url", b"m", old_sig) is True  # retired A
    assert signer_ab.sign("widget-url", b"m").startswith("B.")  # new uses B


def test_unknown_kid_and_legacy_one_part_return_false():
    signer = _signer(("A", Fernet.generate_key()), primary="A")
    assert signer.verify("widget-url", b"m", "Z.deadbeef") is False  # unknown kid
    assert signer.verify("widget-url", b"m", "deadbeef") is False  # legacy 1-part
