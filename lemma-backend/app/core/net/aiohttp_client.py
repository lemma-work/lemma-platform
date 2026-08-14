"""aiohttp sessions that cannot outlive their welcome.

``aiohttp.ClientSession()`` with no ``timeout=`` inherits a **five minute**
total timeout. httpx, which the rest of this codebase uses, defaults to five
seconds — so the two libraries sitting side by side in the same process
disagree by two orders of magnitude, and the one that looks like it has no
opinion is the dangerous one.

Five minutes is long enough for an unresponsive upstream to matter on its own.
It is much worse where the caller holds a database session: the connection is
pinned for the whole wait, which is how an unauthenticated OAuth callback could
hold a pooled connection for minutes. Both halves of that were real findings.

Use :func:`new_aiohttp_session` instead of constructing sessions directly;
``make lint-io-hygiene`` fails the build on a bare ``aiohttp.ClientSession()``.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from app.core.config import settings


def default_client_timeout() -> aiohttp.ClientTimeout:
    """The timeout every outbound aiohttp call gets unless it asks for another.

    Split like the shared httpx client's: a separate, short connect budget so a
    host that is simply unreachable fails fast instead of consuming the whole
    total budget before anyone finds out.
    """
    return aiohttp.ClientTimeout(
        total=settings.outbound_http_timeout_seconds,
        connect=settings.outbound_http_connect_timeout_seconds,
    )


def new_aiohttp_session(
    *, timeout: aiohttp.ClientTimeout | None = None, **kwargs: Any
) -> aiohttp.ClientSession:
    """An ``aiohttp.ClientSession`` with a bounded timeout by construction."""
    return aiohttp.ClientSession(timeout=timeout or default_client_timeout(), **kwargs)
