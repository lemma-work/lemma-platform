from __future__ import annotations

from uuid import uuid4

import pytest
from starlette import status

from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    create_role_visibility_context,
    invite_org_member,
    item_names,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def test_pod_permission_catalog_is_pod_scoped(
    authenticated_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Permission Pod {uuid4().hex[:8]}",
            "description": "Permission API e2e pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    permissions = await authenticated_client.get(f"/pods/{pod_id}/permissions/catalog")
    assert permissions.status_code == status.HTTP_200_OK, permissions.text
    permission_ids = {item["id"] for item in permissions.json()["items"]}
    assert "app.update" in permission_ids
    assert "pod.role.manage" in permission_ids
    assert "org.member.manage" not in permission_ids
    assert "connector.manage" not in permission_ids

    effective_permissions = await authenticated_client.get(
        f"/pods/{pod_id}/permissions/me"
    )
    assert effective_permissions.status_code == status.HTTP_200_OK, (
        effective_permissions.text
    )
    effective_payload = effective_permissions.json()
    assert effective_payload["pod_id"] == pod_id
    assert "pod.read" in effective_payload["actions"]
    assert "pod.role.manage" in effective_payload["actions"]


async def test_custom_pod_role_permission_assignment_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Custom Permission Pod {uuid4().hex[:8]}",
            "description": "Custom permission role pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    role_response = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "finance_reader",
            "permission_ids": ["pod.read", "app.read"],
        },
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text
    role_payload = role_response.json()
    assert role_payload["name"] == "FINANCE_READER"
    assert set(role_payload["permission_ids"]) == {"pod.read", "app.read"}

    user = await signup_user(async_client, "permission-role")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=user,
    )
    viewer_member = await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", "FINANCE_READER"],
    )

    member = await async_client.get(
        f"/pods/{pod_id}/members/{viewer_member['pod_member_id']}",
        headers=auth_headers(user),
    )
    assert member.status_code == status.HTTP_200_OK, member.text
    payload = member.json()
    assert "FINANCE_READER" in payload["roles"]

    effective_permissions = await async_client.get(
        f"/pods/{pod_id}/permissions/me",
        headers=auth_headers(user),
    )
    assert effective_permissions.status_code == status.HTTP_200_OK, (
        effective_permissions.text
    )
    effective_actions = set(effective_permissions.json()["actions"])
    assert {"pod.read", "app.read"}.issubset(effective_actions)
    assert "pod.role.manage" not in effective_actions


async def test_resource_access_api_manages_role_and_member_grants_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="resource-access",
        custom_role="AGENT_REVIEWERS",
    )
    pod_id = ctx["pod_id"]

    role_list = await authenticated_client.get(f"/pods/{pod_id}/roles")
    assert role_list.status_code == status.HTTP_200_OK, role_list.text
    custom_role = next(
        role
        for role in role_list.json()["items"]
        if role["name"] == ctx["custom_role"]
    )

    agent_name = f"restricted_share_agent_{uuid4().hex[:8]}"
    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Help only selected people.",
            "visibility": "RESTRICTED",
        },
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    access_path = f"/pods/{pod_id}/resources/agent/{agent_name}/access"

    empty_access = await authenticated_client.get(access_path)
    assert empty_access.status_code == status.HTTP_200_OK, empty_access.text
    assert empty_access.json()["grants"] == []

    role_grant = await authenticated_client.put(
        f"{access_path}/grantees/ROLE/{custom_role['id']}",
        json={"permission_ids": ["agent.read"]},
    )
    assert role_grant.status_code == status.HTTP_200_OK, role_grant.text

    member_grant = await authenticated_client.put(
        f"{access_path}/grantees/POD_MEMBER/{ctx['viewer_member']['pod_member_id']}",
        json={"permission_ids": ["agent.read", "agent.execute"]},
    )
    assert member_grant.status_code == status.HTTP_200_OK, member_grant.text

    access = await authenticated_client.get(access_path)
    assert access.status_code == status.HTTP_200_OK, access.text
    grants = access.json()["grants"]
    assert {
        (grant["grantee_type"], grant["grantee_id"], tuple(grant["permission_ids"]))
        for grant in grants
    } == {
        ("ROLE", custom_role["id"], ("agent.read",)),
        (
            "POD_MEMBER",
            ctx["viewer_member"]["pod_member_id"],
            ("agent.execute", "agent.read"),
        ),
    }
    assert next(grant for grant in grants if grant["grantee_type"] == "ROLE")[
        "role_name"
    ] == ctx["custom_role"]
    member_access = next(
        grant for grant in grants if grant["grantee_type"] == "POD_MEMBER"
    )
    assert member_access["email"] == ctx["viewer"]["email"]

    viewer_list = await async_client.get(
        f"/pods/{pod_id}/agents",
        headers=ctx["viewer_headers"],
    )
    assert viewer_list.status_code == status.HTTP_200_OK, viewer_list.text
    assert agent_name in item_names(viewer_list.json())

    custom_list = await async_client.get(
        f"/pods/{pod_id}/agents",
        headers=ctx["custom_headers"],
    )
    assert custom_list.status_code == status.HTTP_200_OK, custom_list.text
    assert agent_name in item_names(custom_list.json())

    deleted = await authenticated_client.delete(
        f"{access_path}/grantees/POD_MEMBER/{ctx['viewer_member']['pod_member_id']}",
    )
    assert deleted.status_code == status.HTTP_200_OK, deleted.text
    assert {
        (grant["grantee_type"], grant["grantee_id"])
        for grant in deleted.json()["grants"]
    } == {("ROLE", custom_role["id"])}

    viewer_list_after_delete = await async_client.get(
        f"/pods/{pod_id}/agents",
        headers=ctx["viewer_headers"],
    )
    assert viewer_list_after_delete.status_code == status.HTTP_200_OK, (
        viewer_list_after_delete.text
    )
    assert agent_name not in item_names(viewer_list_after_delete.json())


