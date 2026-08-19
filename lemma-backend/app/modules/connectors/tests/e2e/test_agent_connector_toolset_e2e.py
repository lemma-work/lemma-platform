"""The agent's connector tools, driven against a live MCP server.

The four tools were registered and unit-tested, but nothing drove them against
a real install -- so "an agent can use a tenant connector" was an assumption.
This walks the path an agent actually takes: list the installs, search their
operations, read one's schema, run it, and get a real answer back from a real
server.

It also pins the two things that are easy to get wrong in a toolset: a bad
argument must come back as something the model can correct rather than an
exception that ends the run, and the tools must reach the same authorization
decision as the HTTP route, since they share one implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture(scope="module")
async def agent_mcp_server():
    from fastmcp import FastMCP

    server = FastMCP("agent-toolset")

    @server.tool
    def convert_currency(amount: float, to_currency: str) -> dict:
        """Convert an amount into another currency."""
        return {"amount": amount * 2, "currency": to_currency}

    port = _free_port()
    task = asyncio.create_task(
        server.run_async(transport="http", host="127.0.0.1", port=port, show_banner=False)
    )

    async def probe() -> None:
        if task.done():
            raise RuntimeError(f"MCP server failed to start: {task.exception()}")
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()

    # retry_exceptions=(OSError,): the port not listening yet is the expected
    # "not ready" case. A crashed server task instead raises RuntimeError from
    # inside probe(), which is not in retry_exceptions and so propagates
    # immediately, same as the original loop's eager task.done() check.
    # interval kept at the original 0.05s (already tighter than the usual
    # 0.15s default) since this is a hot local port check.
    await eventually(
        label=f"MCP server on port {port} to start listening",
        probe=probe,
        done=lambda _: True,
        retry_exceptions=(OSError,),
        timeout_seconds=5.0,
        interval_seconds=0.05,
    )

    yield f"http://127.0.0.1:{port}/mcp"

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def allow_private_targets(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)


def _run_context(deps):
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    return RunContext(deps=deps, model=None, usage=RunUsage(), prompt=None)


@pytest_asyncio.fixture
async def installed_connector(
    db_session, fixed_test_org, fixed_test_user, agent_mcp_server, allow_private_targets
):
    """An MCP install with a connected account, as an org admin would leave it."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.api.dependencies import get_connector_service

    connector = Connector(
        id=f"mcp-agent-{uuid4().hex[:8]}",
        title="Agent MCP",
        description="MCP server for the agent toolset test.",
        kinds=[
            {
                "kind": "mcp",
                "auth_scheme": "API_KEY",
                "discovery": "mcp",
                "auth_config_schema": {
                    "type": "object",
                    "required": ["server_url"],
                    "properties": {"server_url": {"type": "string"}},
                    "additionalProperties": False,
                },
            }
        ],
        is_active=True,
    )
    db_session.add(connector)
    await db_session.commit()

    org_id = UUID(str(fixed_test_org["id"]))
    user_id = UUID(str(fixed_test_user["id"]))
    service = get_connector_service(SqlAlchemyUnitOfWork(db_session))
    install = await service.create_auth_config(
        user_id=user_id,
        organization_id=org_id,
        connector_id=connector.id,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        config={"server_url": agent_mcp_server},
        name=f"agent-mcp-{uuid4().hex[:8]}",
    )
    await service.create_account(
        user_id=user_id,
        organization_id=org_id,
        auth_config_id=install.id,
        credentials={"api_key": "unused-by-this-server"},
    )
    return install, org_id, user_id


@pytest.fixture
def agent_deps(installed_connector, connector_test_pod):
    from app.modules.agent.tools.context import BaseAgentContext

    _install, org_id, user_id = installed_connector
    return BaseAgentContext(
        user_id=user_id,
        org_id=org_id,
        pod_id=UUID(str(connector_test_pod["id"])),
        conversation_id=uuid4(),
    )


class TestTheAgentCanUseATenantConnector:
    async def test_list_shows_the_install_and_its_kind(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.pydantic_adapter import list_connectors

        install, _org, _user = installed_connector
        result = await list_connectors(_run_context(agent_deps))
        names = {item["auth_config"] for item in result["items"]}
        assert install.name in names
        entry = next(i for i in result["items"] if i["auth_config"] == install.name)
        # The agent is told the kind, so it knows what it is talking to.
        assert entry["kind"] == "mcp"

    async def test_search_finds_the_servers_own_tool(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.models import (
            SearchConnectorOperationsRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            search_connector_operations,
        )

        install, _org, _user = installed_connector
        result = await search_connector_operations(
            _run_context(agent_deps),
            SearchConnectorOperationsRequest(
                auth_config=install.name, query="currency", limit=10
            ),
        )
        found = str(result)
        # Discovered from the live server, not from any catalog.
        assert "convert_currency" in found

    async def test_describe_returns_the_schema_the_server_published(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.models import (
            DescribeConnectorOperationRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            describe_connector_operation,
        )

        install, _org, _user = installed_connector
        result = await describe_connector_operation(
            _run_context(agent_deps),
            DescribeConnectorOperationRequest(
                auth_config=install.name, operation="convert_currency"
            ),
        )
        schema = result.get("input_schema") or {}
        assert "amount" in (schema.get("properties") or {})

    async def test_running_it_returns_the_servers_answer(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )

        install, _org, _user = installed_connector
        result = await run_connector_operation(
            _run_context(agent_deps),
            RunConnectorOperationRequest(
                auth_config=install.name,
                operation="convert_currency",
                arguments={"amount": 21, "to_currency": "EUR"},
            ),
        )
        assert "error" not in result, result
        # 21 * 2, computed by the MCP server.
        assert "42" in str(result)

    async def test_bad_arguments_come_back_as_something_the_model_can_fix(
        self, installed_connector, agent_deps
    ):
        # Not an exception: an exception ends the run, whereas a structured
        # result lets the model correct itself on the next turn. The schema is
        # returned with it so it can do that without another round trip.
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )

        install, _org, _user = installed_connector
        result = await run_connector_operation(
            _run_context(agent_deps),
            RunConnectorOperationRequest(
                auth_config=install.name,
                operation="convert_currency",
                arguments={"amount": "twenty-one", "to_currency": "EUR"},
            ),
        )
        assert result["error"] == "invalid_arguments"
        assert result["violations"]
        assert result["input_schema"]

    async def test_an_unknown_operation_is_reported_not_raised(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )

        install, _org, _user = installed_connector
        result = await run_connector_operation(
            _run_context(agent_deps),
            RunConnectorOperationRequest(
                auth_config=install.name,
                operation="no_such_tool",
                arguments={},
            ),
        )
        assert "error" in result

    async def test_an_agent_outside_the_org_sees_nothing(
        self, installed_connector, agent_deps
    ):
        # The toolset resolves through the same authorization path as the HTTP
        # route, so an install belongs to exactly one org here too. The refusal
        # arrives as data rather than an exception: the other three tools
        # already behave that way, and raising would end the agent's run over
        # something it can simply report.
        from app.modules.agent.tools.context import BaseAgentContext
        from app.modules.agent.tools.connectors.pydantic_adapter import list_connectors

        install, _org, user_id = installed_connector
        stranger = BaseAgentContext(
            user_id=user_id,
            org_id=uuid4(),
            pod_id=agent_deps.pod_id,
            conversation_id=uuid4(),
        )
        result = await list_connectors(_run_context(stranger))
        assert "error" in result
        names = {item["auth_config"] for item in result.get("items", [])}
        assert install.name not in names
