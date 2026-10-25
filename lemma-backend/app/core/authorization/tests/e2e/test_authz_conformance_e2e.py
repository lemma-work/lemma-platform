"""Authorization conformance: who may call what, checked against the real API.

The spine of the authorization revamp. Each step adds the rows for what it
closes; a row is tagged with the audit finding it proves, so ``-k C2`` runs that
finding's proof.

This first part is the route policy (``app/core/api/actor_policy.py``): a
workload's token -- the pod's assistant or a named agent, acting for a person --
reaches only pod routes and the few routes outside a pod opened to it. Routes
are enumerated from the running app rather than listed, so a route added later
is covered the day it lands.
"""

from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID, uuid4, uuid7

import pytest
from httpx import AsyncClient
from starlette import status
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.api.actor_policy import WORKLOAD_NOT_ALLOWED, workload_may_call
from app.core.auth_exemptions import exemption_of
from app.core.live.protocol import CLOSE_FORBIDDEN
from app.modules.test_support.e2e.live_socket import ws_url
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

backend_server = _backend_server

_PARAM = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _routes(app) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for included in app.routes:
        candidates = getattr(included, "effective_candidates", None)
        for candidate in candidates() if callable(candidates) else [included]:
            route = getattr(candidate, "route", candidate)
            for method in getattr(route, "methods", None) or ():
                if method not in {"HEAD", "OPTIONS"}:
                    found.add((method, route.path))
    return sorted(found)


def _concrete(template: str, values: dict[str, str]) -> str:
    return _PARAM.sub(lambda m: values.get(m.group(1), str(uuid4())), template)


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Conformance {uuid4().hex[:8]}",
            "description": "authorization conformance e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _workload_headers(
    *, user_id: str, pod_id: str, default_agent: bool
) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID if default_agent else uuid7(),
        workload_name=DEFAULT_POD_AGENT_NAME if default_agent else "helper",
        pod_id=UUID(pod_id),
        session_id=f"conformance-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def world(authenticated_client: AsyncClient, fixed_test_org, fixed_test_user):
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id)
    other_pod_id = await _pod(authenticated_client, org_id)
    workloads = {
        "assistant": await _workload_headers(
            user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=True
        ),
        "agent": await _workload_headers(
            user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=False
        ),
    }
    return {
        "org_id": org_id,
        "pod_id": pod_id,
        "other_pod_id": other_pod_id,
        "workloads": workloads,
    }


def _closed_routes(app, values: dict[str, str]) -> list[tuple[str, str, str]]:
    closed = []
    for method, template in _routes(app):
        if workload_may_call(method, template):
            continue
        path = _concrete(template, values)
        if exemption_of(path, method) is not None:
            continue
        closed.append((method, template, path))
    return closed


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_C2_H1_M2_every_route_a_workload_is_not_opened_to_refuses_it(
    async_client: AsyncClient, test_app, world, workload: str
):
    """C2, C3, H1, H8 (HTTP half), M2, M24: a workload's token was the person on
    every route that built no context; three of them minted sessions."""
    headers = world["workloads"][workload]
    values = {"organization_id": world["org_id"], "pod_id": world["pod_id"]}
    closed = _closed_routes(test_app, values)
    assert len(closed) > 50, "the route enumeration found too few routes"

    wrong = []
    for method, template, path in closed:
        response = await async_client.request(
            method, path, headers=headers, json={} if method != "GET" else None
        )
        code = (
            response.json().get("code")
            if response.headers.get("content-type", "").startswith("application/json")
            and isinstance(response.json(), dict)
            else None
        )
        if response.status_code != status.HTTP_403_FORBIDDEN or (
            code != WORKLOAD_NOT_ALLOWED
        ):
            wrong.append(f"{method} {template} -> {response.status_code} {code}")
    assert not wrong, "\n".join(wrong)


