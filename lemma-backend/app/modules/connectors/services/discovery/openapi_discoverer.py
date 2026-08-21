"""Discover connector operations from an OpenAPI spec (URL or inline)."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


from app.core.net.url_guard import fetch_guarded
from app.modules.connectors.config import connector_settings
from app.modules.connectors.infrastructure.openapi import build_operation_descriptors
from app.modules.connectors.services.discovery.base import DiscoveredOperation

SpecFetcher = Callable[[str, dict[str, str] | None], Awaitable[dict[str, Any]]]

_FETCH_TIMEOUT_SECONDS = 20.0


async def _default_fetch_spec(
    url: str, headers: dict[str, str] | None
) -> dict[str, Any]:
    """Fetch a tenant-supplied spec URL, guarded and capped.

    The URL comes from the install, so every hop is re-validated (a public URL
    that redirects to ``10.0.0.5`` is the obvious way around an install-time
    check) and the body is cut off at the configured ceiling rather than being
    buffered in full.
    """
    from app.core.net.http_client import get_shared_http_client

    raw = await fetch_guarded(
        get_shared_http_client(),
        url,
        max_bytes=connector_settings.connector_spec_max_bytes,
        timeout=_FETCH_TIMEOUT_SECONDS,
        headers=headers or {},
    )
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"OpenAPI spec at '{url}' is not valid JSON.") from exc


def _spec_default_server(spec: dict[str, Any]) -> str | None:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url")
    return None


async def discover_openapi(
    *,
    connection_config: dict[str, Any],
    credentials: dict[str, Any] | None = None,
    fetch_spec: SpecFetcher | None = None,
) -> list[DiscoveredOperation]:
    """Load the spec (inline or via URL) and build one operation per spec path.

    ``connection_config``: ``spec_inline`` | ``spec_url``, ``server_url`` (overrides
    the spec's server), optional ``operation_allowlist`` (None => all operations),
    ``overrides`` and ``default_headers``.
    """
    connection_config = connection_config or {}
    spec = connection_config.get("spec_inline")
    if spec is None:
        spec_url = connection_config.get("spec_url")
        if not spec_url:
            raise ValueError("OpenAPI discovery requires 'spec_url' or 'spec_inline'.")
        fetcher = fetch_spec or _default_fetch_spec
        spec = await fetcher(spec_url, connection_config.get("spec_headers"))

    server_url = connection_config.get("server_url") or _spec_default_server(spec)
    if not server_url:
        raise ValueError(
            "OpenAPI discovery requires a 'server_url' (none found in spec)."
        )

    descriptors = build_operation_descriptors(
        spec,
        server_url=server_url,
        allowlist=connection_config.get("operation_allowlist"),  # None => all
        overrides=connection_config.get("overrides", {}),
        default_headers=connection_config.get("default_headers"),
    )
    return [
        DiscoveredOperation(
            name=d.public_name,
            display_name=d.display_name,
            description=d.description,
            input_schema=d.input_schema,
            output_schema=d.output_schema,
            execution=d.execution,
            tags=d.tags,
        )
        for d in descriptors
    ]