async def test_pod_update_role_manage_and_delete_permissions_are_separate_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Pod Permission Split {uuid4().hex[:8]}",
            "description": "Pod permission split e2e pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    updater_role = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "pod_updaters",
            "permission_ids": ["pod.read", "pod.update"],
        },
    )
    assert updater_role.status_code == status.HTTP_201_CREATED, updater_role.text
    role_manager_role = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "role_managers",
            "permission_ids": ["pod.read", "pod.role.manage"],
        },
    )
    assert role_manager_role.status_code == status.HTTP_201_CREATED, (
        role_manager_role.text
    )

    updater = await signup_user(async_client, "pod-updater")
    updater_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=updater,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=updater_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", "POD_UPDATERS"],
    )
    updater_headers = auth_headers(updater)

    pod_update = await async_client.put(
        f"/pods/{pod_id}",
        headers=updater_headers,
        json={"description": "Pod update is allowed."},
    )
    assert pod_update.status_code == status.HTTP_200_OK, pod_update.text

    denied_role_create = await async_client.post(
        f"/pods/{pod_id}/roles",
        headers=updater_headers,
        json={"name": "should_not_create_roles"},
    )
    assert denied_role_create.status_code == status.HTTP_403_FORBIDDEN, (
        denied_role_create.text
    )

    role_manager = await signup_user(async_client, "pod-role-manager")
    role_manager_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=role_manager,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=role_manager_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", "ROLE_MANAGERS"],
    )
    role_manager_headers = auth_headers(role_manager)

    managed_role = await async_client.post(
        f"/pods/{pod_id}/roles",
        headers=role_manager_headers,
        json={"name": "managed_by_custom_role"},
    )
    assert managed_role.status_code == status.HTTP_201_CREATED, managed_role.text

    denied_pod_delete = await async_client.delete(
        f"/pods/{pod_id}",
        headers=role_manager_headers,
    )
    assert denied_pod_delete.status_code == status.HTTP_403_FORBIDDEN, (
        denied_pod_delete.text
    )


