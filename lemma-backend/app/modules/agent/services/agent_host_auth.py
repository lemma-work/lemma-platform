"""Device proof, pairing, and scoped tokens for Agent Host."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.crypto import get_secret_signer
from app.modules.agent.domain.agent_host import AgentHostTokenClaims


_TOKEN_PURPOSE = "agent-host-device"
_TOKEN_CAPABILITIES = ("control", "events", "harnesses", "mcp")
DEFAULT_AGENT_HOST_TOKEN_TTL_SECONDS = 600
MAX_AGENT_HOST_CLOCK_SKEW_SECONDS = 120


class InvalidAgentHostCredential(ValueError):
    """A host pairing proof or device token is invalid."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def generate_pairing_code() -> str:
    return secrets.token_urlsafe(32)


def pairing_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def decode_public_key(public_key: str) -> bytes:
    try:
        raw = _b64d(public_key)
    except Exception as exc:
        raise InvalidAgentHostCredential("malformed public key") from exc
    if len(raw) != 32:
        raise InvalidAgentHostCredential("Ed25519 public key must be 32 bytes")
    return raw


def public_key_fingerprint(public_key: str) -> str:
    return hashlib.sha256(decode_public_key(public_key)).hexdigest()


def host_signature_payload(*, host_id: UUID, nonce: str, timestamp: int) -> bytes:
    return f"lemma-agent-host\n{host_id}\n{timestamp}\n{nonce}".encode()


def pairing_signature_payload(
    *,
    pairing_code: str,
    installation_id: str,
    nonce: str,
    timestamp: int,
) -> bytes:
    return (
        "lemma-agent-host-pair\n"
        f"{pairing_code}\n{installation_id}\n{timestamp}\n{nonce}"
    ).encode()


def verify_pairing_signature(
    *,
    public_key: str,
    pairing_code: str,
    installation_id: str,
    nonce: str,
    timestamp: int,
    signature: str,
    now_epoch: int | None = None,
) -> None:
    now = int(time.time()) if now_epoch is None else now_epoch
    if abs(now - timestamp) > MAX_AGENT_HOST_CLOCK_SKEW_SECONDS:
        raise InvalidAgentHostCredential("signed pairing timestamp is outside clock skew")
    try:
        Ed25519PublicKey.from_public_bytes(decode_public_key(public_key)).verify(
            _b64d(signature),
            pairing_signature_payload(
                pairing_code=pairing_code,
                installation_id=installation_id,
                nonce=nonce,
                timestamp=timestamp,
            ),
        )
    except InvalidAgentHostCredential:
        raise
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidAgentHostCredential("invalid pairing signature") from exc


def verify_host_signature(
    *,
    public_key: str,
    host_id: UUID,
    nonce: str,
    timestamp: int,
    signature: str,
    now_epoch: int | None = None,
) -> None:
    now = int(time.time()) if now_epoch is None else now_epoch
    if abs(now - timestamp) > MAX_AGENT_HOST_CLOCK_SKEW_SECONDS:
        raise InvalidAgentHostCredential("signed host timestamp is outside clock skew")
    try:
        raw_signature = _b64d(signature)
        Ed25519PublicKey.from_public_bytes(decode_public_key(public_key)).verify(
            raw_signature,
            host_signature_payload(
                host_id=host_id,
                nonce=nonce,
                timestamp=timestamp,
            ),
        )
    except InvalidAgentHostCredential:
        raise
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidAgentHostCredential("invalid host signature") from exc


def mint_agent_host_token(
    *,
    host_id: UUID,
    user_id: UUID,
    organization_id: UUID | None,
    now_epoch: int | None = None,
    ttl_seconds: int = DEFAULT_AGENT_HOST_TOKEN_TTL_SECONDS,
) -> tuple[str, datetime]:
    now = int(time.time()) if now_epoch is None else now_epoch
    expires_at_epoch = now + ttl_seconds
    payload = json.dumps(
        {
            "h": str(host_id),
            "u": str(user_id),
            "o": str(organization_id) if organization_id else None,
            "e": expires_at_epoch,
            "c": list(_TOKEN_CAPABILITIES),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    token = f"{_b64e(payload)}.{get_secret_signer().sign(_TOKEN_PURPOSE, payload)}"
    return token, datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc)


def verify_agent_host_token(
    token: str,
    *,
    required_capability: str,
    now_epoch: int | None = None,
) -> AgentHostTokenClaims:
    now = int(time.time()) if now_epoch is None else now_epoch
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        if not get_secret_signer().verify(_TOKEN_PURPOSE, payload, signature):
            raise InvalidAgentHostCredential("device token signature mismatch")
        data = json.loads(payload)
        expires_at_epoch = int(data["e"])
        if expires_at_epoch < now:
            raise InvalidAgentHostCredential("device token expired")
        capabilities = tuple(str(item) for item in data["c"])
        if required_capability not in capabilities:
            raise InvalidAgentHostCredential(
                f"device token lacks {required_capability!r} capability"
            )
        return AgentHostTokenClaims(
            host_id=UUID(str(data["h"])),
            user_id=UUID(str(data["u"])),
            organization_id=UUID(str(data["o"])) if data.get("o") else None,
            expires_at_epoch=expires_at_epoch,
            capabilities=capabilities,
        )
    except InvalidAgentHostCredential:
        raise
    except Exception as exc:
        raise InvalidAgentHostCredential("malformed device token") from exc
