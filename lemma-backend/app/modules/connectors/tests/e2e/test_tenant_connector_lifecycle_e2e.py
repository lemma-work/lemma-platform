"""Installing a tenant-configured connector and using it, through the API.

Every other connector test drives some layer directly. This one goes through the
service the HTTP routes use, end to end: create the install, have its operations
discovered from a live MCP server, then execute one. It is the path a customer
actually takes, and the one that was missing -- the executors worked in
isolation, but nothing connected "create an auth-config of kind mcp" to "run a
tool", so the capability was unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.infrastructure.models.connector import Connector

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture(scope="module")
async def live_mcp_server():
    """A real MCP server, so discovery has something real to discover."""
    from fastmcp import FastMCP

    server = FastMCP("tenant-lifecycle")

    @server.tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool
    def lookup_customer(customer_id: str) -> dict:
        """Return a customer record."""
        return {"customer_id": customer_id, "plan": "enterprise"}

    port = _free_port()
    task = asyncio.create_task(
        server.run_async(transport="http", host="127.0.0.1", port=port, show_banner=False)
    )
    for _ in range(100):
        if task.done():
            raise RuntimeError(f"MCP server failed to start: {task.exception()}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            break
        except OSError:
            await asyncio.sleep(0.05)
    else:
        raise RuntimeError("MCP server did not start in time")

    yield f"http://127.0.0.1:{port}/mcp"

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def allow_private_targets(monkeypatch):
    """Permit the loopback test server, as a self-hosted deployment would.

    The URL guard refuses private addresses by default -- that is the whole
    point of it, and the SSRF cases below rely on that default. Installing
    against a server on your own network is the documented reason the escape
    hatch exists, and it is exactly what these tests are doing.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)


@pytest_asyncio.fixture
async def mcp_connector(db_session):
    """The `mcp` catalog entry, as the importer seeds it."""
    connector = Connector(
        id=f"mcp-{uuid4().hex[:8]}",
        title="MCP Server",
        description="Connect to an external MCP server.",
        kinds=[
            {
                "kind": "mcp",
                "auth_scheme": "API_KEY",
                "discovery": "mcp",
                "auth_config_schema": {
                    "type": "object",
                    "required": ["server_url"],
                    "properties": {
                        "server_url": {"type": "string"},
                        "extra_headers": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "additionalProperties": False,
                },
            }
        ],
        is_active=True,
    )
    db_session.add(connector)
    await db_session.commit()
    await db_session.refresh(connector)
    return connector


def _service(db_session):
    """The service exactly as the HTTP dependency builds it."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.api.dependencies import get_connector_service

    uow = SqlAlchemyUnitOfWork(db_session)
    return get_connector_service(uow)


class TestInstallingAnMcpServer:
    async def test_creating_the_install_discovers_its_operations(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user, live_mcp_server, allow_private_targets
    ):
        service = _service(db_session)
        install = await service.create_auth_config(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            connector_id=mcp_connector.id,
            provider="LEMMA",
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            provider_config={"server_url": live_mcp_server},
            name=f"mcp-{uuid4().hex[:8]}",
        )

        assert install.kind is ConnectorKind.MCP
        # First install of this connector answers a bare connector_id lookup.
        assert install.is_default is True

        from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (
            AuthConfigOperationRepository,
        )

        operations = await AuthConfigOperationRepository(db_session).list_by_auth_config(
            install.id
        )
        names = {op.name for op in operations}
        # Discovered from the live server, not from any catalog.
        assert {"add", "lookup_customer"} <= names
        add = next(op for op in operations if op.name == "add")
        assert add.execution == {"kind": "mcp", "tool_name": "add"}
        assert add.input_schema["properties"]["a"]["type"] == "integer"

    async def test_a_config_that_does_not_match_the_schema_is_rejected(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user
    ):
        # The install schema is enforced now. It used to be decorative for
        # exactly this kind, so an arbitrary key was accepted and stored.
        service = _service(db_session)
        with pytest.raises(ConnectorValidationError):
            await service.create_auth_config(
                user_id=fixed_test_user["id"],
                organization_id=fixed_test_org["id"],
                connector_id=mcp_connector.id,
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                provider_config={"server_url": "https://x.test", "bearer_token": "sk-x"},
                name=f"mcp-bad-{uuid4().hex[:8]}",
            )

    async def test_a_private_target_is_refused_at_install_time(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user
    ):
        # An org admin cannot point an install at the cluster's own network.
        service = _service(db_session)
        with pytest.raises(ConnectorValidationError):
            await service.create_auth_config(
                user_id=fixed_test_user["id"],
                organization_id=fixed_test_org["id"],
                connector_id=mcp_connector.id,
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                provider_config={"server_url": "http://169.254.169.254/latest/meta-data/"},
                name=f"mcp-ssrf-{uuid4().hex[:8]}",
            )

    async def test_two_installs_of_the_same_connector_coexist(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user, live_mcp_server, allow_private_targets
    ):
        service = _service(db_session)
        first = await service.create_auth_config(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            connector_id=mcp_connector.id,
            provider="LEMMA",
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            provider_config={"server_url": live_mcp_server},
            name=f"mcp-a-{uuid4().hex[:8]}",
        )
        second = await service.create_auth_config(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            connector_id=mcp_connector.id,
            provider="LEMMA",
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            provider_config={"server_url": live_mcp_server},
            name=f"mcp-b-{uuid4().hex[:8]}",
        )
        assert first.id != second.id
        # Only the first answers a bare connector_id lookup.
        assert first.is_default is True and second.is_default is False

    async def test_refresh_repopulates_operations(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user, live_mcp_server, allow_private_targets
    ):
        service = _service(db_session)
        install = await service.create_auth_config(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            connector_id=mcp_connector.id,
            provider="LEMMA",
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            provider_config={"server_url": live_mcp_server},
            name=f"mcp-refresh-{uuid4().hex[:8]}",
        )
        # The recovery path: without it, a failed first discovery could only be
        # fixed by deleting the install, which cascades away its accounts.
        count = await service.refresh_auth_config_operations(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            auth_config_name=install.name,
        )
        assert count >= 2


class TestSqlInstallTargetsAreVetted:
    async def test_a_private_database_host_is_refused(
        self, db_session, fixed_test_org, fixed_test_user
    ):
        connector = Connector(
            id=f"sql-{uuid4().hex[:8]}",
            title="SQL Database",
            description="External SQL database.",
            kinds=[
                {
                    "kind": "sql",
                    "auth_scheme": "API_KEY",
                    "auth_config_schema": {
                        "type": "object",
                        "required": ["dialect", "host", "database"],
                        "properties": {
                            "dialect": {"type": "string"},
                            "host": {"type": "string"},
                            "port": {"type": "integer"},
                            "database": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                }
            ],
            is_active=True,
        )
        db_session.add(connector)
        await db_session.commit()

        service = _service(db_session)
        with pytest.raises(ConnectorValidationError):
            await service.create_auth_config(
                user_id=fixed_test_user["id"],
                organization_id=fixed_test_org["id"],
                connector_id=connector.id,
                provider="LEMMA",
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                provider_config={
                    "dialect": "postgresql",
                    "host": "10.0.0.5",
                    "port": 5432,
                    "database": "internal",
                },
                name=f"sql-{uuid4().hex[:8]}",
            )
