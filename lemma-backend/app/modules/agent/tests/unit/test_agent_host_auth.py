"""Unit tests for Agent Host pairing codes and host credentials."""

from __future__ import annotations

from app.modules.agent.services.agent_host_auth import (
    generate_host_secret,
    generate_pairing_code,
    host_secret_hash,
    pairing_code_hash,
)


def test_pairing_code_hash_is_deterministic_without_retaining_code() -> None:
    assert pairing_code_hash("one-time-code") == pairing_code_hash("one-time-code")
    assert pairing_code_hash("one-time-code") != "one-time-code"


def test_generated_codes_and_secrets_have_entropy_and_differ() -> None:
    codes = {generate_pairing_code() for _ in range(8)}
    secrets = {generate_host_secret() for _ in range(8)}
    assert len(codes) == 8
    assert len(secrets) == 8
    assert all(len(code) >= 32 for code in codes)
    assert all(len(secret) >= 32 for secret in secrets)


def test_host_secret_hash_is_deterministic_sha256_without_retaining_secret() -> None:
    digest = host_secret_hash("host-secret")
    assert digest == host_secret_hash("host-secret")
    assert digest != "host-secret"
    assert len(digest) == 64
    assert host_secret_hash("host-secret") != host_secret_hash("other-secret")