async def test_workload_permission_apis_replace_resource_grants_e2e(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Workload Permission Pod {uuid4().hex[:8]}",
            "description": "Workload permission API pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    table_response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "agent_tables",
            "visibility": "RESTRICTED",
            "columns": [{"name": "title", "type": "TEXT"}],
        },
    )
    assert table_response.status_code == status.HTTP_201_CREATED, table_response.text
    table = table_response.json()
    connector_id = "telegram"
    connector = await db_session.get(Connector, connector_id)
    if connector is None:
        db_session.add(
            Connector(
                id=connector_id,
                title="Telegram",
                description="Permission API telegram app id grant e2e",
                kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
    await db_session.commit()

    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "Grant Agent", "instruction": "Use only granted resources."},
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    agent = agent_response.json()

    agent_grants = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent['name']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": table["name"],
                    "permission_ids": ["datastore.table.read"],
                },
                {
                    "resource_type": "connector",
                    "resource_name": connector_id,
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    )
    assert agent_grants.status_code == status.HTTP_200_OK, agent_grants.text
    assert agent_grants.json()["agent_id"] == agent["id"]
    agent_grant_names = {
        grant["resource_name"] for grant in agent_grants.json()["grants"]
    }
    assert {table["name"], connector_id} <= agent_grant_names
    agent_permissions = await authenticated_client.get(
        f"/pods/{pod_id}/agents/{agent['name']}/permissions"
    )
    assert agent_permissions.status_code == status.HTTP_200_OK, agent_permissions.text
    agent_get = await authenticated_client.get(f"/pods/{pod_id}/agents/{agent['name']}")
    assert agent_get.status_code == status.HTTP_200_OK, agent_get.text
    assert agent_get.json()["permissions"] == agent_permissions.json()

    function_response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": "grant_function", "description": "Grant test function"},
    )
    assert function_response.status_code == status.HTTP_201_CREATED, (
        function_response.text
    )
    function = function_response.json()

    function_grants = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{function['name']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": table["name"],
                    "permission_ids": ["datastore.table.read"],
                },
                {
                    "resource_type": "connector",
                    "resource_name": connector_id,
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    )
    assert function_grants.status_code == status.HTTP_200_OK, function_grants.text
    assert function_grants.json()["function_id"] == function["id"]
    function_grant_names = {
        grant["resource_name"] for grant in function_grants.json()["grants"]
    }
    assert {table["name"], connector_id} <= function_grant_names
    function_permissions = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function['name']}/permissions"
    )
    assert function_permissions.status_code == status.HTTP_200_OK, (
        function_permissions.text
    )
    function_get = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function['name']}"
    )
    assert function_get.status_code == status.HTTP_200_OK, function_get.text
    assert function_get.json()["permissions"] == function_permissions.json()


async def test_connector_app_permission_string_id_reports_missing_app_e2e(
    authenticated_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Missing App Permission Pod {uuid4().hex[:8]}",
            "description": "Missing app permission API pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Missing Telegram Grant Agent",
            "instruction": "Use only granted resources.",
        },
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    agent = agent_response.json()

    grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent['name']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "connector",
                    "resource_name": "missing_permission_app",
                    "permission_ids": ["connector.use"],
                }
            ]
        },
    )
    assert grant_response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unknown resource name(s)" in grant_response.text
    assert "connector:missing_permission_app" in grant_response.text


async def test_org_roles_are_system_only_and_updated_via_member_role_api(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    role_response = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/roles",
        json={
            "name": "org_inviter",
            "permission_ids": ["org.read", "org.invitation.manage"],
        },
    )
    assert role_response.status_code == status.HTTP_404_NOT_FOUND, role_response.text

    user = await signup_user(async_client, "org-permission-role")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=user,
    )
    role_update = await authenticated_client.patch(
        f"/organizations/{fixed_test_org['id']}/members/{org_member['id']}/role",
        json={"role": "ORG_EDITOR"},
    )
    assert role_update.status_code == status.HTTP_200_OK, role_update.text
    assert role_update.json()["role"] == "ORG_EDITOR"

    members = await async_client.get(
        f"/organizations/{fixed_test_org['id']}/members",
        headers=auth_headers(user),
    )
    assert members.status_code == status.HTTP_200_OK, members.text


