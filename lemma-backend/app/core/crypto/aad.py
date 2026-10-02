"""Canonical associated data for AEAD.

Every part is length-prefixed, so ``("ab", "c")`` and ``("a", "bc")`` encode
differently. A plain join would let two different contexts produce the same
bytes, and then a ciphertext bound to one would open under the other.
"""

from __future__ import annotations


def encode_aad(*parts: str) -> bytes:
    """Encode ``parts`` as ``len(part) || part`` for each, in order."""
    out = bytearray()
    for part in parts:
        raw = part.encode("utf-8")
        out += len(raw).to_bytes(4, "big")
        out += raw
    return bytes(out)
