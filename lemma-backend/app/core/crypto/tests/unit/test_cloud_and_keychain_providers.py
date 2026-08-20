"""Provider tests that keep optional cloud and OS dependencies out of CI."""

from __future__ import annotations

import json
import sys
import types

from cryptography.fernet import Fernet
import pytest

from app.core.crypto.providers.gcp_secret_manager import GcpSecretManagerKeyProvider
from app.core.crypto.providers.keychain import KeychainKeyProvider

pytestmark = pytest.mark.unit


def _install_secretmanager(monkeypatch, client) -> None:
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    secretmanager = types.ModuleType("google.cloud.secretmanager")
    secretmanager.SecretManagerServiceClient = lambda: client
    cloud.secretmanager = secretmanager
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", secretmanager)


class _SecretManagerClient:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.requests: list[dict] = []

    def access_secret_version(self, *, request):
        self.requests.append(request)
        return types.SimpleNamespace(
            payload=types.SimpleNamespace(data=self.raw.encode())
        )


def _keyset(kid: str) -> str:
    return json.dumps(
        [{"kid": kid, "key": Fernet.generate_key().decode(), "primary": True}]
    )


def test_gcp_secret_manager_loads_latest_version_once(monkeypatch):
    client = _SecretManagerClient(_keyset("gcp1"))
    _install_secretmanager(monkeypatch, client)

    provider = GcpSecretManagerKeyProvider(
        "projects/demo/secrets/lemma-keyset"
    )

    assert provider.primary_kid == "gcp1"
    assert provider.encryption_keyring() is provider.signing_keyring()
    assert client.requests == [
        {"name": "projects/demo/secrets/lemma-keyset/versions/latest"}
    ]


def test_gcp_secret_manager_preserves_explicit_version(monkeypatch):
    client = _SecretManagerClient(_keyset("gcp1"))
    _install_secretmanager(monkeypatch, client)

    provider = GcpSecretManagerKeyProvider(
        "projects/demo/secrets/lemma-keyset/versions/7"
    )

    assert provider.primary_kid == "gcp1"
    assert client.requests == [
        {"name": "projects/demo/secrets/lemma-keyset/versions/7"}
    ]


def test_gcp_secret_manager_requires_a_secret_name():
    with pytest.raises(RuntimeError, match="GCP_SECRET_MANAGER_SECRET_NAME"):
        GcpSecretManagerKeyProvider("")


class _FakeKeychain:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.set_calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        return self.value

    def set_password(self, service: str, username: str, value: str) -> None:
        self.value = value
        self.set_calls.append((service, username, value))


def test_keychain_generates_and_caches_a_keyset(monkeypatch):
    fake = _FakeKeychain()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    provider = KeychainKeyProvider(service="test-service", username="test-user")

    assert provider.primary_kid == "kc1"
    assert provider.encryption_keyring() is provider.signing_keyring()
    assert len(fake.set_calls) == 1
    assert fake.set_calls[0][:2] == ("test-service", "test-user")


def test_keychain_reuses_an_existing_keyset(monkeypatch):
    raw = _keyset("existing")
    fake = _FakeKeychain(raw)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    provider = KeychainKeyProvider()

    assert provider.primary_kid == "existing"
    assert fake.set_calls == []
