"""The agent's connector tools, driven against a live MCP server.

The four tools were registered and unit-tested, but nothing drove them against
a real install -- so "an agent can use a tenant connector" was an assumption.
This walks the path an agent actually takes: list the pod's connectors, search
their operations, read one's schema, run it, and get a real answer back from a
real server.

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

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
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

    @server.tool
    def refuse_transfer(amount: float) -> dict:
        """Move money; this server always declines."""
        raise ValueError(f"insufficient funds to move {amount}")

    port = _free_port()
    task = asyncio.create_task(
        server.run_async(
            transport="http", host="127.0.0.1", port=port, show_banner=False
        )
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


#: The pod connector every test here runs through.
SLUG = "fx"


@pytest_asyncio.fixture
async def installed_connector(
    db_session,
    fixed_test_org,
    fixed_test_user,
    agent_mcp_server,
    allow_private_targets,
    connector_test_pod,
    authenticated_client,
):
    """An MCP install with a connected account, declared in the pod as `fx`."""
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
    declared = await authenticated_client.post(
        f"/pods/{connector_test_pod['id']}/connectors",
        json={"slug": SLUG, "auth_config_id": str(install.id), "mode": "PER_MEMBER"},
    )
    assert declared.status_code == 201, declared.text
    return install, org_id, user_id


@pytest.fixture
def agent_deps(installed_connector, connector_test_pod):
    """The pod's own assistant, as `run_context_builder` builds it.

    Addressed by its ``agents`` row id, which is its pod's, and carrying the
    flag every builder of this context sets. The named-agent cases below build
    their own context with a real agent id instead.
    """
    from app.modules.agent.domain.context import ToolContext

    _install, org_id, user_id = installed_connector
    pod_id = UUID(str(connector_test_pod["id"]))
    return ToolContext(
        user_id=user_id,
        org_id=org_id,
        pod_id=pod_id,
        conversation_id=uuid4(),
        workload_id=pod_id,
        agent_name=DEFAULT_POD_AGENT_NAME,
        is_pod_default_agent=True,
    )


class TestTheAgentCanUseATenantConnector:
    async def test_list_shows_the_pod_connector_and_whose_account_it_uses(
        self, installed_connector, agent_deps
    ):
        from app.modules.agent.tools.connectors.pydantic_adapter import list_connectors

        install, _org, _user = installed_connector
        result = await list_connectors(_run_context(agent_deps))
        entry = next(i for i in result["items"] if i["connector"] == SLUG)
        assert entry["connector_id"] == install.connector_id
        # The agent is told whose account a call uses.
        assert entry["mode"] == "PER_MEMBER"

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
                connector=SLUG, query="currency", limit=10
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
                connector=SLUG, operation="convert_currency"
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
                connector=SLUG,
                operation="convert_currency",
                payload={"amount": 21, "to_currency": "EUR"},
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
                connector=SLUG,
                operation="convert_currency",
                payload={"amount": "twenty-one", "to_currency": "EUR"},
            ),
        )
        assert result["code"] == "invalid_arguments"
        assert result["violations"]
        assert result["input_schema"]

    async def test_a_refusal_names_the_install_the_tool_and_what_the_server_said(
        self, installed_connector, agent_deps, agent_mcp_server
    ):
        """The error types fix their own message, so the model was told
        "Connector rejected the operation request" and nothing else: not which
        install, not which tool, not the server's reason. And with no
        `success: false` the failure counted as a successful tool call."""
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )
        from app.modules.agent.tools.tool_errors import result_is_failure

        install, _org, _user = installed_connector
        result = await run_connector_operation(
            _run_context(agent_deps),
            RunConnectorOperationRequest(
                connector=SLUG,
                operation="refuse_transfer",
                payload={"amount": 5},
            ),
        )

        assert result_is_failure(result), result
        assert result["connector"] == SLUG
        assert result["operation"] == "refuse_transfer"
        assert "insufficient funds" in result["upstream_message"]
        assert result["server_host"] == "127.0.0.1"
        # The host, not the URL: a path or query could carry a secret.
        assert agent_mcp_server not in str(result)

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
                connector=SLUG,
                operation="no_such_tool",
                payload={},
            ),
        )
        assert "error" in result

    async def test_an_agent_in_another_pod_sees_nothing(
        self, installed_connector, agent_deps
    ):
        # The toolset resolves through the same authorization path as the HTTP
        # route, so an install belongs to exactly one org here too. The refusal
        # arrives as data rather than an exception: the other three tools
        # already behave that way, and raising would end the agent's run over
        # something it can simply report.
        from app.modules.agent.domain.context import ToolContext
        from app.modules.agent.tools.connectors.pydantic_adapter import list_connectors

        install, _org, user_id = installed_connector
        elsewhere = uuid4()
        stranger = ToolContext(
            user_id=user_id,
            org_id=agent_deps.org_id,
            pod_id=elsewhere,
            conversation_id=uuid4(),
            workload_id=elsewhere,
            agent_name=DEFAULT_POD_AGENT_NAME,
            is_pod_default_agent=True,
        )
        result = await list_connectors(_run_context(stranger))
        names = {item["connector"] for item in result.get("items", [])}
        assert SLUG not in names, result


class TestAnMcpConnectorReachesANamedAgentOnlyWhenGranted:
    """The grant gate, over a real MCP server.

    Both halves of this existed and never met. The tests above prove an agent
    can drive a live MCP install, but they run as the *default* pod agent, which
    mirrors the person who asked and needs no grant -- so they say nothing about
    whether a grant is required. The pod module proves a named agent needs one,
    but against a synthetic connector with a stub operation, so it says nothing
    about MCP.

    Worth stating plainly, because it is the question this answers: a granted
    MCP connector is *not* registered as an MCP server on the agent. Nothing in
    the backend wires one -- the runtime is built with toolsets and no
    `MCPServer` anywhere. The agent reaches the server's tools through the
    connector toolset, so every call is resolved and authorized on this side and
    the credential never leaves it.
    """

    @staticmethod
    def _named_agent_context(installed_connector, pod_id, *, agent_id, agent_name):
        from app.modules.agent.domain.context import ToolContext

        _install, org_id, user_id = installed_connector
        return ToolContext(
            user_id=user_id,
            org_id=org_id,
            pod_id=pod_id,
            conversation_id=uuid4(),
            workload_id=agent_id,
            agent_name=agent_name,
        )

    async def test_a_named_agent_without_a_grant_cannot_run_anything(
        self, installed_connector, connector_test_pod, authenticated_client
    ):
        """Refused as data, not as an exception: the toolset reports it so the
        model can say so, rather than ending the run."""
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )

        install, _org_id, _user_id = installed_connector
        agent = await _create_agent(authenticated_client, connector_test_pod)
        deps = self._named_agent_context(
            installed_connector,
            UUID(str(connector_test_pod["id"])),
            agent_id=UUID(agent["id"]),
            agent_name=agent["name"],
        )

        result = await run_connector_operation(
            _run_context(deps),
            RunConnectorOperationRequest(
                connector=SLUG,
                operation="convert_currency",
                payload={"amount": 21, "to_currency": "EUR"},
            ),
        )
        assert result["success"] is False, result
        assert result.get("code") == "MISSING_WORKLOAD_RESOURCE_GRANT", result
        assert "42" not in str(result), result

    async def test_an_ungranted_agent_is_not_shown_the_connector_at_all(
        self, installed_connector, connector_test_pod, authenticated_client
    ):
        """The operation list is the connector's, and so is the grant.

        It used to be readable by any agent in the organization, because the
        grant was only checked where a credential was resolved. Now an agent
        sees what it may run and nothing else, so a search across connectors
        comes back empty rather than full of operations it would be refused.
        """
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            search_connector_operations,
        )
        from app.modules.agent.tools.connectors.models import (
            SearchConnectorOperationsRequest,
        )

        install, _org_id, _user_id = installed_connector
        agent = await _create_agent(authenticated_client, connector_test_pod)
        deps = self._named_agent_context(
            installed_connector,
            UUID(str(connector_test_pod["id"])),
            agent_id=UUID(agent["id"]),
            agent_name=agent["name"],
        )

        across = await search_connector_operations(
            _run_context(deps), SearchConnectorOperationsRequest()
        )
        assert across["items"] == [], across
        named = await search_connector_operations(
            _run_context(deps),
            SearchConnectorOperationsRequest(connector=SLUG),
        )
        assert named["success"] is False, named

    async def test_a_granted_named_agent_reaches_the_real_server(
        self, installed_connector, connector_test_pod, authenticated_client
    ):
        from app.modules.agent.tools.connectors.pydantic_adapter import (
            run_connector_operation,
        )
        from app.modules.agent.tools.connectors.models import (
            RunConnectorOperationRequest,
        )

        install, _org_id, _user_id = installed_connector
        pod_id = str(connector_test_pod["id"])
        agent = await _create_agent(authenticated_client, connector_test_pod)
        await _grant_connector_to_agent(
            authenticated_client,
            pod_id=pod_id,
            agent_name=agent["name"],
            connector_id=SLUG,
        )

        deps = self._named_agent_context(
            installed_connector,
            UUID(pod_id),
            agent_id=UUID(agent["id"]),
            agent_name=agent["name"],
        )
        result = await run_connector_operation(
            _run_context(deps),
            RunConnectorOperationRequest(
                connector=SLUG,
                operation="convert_currency",
                payload={"amount": 21, "to_currency": "EUR"},
            ),
        )
        # The answer comes from the MCP server itself, so this is the whole
        # path: grant, resolution, credential, transport, tool call.
        assert "error" not in result, result
        assert "42" in str(result), result


async def _create_agent(authenticated_client, connector_test_pod) -> dict:
    response = await authenticated_client.post(
        f"/pods/{connector_test_pod['id']}/agents",
        json={
            "name": f"connector-agent-{uuid4().hex[:8]}",
            "instruction": "Drive the tenant connector.",
            "toolsets": ["CONNECTORS"],
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _grant_connector_to_agent(
    authenticated_client, *, pod_id: str, agent_name: str, connector_id: str
) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "connector",
                    "resource_name": connector_id,
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
