"""Sealing a stream too large to hold, the way the login copies are sealed.

A login copy is one small archive sealed as one AES-256-GCM message under a
fresh data key, the key wrapped by the deployment's secret cipher. A home
carried across a size move is the same kind of secret -- SSH keys, ``.env``
files -- but can be gigabytes, so it is sealed the same way in frames: one
fresh data key per object, the machine id as associated data, and each frame
its own AES-GCM message. A frame's nonce is a random prefix, the frame's index
and whether it is the last, so a frame cannot be dropped, reordered or the
stream cut short without the open failing.

The wrapped data key travels in the object's header. Unwrapping it needs the
deployment's key, which is not in the bucket: a bucket alone opens nothing.
"""

from __future__ import annotations

import base64
import os
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.crypto import envelope
from app.core.crypto.ports import SecretCipher

_MAGIC = b"LSS1"
#: Plaintext bytes per frame; each sealed frame is this plus the tag.
FRAME_BYTES = 1024 * 1024
_TAG_BYTES = 16
_PREFIX_BYTES = 7
_LENGTH_BYTES = 2


class SealedStreamBroken(ValueError):
    """The sealed object is not one this deployment sealed, or was changed."""


def _default_cipher() -> SecretCipher:
    from app.core.crypto.factory import get_secret_cipher

    return get_secret_cipher()


def _nonce(prefix: bytes, index: int, *, last: bool) -> bytes:
    return prefix + index.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


async def seal(
    plain: AsyncIterable[bytes],
    *,
    context: bytes,
    cipher: SecretCipher | None = None,
) -> AsyncIterator[bytes]:
    """``plain`` sealed, as it streams. ``context`` must be given again to open it."""
    key = AESGCM.generate_key(bit_length=256)
    wrapped = (
        (cipher or _default_cipher()).encrypt_str(base64.b64encode(key).decode()) or ""
    ).encode()
    if not wrapped or len(wrapped) >= 1 << (8 * _LENGTH_BYTES):
        raise SealedStreamBroken("The data key could not be wrapped.")
    prefix = os.urandom(_PREFIX_BYTES)
    aead = AESGCM(key)
    yield _MAGIC + len(wrapped).to_bytes(_LENGTH_BYTES, "big") + wrapped + prefix
    buffer = bytearray()
    index = 0
    async for chunk in plain:
        buffer += chunk
        while len(buffer) > FRAME_BYTES:
            yield aead.encrypt(
                _nonce(prefix, index, last=False), bytes(buffer[:FRAME_BYTES]), context
            )
            del buffer[:FRAME_BYTES]
            index += 1
    yield aead.encrypt(_nonce(prefix, index, last=True), bytes(buffer), context)


async def open_sealed(
    sealed: AsyncIterable[bytes],
    *,
    context: bytes,
    cipher: SecretCipher | None = None,
) -> AsyncIterator[bytes]:
    """The plaintext of a stream :func:`seal` produced, as it streams.

    Raises :class:`SealedStreamBroken` at the first frame that does not
    authenticate -- including a stream that ends early -- so a consumer must
    treat what it already received as void.
    """
    buffer = bytearray()
    chunks = aiter(sealed)
    header = await _header(chunks, buffer)
    aead = AESGCM(_unwrap(header.wrapped, cipher or _default_cipher()))
    sealed_frame = FRAME_BYTES + _TAG_BYTES
    index = 0
    async for chunk in chunks:
        buffer += chunk
        # A full frame is the last only if nothing follows it, so keep one back.
        while len(buffer) > sealed_frame:
            yield _open_frame(
                aead, header.prefix, index, bytes(buffer[:sealed_frame]), context
            )
            del buffer[:sealed_frame]
            index += 1
    yield _open_frame(aead, header.prefix, index, bytes(buffer), context, last=True)


@dataclass(frozen=True, slots=True)
class _Header:
    wrapped: bytes
    prefix: bytes


async def _fill(chunks: AsyncIterator[bytes], buffer: bytearray, size: int) -> None:
    while len(buffer) < size:
        try:
            buffer += await anext(chunks)
        except StopAsyncIteration:
            raise SealedStreamBroken("The sealed object ends inside its header.")


async def _header(chunks: AsyncIterator[bytes], buffer: bytearray) -> _Header:
    fixed = len(_MAGIC) + _LENGTH_BYTES
    await _fill(chunks, buffer, fixed)
    if bytes(buffer[: len(_MAGIC)]) != _MAGIC:
        raise SealedStreamBroken("This is not a sealed object.")
    wrapped_length = int.from_bytes(buffer[len(_MAGIC) : fixed], "big")
    end = fixed + wrapped_length + _PREFIX_BYTES
    await _fill(chunks, buffer, end)
    header = _Header(
        bytes(buffer[fixed : fixed + wrapped_length]),
        bytes(buffer[fixed + wrapped_length : end]),
    )
    del buffer[:end]
    return header


def _unwrap(wrapped: bytes, cipher: SecretCipher) -> bytes:
    """The data key, only ever from a wrapped one: the cipher hands back a
    plaintext value unchanged, and a key planted in the bucket in the clear
    must not open anything."""
    try:
        token = wrapped.decode()
        if not envelope.is_encrypted_str(token):
            raise ValueError("not wrapped")
        key = base64.b64decode(cipher.decrypt_str(token) or "")
    except (ValueError, InvalidTag) as exc:
        raise SealedStreamBroken("The data key could not be unwrapped.") from exc
    if len(key) != 32:
        raise SealedStreamBroken("The data key could not be unwrapped.")
    return key


def _open_frame(
    aead: AESGCM,
    prefix: bytes,
    index: int,
    frame: bytes,
    context: bytes,
    *,
    last: bool = False,
) -> bytes:
    try:
        return aead.decrypt(_nonce(prefix, index, last=last), frame, context)
    except InvalidTag as exc:
        raise SealedStreamBroken(
            "The sealed object was changed, cut short or sealed for another machine."
        ) from exc


__all__ = ["FRAME_BYTES", "SealedStreamBroken", "open_sealed", "seal"]
