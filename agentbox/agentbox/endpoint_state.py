from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agentbox.config import settings


_FORMAT = "aesgcm-v2"


def _keyring() -> list[tuple[str, bytes]]:
    keys: list[tuple[str, bytes]] = []
    for encoded in settings.agentbox_endpoint_state_keys.split(","):
        encoded = encoded.strip()
        if not encoded:
            continue
        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except Exception as exc:
            raise ValueError("Invalid AGENTBOX_ENDPOINT_STATE_KEYS encoding") from exc
        if len(key) != 32:
            raise ValueError("AgentBox endpoint-state keys must decode to 32 bytes")
        key_id = hashlib.sha256(key).hexdigest()[:16]
        keys.append((key_id, key))
    if not keys:
        raise RuntimeError(
            "AGENTBOX_ENDPOINT_STATE_KEYS must contain at least one 32-byte key"
        )
    return keys


def validate_endpoint_state_keyring() -> None:
    """Fail fast when durable endpoint credentials cannot be protected."""

    _keyring()


def _associated_data(sandbox_id: str, generation: int) -> bytes:
    return f"{sandbox_id}:{generation}".encode()


def seal_endpoint_state(
    sandbox_id: str,
    generation: int,
    endpoints: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Encrypt provider route credentials before they enter durable state."""

    nonce = os.urandom(12)
    plaintext = json.dumps(
        endpoints,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    key_id, key = _keyring()[0]
    ciphertext = AESGCM(key).encrypt(
        nonce,
        plaintext,
        _associated_data(sandbox_id, generation),
    )
    return {
        "format": _FORMAT,
        "key_id": key_id,
        "nonce": base64.urlsafe_b64encode(nonce).decode(),
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode(),
    }


def open_endpoint_state(
    sandbox_id: str,
    generation: int,
    sealed: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Authenticate and decrypt the routes for one exact sandbox generation."""

    if sealed.get("format") != _FORMAT:
        raise ValueError("Unsupported persisted sandbox endpoint format")
    key_id = str(sealed["key_id"])
    key = next((key for candidate, key in _keyring() if candidate == key_id), None)
    if key is None:
        raise ValueError("Persisted sandbox endpoint key is unavailable")
    nonce = base64.urlsafe_b64decode(str(sealed["nonce"]))
    ciphertext = base64.urlsafe_b64decode(str(sealed["ciphertext"]))
    plaintext = AESGCM(key).decrypt(
        nonce,
        ciphertext,
        _associated_data(sandbox_id, generation),
    )
    value = json.loads(plaintext)
    if not isinstance(value, dict):
        raise ValueError("Persisted sandbox endpoint state is not an object")
    return {
        str(app_name): dict(endpoint)
        for app_name, endpoint in value.items()
        if isinstance(endpoint, dict)
    }
