from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _create_test_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Default delegated pod {uuid4().hex[:8]}",
            "description": "Default pod agent delegated authz e2e pod",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _default_pod_agent_headers(*, user_id: str, pod_id: str) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID,
        workload_name=DEFAULT_POD_AGENT_NAME,
        pod_id=UUID(pod_id),
        session_id=f"default-pod-agent-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


async def _agent_headers(
    *,
    user_id: str,
    pod_id: str,
    agent_id: str,
    agent_name: str,
) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(agent_id),
        workload_name=agent_name,
        pod_id=UUID(pod_id),
        session_id=f"named-agent-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}



async def _seed_agent_owned_connector(
    db_session,
    *,
    organization_id: str,
    pod_id: str,
    connector_id: str,
    account_user_id: str,
    api_key: str = "agent-owned-secret",
) -> Account:
    app = Connector(
        id=connector_id,
        title=f"{connector_id} title",
        description="Agent owned connector e2e",
        kinds=[
            {
                "kind": "package",
                "auth_scheme": "API_KEY",
                "system_default_available": True,
            }
        ],
        is_active=True,
    )
    auth_config_id = uuid4()
    auth_config = AuthConfig(
        id=auth_config_id,
        organization_id=organization_id,
        connector_id=connector_id,
        name=connector_id,
        kind="package",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
    )
    account = Account(
        id=uuid4(),
        connector_id=connector_id,
        user_id=account_user_id,
        organization_id=organization_id,
        auth_config_id=auth_config_id,
        credentials={"api_key": api_key},
    )
    operation = ConnectorOperation(
        id=f"{connector_id}:echo",
        connector_id=connector_id,
        name="echo",
        provider_operation_name="echo",
        display_name="Echo",
        description="Echo payload",
        input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        output_schema={"type": "object"},
    )
    db_session.add_all([app, auth_config, account, operation])
    await db_session.commit()
    return account


async def _replace_role_resource_grants(
    authenticated_client,
    *,
    pod_id: str,
    role_name: str,
    grants: list[dict],
) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{role_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


async def _replace_agent_resource_grants(
    authenticated_client,
    *,
    pod_id: str,
    agent_name: str,
    grants: list[dict],
) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.asyncio
async def test_default_pod_agent_delegation_inherits_user_access_across_pod_modules(
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    delegated_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
    )

    create_table = await async_client.post(
        f"/pods/{pod_id}/datastore/tables",
        headers=delegated_headers,
        json={
            "name": "customers",
            "enable_rls": False,
            "columns": [{"name": "name", "type": "TEXT", "required": True}],
        },
    )
    assert create_table.status_code == status.HTTP_201_CREATED, create_table.text

    create_conversation = await async_client.post(
        f"/pods/{pod_id}/conversations",
        headers=delegated_headers,
        json={"title": "Delegated default pod chat"},
    )
    assert create_conversation.status_code == status.HTTP_201_CREATED, (
        create_conversation.text
    )

    read_endpoints = [
        f"/pods/{pod_id}",
        f"/pods/{pod_id}/members",
        f"/pods/{pod_id}/roles",
        f"/pods/{pod_id}/agents",
        f"/pods/{pod_id}/conversations",
        f"/pods/{pod_id}/datastore/tables",
        f"/pods/{pod_id}/datastore/tables/customers",
        f"/pods/{pod_id}/datastore/files",
        f"/pods/{pod_id}/functions",
        f"/pods/{pod_id}/workflows",
        f"/pods/{pod_id}/apps",
        f"/pods/{pod_id}/schedules",
    ]
    for endpoint in read_endpoints:
        response = await async_client.get(endpoint, headers=delegated_headers)
        assert response.status_code == status.HTTP_200_OK, (
            f"{endpoint} returned {response.status_code}: {response.text}"
        )

    tables = await async_client.get(
        f"/pods/{pod_id}/datastore/tables",
        headers=delegated_headers,
    )
    assert tables.status_code == status.HTTP_200_OK, tables.text
    assert {item["name"] for item in tables.json()["items"]} == {"customers"}


@pytest.mark.asyncio
async def test_default_pod_agent_for_org_owner_reads_apps_via_org_owner_shortcut(
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
):
    """Regression: an org owner's default pod agent must inherit the org-owner pod
    allow, not just explicit pod-role permissions.

    An org owner gets ``app.read`` on a pod they are not an explicit member of via
    the ``_is_org_owner_of_pod`` shortcut. That shortcut was gated to
    ``actor_type == USER``, so the default pod agent (a DELEGATED_USER_WORKLOAD that
    inherits the invoking user verbatim) was denied ``app.read`` -> 403 on
    ``lemma apps list`` / ``lemma pods import`` from the workspace, even though the
    same user could do it directly.
    """
    org_id = fixed_test_org["id"]

    # A different org member owns the pod; the org owner is NOT a pod member, so
    # their only path to app.read is the org-owner shortcut.
    member = await signup_user(async_client, "default-pod-agent-pod-owner")
    await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    create = await async_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Org-owner app-read pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
        headers=auth_headers(member),
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    pod_id = create.json()["id"]

    # The org owner reads apps directly (USER actor) — allowed via the shortcut.
    owner_apps = await authenticated_client.get(f"/pods/{pod_id}/apps")
    assert owner_apps.status_code == status.HTTP_200_OK, owner_apps.text

    # The org owner's DEFAULT POD AGENT must get the same pod-scoped access.
    delegated_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
    )
    agent_apps = await async_client.get(
        f"/pods/{pod_id}/apps", headers=delegated_headers
    )
    assert agent_apps.status_code == status.HTTP_200_OK, agent_apps.text


