#!/usr/bin/env python3
"""Normalize an Ubuntu ARM64 kernel into the raw Image VZ boots directly."""

from __future__ import annotations

import argparse
import gzip
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ARM64_MAGIC_OFFSET = 56
ARM64_MAGIC = b"ARM\x64"
MAX_KERNEL_BYTES = 512 * 1024 * 1024


def _is_raw_image(data: bytes) -> bool:
    return len(data) >= 64 and data[ARM64_MAGIC_OFFSET : ARM64_MAGIC_OFFSET + 4] == ARM64_MAGIC


def _write_checked(output: Path, contents: bytes) -> None:
    if not _is_raw_image(contents):
        raise SystemExit("decompressed kernel is not an ARM64 Linux Image")
    if len(contents) > MAX_KERNEL_BYTES:
        raise SystemExit("decompressed kernel exceeds 512 MiB")
    output.write_bytes(contents)


def extract(source: Path, output: Path) -> None:
    data = source.read_bytes()
    if len(data) > MAX_KERNEL_BYTES:
        raise SystemExit("packaged kernel exceeds 512 MiB")
    if _is_raw_image(data):
        output.write_bytes(data)
        return
    if data.startswith(b"\x1f\x8b"):
        _write_checked(output, gzip.decompress(data))
        return
    if len(data) < 32 or data[:2] != b"MZ" or data[4:8] != b"zimg":
        raise SystemExit("unsupported ARM64 kernel format")

    payload_offset, payload_size = struct.unpack_from("<II", data, 8)
    payload_end = payload_offset + payload_size
    if payload_offset < 64 or payload_size == 0 or payload_end > len(data):
        raise SystemExit("invalid ARM64 zboot payload bounds")
    compression = data[24:32].split(b"\0", 1)[0].decode("ascii", errors="strict")
    payload = data[payload_offset:payload_end]
    if compression == "gzip":
        _write_checked(output, gzip.decompress(payload))
        return
    if compression != "zstd":
        raise SystemExit(f"unsupported ARM64 zboot compression: {compression!r}")

    executable = shutil.which("zstd")
    if executable is None:
        raise SystemExit("zstd is required to unpack this Ubuntu ARM64 kernel")
    with tempfile.NamedTemporaryFile(prefix="lemma-kernel-", suffix=".zst") as temporary:
        temporary.write(payload)
        temporary.flush()
        with output.open("wb") as destination:
            subprocess.run(
                [executable, "--decompress", "--stdout", temporary.name],
                check=True,
                stdout=destination,
            )
    normalized = output.read_bytes()
    if not _is_raw_image(normalized) or len(normalized) > MAX_KERNEL_BYTES:
        output.unlink(missing_ok=True)
        raise SystemExit("decompressed kernel is not a bounded ARM64 Linux Image")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract(args.input, args.output)


if __name__ == "__main__":
    main()