async def test_restricted_table_requires_explicit_resource_grant_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Restricted Table Pod {uuid4().hex[:8]}",
            "description": "Restricted table grant pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    create_shared = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "shared_notes",
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        },
    )
    assert create_shared.status_code == status.HTTP_201_CREATED, create_shared.text
    create_restricted = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "board_only",
            "visibility": "RESTRICTED",
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        },
    )
    assert create_restricted.status_code == status.HTTP_201_CREATED, (
        create_restricted.text
    )
    restricted_table = create_restricted.json()
    assert restricted_table["visibility"] == "RESTRICTED"

    role_response = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "board_readers",
            "permission_ids": ["pod.read", "datastore.table.read"],
        },
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text

    viewer = await signup_user(async_client, "restricted-table-viewer")
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
        roles=["POD_VIEWER", "BOARD_READERS"],
    )
    viewer_headers = auth_headers(viewer)

    list_before = await async_client.get(
        f"/pods/{pod_id}/datastore/tables",
        headers=viewer_headers,
    )
    assert list_before.status_code == status.HTTP_200_OK, list_before.text
    assert {item["name"] for item in list_before.json()["items"]} == {"shared_notes"}

    get_before = await async_client.get(
        f"/pods/{pod_id}/datastore/tables/board_only",
        headers=viewer_headers,
    )
    assert get_before.status_code == status.HTTP_403_FORBIDDEN, get_before.text

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/BOARD_READERS/permissions",
        json={
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": restricted_table["name"],
                    "permission_ids": ["datastore.table.read"],
                }
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    get_after = await async_client.get(
        f"/pods/{pod_id}/datastore/tables/board_only",
        headers=viewer_headers,
    )
    assert get_after.status_code == status.HTTP_200_OK, get_after.text

    list_after = await async_client.get(
        f"/pods/{pod_id}/datastore/tables",
        headers=viewer_headers,
    )
    assert list_after.status_code == status.HTTP_200_OK, list_after.text
    assert {item["name"] for item in list_after.json()["items"]} == {
        "shared_notes",
        "board_only",
    }


