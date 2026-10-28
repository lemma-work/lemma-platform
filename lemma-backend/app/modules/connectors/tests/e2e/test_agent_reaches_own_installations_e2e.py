"""The agent's connector tools reach the person's own apps with no setup.

Review of #1039: on main the tools listed and ran every installation in the
organization, on the person's own account. Pod connectors narrowed them to
what the pod declares, and the migration declared connectors only where a
workload already held a grant -- so an ordinary pod's assistant could not
reach its person's Gmail at all. The tools now offer each installation the
person has an account on, and the first run declares it in the pod as a
connector of each member's own account (source AUTO). A named agent goes
through the same declaration and is then asked for ``connector.use`` like on
any connector; its refusal names the connector and the agent, for the card
offering it the grant.
"""

from __future__ import annotations

from app.modules.agent.domain.tools import NoArguments

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.pod_connector import PodConnector
from app.modules.connectors.tests.e2e.test_agent_connector_toolset_e2e import (  # noqa: F401 -- fixtures
    _create_agent,
    agent_mcp_server,
    allow_private_targets,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def own_installation(
    db_session,
    fixed_test_org,
    fixed_test_user,
    agent_mcp_server,  # noqa: F811
    allow_private_targets,  # noqa: F811
    connector_test_pod,
):
    """An MCP install the person has an account on, and the pod declares
    nothing for."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.connectors.api.dependencies import get_connector_service

    connector = Connector(
        id=f"mcp-own-{uuid4().hex[:8]}",
        title="Own MCP",
        description="An installation the pod has not declared.",
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
        name=f"own-mcp-{uuid4().hex[:8]}",
    )
    await service.create_account(
        user_id=user_id,
        organization_id=org_id,
        auth_config_id=install.id,
        credentials={"api_key": "unused-by-this-server"},
    )
    return install, org_id, user_id, UUID(str(connector_test_pod["id"]))


def _deps(own_installation, *, agent_id=None, agent_name=None):
    from app.modules.agent.domain.context import ToolContext

    _install, org_id, user_id, pod_id = own_installation
    return ToolContext(
        user_id=user_id,
        org_id=org_id,
        pod_id=pod_id,
        conversation_id=uuid4(),
        workload_id=agent_id or pod_id,
        agent_name=agent_name or DEFAULT_POD_AGENT_NAME,
        is_pod_default_agent=agent_id is None,
    )


async def _declared(db_session, pod_id: UUID, auth_config_id) -> list[PodConnector]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(PodConnector).where(
            PodConnector.pod_id == pod_id,
            PodConnector.auth_config_id == auth_config_id,
        )
    )
    return list(rows.scalars())


async def test_the_assistant_lists_and_runs_the_persons_own_installation(
    own_installation, db_session
):
    from app.modules.agent.infrastructure.tools.connectors.models import (
        RunConnectorOperationRequest,
    )
    from app.modules.agent.infrastructure.tools.connectors.tools import (
        list_connectors,
        run_connector_operation,
    )

    install, _org, _user, pod_id = own_installation
    deps = _deps(own_installation)

    listed = await list_connectors(deps, NoArguments())
    offered = [i for i in listed["items"] if i["connector"] == install.name]
    assert offered and offered[0]["mode"] == "PER_MEMBER", listed
    assert await _declared(db_session, pod_id, install.id) == []

    result = await run_connector_operation(
        deps,
        RunConnectorOperationRequest(
            connector=install.name,
            operation="convert_currency",
            payload={"amount": 21, "to_currency": "EUR"},
        ),
    )

    assert "42" in str(result), result
    declared = await _declared(db_session, pod_id, install.id)
    assert [(c.mode, c.source) for c in declared] == [("PER_MEMBER", "AUTO")]
    # Declared once: the next run goes through it, and declares nothing more.
    again = await run_connector_operation(
        deps,
        RunConnectorOperationRequest(
            connector=install.name,
            operation="convert_currency",
            payload={"amount": 1, "to_currency": "EUR"},
        ),
    )
    assert "error" not in again, again
    assert len(await _declared(db_session, pod_id, install.id)) == 1


async def test_a_named_agent_is_refused_with_what_a_grant_card_needs(
    own_installation, db_session, connector_test_pod, authenticated_client
):
    """The declaration is the same; the grant is not implied. The refusal
    names the declared connector and the agent, and the connector stays
    declared so the grant can be given on it."""
    from app.modules.agent.infrastructure.tools.connectors.models import (
        RunConnectorOperationRequest,
    )
    from app.modules.agent.infrastructure.tools.connectors.tools import (
        run_connector_operation,
    )
    from app.modules.connectors.contracts.pod_connectors import (
        ConnectorUseRefusedError,
        resolve_pod_connector,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent.infrastructure.tools.authority import (
        tool_authorization_context,
    )

    install, _org, _user, pod_id = own_installation
    agent = await _create_agent(authenticated_client, connector_test_pod)
    deps = _deps(own_installation, agent_id=UUID(agent["id"]), agent_name=agent["name"])

    result = await run_connector_operation(
        deps,
        RunConnectorOperationRequest(
            connector=install.name,
            operation="convert_currency",
            payload={"amount": 21, "to_currency": "EUR"},
        ),
    )
    # Refused, not run. Not `"42" not in str(result)`: the refusal carries ids
    # and a random slug, and any of them can contain "42".
    assert result.get("success") is False, result
    declared = await _declared(db_session, pod_id, install.id)
    assert len(declared) == 1, "declared before the grant was asked"

    uow = SqlAlchemyUnitOfWork(db_session)
    ctx = await tool_authorization_context(uow, deps)
    with pytest.raises(ConnectorUseRefusedError) as refused:
        await resolve_pod_connector(uow, ctx, pod_id=pod_id, slug=declared[0].slug)
    details = refused.value.details
    assert details["pod_connector"] == declared[0].slug
    assert details["pod_connector_id"] == str(declared[0].id)
    assert details["connector_id"] == install.connector_id
    assert details["workload_id"] == agent["id"]
    assert "connector.use" in details["permission_ids"]
