"""Composio-backed transport for email surface platforms.

Email surfaces (Gmail, Outlook) can be connected through either the native
(LEMMA) provider or Composio. Composio never exposes the underlying provider
OAuth token — it proxies API calls behind a connected-account id — so a
Composio-backed surface cannot call the Microsoft Graph / Gmail REST APIs
directly. Instead it must run the equivalent Composio operation
(``OUTLOOK_REPLY_EMAIL``, ``GMAIL_REPLY_TO_THREAD`` ...).

This module centralises:
- detecting whether a resolved credentials payload is Composio-backed, and
- executing a Composio operation with those credentials.

The platform services branch on :func:`is_composio_credentials` and dispatch the
provider I/O either here (Composio) or through their native httpx path.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.composition.surface_connectors import (
    ComposioOperationGateway,
)

# Reserved key the surface credential resolver stamps with the account's
# auth-config provider (COMPOSIO / LEMMA). Falls back to connection_id sniffing
# for credential payloads produced outside the resolver.
PROVIDER_KEY = "provider"
_COMPOSIO_PROVIDER = "COMPOSIO"


def is_composio_credentials(credentials: dict[str, Any]) -> bool:
    """True when the credentials must be used through Composio operations."""
    provider = str(credentials.get(PROVIDER_KEY) or "").upper()
    if provider:
        return provider == _COMPOSIO_PROVIDER
    # No explicit provider stamp: a connection_id only exists on Composio
    # connected accounts, so treat its presence as the signal.
    return bool(credentials.get("connection_id"))


async def execute_composio_operation(
    *,
    connector_id: str,
    operation_name: str,
    payload: dict[str, Any],
    credentials: dict[str, Any],
) -> Any:
    """Run a Composio operation for an email surface account.

    Returns the unwrapped operation ``data`` (the gateway raises a typed
    ``OperationExecution*Error`` when Composio reports failure).
    """
    gateway = ComposioOperationGateway()
    return await gateway.execute_operation(
        connector_id=connector_id,
        operation_name=operation_name,
        payload=payload or {},
        third_party_credentials=credentials,
        provider=_COMPOSIO_PROVIDER,
    )


async def fetch_composio_file_bytes(data: Any) -> bytes:
    """Resolve the bytes of a Composio file-download result.

    Composio attachment operations (``*_GET_ATTACHMENT``,
    ``OUTLOOK_DOWNLOAD_OUTLOOK_ATTACHMENT``) do not return inline content — they
    upload the file and return ``data.file.s3url``. A few operations instead
    inline base64, so both shapes are handled.
    """
    payload = data if isinstance(data, dict) else {}
    file_info = payload.get("file") if isinstance(payload.get("file"), dict) else payload

    inline_b64 = (
        file_info.get("content_b64")
        or file_info.get("contentBytes")
        or file_info.get("data")
    )
    if isinstance(inline_b64, str) and inline_b64.strip():
        import base64

        return base64.b64decode(inline_b64.encode("ascii"))

    s3url = file_info.get("s3url") or file_info.get("s3_url") or file_info.get("url")
    if not isinstance(s3url, str) or not s3url.strip():
        raise ValueError(
            "Composio file-download result did not include an s3url or inline content."
        )
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        response = await client.get(s3url)
        response.raise_for_status()
        return response.content
