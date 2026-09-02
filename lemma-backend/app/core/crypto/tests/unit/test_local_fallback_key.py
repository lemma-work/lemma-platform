"""The published local fallback: what it costs, and what upgrading off it must not break.

`local_fallback_secret()` is `base64(sha256(b"lemma-local-connector-secret-key"))`
-- a literal in a public repository. It exists so a checkout runs with no
configuration, and for `ENVIRONMENT=testing` that is all it is. For a self-host
it means credentials at rest are encrypted with a key any reader can compute, so
two things have to hold:

* an install that is *still* on it is told so, at a level a log destination
  keeps;
* an install that moves *off* it -- `lemma-stack` now renders a per-installation
  `SECRET_ENCRYPTION_KEY` -- can still read the rows it wrote before.

The second is the one that turns a security fix into a data-loss incident if it
is wrong, so it is pinned against the real cipher rather than argued about.
"""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import keys as crypto_keys
from app.core.crypto.cipher import EnvelopeSecretCipher
from app.core.crypto.keys import (
    derive_kid,
    legacy_candidate_secrets,
    load_static_keyring,
    local_fallback_secret,
)
from app.core.crypto.providers.static import StaticKeyProvider

pytestmark = pytest.mark.unit


class _Settings:
    def __init__(self, environment: str, key: str | None) -> None:
        self.environment = environment
        self.secret_encryption_key = key
        self.secret_encryption_keyset = None

    def is_local_mode(self) -> bool:
        return self.environment in {"local", "testing"}


@pytest.fixture
def _no_legacy_env(monkeypatch):
    monkeypatch.delenv(crypto_keys.LEGACY_ENV_VAR, raising=False)


def _cipher(settings) -> EnvelopeSecretCipher:
    return EnvelopeSecretCipher(
        StaticKeyProvider(load_static_keyring()),
        legacy_secrets=legacy_candidate_secrets(),
    )


def test_rows_written_under_the_fallback_still_read_after_a_key_is_configured(
    monkeypatch, _no_legacy_env
):
    """The lemma-stack upgrade path: new key for new writes, old rows unharmed."""
    before = _Settings("local", None)
    monkeypatch.setattr(crypto_keys, "settings", before)
    old = _cipher(before)
    stored = old.encrypt_json({"api_key": "ROW-WRITTEN-BEFORE-THE-UPGRADE"})
    assert stored is not None
    assert stored["kid"] == derive_kid(local_fallback_secret())

    after = _Settings("local", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(crypto_keys, "settings", after)
    upgraded = _cipher(after)

    assert upgraded.decrypt_json(stored) == {"api_key": "ROW-WRITTEN-BEFORE-THE-UPGRADE"}
    # And a new write is under the new key, not the published one.
    fresh = upgraded.encrypt_json({"api_key": "NEW"})
    assert fresh is not None
    assert fresh["kid"] != stored["kid"]


def test_the_published_key_never_signs_once_an_installation_has_its_own(
    monkeypatch, _no_legacy_env
):
    """Retained for reading only.

    The keyring is also the *signing* keyring, and `HkdfSecretSigner.verify`
    accepts any key id it holds -- so a published key left in it would let
    anyone mint a valid download URL or embed token. Reading old ciphertext goes
    through the legacy candidates instead, which the signer never consults.
    """
    settings = _Settings("local", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(crypto_keys, "settings", settings)

    keyring = load_static_keyring()

    assert keyring.get(derive_kid(local_fallback_secret())) is None


def test_a_local_install_still_on_the_published_key_is_told_so(
    monkeypatch, caplog, _no_legacy_env
):
    monkeypatch.setattr(crypto_keys, "settings", _Settings("local", None))

    with caplog.at_level(logging.DEBUG):
        load_static_keyring()

    warnings = [
        record.msg["event"]
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert warnings == ["crypto.keys.published_local_encryption_key.degraded"]


def test_a_test_run_is_not_warned(monkeypatch, caplog, _no_legacy_env):
    """`testing` is what the constant is for; a warning per suite is noise."""
    monkeypatch.setattr(crypto_keys, "settings", _Settings("testing", None))

    with caplog.at_level(logging.DEBUG):
        load_static_keyring()

    assert [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ] == []
