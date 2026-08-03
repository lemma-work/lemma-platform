"""Delegated-workload connector-connector grant authorization.

Connectors are org-wide capability resources, always available to
everyone. A grant on a connector (``connector.use``) is a
*capability* grant -- "this principal may use this app type at all" -- never a
*sharing* grant. So a grant on an app, held by whomever (a FUNCTION/AGENT
workload, a ROLE, or a POD_MEMBER), must never flip the app to RESTRICTED. The
real access boundary is the connected *account*, which is user-owned and
enforced separately in ``account_resolution_service`` (the invoking user's own
account is returned directly; someone else's requires
``connector_account.use``).

Regression for the runtime 403::

    CONNECTOR_ACCESS_DENIED / MISSING_RESOURCE_GRANT for connector.use

raised by ``account_resolution_service._resolve_workload_account``. A cron
workflow fanned a JOB function out per pod member, delegating each run to that
member. The function held the app capability grant, but a stray human
(ROLE/POD_MEMBER) grant on the app -- held only by the pod owner -- flipped the
app to RESTRICTED, which then forced the "both invoking user and workload must
be granted" rule and denied every other member's poll. These tests pin the app
POD-visible regardless of human grants, so the workload capability grant alone
gates app access.

These tests exercise the real DB-backed authorizer (visibility derivation +
grant matching) without needing a workspace sandbox.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.authorization.context import ResourceRef, ResourceVisibility
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.resource_names import connector_resource_id
from app.core.authorization.service import AuthorizationDataService
from app.modules.connectors.infrastructure.models.connector import Connector

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Workload App Grant Pod {uuid4().hex[:8]}",
            "description": "delegated workload connector app grant",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _seed_connector(db_session, connector_id: str) -> None:
    if await db_session.get(Connector, connector_id) is None:
        db_session.add(
            Connector(
                id=connector_id,
                title=f"{connector_id} title",
                description="App for delegated workload grant tests",
                kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
        await db_session.commit()


async def _create_function(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "delegated workload grant test"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _replace_function_grants(
    authenticated_client, pod_id: str, function_name: str, grants: list[dict]
) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{function_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


async def _replace_role_grants(
    authenticated_client, pod_id: str, role_name: str, grants: list[dict]
) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{role_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _app_grant(connector_id: str) -> dict:
    return {
        "resource_type": "connector",
        "resource_name": connector_id,
        "permission_ids": ["connector.use"],
    }


async def _build_function_workload_context(
    db_session, *, user_id: UUID, function_id: UUID, pod_id: UUID, function_name: str
):
    return await AuthorizationDataService(db_session).build_delegated_workload_context(
        user_id=user_id,
        principal_type="FUNCTION",
        principal_id=function_id,
        pod_id=pod_id,
        delegation_scope=frozenset([Permissions.CONNECTOR_USE]),
        delegation_actor_name=function_name,
    )


async def _setup_pod_app_function(
    authenticated_client, fixed_test_org, db_session
) -> tuple[str, str, dict]:
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    connector_id = f"grant_app_{uuid4().hex[:8]}"
    await _seed_connector(db_session, connector_id)
    function = await _create_function(
        authenticated_client, pod_id, f"grant_func_{uuid4().hex[:8]}"
    )
    return pod_id, connector_id, function


@pytest.mark.asyncio
async def test_workload_app_grant_allows_delegated_workload(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """The function's own capability grant lets the delegated workload through."""
    pod_id, connector_id, function = await _setup_pod_app_function(
        authenticated_client, fixed_test_org, db_session
    )
    await _replace_function_grants(
        authenticated_client, pod_id, function["name"], [_app_grant(connector_id)]
    )

    pod_uuid = UUID(pod_id)
    app_ref = ResourceRef.connector(
        pod_id=pod_uuid,
        pod_connector_id=connector_resource_id(connector_id),
    )
    ctx = await _build_function_workload_context(
        db_session,
        user_id=UUID(fixed_test_user["id"]),
        function_id=UUID(function["id"]),
        pod_id=pod_uuid,
        function_name=function["name"],
    )

    hydrated = await ctx.authorizer._hydrate_connector(app_ref)
    assert hydrated.visibility == ResourceVisibility.POD

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.CONNECTOR_USE, app_ref
    )
    assert decision.allowed, decision.reason_code
    assert decision.reason_code == "POD_VISIBLE"


