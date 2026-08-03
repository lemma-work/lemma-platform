"""One outbound HTTP client for the whole process.

Connector execution used to build a fresh ``httpx.AsyncClient`` inside every
call and tear it down again on the way out, so no TCP or TLS connection was ever
reused: each operation paid a fresh handshake against the same handful of hosts.
A process-wide client with a bounded pool removes that cost entirely on a warm
pool, and gives one place to cap concurrency against upstreams.

``follow_redirects`` is off by design. Redirects are followed manually by the
callers that want them, re-validating the target on every hop, because a
tenant-supplied URL that redirects into the private network is otherwise an SSRF
the initial check cannot see.
"""

from __future__ import annotations

import httpx

from app.core.config import settings

_client: httpx.AsyncClient | None = None

_USER_AGENT = "lemma-connectors"


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=settings.outbound_http_max_connections,
            max_keepalive_connections=settings.outbound_http_max_keepalive,
            keepalive_expiry=30.0,
        ),
        # Split so a slow upstream body cannot masquerade as a connect failure,
        # and so pool starvation surfaces quickly instead of hanging. Callers
        # override the read timeout per request from their kind's deadline.
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
        headers={"User-Agent": _USER_AGENT},
    )


def get_shared_http_client() -> httpx.AsyncClient:
    """Return the process-wide outbound client, creating it on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = _build_client()
    return _client


async def close_shared_http_client() -> None:
    """Close the shared client. Called from the app lifespan on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
