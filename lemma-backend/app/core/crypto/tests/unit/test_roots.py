"""Root keys: the local keyset root, Cloud KMS, and refusing a bad configuration."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet

from app.core.crypto.aad import encode_aad
from app.core.crypto.aead import InvalidTag, new_key, open_sealed, seal
from app.core.crypto.crc32c import crc32c
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.roots import factory as root_factory
from app.core.crypto.roots.gcp_kms import GcpKmsRoot
from app.core.crypto.roots.kms_client import CloudKms, crypto_key_of
from app.core.crypto.roots.local import LocalKeyringRoot
from app.core.crypto.roots.ports import RootKeyError

pytestmark = pytest.mark.unit

KEY = "projects/p/locations/global/keyRings/r/cryptoKeys/k"


def _keyring(*kids: str, primary: str) -> Keyring:
    return Keyring(
        primary_kid=primary,
        keys={kid: KeyMaterial(kid, Fernet.generate_key()) for kid in kids},
    )


# ---------------------------------------------------------------- primitives
def test_aad_parts_cannot_be_rearranged_into_the_same_bytes():
    assert encode_aad("ab", "c") != encode_aad("a", "bc")
    assert encode_aad("a", "") != encode_aad("a")


def test_sealed_value_opens_only_under_its_own_context():
    key = new_key()
    sealed = seal(key, b"token", b"context-a")
    assert open_sealed(key, sealed, b"context-a") == b"token"
    with pytest.raises(InvalidTag):
        open_sealed(key, sealed, b"context-b")
    with pytest.raises(InvalidTag):
        open_sealed(new_key(), sealed, b"context-a")
    with pytest.raises(InvalidTag):
        open_sealed(key, sealed[:-1] + bytes([sealed[-1] ^ 1]), b"context-a")
    with pytest.raises(InvalidTag):
        open_sealed(key, sealed[:5], b"context-a")


def test_crc32c_matches_the_castagnoli_check_value():
    # The standard CRC-32C check value for "123456789".
    assert crc32c(b"123456789") == 0xE3069283


# ---------------------------------------------------------------- local root
async def test_local_root_round_trips_and_is_bound_to_its_aad():
    root = LocalKeyringRoot("static", lambda: _keyring("a", primary="a"))
    wrapped = await root.wrap(b"k" * 32, aad=b"kek-1")
    assert wrapped.root_ref == "local:a"
    assert await root.unwrap(wrapped.root_ref, wrapped.blob, aad=b"kek-1") == b"k" * 32
    with pytest.raises(RootKeyError):
        await root.unwrap(wrapped.root_ref, wrapped.blob, aad=b"kek-2")


async def test_a_new_primary_asks_for_a_rewrap_and_the_old_entry_still_opens():
    keys = _keyring("a", "b", primary="a")
    old = await LocalKeyringRoot("static", lambda: keys).wrap(b"k" * 32, aad=b"x")
    rotated = Keyring(primary_kid="b", keys=keys.keys)
    root = LocalKeyringRoot("static", lambda: rotated)

    assert root.needs_rewrap(old.root_ref) is True
    assert await root.unwrap(old.root_ref, old.blob, aad=b"x") == b"k" * 32
    assert root.needs_rewrap((await root.wrap(b"k" * 32, aad=b"x")).root_ref) is False


async def test_removing_a_keyset_entry_too_early_says_what_to_do():
    wrapped = await LocalKeyringRoot("static", lambda: _keyring("a", primary="a")).wrap(
        b"k" * 32, aad=b"x"
    )
    root = LocalKeyringRoot("static", lambda: _keyring("b", primary="b"))
    with pytest.raises(RootKeyError, match="put\\s+it back"):
        await root.unwrap(wrapped.root_ref, wrapped.blob, aad=b"x")


async def test_a_kms_wrapped_key_is_refused_by_a_local_root():
    root = LocalKeyringRoot("static", lambda: _keyring("a", primary="a"))
    with pytest.raises(RootKeyError, match="gcp_kms"):
        await root.unwrap(f"gcp_kms:{KEY}/cryptoKeyVersions/1", b"blob", aad=b"x")


# ------------------------------------------------------------------ cloud kms
@dataclass
class _Encrypted:
    name: str
    ciphertext: bytes
    ciphertext_crc32c: int
    verified_plaintext_crc32c: bool = True
    verified_additional_authenticated_data_crc32c: bool = True


@dataclass
class _Decrypted:
    plaintext: bytes
    plaintext_crc32c: int


class PermissionDenied(Exception):
    """Named like google.api_core's, which is how errors are explained."""


class _FakeKmsApi:
    """Cloud KMS reduced to AES-GCM under a per-version key, AAD included."""

    def __init__(self, primary_version: int = 1) -> None:
        self.keys = {1: new_key(), 2: new_key()}
        self.primary_version = primary_version
        self.requests: list[dict[str, object]] = []
        self.corrupt_response = False
        self.fail: Exception | None = None

    def encrypt(self, *, request, retry, timeout):
        self.requests.append({**request, "retry": retry, "timeout": timeout})
        if self.fail:
            raise self.fail
        name = str(request["name"])
        version = (
            int(name.rsplit("/", 1)[-1])
            if "cryptoKeyVersions" in name
            else self.primary_version
        )
        assert request["plaintext_crc32c"] == crc32c(request["plaintext"])
        aad = request.get("additional_authenticated_data", b"")
        ct = bytes([version]) + seal(self.keys[version], request["plaintext"], aad)
        crc = crc32c(ct) ^ (1 if self.corrupt_response else 0)
        return _Encrypted(f"{KEY}/cryptoKeyVersions/{version}", ct, crc)

    def decrypt(self, *, request, retry, timeout):
        self.requests.append({**request, "retry": retry, "timeout": timeout})
        if self.fail:
            raise self.fail
        assert "cryptoKeyVersions" not in str(request["name"])
        ct = request["ciphertext"]
        aad = request.get("additional_authenticated_data", b"")
        plaintext = open_sealed(self.keys[ct[0]], ct[1:], aad)
        return _Decrypted(plaintext, crc32c(plaintext))