@pytest.mark.asyncio
async def test_default_pod_agent_for_org_owner_can_create_and_deploy_app(
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
):
    """Regression: the org owner's default pod agent must be able to CREATE and then
    UPDATE/deploy an app in a pod it only reaches via the org-owner shortcut.

    `app.create` checks the POD resource (already covered by the shortcut), but the
    follow-up `app.update` (the bundle deploy) checks the APP resource. The shortcut
    used to cover only the POD entity, so the deploy 403'd right after create. The
    org owner is not an explicit pod member here, so the shortcut is the only path.
    """
    org_id = fixed_test_org["id"]
    member = await signup_user(async_client, "default-pod-agent-deploy-owner")
    await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    create = await async_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Org-owner deploy pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
        headers=auth_headers(member),
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    pod_id = create.json()["id"]

    delegated_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
    )

    create_app = await async_client.post(
        f"/pods/{pod_id}/apps",
        headers=delegated_headers,
        json={"name": "dashboard", "description": "v1"},
    )
    assert create_app.status_code == status.HTTP_201_CREATED, create_app.text

    # The deploy step: app.update on the APP resource — previously a 403.
    update_app = await async_client.patch(
        f"/pods/{pod_id}/apps/dashboard",
        headers=delegated_headers,
        json={"description": "v2"},
    )
    assert update_app.status_code == status.HTTP_200_OK, update_app.text
    assert update_app.json()["description"] == "v2"


@pytest.mark.asyncio
async def test_default_pod_agent_delegation_does_not_exceed_current_user_access(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    viewer = await signup_user(async_client, "default-pod-agent-viewer")
    viewer_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=viewer_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER"],
    )
    viewer_delegated_headers = await _default_pod_agent_headers(
        user_id=viewer["id"],
        pod_id=pod_id,
    )

    list_tables = await async_client.get(
        f"/pods/{pod_id}/datastore/tables",
        headers=viewer_delegated_headers,
    )
    assert list_tables.status_code == status.HTTP_200_OK, list_tables.text

    create_table = await async_client.post(
        f"/pods/{pod_id}/datastore/tables",
        headers=viewer_delegated_headers,
        json={
            "name": "forbidden_customers",
            "enable_rls": False,
            "columns": [{"name": "name", "type": "TEXT"}],
        },
    )
    assert create_table.status_code == status.HTTP_403_FORBIDDEN, create_table.text

    normal_viewer_list = await async_client.get(
        f"/pods/{pod_id}/datastore/tables",
        headers=auth_headers(viewer),
    )
    assert normal_viewer_list.status_code == status.HTTP_200_OK, normal_viewer_list.text