async def test_a_person_is_never_refused_as_a_workload(
    authenticated_client: AsyncClient, test_app, world
):
    """The policy keys on the token's claims, never on the route alone."""
    # Reads only, and an organization that does not exist: nothing is changed.
    values = {"organization_id": str(uuid4()), "pod_id": world["pod_id"]}
    wrong = []
    for method, template, path in _closed_routes(test_app, values):
        if method != "GET":
            continue
        try:
            response = await authenticated_client.get(path)
        except RuntimeError:
            # A route whose dependencies this environment lacks (a sandbox
            # provider) fails past the policy -- which is all this asks.
            continue
        if WORKLOAD_NOT_ALLOWED in response.text:
            wrong.append(f"{method} {template}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_a_workload_still_reaches_what_it_is_opened_to(
    async_client: AsyncClient, world, workload: str
):
    headers = world["workloads"][workload]

    me = await async_client.get("/users/me", headers=headers)
    own_pod = await async_client.get(f"/pods/{world['pod_id']}/agents", headers=headers)

    assert me.status_code == status.HTTP_200_OK, me.text
    # Past the policy, the pod's own authorization decides: the assistant reads
    # what its person reads, and a named agent with no grants is refused for
    # want of one -- not for being a workload.
    if workload == "assistant":
        assert own_pod.status_code == status.HTTP_200_OK, own_pod.text
    else:
        assert own_pod.json().get("code") == "MISSING_WORKLOAD_RESOURCE_GRANT", (
            own_pod.text
        )


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_a_workload_is_pinned_to_its_own_pod(
    async_client: AsyncClient, world, workload: str
):
    response = await async_client.get(
        f"/pods/{world['other_pod_id']}/agents", headers=world["workloads"][workload]
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


@pytest.mark.parametrize("workload", ["assistant", "agent"])
@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("POST", "/pods/{pod_id}/join-requests/{join_request_id}/approve"),
        ("PUT", "/pods/{pod_id}/functions/{function_name}/contacts"),
    ],
)
async def test_pod_routes_that_need_a_person_refuse_a_workload(
    async_client: AsyncClient, world, workload: str, method: str, template: str
):
    path = _concrete(template, {"pod_id": world["pod_id"], "function_name": "fn"})

    response = await async_client.request(
        method, path, headers=world["workloads"][workload], json={}
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == WORKLOAD_NOT_ALLOWED


async def _socket_outcome(server, pod_id: str, headers: dict[str, str]) -> object:
    """``"ready"`` if the pod's live socket opens, else the code it closed with."""
    async with connect(
        ws_url(server, f"/pods/{pod_id}/live"), additional_headers=headers
    ) as ws:
        try:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 15))
        except ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd else None
        return frame.get("type")


async def test_H8_the_assistant_keeps_the_live_socket_in_its_own_pod(
    backend_server, world
):
    outcome = await _socket_outcome(
        backend_server, world["pod_id"], world["workloads"]["assistant"]
    )

    assert outcome == "ready"


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_H8_a_workload_cannot_open_another_pods_live_socket(
    backend_server, world, workload: str
):
    """H8: the socket kept only the token's user id, so a pod-A agent streamed
    the person's direct messages and inbox in pod B."""
    outcome = await _socket_outcome(
        backend_server, world["other_pod_id"], world["workloads"][workload]
    )

    assert outcome == CLOSE_FORBIDDEN


async def test_H8_a_named_agent_cannot_hold_a_live_socket(backend_server, world):
    """Its topics read as the person; a named agent's grants never applied."""
    outcome = await _socket_outcome(
        backend_server, world["pod_id"], world["workloads"]["agent"]
    )

    assert outcome == CLOSE_FORBIDDEN


# --- Checkpoint B: membership first, org owners as admins, workloads per pod ---


async def _member_of(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    role: str,
) -> tuple[dict, dict]:
    from app.modules.test_support.e2e_authz import (
        add_pod_member,
        invite_org_member,
        signup_user,
    )

    user = await signup_user(async_client, "conformance-member")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=user
    )
    pod_member = await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role=role,
    )
    return user, pod_member