async def test_restricted_app_requires_explicit_resource_grant_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Restricted App Pod {uuid4().hex[:8]}",
            "description": "Restricted app grant pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    shared_name = f"shared_app_{uuid4().hex[:8]}"
    restricted_name = f"board_app_{uuid4().hex[:8]}"
    shared = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={"name": shared_name},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    restricted = await authenticated_client.post(
        f"/pods/{pod_id}/apps",
        json={"name": restricted_name, "visibility": "RESTRICTED"},
    )
    assert restricted.status_code == status.HTTP_201_CREATED, restricted.text
    restricted_app = restricted.json()
    assert restricted_app["visibility"] == "RESTRICTED"

    role_response = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "app_readers",
            "permission_ids": ["pod.read", "app.read"],
        },
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text

    viewer = await signup_user(async_client, "restricted-app-viewer")
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
        roles=["POD_VIEWER", "APP_READERS"],
    )
    viewer_headers = auth_headers(viewer)

    list_before = await async_client.get(
        f"/pods/{pod_id}/apps",
        headers=viewer_headers,
    )
    assert list_before.status_code == status.HTTP_200_OK, list_before.text
    assert {item["name"] for item in list_before.json()["items"]} == {shared_name}

    get_before = await async_client.get(
        f"/pods/{pod_id}/apps/{restricted_name}",
        headers=viewer_headers,
    )
    assert get_before.status_code == status.HTTP_403_FORBIDDEN, get_before.text

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/APP_READERS/permissions",
        json={
            "grants": [
                {
                    "resource_type": "app",
                    "resource_name": restricted_app["name"],
                    "permission_ids": ["app.read"],
                }
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    get_after = await async_client.get(
        f"/pods/{pod_id}/apps/{restricted_name}",
        headers=viewer_headers,
    )
    assert get_after.status_code == status.HTTP_200_OK, get_after.text

    list_after = await async_client.get(
        f"/pods/{pod_id}/apps",
        headers=viewer_headers,
    )
    assert list_after.status_code == status.HTTP_200_OK, list_after.text
    assert {item["name"] for item in list_after.json()["items"]} == {
        shared_name,
        restricted_name,
    }


async def test_restricted_workload_resources_appear_in_lists_after_role_grant_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Restricted Workload Pod {uuid4().hex[:8]}",
            "description": "Restricted agent function workflow grant pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    agent_name = f"grant_agent_{uuid4().hex[:8]}"
    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Stay hidden until a role resource grant is present.",
            "visibility": "RESTRICTED",
        },
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    agent = agent_response.json()

    function_name = f"grant_function_{uuid4().hex[:8]}"
    function_response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": function_name,
            "description": "Restricted function grant list test",
            "visibility": "RESTRICTED",
        },
    )
    assert function_response.status_code == status.HTTP_201_CREATED, (
        function_response.text
    )
    function = function_response.json()

    workflow_name = f"grant_workflow_{uuid4().hex[:8]}"
    workflow_response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": workflow_name,
            "visibility": "RESTRICTED",
        },
    )
    assert workflow_response.status_code == status.HTTP_201_CREATED, (
        workflow_response.text
    )
    workflow = workflow_response.json()

    role_response = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "workload_readers",
            "permission_ids": [
                "pod.read",
                "agent.read",
                "function.read",
                "workflow.read",
            ],
        },
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text

    viewer = await signup_user(async_client, "restricted-workload-viewer")
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
        roles=["POD_VIEWER", "WORKLOAD_READERS"],
    )
    viewer_headers = auth_headers(viewer)

    agent_list_before = await async_client.get(
        f"/pods/{pod_id}/agents",
        headers=viewer_headers,
    )
    assert agent_list_before.status_code == status.HTTP_200_OK, (
        agent_list_before.text
    )
    assert agent_name not in {item["name"] for item in agent_list_before.json()["items"]}

    function_list_before = await async_client.get(
        f"/pods/{pod_id}/functions",
        headers=viewer_headers,
    )
    assert function_list_before.status_code == status.HTTP_200_OK, (
        function_list_before.text
    )
    assert function_name not in {
        item["name"] for item in function_list_before.json()["items"]
    }

    workflow_list_before = await async_client.get(
        f"/pods/{pod_id}/workflows",
        headers=viewer_headers,
    )
    assert workflow_list_before.status_code == status.HTTP_200_OK, (
        workflow_list_before.text
    )
    assert workflow_name not in {
        item["name"] for item in workflow_list_before.json()["items"]
    }

    agent_get_before = await async_client.get(
        f"/pods/{pod_id}/agents/{agent_name}",
        headers=viewer_headers,
    )
    assert agent_get_before.status_code == status.HTTP_403_FORBIDDEN, (
        agent_get_before.text
    )
    function_get_before = await async_client.get(
        f"/pods/{pod_id}/functions/{function_name}",
        headers=viewer_headers,
    )
    assert function_get_before.status_code == status.HTTP_403_FORBIDDEN, (
        function_get_before.text
    )
    workflow_get_before = await async_client.get(
        f"/pods/{pod_id}/workflows/{workflow_name}",
        headers=viewer_headers,
    )
    assert workflow_get_before.status_code == status.HTTP_403_FORBIDDEN, (
        workflow_get_before.text
    )

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/WORKLOAD_READERS/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent["name"],
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "function",
                    "resource_name": function["name"],
                    "permission_ids": ["function.read"],
                },
                {
                    "resource_type": "workflow",
                    "resource_name": workflow["name"],
                    "permission_ids": ["workflow.read"],
                },
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    agent_get_after = await async_client.get(
        f"/pods/{pod_id}/agents/{agent_name}",
        headers=viewer_headers,
    )
    assert agent_get_after.status_code == status.HTTP_200_OK, agent_get_after.text
    agent_list_after = await async_client.get(
        f"/pods/{pod_id}/agents",
        headers=viewer_headers,
    )
    assert agent_list_after.status_code == status.HTTP_200_OK, agent_list_after.text
    assert agent_name in {item["name"] for item in agent_list_after.json()["items"]}

    function_get_after = await async_client.get(
        f"/pods/{pod_id}/functions/{function_name}",
        headers=viewer_headers,
    )
    assert function_get_after.status_code == status.HTTP_200_OK, function_get_after.text
    function_list_after = await async_client.get(
        f"/pods/{pod_id}/functions",
        headers=viewer_headers,
    )
    assert function_list_after.status_code == status.HTTP_200_OK, (
        function_list_after.text
    )
    assert function_name in {
        item["name"] for item in function_list_after.json()["items"]
    }

    workflow_get_after = await async_client.get(
        f"/pods/{pod_id}/workflows/{workflow_name}",
        headers=viewer_headers,
    )
    assert workflow_get_after.status_code == status.HTTP_200_OK, workflow_get_after.text
    workflow_list_after = await async_client.get(
        f"/pods/{pod_id}/workflows",
        headers=viewer_headers,
    )
    assert workflow_list_after.status_code == status.HTTP_200_OK, (
        workflow_list_after.text
    )
    assert workflow_name in {
        item["name"] for item in workflow_list_after.json()["items"]
    }


