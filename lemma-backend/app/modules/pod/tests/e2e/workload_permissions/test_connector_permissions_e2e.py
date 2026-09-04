"""Holistic workload permissions: connectors & connected accounts.

Connectors are org-wide *capability* resources (always POD-visible); the real
access boundary is the connected *account* (user-owned). This suite drives the
``AccountResolutionService`` — the authorization gate every connector operation
goes through — across the modes that matter:

* **plain user** resolves their OWN account with no grant; resolving another
  user's account is rejected;
* **named workload** (agent) must hold ``connector.use`` to resolve any account,
  and ``connector_account.use`` to resolve ANOTHER user's account — and the
  person invoking it must be able to use that account themselves, since a
  workload's authority is its grants intersected with theirs;
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
    replace_workload_grants,
    seed_account,
    seed_auth_config,
    seed_connector,
)
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.test_support.e2e_authz import add_pod_member, invite_org_member

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
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    svc = build_account_resolution_service(db_session)
    ctx = await _user_ctx(
        db_session, user_id=fixed_test_user["id"], pod_id=env["pod_id"]
    )

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
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    other = await signup_user(async_client, "conn-other")
    other_account_id = await seed_account(
        db_session,
        user_id=other["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )
    svc = build_account_resolution_service(db_session)
    ctx = await _user_ctx(
        db_session, user_id=fixed_test_user["id"], pod_id=env["pod_id"]
    )

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
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
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
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"])],
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
    """Pinning ANOTHER user's (shared) account needs connector.use plus
    connector_account.use on the WORKLOAD — the shared-sender pattern, one team
    account pinned on a workload.

    Both grants are necessary and, for this invoker, sufficient: the pod owner
    driving the workload can reach the account themselves, so the invoker half
    of the intersection is satisfied and only the grants are in question. Whose
    ceiling this is gets pinned by the POD_VIEWER case below — without it this
    test would keep passing while reading like proof that grants alone decide.
    """
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
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
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"])],
    )
    with pytest.raises(ConnectorAccessDeniedError):
        await _resolve_other()

    # (2) Workload ALSO granted connector_account.use -> resolves.
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"]), _account_grant(other_account_id)],
    )
    account = await _resolve_other()
    assert str(account.id) == other_account_id


@pytest.mark.parametrize("role", ["POD_USER", "POD_EDITOR", "POD_ADMIN"])
@pytest.mark.asyncio
async def test_a_shared_account_pinned_on_an_agent_serves_the_whole_pod(
    role,
    authenticated_client,
    async_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """The shared-mailbox pattern: one team account, every member's agent run.

    A pod is a trust boundary, so an agent visible inside it should be able to
    use the account it was configured with, whoever set the run going. This
    broke in a way that made the feature impossible to configure rather than
    merely restricted: granting the agent the account is what marked the
    account RESTRICTED, and the invoker check then found no *human* grant on it
    and refused everyone — including a pod admin.

    Parameterised over the roles that hold `connector_account.use`, because the
    bug was insensitive to role and a single-role test would not have shown
    that.
    """
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    mailbox_owner = await signup_user(async_client, f"shared-owner-{role.lower()}")
    shared_account_id = await seed_account(
        db_session,
        user_id=mailbox_owner["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )

    member = await signup_user(async_client, f"shared-member-{role.lower()}")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=member,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=env["pod_id"],
        organization_member_id=org_member["id"],
        role=role,
        roles=[role],
    )

    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"]), _account_grant(shared_account_id)],
    )

    account = await build_account_resolution_service(db_session).resolve_account(
        user_id=UUID(member["id"]),
        connector_id=env["connector_id"],
        auth_actor=await build_workload_ctx(
            db_session,
            user_id=member["id"],
            workload_type=AGENT,
            workload_id=agent["id"],
            pod_id=env["pod_id"],
            workload_name=name,
        ),
        account_id=UUID(shared_account_id),
    )

    assert str(account.id) == shared_account_id


@pytest.mark.asyncio
async def test_a_person_still_cannot_reach_the_shared_account_directly(
    authenticated_client, async_client, fixed_test_org, fixed_test_user, db_session
):
    """The other half of the same change, and the reason it is safe.

    Making the account visible to the pod again does not hand it to people: a
    plain, non-delegated caller is refused another person's account by account
    resolution itself, whatever the visibility says. Using it *through* the
    agent it was pinned on is the only way in.
    """
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    mailbox_owner = await signup_user(async_client, "shared-owner-direct")
    shared_account_id = await seed_account(
        db_session,
        user_id=mailbox_owner["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )
    member = await signup_user(async_client, "shared-member-direct")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=member,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=env["pod_id"],
        organization_member_id=org_member["id"],
        role="POD_USER",
        roles=["POD_USER"],
    )

    name = f"conn_agent_{uuid4().hex[:8]}"
    await create_agent(authenticated_client, env["pod_id"], name)
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"]), _account_grant(shared_account_id)],
    )

    with pytest.raises(AccountResolutionError):
        await build_account_resolution_service(db_session).resolve_account(
            user_id=UUID(member["id"]),
            connector_id=env["connector_id"],
            account_id=UUID(shared_account_id),
        )


@pytest.mark.asyncio
async def test_shared_account_is_refused_to_an_invoker_who_cannot_use_it(
    authenticated_client, async_client, fixed_test_org, fixed_test_user, db_session
):
    """The same workload, the same grants, a different person driving it.

    A shared account pinned on a workload is a credential, and letting anyone
    who may run the workload send through it would make the workload a way to
    borrow a colleague's identity. So the grants above are a ceiling on the
    workload, not a promotion for its invoker: a POD_VIEWER who cannot reach
    the account gets DELEGATION_EXCEEDS_INVOKER, and the person who wants to
    send through it needs their own access to it.
    """
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    owner_of_account = await signup_user(async_client, "conn-shared-owner")
    shared_account_id = await seed_account(
        db_session,
        user_id=owner_of_account["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )

    viewer = await signup_user(async_client, "conn-shared-viewer")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=env["pod_id"],
        organization_member_id=org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER"],
    )

    name = f"conn_agent_{uuid4().hex[:8]}"
    agent = await create_agent(authenticated_client, env["pod_id"], name)
    await replace_workload_grants(
        authenticated_client,
        env["pod_id"],
        AGENT,
        name,
        [_connector_grant(env["connector_id"]), _account_grant(shared_account_id)],
    )
    svc = build_account_resolution_service(db_session)

    with pytest.raises(ConnectorAccessDeniedError) as refusal:
        await svc.resolve_account(
            user_id=UUID(viewer["id"]),
            connector_id=env["connector_id"],
            auth_actor=await build_workload_ctx(
                db_session,
                user_id=viewer["id"],
                workload_type=AGENT,
                workload_id=agent["id"],
                pod_id=env["pod_id"],
                workload_name=name,
            ),
            account_id=UUID(shared_account_id),
        )

    # The remedy is to raise the person's access, not the workload's grants,
    # so the refusal has to be able to say so.
    assert refusal.value.details["reason_code"] == "DELEGATION_EXCEEDS_INVOKER", (
        f"refused for the wrong reason: {refusal.value.details}"
    )


# --------------------------------------------------------------------------- #
# Default pod agent — user-resolved, bypasses capability grant
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_pod_agent_resolves_own_account_without_grant(
    authenticated_client, fixed_test_org, fixed_test_user, db_session
):
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    svc = build_account_resolution_service(db_session)
    ctx = await build_workload_ctx(
        db_session,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=env["pod_id"],
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


@pytest.mark.asyncio
async def test_default_pod_agent_resolves_own_account_without_connector_use(
    authenticated_client, async_client, fixed_test_org, fixed_test_user, db_session
):
    """The shortcut, on the only member for whom it changes the answer.

    For the pod owner the shortcut is invisible: they hold ``connector.use``,
    so the branch it skips would have said yes anyway. A POD_VIEWER does not
    hold it. They still own their account, and the assistant acting for them
    still has to reach it -- that is what "acts as the invoking user" means,
    and it is what this test would lose if the shortcut stopped firing.
    """
    env = await _setup(
        authenticated_client, fixed_test_org, fixed_test_user, db_session
    )
    viewer = await signup_user(async_client, "conn-viewer")
    viewer_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=viewer,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=env["pod_id"],
        organization_member_id=viewer_org_member["id"],
        role="POD_VIEWER",
        roles=["POD_VIEWER"],
    )
    viewer_account_id = await seed_account(
        db_session,
        user_id=viewer["id"],
        organization_id=fixed_test_org["id"],
        auth_config_id=env["auth_config_id"],
        connector_id=env["connector_id"],
    )
    svc = build_account_resolution_service(db_session)

    assistant_ctx = await build_workload_ctx(
        db_session,
        user_id=viewer["id"],
        workload_type=AGENT,
        workload_id=env["pod_id"],
        pod_id=env["pod_id"],
        workload_name=DEFAULT_POD_AGENT_NAME,
        is_default_pod_agent=True,
    )
    account = await svc.resolve_account(
        user_id=UUID(viewer["id"]),
        connector_id=env["connector_id"],
        auth_actor=assistant_ctx,
    )
    assert str(account.id) == viewer_account_id

    # And the shortcut is the assistant's alone: a named agent acting for the
    # same viewer, holding no grants, is still refused.
    named = await create_agent(
        authenticated_client, env["pod_id"], f"named_{uuid4().hex[:6]}"
    )
    named_ctx = await build_workload_ctx(
        db_session,
        user_id=viewer["id"],
        workload_type=AGENT,
        workload_id=named["id"],
        pod_id=env["pod_id"],
        workload_name=named["name"],
    )
    with pytest.raises(ConnectorAccessDeniedError):
        await svc.resolve_account(
            user_id=UUID(viewer["id"]),
            connector_id=env["connector_id"],
            auth_actor=named_ctx,
        )