@pytest.mark.asyncio
async def test_named_agent_connector_access_uses_core_roles_and_resource_grants(
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]
    connector_id = f"agent_app_{suffix}"
    account_owner = await signup_user(async_client, "agent-owned-account")
    account = await _seed_agent_owned_connector(
        db_session,
        organization_id=fixed_test_org["id"],
        pod_id=pod_id,
        connector_id=connector_id,
        account_user_id=account_owner["id"],
    )

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Grant Limited Agent",
            "instruction": "Only use explicitly granted resources.",
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    await _replace_role_resource_grants(
        authenticated_client,
        pod_id=pod_id,
        role_name="POD_ADMIN",
        grants=[
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )
    await _replace_agent_resource_grants(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent["name"],
        grants=[
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )
    delegated_headers = await _agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        agent_id=agent["id"],
        agent_name=agent["name"],
    )
    mock_execution_client = SimpleNamespace(
        list_operations=AsyncMock(return_value=[SimpleNamespace(name="echo")]),
        get_operation=AsyncMock(return_value=SimpleNamespace(descriptor=None)),
        execute_operation=AsyncMock(return_value={"ok": True}),
    )
    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        return_value=mock_execution_client,
    ):
        app_execution = await async_client.post(
            (
                f"/organizations/{fixed_test_org['id']}/connectors/"
                f"{connector_id}/operations/echo/execute"
            ),
            headers=delegated_headers,
            json={"account_id": str(account.id), "payload": {"message": "hello"}},
        )
    assert app_execution.status_code == status.HTTP_200_OK, app_execution.text
    assert app_execution.json()["result"] == {"ok": True}

    create_ungranted_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "No App Agent",
            "instruction": "No connector grants.",
        },
    )
    assert create_ungranted_agent.status_code == status.HTTP_201_CREATED, (
        create_ungranted_agent.text
    )
    ungranted = create_ungranted_agent.json()
    ungranted_headers = await _agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        agent_id=ungranted["id"],
        agent_name=ungranted["name"],
    )
    denied_execution = await async_client.post(
        (
            f"/organizations/{fixed_test_org['id']}/connectors/"
            f"{connector_id}/operations/echo/execute"
        ),
        headers=ungranted_headers,
        json={"account_id": str(account.id), "payload": {"message": "hello"}},
    )
    assert denied_execution.status_code == status.HTTP_403_FORBIDDEN
    assert denied_execution.json()["code"] == "CONNECTOR_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_named_agent_connector_access_resolves_dynamic_account_with_app_id_grant(
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]
    connector_id = f"agent_dynamic_app_{suffix}"
    account = await _seed_agent_owned_connector(
        db_session,
        organization_id=fixed_test_org["id"],
        pod_id=pod_id,
        connector_id=connector_id,
        account_user_id=fixed_test_user["id"],
        api_key="dynamic-agent-secret",
    )

    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Dynamic Grant Agent",
            "instruction": "Use dynamically resolved account resources.",
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    role_grants = await authenticated_client.put(
        f"/pods/{pod_id}/roles/POD_ADMIN/permissions",
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
    assert role_grants.status_code == status.HTTP_200_OK, role_grants.text
    assert role_grants.json()["grants"][0]["resource_name"] == connector_id

    agent_grants = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent['name']}/permissions",
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
    assert agent_grants.status_code == status.HTTP_200_OK, agent_grants.text
    assert agent_grants.json()["grants"][0]["resource_name"] == connector_id

    delegated_headers = await _agent_headers(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        agent_id=agent["id"],
        agent_name=agent["name"],
    )
    mock_execution_client = SimpleNamespace(
        list_operations=AsyncMock(return_value=[SimpleNamespace(name="echo")]),
        get_operation=AsyncMock(return_value=SimpleNamespace(descriptor=None)),
        execute_operation=AsyncMock(return_value={"ok": True}),
    )
    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        return_value=mock_execution_client,
    ) as create_execution_client:
        app_execution = await async_client.post(
            (
                f"/organizations/{fixed_test_org['id']}/connectors/"
                f"{connector_id}/operations/echo/execute"
            ),
            headers=delegated_headers,
            json={"payload": {"message": "hello-dynamic"}},
        )
    assert app_execution.status_code == status.HTTP_200_OK, app_execution.text
    assert app_execution.json()["result"] == {"ok": True}
    assert (
        create_execution_client.call_args.args[1]["api_key"]
        == "dynamic-agent-secret"
    )
    mock_execution_client.execute_operation.assert_awaited_once()
    assert str(account.user_id) == fixed_test_user["id"]
