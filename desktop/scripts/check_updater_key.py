#!/usr/bin/env python3
"""The key that signed this update is the key installed apps will check it with.

The release already refuses to build without an updater private key and without
a committed public key. It has never checked that the two are halves of the same
pair, and nothing else does either -- not the build, which only signs, and not
the feed verification, which only asks whether a signature is non-empty.

So a rotated private key, or a `tauri.conf.json` restored from an older branch,
produces a release that looks entirely correct: signed, notarized, published,
with a feed that names it. Every installed app then rejects the payload at the
last step, and the only symptom is that updates quietly stop working -- for the
installed base, which is exactly the population a fix has to reach.

Minisign puts an 8-byte key id in both halves, so the two can be compared
without the private key ever being decrypted or leaving the runner:

    public key file   Ed | key id (8) | public key (32)
    signature file    Ed | key id (8) | signature (64)

Rotation is a real operation with a real order, and it is written down in
docs/installation.md under "Rotating the update signing key". The short version
is that the new public key has to reach installed apps in a release signed by
the *old* private key, because an app only trusts the key it was built with.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from pathlib import Path

# Minisign's two Ed25519 algorithms: legacy, and prehashed. Both carry the key
# id in the same place.
SIGNATURE_ALGORITHMS = (b"Ed", b"ED")

PUBLIC_KEY_BYTES = 2 + 8 + 32
SIGNATURE_BYTES = 2 + 8 + 64


def _payload_line(text: str) -> str:
    """The base64 body of a minisign file, skipping its comment lines."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("untrusted comment:", "trusted comment:")):
            return line
    raise ValueError("no minisign payload line found")


def _key_id(raw: bytes, *, expected_length: int, what: str) -> str:
    if len(raw) != expected_length:
        raise ValueError(f"{what} is {len(raw)} bytes, expected {expected_length}")
    if raw[:2] not in SIGNATURE_ALGORITHMS:
        raise ValueError(f"{what} is not an Ed25519 minisign {what}: {raw[:2]!r}")
    # Stored little-endian; minisign prints it the other way round, and so does
    # the comment line in the key file, so match what a person will see.
    return raw[2:10][::-1].hex().upper()


def public_key_id(pubkey_base64: str) -> str:
    """The key id an installed app will verify with."""
    try:
        text = base64.b64decode(pubkey_base64, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError) as error:
        raise ValueError(f"updater pubkey is not base64 of a key file: {error}") from error
    raw = base64.b64decode(_payload_line(text), validate=True)
    return _key_id(raw, expected_length=PUBLIC_KEY_BYTES, what="public key")


def signature_key_id(signature: str) -> str:
    """The key id that actually signed the payload.

    Accepts either the minisign signature file or Tauri's base64 of it, because
    which one is on disk is Tauri's business and has changed before.
    """
    signature = signature.strip()
    try:
        decoded = base64.b64decode(signature, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        decoded = signature
    if "comment:" not in decoded:
        decoded = signature
    raw = base64.b64decode(_payload_line(decoded), validate=True)
    return _key_id(raw, expected_length=SIGNATURE_BYTES, what="signature")


def check(config: Path, signature: str, *, rotating: bool = False) -> str:
    """Compare the signing key with the one installed apps verify with.

    `rotating` is the one release in a key rotation where they disagree on
    purpose: the new public key is committed so it reaches installed apps, and
    the old private key still signs so those apps accept the release carrying
    it. It *requires* the mismatch rather than merely tolerating one, so the
    flag cannot be left switched on afterwards and quietly stop checking.
    """
    pubkey = json.loads(config.read_text())["plugins"]["updater"]["pubkey"]
    if not pubkey:
        raise ValueError(
            f"{config} has an empty updater pubkey, so every update this "
            f"release publishes will be rejected by the app that downloads it"
        )
    expected = public_key_id(pubkey)
    actual = signature_key_id(signature)
    if rotating:
        if expected == actual:
            raise ValueError(
                f"a key rotation was declared, and both halves are already key "
                f"{expected}. The rotation is finished: drop the rotation flag "
                f"so releases are checked again."
            )
        return f"{actual} -> {expected}"
    if expected != actual:
        raise ValueError(
            f"the update payload was signed with key {actual}, and installed "
            f"apps verify with key {expected}. Every update from this release "
            f"would be rejected. Rotating the signing key is a two-release "
            f'operation -- see "Rotating the update signing key" in '
            f"docs/installation.md, and declare it with "
            f"LEMMA_UPDATER_KEY_ROTATION=1."
        )
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("desktop/tauri.conf.json")
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signature-file", type=Path)
    group.add_argument("--signature")
    arguments = parser.parse_args()
    signature = (
        arguments.signature
        if arguments.signature is not None
        else arguments.signature_file.read_text()
    )
    rotating = os.environ.get("LEMMA_UPDATER_KEY_ROTATION") == "1"
    try:
        key_id = check(arguments.config, signature, rotating=rotating)
    except (ValueError, KeyError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    if rotating:
        print(
            f"✓ key rotation in progress: {key_id}. This release is signed with "
            f"the old key so installed apps accept it, and carries the new "
            f"public key forward."
        )
    else:
        print(f"✓ update signed with key {key_id}, which installed apps verify with")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