def _kms(api: _FakeKmsApi) -> CloudKms:
    return CloudKms(
        api, retry="RETRY", error_types=(PermissionDenied,), timeout_seconds=7.0
    )


async def test_kms_root_round_trips_with_aad_checksums_timeout_and_retry():
    api = _FakeKmsApi()
    root = GcpKmsRoot(KEY, client=lambda: _kms(api))

    wrapped = await root.wrap(b"k" * 32, aad=b"kek-1")

    assert wrapped.root_ref == f"gcp_kms:{KEY}/cryptoKeyVersions/1"
    assert await root.unwrap(wrapped.root_ref, wrapped.blob, aad=b"kek-1") == b"k" * 32
    encrypt, decrypt = api.requests
    assert encrypt["additional_authenticated_data"] == b"kek-1"
    assert encrypt["additional_authenticated_data_crc32c"] == crc32c(b"kek-1")
    assert decrypt["ciphertext_crc32c"] == crc32c(wrapped.blob)
    assert encrypt["timeout"] == decrypt["timeout"] == 7.0
    assert encrypt["retry"] == decrypt["retry"] == "RETRY"


async def test_kms_unwrap_under_other_aad_fails():
    api = _FakeKmsApi()
    root = GcpKmsRoot(KEY, client=lambda: _kms(api))
    wrapped = await root.wrap(b"k" * 32, aad=b"kek-1")
    with pytest.raises(InvalidTag):
        await root.unwrap(wrapped.root_ref, wrapped.blob, aad=b"kek-2")


async def test_a_corrupted_kms_response_is_an_error_not_a_key():
    api = _FakeKmsApi()
    api.corrupt_response = True
    root = GcpKmsRoot(KEY, client=lambda: _kms(api))
    with pytest.raises(RootKeyError, match="checksum"):
        await root.wrap(b"k" * 32, aad=b"x")


async def test_kms_permission_error_names_the_role_to_grant():
    api = _FakeKmsApi()
    api.fail = PermissionDenied("no")
    root = GcpKmsRoot(KEY, client=lambda: _kms(api))
    with pytest.raises(RootKeyError, match="cryptoKeyEncrypterDecrypter"):
        await root.wrap(b"k" * 32, aad=b"x")


async def test_pinned_version_wraps_with_it_and_other_versions_need_rewrap():
    api = _FakeKmsApi()
    pinned = f"{KEY}/cryptoKeyVersions/2"
    root = GcpKmsRoot(KEY, key_version=pinned, client=lambda: _kms(api))

    wrapped = await root.wrap(b"k" * 32, aad=b"x")

    assert wrapped.root_ref == f"gcp_kms:{pinned}"
    assert root.needs_rewrap(wrapped.root_ref) is False
    assert root.needs_rewrap(f"gcp_kms:{KEY}/cryptoKeyVersions/1") is True


def test_unpinned_root_rewraps_only_after_moving_to_another_key():
    root = GcpKmsRoot(KEY, client=lambda: _kms(_FakeKmsApi()))
    assert root.needs_rewrap(f"gcp_kms:{KEY}/cryptoKeyVersions/9") is False
    assert (
        root.needs_rewrap(
            "gcp_kms:projects/p/locations/global/keyRings/r/cryptoKeys/old/cryptoKeyVersions/1"
        )
        is True
    )
    assert root.needs_rewrap("local:a") is True
    assert crypto_key_of(f"{KEY}/cryptoKeyVersions/3") == KEY


# ------------------------------------------------------------ configuration
class _Settings:
    def __init__(
        self, provider: str, environment: str = "production", **values: str | None
    ) -> None:
        self.provider = provider
        self.environment = environment
        self.gcp_kms_key_name = values.get("gcp_kms_key_name")
        self.gcp_secret_manager_secret_name = values.get(
            "gcp_secret_manager_secret_name"
        )

    def effective_secret_key_provider(self) -> str:
        return self.provider

    def is_local_mode(self) -> bool:
        return self.environment in {"local", "testing"}


class _CryptoSettings:
    def __init__(self, gcp_kms_key_version: str | None = None) -> None:
        self.gcp_kms_key_version = gcp_kms_key_version


@pytest.mark.parametrize(
    ("settings", "crypto", "message"),
    [
        (
            _Settings("gcp_kms", gcp_kms_key_name="my-key"),
            _CryptoSettings(),
            "GCP_KMS_KEY_NAME",
        ),
        (
            _Settings("gcp_kms", gcp_kms_key_name=KEY),
            _CryptoSettings(f"{KEY}-other/cryptoKeyVersions/1"),
            "GCP_KMS_KEY_VERSION",
        ),
        (_Settings("keychain"), _CryptoSettings(), "keychain"),
        (
            _Settings("gcp_secret_manager"),
            _CryptoSettings(),
            "GCP_SECRET_MANAGER_SECRET_NAME",
        ),
    ],
)
def test_a_hosted_process_refuses_a_root_that_cannot_work(settings, crypto, message):
    with pytest.raises(RootKeyError, match=message):
        root_factory.validate_root_key_config(settings, crypto)


def test_a_well_formed_kms_configuration_is_accepted():
    root_factory.validate_root_key_config(
        _Settings("gcp_kms", gcp_kms_key_name=KEY),
        _CryptoSettings(f"{KEY}/cryptoKeyVersions/4"),
    )
