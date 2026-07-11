from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.core.config import settings
from app.modules.datastore.services.files import file_url as file_url_mod
from app.modules.datastore.services.files.file_url import (
    InvalidFileUrlToken,
    build_object_url,
    mint_object_token,
    verify_object_token,
)

KEY = "pods/abc/files/me/docs/report.pdf"


def test_verify_rejects_legacy_dev_secret_token():
    """A token forged with the old committed dev secret must no longer verify —
    the insecure raw-HMAC fallback has been removed (security_appsec-02)."""
    payload = json.dumps(
        {"k": KEY, "e": int(time.time()) + 3600}, separators=(",", ":")
    ).encode("utf-8")
    sig = hmac.new(
        b"lemma-dev-datastore-file-url-secret", payload, hashlib.sha256
    ).digest()

    def _b64e(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    forged = f"{_b64e(payload)}.{_b64e(sig)}"
    with pytest.raises(InvalidFileUrlToken):
        verify_object_token(forged)


def test_mint_and_verify_roundtrip():
    token = mint_object_token(object_key=KEY, expires_at_epoch=int(time.time()) + 3600)
    assert verify_object_token(token) == KEY


def test_verify_rejects_expired():
    token = mint_object_token(object_key=KEY, expires_at_epoch=int(time.time()) - 10)
    with pytest.raises(InvalidFileUrlToken):
        verify_object_token(token)


def test_verify_rejects_tampered_payload():
    token = mint_object_token(object_key=KEY, expires_at_epoch=int(time.time()) + 3600)
    sig_b64 = token.split(".", 1)[1]
    other = mint_object_token(
        object_key="pods/abc/files/secret.pdf",
        expires_at_epoch=int(time.time()) + 3600,
    )
    other_payload_b64 = other.split(".", 1)[0]
    forged = f"{other_payload_b64}.{sig_b64}"  # other payload, original signature
    with pytest.raises(InvalidFileUrlToken):
        verify_object_token(forged)


def test_verify_rejects_garbage():
    with pytest.raises(InvalidFileUrlToken):
        verify_object_token("not-a-real-token")
    with pytest.raises(InvalidFileUrlToken):
        verify_object_token("")


class _FakeStorage:
    """Minimal storage stub recording get_signed_url calls."""

    def __init__(self, signed="https://storage.googleapis.com/bucket/obj?sig=abc"):
        self._signed = signed
        self.calls: list[tuple[str, int]] = []

    async def get_signed_url(self, blob_name: str, expires_hours: int = 1) -> str:
        self.calls.append((blob_name, expires_hours))
        return self._signed


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["gcs", "s3", "azure"])
async def test_build_object_url_uses_native_cloud_signed_url(monkeypatch, backend):
    """On cloud backends the URL is the object store's own signed URL.

    A live bucket isn't available in-sandbox, so we force the backend to ``gcs``
    and assert ``build_object_url`` delegates to ``storage.get_signed_url`` with
    the expiry converted to whole hours.
    """
    monkeypatch.setattr(file_url_mod, "_get_url_cache", lambda: None)
    monkeypatch.setattr(settings, "storage_backend", backend)
    storage = _FakeStorage()

    url, _expires_at = await build_object_url(storage, KEY, expires_seconds=7200)

    assert url == storage._signed
    assert storage.calls == [(KEY, 2)]  # 7200s -> 2h


@pytest.mark.asyncio
async def test_build_object_url_local_serves_tokenized_backend_url(monkeypatch):
    """On the local backend the URL is a tokenized /public/datastore/files link
    and the object store is never asked to sign."""
    monkeypatch.setattr(file_url_mod, "_get_url_cache", lambda: None)
    monkeypatch.setattr(settings, "storage_backend", "local")
    storage = _FakeStorage()

    url, _expires_at = await build_object_url(storage, KEY, expires_seconds=60)

    assert "/public/datastore/files?token=" in url
    assert storage.calls == []  # local storage cannot sign; no delegation