@pytest.mark.asyncio
async def test_human_app_grant_does_not_restrict_app(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """A human ROLE grant on an app does not flip it to RESTRICTED.

    Apps are org-wide; the role grant is just another capability grant. With the
    function's own grant present the delegated workload is allowed exactly as if
    the role grant were absent.
    """
    pod_id, connector_id, function = await _setup_pod_app_function(
        authenticated_client, fixed_test_org, db_session
    )
    await _replace_function_grants(
        authenticated_client, pod_id, function["name"], [_app_grant(connector_id)]
    )
    await _replace_role_grants(
        authenticated_client, pod_id, "POD_ADMIN", [_app_grant(connector_id)]
    )

    pod_uuid = UUID(pod_id)
    app_ref = ResourceRef.connector(
        pod_id=pod_uuid,
        pod_connector_id=connector_resource_id(connector_id),
    )
    ctx = await _build_function_workload_context(
        db_session,
        user_id=UUID(fixed_test_user["id"]),
        function_id=UUID(function["id"]),
        pod_id=pod_uuid,
        function_name=function["name"],
    )

    # The human role grant no longer restricts the app ...
    hydrated = await ctx.authorizer._hydrate_connector(app_ref)
    assert hydrated.visibility == ResourceVisibility.POD
    # ... and the workload's own capability grant carries the run.
    decision = await ctx.authorizer.authorize(
        ctx, Permissions.CONNECTOR_USE, app_ref
    )
    assert decision.allowed, decision.reason_code
    assert decision.reason_code == "POD_VISIBLE"


@pytest.mark.asyncio
async def test_human_app_grant_without_workload_grant_denies(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """A human grant alone never confers the workload's app capability.

    The app stays POD-visible (org-wide), but a delegated workload still needs
    its own ``connector.use`` grant -- absent it, the run is denied
    with MISSING_WORKLOAD_RESOURCE_GRANT, not silently allowed via the human
    grant.
    """
    pod_id, connector_id, function = await _setup_pod_app_function(
        authenticated_client, fixed_test_org, db_session
    )
    # Human grant present; the function gets NO grant of its own.
    await _replace_role_grants(
        authenticated_client, pod_id, "POD_ADMIN", [_app_grant(connector_id)]
    )

    pod_uuid = UUID(pod_id)
    app_ref = ResourceRef.connector(
        pod_id=pod_uuid,
        pod_connector_id=connector_resource_id(connector_id),
    )
    ctx = await _build_function_workload_context(
        db_session,
        user_id=UUID(fixed_test_user["id"]),
        function_id=UUID(function["id"]),
        pod_id=pod_uuid,
        function_name=function["name"],
    )

    hydrated = await ctx.authorizer._hydrate_connector(app_ref)
    assert hydrated.visibility == ResourceVisibility.POD

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.CONNECTOR_USE, app_ref
    )
    assert not decision.allowed
    assert decision.reason_code == "MISSING_WORKLOAD_RESOURCE_GRANT"


@pytest.mark.asyncio
async def test_other_member_app_grant_does_not_block_delegated_workload(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """Direct regression for the per-member cron-poll 403.

    A second member's POD_MEMBER grant on the app must not block a delegated
    workload run for a *different* member who holds no personal app grant. The
    function holds the capability; that is all the app layer requires.
    """
    pod_id, connector_id, function = await _setup_pod_app_function(
        authenticated_client, fixed_test_org, db_session
    )
    await _replace_function_grants(
        authenticated_client, pod_id, function["name"], [_app_grant(connector_id)]
    )

    pod_uuid = UUID(pod_id)
    app_resource_id = connector_resource_id(connector_id)
    # Simulate another member having been granted the app: a POD_MEMBER grant
    # for a grantee the invoking user is not. Before the fix this flipped the
    # app to RESTRICTED and denied the invoking user with MISSING_RESOURCE_GRANT.
    db_session.add(
        ResourcePermissionGrantModel(
            pod_id=pod_uuid,
            resource_type="connector",
            resource_id=app_resource_id,
            grantee_type="POD_MEMBER",
            grantee_id=uuid4(),
            permission_id=Permissions.CONNECTOR_USE,
            created_by_user_id=None,
        )
    )
    await db_session.commit()

    app_ref = ResourceRef.connector(
        pod_id=pod_uuid,
        pod_connector_id=app_resource_id,
    )
    ctx = await _build_function_workload_context(
        db_session,
        user_id=UUID(fixed_test_user["id"]),
        function_id=UUID(function["id"]),
        pod_id=pod_uuid,
        function_name=function["name"],
    )

    hydrated = await ctx.authorizer._hydrate_connector(app_ref)
    assert hydrated.visibility == ResourceVisibility.POD

    decision = await ctx.authorizer.authorize(
        ctx, Permissions.CONNECTOR_USE, app_ref
    )
    assert decision.allowed, decision.reason_code
    assert decision.reason_code == "POD_VISIBLE"
