"""Signed, expiring access to a port inside a sandbox.

A sandbox port is never exposed directly. The backend mints an HMAC-signed
token naming the sandbox, the port, and an expiry, and proxies traffic through
itself. That keeps the sandbox unreachable from outside and makes revocation a
matter of the clock rather than of network reconfiguration.

The signature covers every field it authorises. Signing only the sandbox id
would let a holder change the port; signing only the port would let them change
the sandbox. Expiry is inside the signature for the same reason.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID


class PortAccessInvalid(RuntimeError):
    """The token is malformed, altered, or expired."""


@dataclass(frozen=True, slots=True)
class PortGrant:
    sandbox_id: UUID
    port: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PortAccessSigner:
    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("port access signing key must be at least 32 bytes")

    def sign(self, grant: PortGrant) -> str:
        payload = _encode(
            {
                "s": str(grant.sandbox_id),
                "p": grant.port,
                "e": int(grant.expires_at.timestamp()),
            }
        )
        return f"{payload}.{self._mac(payload)}"

    def verify(self, token: str, *, now: datetime | None = None) -> PortGrant:
        payload, _, signature = token.partition(".")
        if not payload or not signature:
            raise PortAccessInvalid("malformed port access token")
        # Constant time, and computed over the exact bytes presented: a token
        # re-encoded into a different but equivalent base64 form must not
        # verify, or the signature would cover a value we never issued.
        if not hmac.compare_digest(self._mac(payload), signature):
            raise PortAccessInvalid("port access token signature does not match")
        try:
            claims = json.loads(_decode(payload))
            grant = PortGrant(
                sandbox_id=UUID(claims["s"]),
                port=int(claims["p"]),
                expires_at=datetime.fromtimestamp(int(claims["e"]), tz=timezone.utc),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PortAccessInvalid("port access token is not readable") from exc
        if grant.expires_at <= (now or datetime.now(timezone.utc)):
            raise PortAccessInvalid("port access token has expired")
        return grant

    def _mac(self, payload: str) -> str:
        digest = hmac.new(self.key, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _encode(claims: dict[str, object]) -> str:
    raw = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + padding)
