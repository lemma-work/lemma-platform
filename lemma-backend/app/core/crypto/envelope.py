"""Encrypted-envelope formats and (de)serialization.

Two on-disk shapes, plus the legacy v1 we must keep reading forever:

- **v2 dict** (JSONB columns) — versioned and key-id tagged::

      {"_encrypted": "lemma-secret-v2",
       "kid":  "<key / KEK-version id>",   # which key encrypted this
       "alg":  "fernet" | "kms+fernet",
       "dek":  "<b64 wrapped DEK>",         # only for alg == "kms+fernet"
       "ct":   "<b64 ciphertext>"}

- **v2 string** (plain string columns, e.g. webhook secrets) — the same v2 dict,
  JSON-encoded then urlsafe-base64'd behind a short prefix so it stays a single
  opaque token::

      "lsenc1:<b64-of-v2-dict-json>"

- **legacy v1 dict** (read-only) — what ``FernetSecretEncryptionAdapter`` wrote::

      {"_encrypted": "fernet-json-v1", "ciphertext": "<token>"}
"""

from __future__ import annotations

import base64
import json
from typing import Any

V2_MARKER = "lemma-secret-v2"
V1_MARKER = "fernet-json-v1"

ALG_FERNET = "fernet"
ALG_KMS_FERNET = "kms+fernet"

#: Prefix for the compact string envelope (string columns).
STR_PREFIX = "lsenc1:"


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def is_v2(value: Any) -> bool:
    return isinstance(value, dict) and value.get("_encrypted") == V2_MARKER


def is_v1(value: Any) -> bool:
    return isinstance(value, dict) and value.get("_encrypted") == V1_MARKER


def is_encrypted_dict(value: Any) -> bool:
    return is_v2(value) or is_v1(value)


def is_encrypted_str(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(STR_PREFIX)


def make_v2(
    *,
    kid: str,
    alg: str,
    ciphertext: bytes,
    wrapped_dek: bytes | None = None,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "_encrypted": V2_MARKER,
        "kid": kid,
        "alg": alg,
        "ct": b64e(ciphertext),
    }
    if alg == ALG_KMS_FERNET:
        if wrapped_dek is None:
            raise ValueError("kms+fernet envelope requires a wrapped DEK")
        envelope["dek"] = b64e(wrapped_dek)
    return envelope


def encode_str(envelope: dict[str, Any]) -> str:
    """Pack a v2 dict envelope into the compact ``lsenc1:`` string form."""
    raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    return STR_PREFIX + b64e(raw)


def decode_str(value: str) -> dict[str, Any]:
    """Unpack a compact string envelope back into its v2 dict."""
    raw = b64d(value[len(STR_PREFIX) :])
    envelope = json.loads(raw.decode("utf-8"))
    if not is_v2(envelope):
        raise ValueError("Decoded string envelope is not a lemma-secret-v2 object")
    return envelope
