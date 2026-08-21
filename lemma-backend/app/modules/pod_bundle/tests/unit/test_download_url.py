"""Signed download-token mint/verify — valid, tampered, expired."""

import time
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.errors import BundleJobExpiredError
from app.modules.pod_bundle.infrastructure.download_url import (
    DOWNLOAD_PATH,
    build_download_url,
    mint_download_token,
    verify_download_token,
)


def test_mint_then_verify_round_trip():
    job_id = uuid4()
    token = mint_download_token(kind="pod-exports", job_id=job_id, ttl_seconds=3600)
    kind, got_id = verify_download_token(token)
    assert kind == "pod-exports"
    assert got_id == job_id


def test_expired_token_rejected():
    token = mint_download_token(kind="pod-exports", job_id=uuid4(), ttl_seconds=1)
    # Verify as if the clock is well past the expiry.
    with pytest.raises(BundleJobExpiredError):
        verify_download_token(token, now_epoch=int(time.time()) + 10)


def test_tampered_payload_rejected():
    token = mint_download_token(kind="pod-exports", job_id=uuid4(), ttl_seconds=3600)
    payload_b64, signature = token.split(".", 1)
    # Flip a character in the signed payload — signature no longer matches.
    mutated = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    with pytest.raises(BundleJobExpiredError):
        verify_download_token(f"{mutated}.{signature}")


def test_garbage_token_rejected():
    with pytest.raises(BundleJobExpiredError):
        verify_download_token("not-a-real-token")


def test_unknown_kind_rejected(monkeypatch):
    # A validly-signed token whose kind is not a staging kind must be refused.
    import json

    from app.modules.pod_bundle.infrastructure import download_url as m

    payload = json.dumps(
        {"k": "pod-secrets", "i": str(uuid4()), "e": int(time.time()) + 60}
    ).encode()
    from app.core.crypto import get_secret_signer

    token = f"{m._b64e(payload)}.{get_secret_signer().sign(m._PURPOSE, payload)}"
    with pytest.raises(BundleJobExpiredError):
        verify_download_token(token)


def test_build_download_url_shape():
    url = build_download_url(kind="pod-exports", job_id=uuid4(), ttl_seconds=3600)
    assert DOWNLOAD_PATH in url
    assert "token=" in url
    # The token embedded in the URL round-trips through verify.
    token = url.split("token=", 1)[1]
    from urllib.parse import unquote

    kind, _ = verify_download_token(unquote(token))
    assert kind == "pod-exports"
