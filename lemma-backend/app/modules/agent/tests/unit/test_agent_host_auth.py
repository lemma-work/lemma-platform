"""Unit tests for Agent Host pairing proof and scoped device tokens."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.modules.agent.services.agent_host_auth import (
    InvalidAgentHostCredential,
    host_signature_payload,
    mint_agent_host_token,
    pairing_signature_payload,
    pairing_code_hash,
    public_key_fingerprint,
    verify_agent_host_token,
    verify_host_signature,
    verify_pairing_signature,
)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class _Signer:
    def sign(self, purpose: str, payload: bytes) -> str:
        del purpose
        return f"test.{_b64e(payload[::-1])}"

    def verify(self, purpose: str, payload: bytes, signature: str) -> bool:
        return signature == self.sign(purpose, payload)


@pytest.fixture(autouse=True)
def stub_secret_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.agent.services.agent_host_auth.get_secret_signer",
        lambda: _Signer(),
    )


def test_pairing_code_hash_is_deterministic_without_retaining_code() -> None:
    assert pairing_code_hash("one-time-code") == pairing_code_hash("one-time-code")
    assert pairing_code_hash("one-time-code") != "one-time-code"


def test_signed_host_proof_verifies_and_rejects_tampering() -> None:
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_text = _b64e(public)
    host_id = uuid4()
    timestamp = 10_000
    nonce = "nonce-that-is-long-enough"
    signature = _b64e(
        private_key.sign(
            host_signature_payload(
                host_id=host_id,
                nonce=nonce,
                timestamp=timestamp,
            )
        )
    )

    verify_host_signature(
        public_key=public_text,
        host_id=host_id,
        nonce=nonce,
        timestamp=timestamp,
        signature=signature,
        now_epoch=timestamp,
    )
    assert len(public_key_fingerprint(public_text)) == 64

    with pytest.raises(InvalidAgentHostCredential, match="invalid host signature"):
        verify_host_signature(
            public_key=public_text,
            host_id=host_id,
            nonce=f"{nonce}-tampered",
            timestamp=timestamp,
            signature=signature,
            now_epoch=timestamp,
        )

    with pytest.raises(InvalidAgentHostCredential, match="clock skew"):
        verify_host_signature(
            public_key=public_text,
            host_id=host_id,
            nonce=nonce,
            timestamp=timestamp,
            signature=signature,
            now_epoch=timestamp + 121,
        )


def test_pairing_proves_possession_of_submitted_key() -> None:
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key = _b64e(public)
    timestamp = 1_800_000_000
    signature = _b64e(
        private_key.sign(
            pairing_signature_payload(
                pairing_code="pairing-code-with-enough-entropy",
                installation_id="installation-1",
                nonce="nonce-with-enough-entropy",
                timestamp=timestamp,
            )
        )
    )

    verify_pairing_signature(
        public_key=public_key,
        pairing_code="pairing-code-with-enough-entropy",
        installation_id="installation-1",
        nonce="nonce-with-enough-entropy",
        timestamp=timestamp,
        signature=signature,
        now_epoch=timestamp,
    )
    with pytest.raises(InvalidAgentHostCredential, match="invalid pairing signature"):
        verify_pairing_signature(
            public_key=public_key,
            pairing_code="different-pairing-code",
            installation_id="installation-1",
            nonce="nonce-with-enough-entropy",
            timestamp=timestamp,
            signature=signature,
            now_epoch=timestamp,
        )


def test_device_token_is_scoped_and_expires() -> None:
    host_id = uuid4()
    user_id = uuid4()
    token, expires_at = mint_agent_host_token(
        host_id=host_id,
        user_id=user_id,
        organization_id=None,
        now_epoch=100,
        ttl_seconds=10,
    )

    claims = verify_agent_host_token(
        token,
        required_capability="events",
        now_epoch=109,
    )
    assert claims.host_id == host_id
    assert claims.user_id == user_id
    assert int(expires_at.timestamp()) == 110

    with pytest.raises(InvalidAgentHostCredential, match="expired"):
        verify_agent_host_token(
            token,
            required_capability="events",
            now_epoch=111,
        )

    with pytest.raises(InvalidAgentHostCredential, match="lacks"):
        verify_agent_host_token(
            token,
            required_capability="admin",
            now_epoch=109,
        )
