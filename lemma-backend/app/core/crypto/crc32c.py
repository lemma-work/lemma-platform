"""CRC32C (Castagnoli), for Cloud KMS end-to-end integrity checks.

Cloud KMS asks callers to send a CRC32C of each input and to check the one it
returns for each output, so corruption between us and the HSM is detected
rather than stored. The values it covers here are 32-byte keys, so a table
implementation is plenty, and it avoids a native dependency for one checksum.
``zlib.crc32`` is the other polynomial and cannot be used.
"""

from __future__ import annotations

_POLY = 0x82F63B78


def _table() -> tuple[int, ...]:
    rows = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ _POLY if crc & 1 else crc >> 1
        rows.append(crc)
    return tuple(rows)


_TABLE = _table()


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF
