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
import json
import socket
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.modules.connectors.domain.account import AccountStatus
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


@pytest_asyncio.fixture(scope="module")
async def second_mcp_server():
    """A different server with a different toolset, for the repoint tests."""
    from fastmcp import FastMCP

    server = FastMCP("tenant-lifecycle-2")

    @server.tool
    def ping() -> str:
        """Return pong."""
        return "pong"

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
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
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
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                config={"server_url": "https://x.test", "bearer_token": "sk-x"},
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
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                config={"server_url": "http://169.254.169.254/latest/meta-data/"},
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
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
            name=f"mcp-a-{uuid4().hex[:8]}",
        )
        second = await service.create_auth_config(
            user_id=fixed_test_user["id"],
            organization_id=fixed_test_org["id"],
            connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
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
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
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
                config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
                config={
                    "dialect": "postgresql",
                    "host": "10.0.0.5",
                    "port": 5432,
                    "database": "internal",
                },
                name=f"sql-{uuid4().hex[:8]}",
            )


class TestConnectingAnAccountAndExecuting:
    """The half of the lifecycle that install-and-discover does not reach.

    Discovery proves the operations exist; only connecting an account and
    running one proves a tenant connector is actually usable.
    """

    async def test_an_account_can_be_connected_and_an_operation_run(
        self,
        db_session,
        authenticated_client,
        mcp_connector,
        fixed_test_org,
        fixed_test_user,
        live_mcp_server,
        allow_private_targets,
    ):
        service = _service(db_session)
        # The routes declare these as UUID path/token params, so the service
        # only ever sees UUIDs; the fixtures hand back raw JSON strings.
        org_id = UUID(str(fixed_test_org["id"]))
        user_id = UUID(str(fixed_test_user["id"]))
        install = await service.create_auth_config(
            user_id=user_id,
            organization_id=org_id,
            connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
            name=f"mcp-exec-{uuid4().hex[:8]}",
        )

        account = await service.create_account(
            user_id=user_id,
            organization_id=org_id,
            auth_config_id=install.id,
            credentials={"api_key": "not-required-by-this-server"},
        )
        assert account.auth_config_id == install.id

        # Execution goes over HTTP: the use case takes a Request, because the
        # authorization context is built from it. Calling it any other way
        # would be testing a path no caller uses.
        executed = await authenticated_client.post(
            f"/organizations/{org_id}/connectors/{install.name}"
            f"/operations/add/execute",
            json={"payload": {"a": 2, "b": 3}},
        )
        assert executed.status_code == 200, executed.text
        # 2 + 3, computed by the MCP server rather than by anything in-process.
        assert "5" in json.dumps(executed.json()["result"]), executed.text


class TestUpdatingAnInstallInPlace:
    """Rotating an install's target without disconnecting anyone.

    Before the update endpoint, changing an MCP server URL meant delete plus
    recreate, and `accounts.auth_config_id` cascades -- so the routine
    operation silently deleted every account, its grants, and left every
    schedule and surface holding a dangling id.
    """

    @pytest_asyncio.fixture
    async def installed_with_account(
        self,
        db_session,
        mcp_connector,
        fixed_test_org,
        fixed_test_user,
        live_mcp_server,
        allow_private_targets,
    ):
        service = _service(db_session)
        org_id = UUID(str(fixed_test_org["id"]))
        user_id = UUID(str(fixed_test_user["id"]))
        install = await service.create_auth_config(
            user_id=user_id,
            organization_id=org_id,
            connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
            name=f"mcp-upd-{uuid4().hex[:8]}",
        )
        account = await service.create_account(
            user_id=user_id,
            organization_id=org_id,
            auth_config_id=install.id,
            credentials={"api_key": "k"},
        )
        return service, org_id, user_id, install, account

    async def test_renaming_keeps_the_account_attached(
        self, installed_with_account
    ):
        service, org_id, user_id, install, account = installed_with_account
        renamed = f"renamed-{uuid4().hex[:8]}"
        updated, _discovered, marked = await service.update_auth_config(
            user_id=user_id,
            organization_id=org_id,
            auth_config_name=install.name,
            name=renamed,
        )
        assert updated.name == renamed
        # A rename is not a credential change.
        assert marked == 0
        still_there = await service.account_repository.get(account.id)
        assert still_there is not None
        assert still_there.auth_config_id == install.id
        assert still_there.status is AccountStatus.CONNECTED

    async def test_repointing_at_another_server_keeps_the_account_but_asks_for_reauth(
        self, installed_with_account, second_mcp_server
    ):
        service, org_id, user_id, install, account = installed_with_account
        updated, discovered, marked = await service.update_auth_config(
            user_id=user_id,
            organization_id=org_id,
            auth_config_name=install.name,
            config={"server_url": second_mcp_server},
        )
        assert updated.config["server_url"] == second_mcp_server
        # The new server's tools replaced the old ones.
        assert discovered >= 1
        assert marked == 1

        # The whole point: the account still exists, with the same id.
        survivor = await service.account_repository.get(account.id)
        assert survivor is not None
        assert survivor.id == account.id
        assert survivor.status is AccountStatus.REAUTH_REQUIRED
        # Its credentials are untouched -- reconnecting replaces them, but
        # nothing here destroyed them.
        assert survivor.credentials is not None

    async def test_operations_follow_the_new_server(
        self, installed_with_account, second_mcp_server
    ):
        service, org_id, user_id, install, _account = installed_with_account
        from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (
            AuthConfigOperationRepository,
        )

        repo = AuthConfigOperationRepository(service.uow.session)
        before = {op.name for op in await repo.list_by_auth_config(install.id)}
        assert "add" in before

        await service.update_auth_config(
            user_id=user_id,
            organization_id=org_id,
            auth_config_name=install.name,
            config={"server_url": second_mcp_server},
        )
        after = {op.name for op in await repo.list_by_auth_config(install.id)}
        assert "ping" in after
        # The old server's tools are gone rather than accumulating.
        assert "add" not in after

    async def test_a_private_target_is_refused_on_update_too(
        self, installed_with_account
    ):
        # An install vetted once must not become a way to reach the metadata
        # service later.
        service, org_id, user_id, install, _account = installed_with_account
        with pytest.raises(ConnectorValidationError):
            await service.update_auth_config(
                user_id=user_id,
                organization_id=org_id,
                auth_config_name=install.name,
                config={"server_url": "http://169.254.169.254/latest/meta-data/"},
            )

    async def test_an_invalid_config_is_refused_on_update_too(
        self, installed_with_account
    ):
        service, org_id, user_id, install, _account = installed_with_account
        with pytest.raises(ConnectorValidationError):
            await service.update_auth_config(
                user_id=user_id,
                organization_id=org_id,
                auth_config_name=install.name,
                config={"server_url": "https://x.test", "smuggled": "value"},
            )

    async def test_promoting_a_second_install_demotes_the_first(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user, live_mcp_server, allow_private_targets
    ):
        service = _service(db_session)
        org_id = UUID(str(fixed_test_org["id"]))
        user_id = UUID(str(fixed_test_user["id"]))
        first = await service.create_auth_config(
            user_id=user_id, organization_id=org_id, connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server}, name=f"mcp-d1-{uuid4().hex[:8]}",
        )
        second = await service.create_auth_config(
            user_id=user_id, organization_id=org_id, connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server}, name=f"mcp-d2-{uuid4().hex[:8]}",
        )
        assert first.is_default and not second.is_default

        # A partial unique index allows exactly one default per (org,
        # connector), so this only works if the first is demoted in the same
        # transaction.
        promoted, _d, _m = await service.update_auth_config(
            user_id=user_id,
            organization_id=org_id,
            auth_config_name=second.name,
            is_default=True,
        )
        assert promoted.is_default is True
        demoted = await service.auth_config_repository.get(first.id)
        assert demoted.is_default is False


class TestDiscoveredOperationsAreVisibleThroughTheApi:
    """Listing an install's operations must return the ones it discovered.

    Execution always consulted the install's own operation set, but every
    listing path resolved the auth config and then queried the *catalog* by
    (connector_id, kind). For mcp and http the catalog is empty by
    construction, so an MCP server's tools were executable only by someone who
    already knew a name -- list returned nothing, detail 404'd, and the CLI,
    the SDKs and the agent toolset all reported an install with no operations.
    """

    @pytest_asyncio.fixture
    async def install(
        self, db_session, mcp_connector, fixed_test_org, fixed_test_user,
        live_mcp_server, allow_private_targets,
    ):
        service = _service(db_session)
        return await service.create_auth_config(
            user_id=UUID(str(fixed_test_user["id"])),
            organization_id=UUID(str(fixed_test_org["id"])),
            connector_id=mcp_connector.id,
            config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
            config={"server_url": live_mcp_server},
            name=f"mcp-vis-{uuid4().hex[:8]}",
        )

    async def test_listing_returns_the_discovered_tools(
        self, authenticated_client, fixed_test_org, install
    ):
        org_id = fixed_test_org["id"]
        listed = await authenticated_client.get(
            f"/organizations/{org_id}/connectors/{install.name}/operations"
        )
        assert listed.status_code == 200, listed.text
        names = {item["name"] for item in listed.json()["items"]}
        assert {"add", "lookup_customer"} <= names

    async def test_searching_narrows_to_the_matching_tool(
        self, authenticated_client, fixed_test_org, install
    ):
        org_id = fixed_test_org["id"]
        found = await authenticated_client.get(
            f"/organizations/{org_id}/connectors/{install.name}/operations",
            params={"query": "customer"},
        )
        assert found.status_code == 200, found.text
        names = {item["name"] for item in found.json()["items"]}
        assert "lookup_customer" in names

    async def test_detail_returns_the_schema_the_server_published(
        self, authenticated_client, fixed_test_org, install
    ):
        org_id = fixed_test_org["id"]
        detail = await authenticated_client.get(
            f"/organizations/{org_id}/connectors/{install.name}/operations/add"
        )
        assert detail.status_code == 200, detail.text
        schema = detail.json()["input_schema"]
        assert schema["properties"]["a"]["type"] == "integer"

    async def test_batch_details_include_discovered_tools(
        self, authenticated_client, fixed_test_org, install
    ):
        org_id = fixed_test_org["id"]
        batch = await authenticated_client.post(
            f"/organizations/{org_id}/connectors/{install.name}/operations/details",
            json={"operation_names": ["add"]},
        )
        assert batch.status_code == 200, batch.text
        names = {item["name"] for item in batch.json()["items"]}
        assert "add" in names
