"""A carried home is sealed as it streams, and opens only whole and unchanged."""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.crypto.factory import get_secret_cipher
from app.modules.test_support.vault_fake import StaticSealingKeys
from app.modules.workspace.services import sealed_stream
from app.modules.workspace.services.sealed_stream import (
    FRAME_BYTES,
    SealedStreamBroken,
    seal,
)

#: The vault's key-encryption keys, in memory.
KEYS = StaticSealingKeys()


def open_sealed(sealed, *, context: bytes, keyring=KEYS):
    return sealed_stream.open_sealed(sealed, context=context, keyring=keyring)


async def _stream(data: bytes, piece: int = 65536) -> AsyncIterator[bytes]:
    for start in range(0, len(data), piece):
        yield data[start : start + piece]


async def _collect(chunks: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in chunks])


async def _sealed(data: bytes, context: bytes, keyring=KEYS) -> bytes:
    return await _collect(seal(_stream(data), context=context, keyring=keyring))


@pytest.mark.parametrize(
    "size", [0, 1, FRAME_BYTES, FRAME_BYTES + 1, 2 * FRAME_BYTES + 12345]
)
async def test_what_is_sealed_opens_to_the_same_bytes(size: int) -> None:
    data = os.urandom(size)
    context = uuid4().bytes

    sealed = await _sealed(data, context)

    assert await _collect(open_sealed(_stream(sealed, 4096), context=context)) == data


async def test_the_sealed_bytes_do_not_carry_the_plaintext() -> None:
    secret = b"-----BEGIN OPENSSH PRIVATE KEY-----"

    sealed = await _sealed(secret * 1000, uuid4().bytes)

    assert secret not in sealed


async def test_a_carry_sealed_for_another_machine_does_not_open() -> None:
    sealed = await _sealed(b"ssh key", uuid4().bytes)

    with pytest.raises(SealedStreamBroken):
        await _collect(open_sealed(_stream(sealed), context=uuid4().bytes))


async def test_a_carry_cut_short_at_a_frame_boundary_does_not_open() -> None:
    context = uuid4().bytes
    sealed = await _sealed(os.urandom(2 * FRAME_BYTES + 10), context)
    # Drop the last frame: what is left is whole frames, none marked last.
    cut = sealed[: -(10 + 16)]

    with pytest.raises(SealedStreamBroken):
        await _collect(open_sealed(_stream(cut), context=context))


async def test_a_changed_byte_does_not_open() -> None:
    context = uuid4().bytes
    sealed = bytearray(await _sealed(b"x" * 1000, context))
    sealed[-20] ^= 1

    with pytest.raises(SealedStreamBroken):
        await _collect(open_sealed(_stream(bytes(sealed)), context=context))


async def test_a_plaintext_tarball_is_not_taken_for_a_sealed_one() -> None:
    with pytest.raises(SealedStreamBroken):
        await _collect(open_sealed(_stream(b"\x1f\x8b" + b"0" * 100), context=b"x"))


async def test_the_data_key_is_sealed_by_the_vault_and_names_no_key_in_the_clear() -> (
    None
):
    sealed = await _sealed(b"ssh key", uuid4().bytes)

    length = int.from_bytes(sealed[4:6], "big")
    assert sealed[6 : 6 + length].startswith(b"lvs1:")


async def test_a_carry_sealed_under_a_rotated_key_still_opens() -> None:
    """A move under way across a KEK rotation: the old key still opens."""
    keys = StaticSealingKeys()
    context = uuid4().bytes
    sealed = await _sealed(b"ssh key", context, keyring=keys)
    keys.rotate()

    assert await _collect(open_sealed(_stream(sealed), context=context, keyring=keys))


def _legacy_carry(data: bytes, context: bytes) -> bytes:
    """What the previous release wrote: the data key under the Fernet cipher."""
    key = AESGCM.generate_key(bit_length=256)
    wrapped = get_secret_cipher().encrypt_str(base64.b64encode(key).decode())
    assert wrapped is not None and wrapped.startswith("lsenc1:")
    prefix = os.urandom(7)
    header = b"LSS1" + len(wrapped).to_bytes(2, "big") + wrapped.encode() + prefix
    frame = AESGCM(key).encrypt(
        prefix + (0).to_bytes(4, "big") + b"\x01", data, context
    )
    return header + frame


async def test_a_carry_sealed_before_the_vault_still_opens() -> None:
    context = uuid4().bytes

    sealed = _legacy_carry(b"ssh key", context)

    assert await _collect(open_sealed(_stream(sealed), context=context)) == b"ssh key"


async def test_a_data_key_planted_in_the_clear_opens_nothing() -> None:
    context = uuid4().bytes
    key = AESGCM.generate_key(bit_length=256)
    clear = base64.b64encode(key)
    prefix = os.urandom(7)
    header = b"LSS1" + len(clear).to_bytes(2, "big") + clear + prefix
    frame = AESGCM(key).encrypt(
        prefix + (0).to_bytes(4, "big") + b"\x01", b"x", context
    )

    with pytest.raises(SealedStreamBroken):
        await _collect(open_sealed(_stream(header + frame), context=context))
