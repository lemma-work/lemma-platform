"""Discover what an MCP server offers to tell us, beside the tools it lists.

Most servers offer nothing: they answer `events/list` with method-not-found,
or do not speak the revision it is offered on. That is an install with no
events, not a failed discovery, so every refusal here is an empty list.

A server that could not be *reached* said nothing at all, which is not the
same answer. That is None, and the events stored from the last time it
answered are kept: a refresh during a brief outage would otherwise empty the
When... picker and refuse every new schedule on that server.
"""

from __future__ import annotations

from collections.abc import Callable
from app.core.log.log import get_logger
from app.modules.connectors.domain.mcp_events import DiscoveredEvent, parse_descriptors
from app.modules.connectors.infrastructure.adapters.mcp_events_client import (
    TRANSPORT_FAILURE,
    McpEventsClient,
    McpEventsError,
)
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    build_mcp_headers,
)

logger = get_logger(__name__)

ClientFactory = Callable[[str, dict[str, str]], McpEventsClient]


async def discover_mcp_events(
    *,
    connection_config: dict[str, object] | None,
    credentials: dict[str, object] | None,
    client: ClientFactory = McpEventsClient,
) -> list[DiscoveredEvent] | None:
    server_url = (connection_config or {}).get("server_url")
    if not isinstance(server_url, str) or not server_url:
        return []
    try:
        listed = await client(
            server_url, build_mcp_headers(connection_config, credentials)
        ).list_events()
    except McpEventsError as exc:
        if exc.code == TRANSPORT_FAILURE:
            logger.warning(
                "connectors.discovery.mcp_events.unreachable.degraded",
                code=exc.code,
            )
            return None
        logger.info(
            "connectors.discovery.mcp_events.none_offered",
            code=exc.code,
        )
        return []
    return parse_descriptors(listed)
