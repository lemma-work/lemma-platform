"""Telling "nothing to discover" apart from "the server refused me".

Both used to be the integer zero, and the refresh endpoint -- which exists
precisely to retry a discovery that failed at install time -- rendered them the
same way, with HTTP 200. So the recovery path reported success on the failure
it was built to recover from.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
)
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    ConnectorKind,
    LemmaProviderCapability,
    McpKindSpec,
)
from app.modules.connectors.domain.errors import ConnectorUnauthorizedError
from app.modules.connectors.services.install_provisioning import (
    DiscoveryStatus,
    discover_install_operations,
)

ORG_ID = uuid4()


def _install(kind: ConnectorKind) -> AuthConfigEntity:
    return AuthConfigEntity(
        id=uuid4(),
        organization_id=ORG_ID,
        connector_id="mcp",
        kind=kind,
        provider="LEMMA",
        config_source=AuthConfigSource.SYSTEM_DEFAULT,
        name="an-install",
        config={"server_url": "https://mcp.example.com/mcp"},
    )


def _connector(spec) -> ConnectorEntity:
    return ConnectorEntity(id="mcp", provider_capabilities=[spec])


def _dispatcher(result):
    """A `KindDispatcher` stand-in whose `discover` does `result`."""

    async def _discover(_install, _credentials):
        if isinstance(result, Exception):
            raise result
        return result

    return Mock(return_value=Mock(discover=_discover))


async def test_a_refused_discovery_says_so_rather_than_returning_zero():
    outcome = await _run(
        _dispatcher(ConnectorUnauthorizedError("the server refused the listing"))
    )

    assert outcome.status is DiscoveryStatus.FAILED
    assert outcome.operation_count == 0
    # The connector error's own code, so a client can tell an auth problem from
    # an unreachable host without parsing a sentence.
    assert outcome.reason == "CONNECTOR_UNAUTHORIZED"


async def test_a_kind_that_discovers_nothing_is_not_a_failure():
    outcome = await discover_install_operations(
        _install(ConnectorKind.PACKAGE),
        _connector(LemmaProviderCapability()),
        repository=AsyncMock(),
        uow=AsyncMock(),
    )

    assert outcome.status is DiscoveryStatus.NOT_APPLICABLE
    assert outcome.reason == "kind_has_no_discovery"


async def test_a_server_that_advertises_nothing_is_a_successful_discovery():
    outcome = await _run(_dispatcher([]))

    assert outcome.status is DiscoveryStatus.OK
    assert outcome.operation_count == 0


async def _run(dispatcher):
    with patch("app.modules.connectors.services.execution.KindDispatcher", dispatcher):
        return await discover_install_operations(
            _install(ConnectorKind.MCP),
            _connector(McpKindSpec()),
            repository=AsyncMock(),
            uow=AsyncMock(),
        )
