"""Holistic workload permissions: connectors & connected accounts.

Connectors are org-wide *capability* resources (always POD-visible); the real
access boundary is the connected *account* (user-owned). This suite drives the
``AccountResolutionService`` — the authorization gate every connector operation
goes through — across the modes that matter:

* **plain user** resolves their OWN account with no grant; resolving another
  user's account is rejected;
* **named workload** (agent) must hold ``connector.use`` to resolve any account,
  and ``connector_account.use`` to resolve ANOTHER user's account;
* **default pod agent** ("user-resolved") bypasses the capability grant and
  resolves the invoking user's own account directly.

The authorizer-decision view (connector stays POD-visible; workload needs
``connector.use``; human grants don't restrict) is covered by
``test_workload_connector_grant_e2e.py``; this complements it with the
account-resolution behaviour those tests don't reach.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.authorization.service import AuthorizationDataService
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)
from app.modules.datastore.tests.e2e.harness import signup_user
from app.modules.pod.tests.e2e.workload_permissions.harness import (
    AGENT,
    build_account_resolution_service,
    build_workload_ctx,
    create_agent,
    create_pod,
    replace_role_grants,
    replace_workload_grants,
    seed_account,
    seed_auth_config,
    seed_connector,
)
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID, DEFAULT_POD_AGENT_NAME

pytestmark = pytest.mark.e2e


def _connector_grant(connector_id: str) -> dict:
    return {
        "resource_type": "connector",
        "resource_name": connector_id,
        "permission_ids": ["connector.use"],
    }


def _account_grant(account_id: str) -> dict:
    return {
        "resource_type": "connector_account",
        "resource_name": account_id,
        "permission_ids": ["connector_account.use"],
    }


async def _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session):
    """Pod + active connector + auth config + the owner's connected account."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    connector_id = await seed_connector(db_session, f"conn_{uuid4().hex[:8]}")
    auth_config_id = await seed_auth_config(
        db_session,
        organization_id=fixed_test_org["id"],
        connector_id=connector_id,
        name=f"ac_{uuid4().hex[:8]}",
    )
    owner_account_id = await seed_account(
        db_session,
        user_id=fixed_test_user["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=auth_config_id,
        connector_id=connector_id,
    )
    return {
        "pod_id": pod_id,
        "connector_id": connector_id,
        "auth_config_id": auth_config_id,
        "owner_account_id": owner_account_id,
    }


async def _user_ctx(db_session, *, user_id: str, pod_id: str):
    return await AuthorizationDataService(db_session).build_user_context(
        user_id=UUID(user_id), pod_id=UUID(pod_id)
    )


# --------------------------------------------------------------------------- #
# Plain user (user-resolved, non-delegated)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_plain_user_resolves_own_account(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    svc = build_account_resolution_service(db_session)
    ctx = await _user_ctx(db_session, user_id=fixed_test_user["id"], pod_id=env["pod_id"])

    account = await svc.resolve_account(
        user_id=UUID(fixed_test_user["id"]),
        connector_id=env["connector_id"],
        auth_actor=ctx,
    )
    assert str(account.id) == env["owner_account_id"]


@pytest.mark.asyncio
async def test_plain_user_cannot_resolve_other_users_account(
    authenticated_client, async_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    other = await signup_user(async_client, "conn-other")
    other_account_id = await seed_account(
        db_session,
        user_id=other["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )
    svc = build_account_resolution_service(db_session)
    ctx = await _user_ctx(db_session, user_id=fixed_test_user["id"], pod_id=env["pod_id"])

    with pytest.raises(AccountResolutionError):
        await svc.resolve_account(
            user_id=UUID(fixed_test_user["id"]),
            connector_id=env["connector_id"],
            auth_actor=ctx,
            account_id=UUID(other_account_id),
        )


# --------------------------------------------------------------------------- #
# Named workload (agent) — capability + account gates
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_named_workload_without_connector_use_is_denied(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    # No connector.use grant.
    await replace_workload_grants(authenticated_client, env["pod_id"], AGENT, name, [])

    svc = build_account_resolution_service(db_session)
    ctx = await build_workload_ctx(
        db_session,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=agent["id"],
        pod_id=env["pod_id"],
        workload_name=name,
    )
    with pytest.raises(ConnectorAccessDeniedError):
        await svc.resolve_account(
            user_id=UUID(fixed_test_user["id"]),
            connector_id=env["connector_id"],
            auth_actor=ctx,
        )


@pytest.mark.asyncio
async def test_named_workload_with_connector_use_resolves_own_account(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    await replace_workload_grants(
        authenticated_client, env["pod_id"], AGENT, name, [_connector_grant(env["connector_id"])]
    )

    svc = build_account_resolution_service(db_session)
    ctx = await build_workload_ctx(
        db_session,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=agent["id"],
        pod_id=env["pod_id"],
        workload_name=name,
    )
    account = await svc.resolve_account(
        user_id=UUID(fixed_test_user["id"]),
        connector_id=env["connector_id"],
        auth_actor=ctx,
    )
    assert str(account.id) == env["owner_account_id"]


@pytest.mark.asyncio
async def test_named_workload_other_account_requires_workload_grant(
    authenticated_client, async_client, fixed_test_org, fixed_test_user, db_session
):
    """Grant-first: pinning ANOTHER user's (shared) account needs grants on the
    WORKLOAD alone — connector.use plus connector_account.use — and is then
    invoker-independent. The invoking user needs no matching grant. This is the
    shared-sender pattern (one team account pinned on a workload).
    """
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    other = await signup_user(async_client, "conn-other2")
    other_account_id = await seed_account(
        db_session,
        user_id=other["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )

    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    svc = build_account_resolution_service(db_session)

    async def _ctx():
        return await build_workload_ctx(
            db_session,
            user_id=fixed_test_user["id"],
            workload_type=AGENT,
            workload_id=agent["id"],
            pod_id=env["pod_id"],
            workload_name=name,
        )

    async def _resolve_other():
        return await svc.resolve_account(
            user_id=UUID(fixed_test_user["id"]),
            connector_id=env["connector_id"],
            auth_actor=await _ctx(),
            account_id=UUID(other_account_id),
        )

    # (1) connector.use only — the workload holds no account grant -> denied.
    await replace_workload_grants(
        authenticated_client, env["pod_id"], AGENT, name, [_connector_grant(env["connector_id"])]
    )
    with pytest.raises(ConnectorAccessDeniedError):
        await _resolve_other()

    # (2) Workload ALSO granted connector_account.use -> resolves, even though the
    #     invoking user holds no grant on the account. The workload's grants are
    #     standalone authority (grant-first).
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"]), _account_grant(other_account_id)],
    )
    account = await _resolve_other()
    assert str(account.id) == other_account_id


# --------------------------------------------------------------------------- #
# Default pod agent — user-resolved, bypasses capability grant
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_pod_agent_resolves_own_account_without_grant(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(authenticated_client, fixed_test_org, fixed_test_user, db_session)
    svc = build_account_resolution_service(db_session)
    ctx = await build_workload_ctx(
        db_session,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=str(DEFAULT_POD_AGENT_ID),
        pod_id=env["pod_id"],
        workload_name=DEFAULT_POD_AGENT_NAME,
        is_default_pod_agent=True,
    )
    account = await svc.resolve_account(
        user_id=UUID(fixed_test_user["id"]),
        connector_id=env["connector_id"],
        auth_actor=ctx,
    )
    assert str(account.id) == env["owner_account_id"]