async def test_workload_create_permissions_do_not_imply_update_or_delete_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Workload Create Only Pod {uuid4().hex[:8]}",
            "description": "Create permission separation pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    pod_id = pod_response.json()["id"]

    role_response = await authenticated_client.post(
        f"/pods/{pod_id}/roles",
        json={
            "name": "workload_creators",
            "permission_ids": [
                "pod.read",
                "agent.create",
                "function.create",
                "workflow.create",
            ],
        },
    )
    assert role_response.status_code == status.HTTP_201_CREATED, role_response.text

    creator = await signup_user(async_client, "workload-create-only")
    creator_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=creator,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=creator_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER", "WORKLOAD_CREATORS"],
    )
    creator_headers = auth_headers(creator)

    created_agent = await async_client.post(
        f"/pods/{pod_id}/agents",
        headers=creator_headers,
        json={
            "name": f"creator_agent_{uuid4().hex[:8]}",
            "instruction": "Create-only role can create agents.",
        },
    )
    assert created_agent.status_code == status.HTTP_201_CREATED, created_agent.text
    created_function = await async_client.post(
        f"/pods/{pod_id}/functions",
        headers=creator_headers,
        json={
            "name": f"creator_function_{uuid4().hex[:8]}",
            "description": "Create-only role can create functions.",
        },
    )
    assert created_function.status_code == status.HTTP_201_CREATED, created_function.text
    created_workflow = await async_client.post(
        f"/pods/{pod_id}/workflows",
        headers=creator_headers,
        json={"name": f"creator_workflow_{uuid4().hex[:8]}"},
    )
    assert created_workflow.status_code == status.HTTP_201_CREATED, (
        created_workflow.text
    )

    owner_agent_name = f"owner_agent_{uuid4().hex[:8]}"
    owner_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": owner_agent_name,
            "instruction": "Create-only role cannot update or delete this agent.",
        },
    )
    assert owner_agent.status_code == status.HTTP_201_CREATED, owner_agent.text
    owner_function_name = f"owner_function_{uuid4().hex[:8]}"
    owner_function = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": owner_function_name,
            "description": "Create-only role cannot update or delete this function.",
        },
    )
    assert owner_function.status_code == status.HTTP_201_CREATED, owner_function.text
    owner_workflow_name = f"owner_workflow_{uuid4().hex[:8]}"
    owner_workflow = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": owner_workflow_name},
    )
    assert owner_workflow.status_code == status.HTTP_201_CREATED, owner_workflow.text

    denied_agent_update = await async_client.patch(
        f"/pods/{pod_id}/agents/{owner_agent_name}",
        headers=creator_headers,
        json={"description": "denied"},
    )
    assert denied_agent_update.status_code == status.HTTP_403_FORBIDDEN, (
        denied_agent_update.text
    )
    denied_function_update = await async_client.patch(
        f"/pods/{pod_id}/functions/{owner_function_name}",
        headers=creator_headers,
        json={"description": "denied"},
    )
    assert denied_function_update.status_code == status.HTTP_403_FORBIDDEN, (
        denied_function_update.text
    )
    denied_workflow_update = await async_client.patch(
        f"/pods/{pod_id}/workflows/{owner_workflow_name}",
        headers=creator_headers,
        json={"description": "denied"},
    )
    assert denied_workflow_update.status_code == status.HTTP_403_FORBIDDEN, (
        denied_workflow_update.text
    )

    denied_agent_delete = await async_client.delete(
        f"/pods/{pod_id}/agents/{owner_agent_name}",
        headers=creator_headers,
    )
    assert denied_agent_delete.status_code == status.HTTP_403_FORBIDDEN, (
        denied_agent_delete.text
    )
    denied_function_delete = await async_client.delete(
        f"/pods/{pod_id}/functions/{owner_function_name}",
        headers=creator_headers,
    )
    assert denied_function_delete.status_code == status.HTTP_403_FORBIDDEN, (
        denied_function_delete.text
    )
    denied_workflow_delete = await async_client.delete(
        f"/pods/{pod_id}/workflows/{owner_workflow_name}",
        headers=creator_headers,
    )
    assert denied_workflow_delete.status_code == status.HTTP_403_FORBIDDEN, (
        denied_workflow_delete.text
    )


