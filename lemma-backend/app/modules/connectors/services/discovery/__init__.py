"""Per-auth-config operation discovery for package-free connector kinds.

Discoverers turn an org's connection config (MCP server URL, OpenAPI spec URL)
into a list of ``DiscoveredOperation`` records that the connector service backfills
into ``connector_operations`` scoped to the auth-config, and cleans up on delete.
"""

from app.modules.connectors.services.discovery.base import DiscoveredOperation
from app.modules.connectors.services.discovery.mcp_discoverer import discover_mcp
from app.modules.connectors.services.discovery.openapi_discoverer import discover_openapi

__all__ = ["DiscoveredOperation", "discover_mcp", "discover_openapi"]