def _bearer(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


async def _table(client: AsyncClient, pod_id: str, headers, **extra) -> str:
    name = f"t_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        headers=headers,
        json={
            "name": name,
            "enable_rls": False,
            "columns": [{"name": "note", "type": "TEXT"}],
            **extra,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return name


async def test_H12_a_member_removed_from_the_pod_loses_what_they_created(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """H12: the owner branch had no membership check, so a removed member kept
    reading, changing and deleting what they had made."""
    user, pod_member = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_EDITOR",
    )
    table = await _table(async_client, world["pod_id"], _bearer(user))
    removed = await authenticated_client.delete(
        f"/pods/{world['pod_id']}/members/{pod_member['pod_member_id']}"
    )
    assert removed.status_code in (200, 204), removed.text

    path = f"/pods/{world['pod_id']}/datastore/tables/{table}"
    read = await async_client.get(path, headers=_bearer(user))
    delete = await async_client.delete(path, headers=_bearer(user))
    still_there = await authenticated_client.get(path)

    assert read.status_code in (403, 404), read.text
    assert delete.status_code in (403, 404), delete.text
    assert still_there.status_code == status.HTTP_200_OK, still_there.text


async def test_M4_an_org_owner_does_not_read_a_members_personal_table(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """M4: an org-owner shortcut ran before the owner and visibility checks."""
    user, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_EDITOR",
    )
    table = await _table(
        async_client, world["pod_id"], _bearer(user), visibility="PERSONAL"
    )

    as_owner = await authenticated_client.get(
        f"/pods/{world['pod_id']}/datastore/tables/{table}"
    )

    assert as_owner.status_code in (403, 404), as_owner.text


async def test_H10_a_check_without_a_resource_needs_the_workloads_own_grant(
    world, db_session, fixed_test_user
):
    """H10: a named agent was decided on its person's permissions alone when a
    check named no resource -- every saved site login, with no grant."""
    from app.core.authorization.permissions import Permissions
    from app.core.authorization.service import AuthorizationDataService

    service = AuthorizationDataService(db_session)
    kwargs = {
        "user_id": UUID(fixed_test_user["id"]),
        "principal_type": "AGENT",
        "pod_id": UUID(world["pod_id"]),
    }
    agent = await service.build_delegated_workload_context(
        principal_id=uuid7(), **kwargs
    )
    assistant = await service.build_delegated_workload_context(
        principal_id=DEFAULT_POD_AGENT_ID, is_default_pod_agent=True, **kwargs
    )

    assert not await agent.can(Permissions.WEB_LOGIN_USE)
    assert await assistant.can(Permissions.WEB_LOGIN_USE)


async def test_M3_a_workload_is_refused_a_deleted_pod(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """M3: only a person's context carried the deleted flag."""
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    headers = await _workload_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=False
    )
    deleted = await authenticated_client.delete(f"/pods/{pod_id}")
    assert deleted.status_code in (200, 202, 204), deleted.text

    response = await async_client.get(f"/pods/{pod_id}/agents", headers=headers)

    assert response.status_code in (403, 404), response.text


# --- Checkpoint C: conferral bounded by the resource, and by who is asking ---


def _grant(resource_type: str, name: str, *permission_ids: str) -> dict:
    return {
        "resource_type": resource_type,
        "resource_name": name,
        "permission_ids": list(permission_ids),
    }


async def test_H13_an_inline_grant_cannot_arm_an_agent_beyond_its_author(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """H13: create/update's inline ``permissions`` skipped the conferral bound,
    so an editor gave an agent deletes they could not do themselves."""
    editor, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_EDITOR",
    )
    table = await _table(authenticated_client, world["pod_id"], {})

    response = await async_client.post(
        f"/pods/{world['pod_id']}/agents",
        headers=_bearer(editor),
        json={
            "name": f"armed_{uuid4().hex[:6]}",
            "instruction": "Tidy up.",
            "permissions": {
                "grants": [_grant("datastore_table", table, "datastore.table.delete")]
            },
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"


async def test_M1_a_grant_is_bounded_by_the_resource_not_the_permission_id(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """M1: holding ``datastore.table.read`` by role is not reading a RESTRICTED
    table nobody shared with you -- the bound compared ids, not access."""
    editor, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_EDITOR",
    )
    secret = await _table(
        authenticated_client, world["pod_id"], {}, visibility="RESTRICTED"
    )
    agent = await async_client.post(
        f"/pods/{world['pod_id']}/agents",
        headers=_bearer(editor),
        json={"name": f"reader_{uuid4().hex[:6]}", "instruction": "Read."},
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    response = await async_client.put(
        f"/pods/{world['pod_id']}/agents/{agent.json()['name']}/permissions",
        headers=_bearer(editor),
        json={"grants": [_grant("datastore_table", secret, "datastore.table.read")]},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"


async def test_M1_an_agent_cannot_rewrite_its_own_grants(
    authenticated_client: AsyncClient, async_client: AsyncClient, world, fixed_test_user
):
    """M1: an agent holding ``agent.update`` on itself widened its own grants."""
    name = f"self_{uuid4().hex[:6]}"
    created = await authenticated_client.post(
        f"/pods/{world['pod_id']}/agents",
        json={"name": name, "instruction": "Help."},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    agent_id = created.json()["id"]
    armed = await authenticated_client.put(
        f"/pods/{world['pod_id']}/agents/{name}/permissions",
        json={"grants": [_grant("agent", name, "agent.read", "agent.update")]},
    )
    assert armed.status_code == status.HTTP_200_OK, armed.text
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(agent_id),
        workload_name=name,
        pod_id=UUID(world["pod_id"]),
        session_id=f"conformance-{uuid4().hex}",
        invoked_by_user_id=UUID(fixed_test_user["id"]),
    )
    token = await get_user_token(UUID(fixed_test_user["id"]), delegation_claims=claims)

    response = await async_client.put(
        f"/pods/{world['pod_id']}/agents/{name}/permissions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "grants": [
                _grant("agent", name, "agent.read", "agent.update", "agent.delete")
            ]
        },
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"


async def _agent_token(
    authenticated_client: AsyncClient,
    *,
    pod_id: str,
    user_id: str,
    grants: list[dict],
) -> dict[str, str]:
    """A named agent with exactly ``grants``, and a token of it acting for the user."""
    name = f"agent_{uuid4().hex[:6]}"
    created = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Help.", "permissions": {"grants": grants}},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(created.json()["id"]),
        workload_name=name,
        pod_id=UUID(pod_id),
        session_id=f"conformance-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


async def test_H19_reading_a_tables_schema_is_not_reading_its_rows(
    authenticated_client: AsyncClient, async_client: AsyncClient, world, fixed_test_user
):
    """H19: SQL checked ``table.read`` only; the records API needs ``record.read``."""
    table = await _table(authenticated_client, world["pod_id"], {})
    headers = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[_grant("datastore_table", table, "datastore.table.read")],
    )

    response = await async_client.post(
        f"/pods/{world['pod_id']}/datastore/query",
        headers=headers,
        json={"query": f"SELECT note FROM {table}"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


@pytest.mark.parametrize(
    ("method", "suffix"),
    [("POST", "bundle/exports"), ("POST", "bundle/imports")],
)
async def test_H17_a_named_agent_cannot_run_a_bundle_job_as_its_person(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    world,
    fixed_test_user,
    method: str,
    suffix: str,
):
    """H17: bundle jobs rebuild the person's whole context, so an agent holding
    only ``pod.read`` exported everything its person could read."""
    headers = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[],
    )

    response = await async_client.request(
        method, f"/pods/{world['pod_id']}/{suffix}", headers=headers, json={}
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == WORKLOAD_NOT_ALLOWED


async def test_H17_the_assistant_still_exports_its_pod(
    async_client: AsyncClient, world
):
    response = await async_client.post(
        f"/pods/{world['pod_id']}/bundle/exports",
        headers=world["workloads"]["assistant"],
        json={},
    )

    assert response.json().get("code") != WORKLOAD_NOT_ALLOWED, response.text


async def test_M21_moving_a_file_into_a_folder_needs_write_there(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """M21: a move checked write on the source only. A POD_USER -- who may read
    the pod's folders but not write them -- could move a file of their own into
    a shared folder that creating it there would have been refused."""
    from app.modules.datastore.tests.e2e.harness import DatastoreApi

    user, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_USER",
    )
    shared = f"/shared_{uuid4().hex[:6]}"
    await DatastoreApi(authenticated_client, world["pod_id"]).create_folder(shared)
    user_api = DatastoreApi(async_client, world["pod_id"], user=user)
    await user_api.upload_file("mine.md", b"# mine", directory_path="/me")

    await user_api.update_file(
        "/me/mine.md",
        new_path=f"{shared}/mine.md",
        expected_status=status.HTTP_403_FORBIDDEN,
    )


def _mint(pod_id: str, path: str) -> str:
    return f"/pods/{pod_id}/datastore/files/signed-url?path={path}"


async def test_M22_a_named_agent_mints_a_public_link_only_where_granted(
    authenticated_client: AsyncClient, async_client: AsyncClient, world, fixed_test_user
):
    """M22: any ``folder.read`` holder -- agents included -- minted a 7-day
    anonymous link to a file, outside every grant. Making one is ``folder.share``
    now: a named agent holds it only where it was granted, beside the read."""
    from app.modules.datastore.tests.e2e.harness import DatastoreApi

    folder = f"/outbox_{uuid4().hex[:6]}"
    owner_api = DatastoreApi(authenticated_client, world["pod_id"])
    await owner_api.create_folder(folder)
    await owner_api.upload_file("notes.md", b"# notes", directory_path=folder)
    path = f"{folder}/notes.md"
    reads_only = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[_grant("folder", folder, "folder.read")],
    )
    shares = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[_grant("folder", folder, "folder.read", "folder.share")],
    )

    refused = await async_client.post(
        _mint(world["pod_id"], path), headers=reads_only, json={}
    )
    minted = await async_client.post(
        _mint(world["pod_id"], path), headers=shares, json={}
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert minted.status_code == status.HTTP_201_CREATED, minted.text
    opened = await async_client.get(
        "/public/s/" + minted.json()["signed_url"].rstrip("/").rsplit("/", 1)[-1]
    )
    assert opened.status_code == status.HTTP_200_OK, opened.text


async def test_M22_a_viewer_and_the_assistant_mint_as_a_person(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """Every person role holds ``folder.share``, so separating it took nothing
    from people; the assistant holds what its person holds."""
    from app.modules.datastore.tests.e2e.harness import DatastoreApi

    await DatastoreApi(authenticated_client, world["pod_id"]).upload_file(
        "handout.md", b"# handout"
    )
    viewer, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_VIEWER",
    )

    by_viewer = await async_client.post(
        _mint(world["pod_id"], "/handout.md"), headers=_bearer(viewer), json={}
    )
    by_assistant = await async_client.post(
        _mint(world["pod_id"], "/handout.md"),
        headers=world["workloads"]["assistant"],
        json={},
    )

    assert by_viewer.status_code == status.HTTP_201_CREATED, by_viewer.text
    assert by_assistant.status_code == status.HTTP_201_CREATED, by_assistant.text


async def test_M22_a_pod_admin_sees_and_revokes_any_members_link(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """M22: only the minter could list or revoke a link, so a departed member's
    public links outlived their access with nobody able to withdraw them."""
    from app.modules.datastore.tests.e2e.harness import DatastoreApi

    editor, _ = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_EDITOR",
    )
    editor_api = DatastoreApi(async_client, world["pod_id"], user=editor)
    await editor_api.upload_file("handout.md", b"# handout")
    minted = await async_client.post(
        f"/pods/{world['pod_id']}/datastore/files/signed-url",
        params={"path": "/handout.md"},
        headers=_bearer(editor),
        json={},
    )
    assert minted.status_code in (200, 201), minted.text
    code = minted.json()["signed_url"].rstrip("/").rsplit("/", 1)[-1]

    listed = await authenticated_client.get(
        f"/pods/{world['pod_id']}/datastore/files/signed-urls"
    )
    revoked = await authenticated_client.delete(
        f"/pods/{world['pod_id']}/datastore/files/signed-urls/{code}"
    )

    assert code in listed.text, listed.text
    assert revoked.status_code in (200, 204), revoked.text


# --- H14: deleting records is its own permission, and a destructive one ---


def _records(pod_id: str, table: str) -> str:
    return f"/pods/{pod_id}/datastore/tables/{table}/records"


async def _record(client: AsyncClient, pod_id: str, table: str, headers) -> str:
    response = await client.post(
        _records(pod_id, table), headers=headers, json={"data": {"note": "keep"}}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return str(response.json()["id"])


async def _deletes(
    client: AsyncClient, pod_id: str, table: str, record_id: str, headers
) -> tuple:
    one = await client.delete(f"{_records(pod_id, table)}/{record_id}", headers=headers)
    bulk = await client.post(
        f"{_records(pod_id, table)}/bulk/delete",
        headers=headers,
        json={"record_ids": [record_id]},
    )
    return one, bulk


async def test_H14_writing_records_is_not_deleting_them(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """H14: a record delete asked for ``record.write``, so whoever could write
    one row of a table could delete every row of it."""
    viewer, pod_member = await _member_of(
        authenticated_client,
        async_client,
        org_id=world["org_id"],
        pod_id=world["pod_id"],
        role="POD_VIEWER",
    )
    table = await _table(authenticated_client, world["pod_id"], {})
    granted = await authenticated_client.put(
        f"/pods/{world['pod_id']}/resources/datastore_table/{table}"
        f"/access/grantees/POD_MEMBER/{pod_member['pod_member_id']}",
        json={"permission_ids": ["datastore.record.read", "datastore.record.write"]},
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text
    # The grant is real: the member writes.
    record_id = await _record(async_client, world["pod_id"], table, _bearer(viewer))

    one, bulk = await _deletes(
        async_client, world["pod_id"], table, record_id, _bearer(viewer)
    )
    kept = await authenticated_client.get(
        f"{_records(world['pod_id'], table)}/{record_id}"
    )

    assert one.status_code == status.HTTP_403_FORBIDDEN, one.text
    assert bulk.status_code == status.HTTP_403_FORBIDDEN, bulk.text
    assert kept.status_code == status.HTTP_200_OK, kept.text


async def test_H14_the_assistant_asks_before_deleting_a_record(
    authenticated_client: AsyncClient, async_client: AsyncClient, world
):
    """H14 / PS-ACCESS-021: record deletes were not destructive, so the pod's
    assistant deleted rows -- all of them, in bulk -- without asking."""
    table = await _table(authenticated_client, world["pod_id"], {})
    record_id = await _record(authenticated_client, world["pod_id"], table, {})

    one, bulk = await _deletes(
        async_client,
        world["pod_id"],
        table,
        record_id,
        world["workloads"]["assistant"],
    )
    kept = await authenticated_client.get(
        f"{_records(world['pod_id'], table)}/{record_id}"
    )

    for response in (one, bulk):
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
        assert response.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"
    assert kept.status_code == status.HTTP_200_OK, kept.text


async def test_H14_a_named_agent_deletes_records_only_when_granted_it(
    authenticated_client: AsyncClient, async_client: AsyncClient, world, fixed_test_user
):
    """H14: ``record.write`` carried deletes. Now a grant of
    ``datastore.record.delete`` is the standing authority a headless run needs."""
    table = await _table(authenticated_client, world["pod_id"], {})
    writes = ["datastore.table.read", "datastore.record.read", "datastore.record.write"]
    writer = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[_grant("datastore_table", table, *writes)],
    )
    deleter = await _agent_token(
        authenticated_client,
        pod_id=world["pod_id"],
        user_id=fixed_test_user["id"],
        grants=[_grant("datastore_table", table, *writes, "datastore.record.delete")],
    )
    record_id = await _record(async_client, world["pod_id"], table, writer)

    refused, _ = await _deletes(async_client, world["pod_id"], table, record_id, writer)
    allowed = await async_client.delete(
        f"{_records(world['pod_id'], table)}/{record_id}", headers=deleter
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert allowed.status_code == status.HTTP_204_NO_CONTENT, allowed.text