async def _create_pod(authenticated_client, fixed_test_org, prefix: str) -> str:
    pod_response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"{prefix} {uuid4().hex[:8]}",
            "description": f"{prefix} e2e pod",
        },
    )
    assert pod_response.status_code == status.HTTP_201_CREATED, pod_response.text
    return pod_response.json()["id"]


async def test_grant_unknown_resource_names_report_every_missing_name_e2e(
    authenticated_client,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Unknown Names Pod")

    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "names_agent", "instruction": "Use granted resources."},
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text

    grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/names_agent/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": "missing_function",
                    "permission_ids": ["function.execute"],
                },
                {
                    "resource_type": "datastore_table",
                    "resource_name": "missing_table",
                    "permission_ids": ["datastore.table.read"],
                },
            ]
        },
    )
    assert grant_response.status_code == status.HTTP_400_BAD_REQUEST
    assert "function:missing_function" in grant_response.text
    assert "datastore_table:missing_table" in grant_response.text

    # UUID strings are not names; they must be rejected the same way.
    uuid_grant = await authenticated_client.put(
        f"/pods/{pod_id}/agents/names_agent/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": str(uuid4()),
                    "permission_ids": ["function.execute"],
                }
            ]
        },
    )
    assert uuid_grant.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unknown resource name(s)" in uuid_grant.text


async def test_grant_permission_resource_type_mismatch_is_rejected_e2e(
    authenticated_client,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Mismatch Pod")

    for name in ("mismatch_agent", "mismatch_target"):
        agent_response = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={"name": name, "instruction": "Use granted resources."},
        )
        assert agent_response.status_code == status.HTTP_201_CREATED, (
            agent_response.text
        )

    grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/mismatch_agent/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": "mismatch_target",
                    "permission_ids": ["app.read"],
                }
            ]
        },
    )
    assert grant_response.status_code == status.HTTP_400_BAD_REQUEST
    assert "do not apply to the resource type" in grant_response.text
    assert "agent:app.read" in grant_response.text


async def test_agent_delete_removes_grants_in_both_directions_e2e(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    from sqlalchemy import select as sa_select

    from app.core.authorization.models import ResourcePermissionGrantModel

    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Cleanup Pod")

    for name in ("cleanup_agent", "cleanup_target"):
        agent_response = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={"name": name, "instruction": "Use granted resources."},
        )
        assert agent_response.status_code == status.HTTP_201_CREATED, (
            agent_response.text
        )

    # cleanup_agent is the grantee of one grant (on cleanup_target)...
    grantee_side = await authenticated_client.put(
        f"/pods/{pod_id}/agents/cleanup_agent/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": "cleanup_target",
                    "permission_ids": ["agent.execute"],
                }
            ]
        },
    )
    assert grantee_side.status_code == status.HTTP_200_OK, grantee_side.text

    # ...and the resource of another (cleanup_target granted execute on it).
    resource_side = await authenticated_client.put(
        f"/pods/{pod_id}/agents/cleanup_target/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": "cleanup_agent",
                    "permission_ids": ["agent.execute"],
                }
            ]
        },
    )
    assert resource_side.status_code == status.HTTP_200_OK, resource_side.text

    agent_get = await authenticated_client.get(f"/pods/{pod_id}/agents/cleanup_agent")
    assert agent_get.status_code == status.HTTP_200_OK, agent_get.text
    agent_id = agent_get.json()["id"]

    deleted = await authenticated_client.delete(f"/pods/{pod_id}/agents/cleanup_agent")
    assert deleted.status_code == status.HTTP_200_OK, deleted.text

    from uuid import UUID as _UUID

    rows = (
        await db_session.execute(
            sa_select(ResourcePermissionGrantModel).where(
                ResourcePermissionGrantModel.pod_id == _UUID(pod_id),
            )
        )
    ).scalars().all()
    leftover = [
        row
        for row in rows
        if str(row.resource_id) == agent_id or str(row.grantee_id) == agent_id
    ]
    assert leftover == []


