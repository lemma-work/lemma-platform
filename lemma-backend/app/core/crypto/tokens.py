"""Signed, expiring claim tokens: ``<payload>.<kid>.<sig>``.

The format every purpose-bound token in the platform already uses (widget embed
URLs, datastore file URLs, app access), in one place: a compact JSON payload,
base64url, signed by the unified signer under a purpose of its own so a token
minted for one purpose never verifies as another. ``exp`` is always present and
always checked.

A token here is a bearer credential. Callers keep them short-lived and check
anything revocable (a session row, say) themselves.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping

from app.core.crypto import get_secret_signer

__all__ = ["InvalidSignedToken", "mint_signed_token", "verify_signed_token"]


class InvalidSignedToken(Exception):
    """A token that is malformed, signed for another purpose, or expired."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_signed_token(
    purpose: str,
    claims: Mapping[str, object],
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """A token carrying ``claims`` and an ``exp`` ``ttl_seconds`` from now."""
    if "exp" in claims:
        raise ValueError("exp is set by mint_signed_token")
    issued = int(now if now is not None else time.time())
    payload = json.dumps(
        {**claims, "exp": issued + ttl_seconds}, separators=(",", ":")
    ).encode("utf-8")
    return f"{_b64e(payload)}.{get_secret_signer().sign(purpose, payload)}"


def verify_signed_token(
    purpose: str, token: str, *, now: float | None = None
) -> dict[str, object]:
    """The claims of a valid, unexpired ``purpose`` token.

    Raises ``InvalidSignedToken`` for anything else. The ways a token can be
    malformed are few and named: a bad split, base64 or JSON is a ValueError, a
    payload that is not an object a TypeError or AttributeError, a missing or
    non-numeric ``exp`` a KeyError or ValueError.
    """
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        if not get_secret_signer().verify(purpose, payload, signature):
            raise InvalidSignedToken("signature mismatch")
        claims = json.loads(payload)
        if not isinstance(claims, dict):
            raise InvalidSignedToken("payload is not an object")
        if int(claims["exp"]) < int(now if now is not None else time.time()):
            raise InvalidSignedToken("token expired")
        return claims
    except InvalidSignedToken:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidSignedToken("malformed token") from exc
