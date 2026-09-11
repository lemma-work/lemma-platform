"""Stop every authenticated request rebuilding an HTTP client and a CA bundle.

``verify_auth`` is a global dependency, so every request to this process calls
SuperTokens' ``get_session``, which reaches ``Querier.api_request``:

.. code-block:: python

    async with AsyncClient(timeout=30.0) as client:   # supertokens_python/querier.py
        return await client.post(url, ...)

A new ``httpx.AsyncClient`` per call means a new ``ssl.SSLContext`` per call,
and building one parses the whole certifi CA bundle -- roughly 150 PEM
certificates -- as synchronous C work on the event loop. Measured here:

===============================================  ========
``AsyncClient(timeout=30.0)``                     3.4 ms
``AsyncClient(verify=<context built once>)``      0.1 ms
===============================================  ========

34x, on every authenticated request, and it throws away the connection pool
each time so every call also pays a fresh TCP and TLS handshake to the core.

This is the shape ``check_io_hygiene.py``'s ``process-lifetime-construction``
rule exists to catch -- ``httpx.AsyncClient`` is in ``PROCESS_LIFETIME_CLIENTS``
by name. The gate could not catch this one because the call is in
``site-packages`` and the gate scans ``app/`` only, which is the right scope for
it; a dependency is not ours to lint, only to work around.

The patch is deliberately the smallest one that works. Rather than reimplement
``api_request`` -- which owns the retry and the ``AsyncLibraryNotFoundError``
recovery that auth correctness depends on -- it replaces only the ``AsyncClient``
name *in the querier module*, with a handle whose ``__aenter__`` hands back one
shared client and whose ``__aexit__`` declines to close it. Every line of
SuperTokens' own logic runs unchanged.

Same lookup rule as :mod:`jwks_guard`: ``querier.py`` does
``from httpx import AsyncClient``, binding the name into its own namespace, so
the name is replaced where it is looked up rather than where it is defined.
"""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING

from app.core.log.log import get_logger

if TYPE_CHECKING:  # `httpx` stays a runtime-lazy import; this is types only.
    from httpx import AsyncClient

logger = get_logger(__name__)

_installed = False

#: Built once, on first use, and shared by every request thereafter. Not built
#: at install time: ``initialize_supertokens()`` runs before the event loop, and
#: an ``AsyncClient`` binds its connection pool to the loop that first uses it.
_shared_client: "AsyncClient | None" = None
_shared_context: ssl.SSLContext | None = None


def _verify_context() -> ssl.SSLContext:
    global _shared_context
    if _shared_context is None:
        import certifi

        _shared_context = ssl.create_default_context(cafile=certifi.where())
    return _shared_context


class _SharedClientHandle:
    """Async-context shim standing in for ``AsyncClient(...)`` in the querier.

    Holds the constructor arguments SuperTokens passed so the first real client
    is built with them -- the ``timeout=30.0`` it sets is a correctness
    property of the auth path, not a detail to drop.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._kwargs = kwargs

    async def __aenter__(self) -> "AsyncClient":
        global _shared_client
        if _shared_client is None or _shared_client.is_closed:
            from httpx import AsyncClient

            _shared_client = AsyncClient(verify=_verify_context(), **self._kwargs)
        return _shared_client

    async def __aexit__(self, *exc_info: object) -> bool:
        # Emphatically not closing it: outliving the request is the point.
        return False


def install_shared_querier_client() -> None:
    """Give SuperTokens' querier one process-lifetime client. Idempotent."""
    global _installed

    if _installed:
        return

    try:
        from supertokens_python import querier
    except ImportError:  # pragma: no cover - SuperTokens is a hard dependency
        logger.warning("identity.querier_client.install_failed.degraded")
        return

    querier.AsyncClient = _SharedClientHandle  # type: ignore[misc]
    _installed = True


async def close_shared_querier_client() -> None:
    """Close the shared client, symmetric with the rest of the shutdown path."""
    global _shared_client

    client, _shared_client = _shared_client, None
    if client is not None and not client.is_closed:
        await client.aclose()