async def test_agent_update_preserves_workload_capability_grants_e2e(
    authenticated_client,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Preserve Pod")

    for name in ("caller_agent", "callee_agent"):
        agent_response = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={"name": name, "instruction": "Use granted resources."},
        )
        assert agent_response.status_code == status.HTTP_201_CREATED, (
            agent_response.text
        )

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/agents/caller_agent/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": "callee_agent",
                    "permission_ids": ["agent.execute"],
                }
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    # A plain update of the POD-visible callee must not wipe the caller's
    # capability grant on it (regression test for the visibility-transition
    # grant deletion bug).
    update = await authenticated_client.patch(
        f"/pods/{pod_id}/agents/callee_agent",
        json={"description": "Updated description"},
    )
    assert update.status_code == status.HTTP_200_OK, update.text

    permissions = await authenticated_client.get(
        f"/pods/{pod_id}/agents/caller_agent/permissions"
    )
    assert permissions.status_code == status.HTTP_200_OK, permissions.text
    assert permissions.json()["grants"] == [
        {
            "resource_type": "agent",
            "resource_name": "callee_agent",
            "permission_ids": ["agent.execute"],
        }
    ]


async def test_resource_access_unknown_name_returns_404_e2e(
    authenticated_client,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Access 404 Pod")

    access = await authenticated_client.get(
        f"/pods/{pod_id}/resources/agent/missing_agent/access"
    )
    assert access.status_code == status.HTTP_404_NOT_FOUND, access.text
    assert "missing_agent" in access.text


async def test_pod_editor_can_rewire_the_resources_they_author_e2e(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    """A pod editor can replace agent and function grants.

    Replacing grants used to require the delete-tier permission, which
    POD_EDITOR does not hold — so the role that exists to build agents and
    functions could not rewire the ones it had just built, and the denial named
    `agent.delete` at someone who was only editing. Deleting is still gated.
    """
    pod_id = await _create_pod(authenticated_client, fixed_test_org, "Editor Rewire Pod")

    agent_response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "rewire_agent", "instruction": "Use granted resources."},
    )
    assert agent_response.status_code == status.HTTP_201_CREATED, agent_response.text
    function_response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": "rewire_function", "description": "Editor rewire target"},
    )
    assert function_response.status_code == status.HTTP_201_CREATED, (
        function_response.text
    )
    table_response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "rewire_table",
            "visibility": "RESTRICTED",
            "columns": [{"name": "title", "type": "TEXT"}],
        },
    )
    assert table_response.status_code == status.HTTP_201_CREATED, table_response.text
    table_name = table_response.json()["name"]

    editor = await signup_user(async_client, "rewire-editor")
    editor_org = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=editor
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=editor_org["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )
    editor_headers = auth_headers(editor)

    effective = await async_client.get(
        f"/pods/{pod_id}/permissions/me", headers=editor_headers
    )
    assert effective.status_code == status.HTTP_200_OK, effective.text
    actions = effective.json()["actions"]
    assert "agent.update" in actions
    assert "agent.delete" not in actions, (
        "the test is meaningless if the editor holds the delete permission"
    )

    grants = {
        "grants": [
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": ["datastore.table.read"],
            }
        ]
    }

    agent_grants = await async_client.put(
        f"/pods/{pod_id}/agents/rewire_agent/permissions",
        json=grants,
        headers=editor_headers,
    )
    assert agent_grants.status_code == status.HTTP_200_OK, agent_grants.text
    assert {grant["resource_name"] for grant in agent_grants.json()["grants"]} == {
        table_name
    }

    function_grants = await async_client.put(
        f"/pods/{pod_id}/functions/rewire_function/permissions",
        json=grants,
        headers=editor_headers,
    )
    assert function_grants.status_code == status.HTTP_200_OK, function_grants.text
    assert {grant["resource_name"] for grant in function_grants.json()["grants"]} == {
        table_name
    }

    # The delete gate did not move with it.
    deleted = await async_client.delete(
        f"/pods/{pod_id}/agents/rewire_agent", headers=editor_headers
    )
    assert deleted.status_code == status.HTTP_403_FORBIDDEN, deleted.text
