"""The vault's crypto, without a database: binding, sealing, signing keys."""

from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.core.crypto.aead import InvalidTag, new_key
from app.core.crypto.ports import KeyMaterial, Keyring
from app.modules.test_support.vault_fake import StaticSealingKeys
from app.modules.vault.contracts import (
    SealedValueInvalid,
    SecretKind,
    SecretScope,
    open_json,
    open_text,
    seal_value,
)
from app.modules.vault.domain.types import Revealed
from app.modules.vault.services import keyring as keyring_module
from app.modules.vault.services.envelope import open_value, seal_value as seal_secret
from app.modules.vault.domain.errors import KeyNotKept, VaultUnavailable

pytestmark = pytest.mark.unit


def _sealed(scope: SecretScope, purpose: str = "p.q"):
    kek_id, kek, secret_id = uuid4(), new_key(), uuid4()
    wrapped, ct = seal_secret(
        kek_id=kek_id,
        kek=kek,
        secret_id=secret_id,
        version=1,
        kind=SecretKind.TEXT,
        purpose=purpose,
        scope=scope,
        payload=b"value",
    )
    return {
        "kek_id": kek_id,
        "kek": kek,
        "secret_id": secret_id,
        "version": 1,
        "kind": SecretKind.TEXT,
        "purpose": purpose,
        "scope": scope,
        "wrapped_dek": wrapped,
        "ciphertext": ct,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"scope": SecretScope(uuid4())},
        {"purpose": "p.other"},
        {"version": 2},
        {"kind": SecretKind.JSON},
        {"secret_id": uuid4()},
        {"kek_id": uuid4()},
    ],
)
def test_a_value_opens_only_in_the_context_it_was_sealed_for(change):
    sealed = _sealed(SecretScope(uuid4(), pod_id=uuid4()))
    assert open_value(**sealed) == b"value"
    with pytest.raises(InvalidTag):
        open_value(**{**sealed, **change})


def test_scope_needs_an_organization_below_it():
    with pytest.raises(ValueError):
        SecretScope(organization_id=None, pod_id=uuid4())


def test_revealed_never_prints_its_value():
    revealed = Revealed(uuid4(), 1, SecretKind.TEXT, None, b"sk-live-123")
    assert "sk-live" not in repr(revealed) and "sk-live" not in str(revealed)
    assert revealed.text() == "sk-live-123"
    with pytest.raises(TypeError):
        revealed.json()


# ------------------------------------------------------------------ sealer
async def test_a_sealed_value_opens_only_where_it_was_bound():
    keys = StaticSealingKeys()
    token = await seal_value(
        {"LEMMA_TOKEN": "t"},
        purpose="workspace.env_cache",
        bindings=["workspace:env:v3:a"],
        keyring=keys,
    )

    assert token.startswith("lvs1:") and "LEMMA_TOKEN" not in token
    assert await open_json(
        token,
        purpose="workspace.env_cache",
        bindings=["workspace:env:v3:a"],
        keyring=keys,
    ) == {"LEMMA_TOKEN": "t"}
    for purpose, bindings in (
        ("workspace.env_cache", ["workspace:env:v3:b"]),
        ("other.purpose", ["workspace:env:v3:a"]),
    ):
        with pytest.raises(SealedValueInvalid):
            await open_json(token, purpose=purpose, bindings=bindings, keyring=keys)
    with pytest.raises(SealedValueInvalid):
        await open_text(
            token,
            purpose="workspace.env_cache",
            bindings=["workspace:env:v3:a"],
            keyring=keys,
        )


async def test_a_value_sealed_before_a_rotation_still_opens():
    keys = StaticSealingKeys()
    token = await seal_value("ghs_token", purpose="p.q", bindings=["k"], keyring=keys)
    keys.rotate()
    assert (
        await open_text(token, purpose="p.q", bindings=["k"], keyring=keys)
        == "ghs_token"
    )


@pytest.mark.parametrize(
    "junk",
    ["", "plain", "lvs1:!!!", "lvs1:" + base64.urlsafe_b64encode(b"x" * 10).decode()],
)
async def test_anything_else_is_invalid_not_an_exception_of_another_kind(junk):
    with pytest.raises(SealedValueInvalid):
        await open_text(junk, purpose="p.q", bindings=[], keyring=StaticSealingKeys())


# -------------------------------------------------------------- signing keys
def _key(purpose: str, state: str = "active"):
    key_id = uuid4()
    return key_id, keyring_module._Key(key_id, purpose, state, new_key())


def test_a_local_keyset_stays_the_signing_primary_and_vault_keys_still_verify():
    local = Keyring("s1", {"s1": KeyMaterial("s1", Fernet.generate_key())})
    sign_id, sign = _key("sign")
    enc_id, enc = _key("encrypt")

    keyring = keyring_module._signing_keyring(
        {sign_id: sign, enc_id: enc}, {"sign": sign_id, "encrypt": enc_id}, local
    )

    assert keyring.primary_kid == "s1"
    assert keyring.get(keyring_module.signing_kid(sign_id)) is not None
    assert keyring.get(keyring_module.signing_kid(enc_id)) is None


def test_without_a_local_key_the_vault_signing_key_is_primary():
    sign_id, sign = _key("sign")

    keyring = keyring_module._signing_keyring({sign_id: sign}, {"sign": sign_id}, None)

    assert keyring.primary_kid == keyring_module.signing_kid(sign_id)


class _Keys:
    """A keyring that seals, then fails the way it is told to on opening."""

    def __init__(self) -> None:
        self.key_id, self.key = uuid4(), new_key()
        self.failure: Exception | None = None

    async def active_encryption_key(self):
        return self.key_id, self.key

    async def encryption_key(self, key_id):
        if self.failure is not None:
            raise self.failure
        return self.key


async def test_a_seal_under_a_key_no_longer_kept_is_absent_but_an_outage_is_not():
    keys = _Keys()
    sealed = await seal_value("v", purpose="p", bindings=["b"], keyring=keys)

    keys.failure = KeyNotKept(keys.key_id)
    with pytest.raises(SealedValueInvalid):
        await open_text(sealed, purpose="p", bindings=["b"], keyring=keys)

    # Keys that could not be loaded say nothing about this one: not a miss.
    keys.failure = VaultUnavailable("more than 1000 unretired vault keys")
    with pytest.raises(VaultUnavailable) as raised:
        await open_text(sealed, purpose="p", bindings=["b"], keyring=keys)
    assert not isinstance(raised.value, KeyNotKept)
