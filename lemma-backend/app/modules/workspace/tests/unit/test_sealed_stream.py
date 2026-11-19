"""A carried home is sealed as it streams, and opens only whole and unchanged."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from app.modules.workspace.services.sealed_stream import (
    FRAME_BYTES,
    SealedStreamBroken,
    open_sealed,
    seal,
)


async def _stream(data: bytes, piece: int = 65536) -> AsyncIterator[bytes]:
    for start in range(0, len(data), piece):
        yield data[start : start + piece]


async def _collect(chunks: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in chunks])


async def _sealed(data: bytes, context: bytes) -> bytes:
    return await _collect(seal(_stream(data), context=context))


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
