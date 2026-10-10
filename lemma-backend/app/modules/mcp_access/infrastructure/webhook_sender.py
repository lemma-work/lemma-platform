"""Sending one signed request to a subscriber's callback URL.

The URL is the client's, so it is treated like any tenant-supplied target:
checked against the SSRF guard (https only outside local development, no
private addresses, resolved at send time), and redirects are refused outright
rather than followed -- the draft says not to follow them, and a redirect is
the oldest way to point a checked URL somewhere unchecked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from app.core.net.http_client import get_shared_http_client
from app.core.net.url_guard import UnsafeUrlError, assert_safe_url, request_guarded
from app.core.webhooks.signatures import standard_webhook_signature

#: Long enough for a receiver that stores the event before answering, which is
#: what the draft asks of it; short enough that one dead receiver cannot hold a
#: worker.
SEND_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


@dataclass(frozen=True, slots=True)
class SendResult:
    status: int | None
    body: bytes = b""
    #: Why nothing came back, when nothing did: the guard refused the URL, the
    #: connection failed, the receiver timed out.
    failure: str | None = None


async def send_signed(
    *,
    url: str,
    secret: SecretStr,
    message_id: str,
    subscription_id: str,
    body: bytes,
) -> SendResult:
    try:
        await assert_safe_url(url)
    except UnsafeUrlError as exc:
        return SendResult(status=None, failure=f"unsafe_url:{exc.reason}")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": standard_webhook_signature(
            secret.get_secret_value(), message_id, timestamp, body
        ),
        "X-MCP-Subscription-Id": subscription_id,
    }
    try:
        response = await request_guarded(
            get_shared_http_client(),
            "POST",
            url,
            headers=headers,
            content=body,
            follow_redirects=False,
            timeout=SEND_TIMEOUT,
        )
    except httpx.TimeoutException:
        return SendResult(status=None, failure="timeout")
    except httpx.TransportError:
        return SendResult(status=None, failure="connection_refused")
    except UnsafeUrlError as exc:
        return SendResult(status=None, failure=f"unsafe_url:{exc.reason}")
    return SendResult(status=response.status_code, body=response.content[:4096])
