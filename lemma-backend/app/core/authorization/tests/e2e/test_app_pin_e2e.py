"""H20: a pod app's page is its viewer, in its own pod only (``core/app_pin``).

An app's page runs on ``<slug>.<app_base_domain>`` and calls the API with the
viewer's session cookie, which the browser sends with ``Origin`` naming the
app's host. On main that request was the viewer everywhere, so an editor's app
opened by an organization owner acted as that owner across the organization.
Now it keeps all of the viewer's access inside the app's own pod, and outside
it only who is viewing, the connector catalogue and connector setup in its own
organization.

The requests here are a browser's: a cookie, an ``Origin``, no bearer token. A
bearer token is never recognised as an app.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.api.actor_policy import APP_ROUTE_NOT_ALLOWED, APP_ROUTES
from app.core.app_pin import APP_HOST_UNKNOWN, ORIGIN_NULL_NOT_ALLOWED
from app.core.config import settings
from app.core.live.protocol import CLOSE_FORBIDDEN
from app.modules.test_support.e2e.live_socket import ws_url
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

backend_server = _backend_server

APP_DOMAIN = "apps.pin.test"


@pytest.fixture
def app_hosts(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", APP_DOMAIN)


@pytest.fixture
async def browser(test_app) -> AsyncIterator[AsyncClient]:
    """A client with no credentials of its own: each request names its cookie.

    ``authenticated_client`` is ``async_client`` with a bearer header set on it,
    which would win over any cookie.
    """
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as client:
        yield client


def _cookie(user: dict) -> dict[str, str]:
    # ``rid`` as the SDK sends it, so SuperTokens' anti-CSRF check is satisfied
    # wherever it applies and the refusal under test is the only one.
    return {"Cookie": f"sAccessToken={user['token']}", "rid": "session"}


def _from_app(origin: str, user: dict) -> dict[str, str]:
    return {**_cookie(user), "Origin": origin}


def _bearer(user: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {user['token']}"}


async def _pod(client: AsyncClient, org_id: str, headers=None) -> str:
    response = await client.post(
        "/pods",
        headers=headers,
        json={
            "organization_id": org_id,
            "name": f"App pin {uuid4().hex[:8]}",
            "description": "app pin e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _member(
    owner: AsyncClient, async_client: AsyncClient, org_id: str, pod_id: str, role: str
) -> dict:
    user = await signup_user(async_client, "app-pin")
    org_member = await invite_org_member(owner, async_client, org_id=org_id, user=user)
    await add_pod_member(
        owner, pod_id=pod_id, organization_member_id=org_member["id"], role=role
    )
    return user


async def _app(client: AsyncClient, pod_id: str, headers=None) -> str:
    """An app in ``pod_id``, and the origin its page runs on."""
    name = f"app_{uuid4().hex[:8]}"
    created = await client.post(
        f"/pods/{pod_id}/apps",
        headers=headers,
        json={"name": name, "public_slug": name.replace("_", "-")},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return f"https://{created.json()['public_slug']}.{APP_DOMAIN}"


async def _table(client: AsyncClient, pod_id: str) -> str:
    name = f"t_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "enable_rls": False,
            "columns": [{"name": "note", "type": "TEXT"}],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return name


@pytest.fixture
async def world(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    app_hosts,
):
    """An org owner, an editor in one pod, the editor's app, another pod."""
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id)
    other_pod_id = await _pod(authenticated_client, org_id)
    editor = await _member(
        authenticated_client, async_client, org_id, pod_id, "POD_EDITOR"
    )
    origin = await _app(async_client, pod_id, headers=_bearer(editor))
    return {
        "org_id": org_id,
        "pod_id": pod_id,
        "other_pod_id": other_pod_id,
        "owner": fixed_test_user,
        "editor": editor,
        "origin": origin,
    }


def _code(response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


# --- the escalation ---


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/organizations/{org_id}", {"name": "Renamed by an app"}),
        ("PUT", "/usage/organizations/{org_id}/contacts-cap", {"limit": 1}),
        (
            "PATCH",
            "/organizations/{org_id}/members/00000000-0000-0000-0000-000000000001/role",
            {"role": "ORG_OWNER"},
        ),
        ("GET", "/pods/{other_pod_id}/datastore/tables", None),
        ("POST", "/pods", None),
    ],
)
async def test_H20_an_editors_app_opened_by_the_org_owner_stays_in_its_pod(
    browser: AsyncClient, world, method: str, path: str, body, monkeypatch
):
    """H20: the owner opening an editor's app handed the app the owner's whole
    organization -- its settings, its spending, every pod."""
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", True)
    concrete = path.format(**world)
    payload = body
    if path == "/pods":
        payload = {
            "organization_id": world["org_id"],
            "name": "Made by an app",
            "type": "HYBRID",
        }

    response = await browser.request(
        method,
        concrete,
        headers=_from_app(world["origin"], world["owner"]),
        json=payload,
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert _code(response) == APP_ROUTE_NOT_ALLOWED, response.text
    assert method in response.json()["message"], response.text


async def test_an_app_manages_its_orgs_people_as_its_viewer(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    browser: AsyncClient,
    world,
):
    """Internal apps pick and onboard colleagues: reading the members and
    inviting someone go through, decided by the viewer's own permission -- an
    owner invites, a plain member gets the route's own refusal, not the pin's."""
    owner = _from_app(world["origin"], world["owner"])

    org = await browser.get(f"/organizations/{world['org_id']}", headers=owner)
    members = await browser.get(
        f"/organizations/{world['org_id']}/members", headers=owner
    )
    invited = await browser.post(
        f"/organizations/{world['org_id']}/invitations",
        headers=owner,
        json={
            "email": f"invited-by-an-app-{uuid4().hex[:6]}@example.com",
            "role": "ORG_MEMBER",
        },
    )
    member = await _member(
        authenticated_client,
        async_client,
        world["org_id"],
        world["pod_id"],
        "POD_USER",
    )
    refused = await browser.post(
        f"/organizations/{world['org_id']}/invitations",
        headers=_from_app(world["origin"], member),
        json={
            "email": f"not-invited-{uuid4().hex[:6]}@example.com",
            "role": "ORG_MEMBER",
        },
    )

    assert org.status_code == status.HTTP_200_OK, org.text
    assert members.status_code == status.HTTP_200_OK, members.text
    assert invited.status_code < 300, invited.text
    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert _code(refused) != APP_ROUTE_NOT_ALLOWED, refused.text
    # Never into a higher role, even by an owner: that is how an editor's app,
    # opened by its owner, would make the editor an owner.
    promoted = await browser.post(
        f"/organizations/{world['org_id']}/invitations",
        headers=owner,
        json={
            "email": f"invited-as-owner-{uuid4().hex[:6]}@example.com",
            "role": "ORG_OWNER",
        },
    )
    assert promoted.status_code == status.HTTP_403_FORBIDDEN, promoted.text
    assert _code(promoted) == APP_ROUTE_NOT_ALLOWED, promoted.text


async def test_H20_the_owner_still_reaches_their_org_outside_the_app(
    browser: AsyncClient, world
):
    """The same cookie, from the workspace rather than the app's page."""
    response = await browser.get(
        f"/organizations/{world['org_id']}/members", headers=_cookie(world["owner"])
    )

    assert response.status_code == status.HTTP_200_OK, response.text


async def test_H20_a_bearer_token_is_never_read_as_an_app(
    async_client: AsyncClient, world
):
    response = await async_client.get(
        f"/organizations/{world['org_id']}/members",
        headers={**_bearer(world["owner"]), "Origin": world["origin"]},
    )

    assert response.status_code == status.HTTP_200_OK, response.text


async def test_H20_an_app_host_that_names_no_app_is_refused(
    browser: AsyncClient, world
):
    response = await browser.get(
        "/users/me",
        headers=_from_app(
            f"https://gone-{uuid4().hex[:6]}.{APP_DOMAIN}", world["owner"]
        ),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert _code(response) == APP_HOST_UNKNOWN, response.text


# --- the app is still its viewer in its own pod ---


async def test_H20_an_app_reads_writes_and_chats_in_its_pod_as_its_viewer(
    authenticated_client: AsyncClient, browser: AsyncClient, world
):
    table = await _table(authenticated_client, world["pod_id"])
    headers = _from_app(world["origin"], world["owner"])
    records = f"/pods/{world['pod_id']}/datastore/tables/{table}/records"

    wrote = await browser.post(records, headers=headers, json={"data": {"note": "hi"}})
    read = await browser.get(records, headers=headers)
    chat = await browser.post(
        f"/pods/{world['pod_id']}/conversations",
        headers=headers,
        json={"agent_runtime": {"profile_id": "system:lemma"}},
    )
    listed = await browser.get(
        f"/pods/{world['pod_id']}/conversations", headers=headers
    )

    assert wrote.status_code == status.HTTP_201_CREATED, wrote.text
    assert read.status_code == status.HTTP_200_OK, read.text
    assert wrote.json()["id"] in {str(item["id"]) for item in read.json()["items"]}
    assert chat.status_code == status.HTTP_201_CREATED, chat.text
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert chat.json()["id"] in {item["id"] for item in listed.json()["items"]}


async def test_H20_an_app_is_held_to_its_viewers_own_access_in_its_pod(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    browser: AsyncClient,
    world,
):
    """Nothing is added: a viewer who may not write cannot write through it."""
    viewer = await _member(
        authenticated_client,
        async_client,
        world["org_id"],
        world["pod_id"],
        "POD_VIEWER",
    )
    table = await _table(authenticated_client, world["pod_id"])
    records = f"/pods/{world['pod_id']}/datastore/tables/{table}/records"

    as_app = await browser.post(
        records, headers=_from_app(world["origin"], viewer), json={"data": {}}
    )
    as_viewer = await browser.post(records, headers=_cookie(viewer), json={"data": {}})

    assert as_app.status_code == as_viewer.status_code, (as_app.text, as_viewer.text)
    assert _code(as_app) == _code(as_viewer) != APP_ROUTE_NOT_ALLOWED


# --- connector setup, as the viewer ---


def _org_route_path(template: str, org_id: str) -> str:
    values = {
        "organization_id": org_id,
        "auth_config_name": f"missing-{uuid4().hex[:6]}",
        "account_id": str(uuid4()),
        "operation_name": "missing",
        "connector_id": f"missing-{uuid4().hex[:6]}",
        "profile_id": str(uuid4()),
        "trigger_name": "missing",
        "member_id": str(uuid4()),
        # The viewer's own machine: refused for want of their consent, which
        # is the handler deciding, not the pin.
        "ref": "me",
        "process_id": str(uuid4()),
        "preview_id": str(uuid4()),
    }
    return template.format(**values)


@pytest.mark.parametrize("member", [False, True], ids=["owner", "member"])
async def test_H20_an_app_calls_every_allowlisted_route_as_its_viewer(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    browser: AsyncClient,
    world,
    member: bool,
):
    """Every route an app is opened to reaches its handler, which decides as
    the viewer -- an owner and a plain member alike."""
    viewer = world["owner"]
    if member:
        viewer = await _member(
            authenticated_client,
            async_client,
            world["org_id"],
            world["pod_id"],
            "POD_USER",
        )
    headers = _from_app(world["origin"], viewer)

    wrong = []
    for method, template in sorted(APP_ROUTES):
        path = _org_route_path(template, world["org_id"])
        response = await browser.request(
            method, path, headers=headers, json={} if method != "GET" else None
        )
        if _code(response) == APP_ROUTE_NOT_ALLOWED or response.status_code >= 500:
            wrong.append(
                f"{method} {template} -> {response.status_code} {response.text}"
            )
    assert not wrong, "\n".join(wrong)


async def test_H20_connector_setup_in_another_org_is_refused(
    browser: AsyncClient, world
):
    other_org = str(uuid4())

    response = await browser.get(
        f"/organizations/{other_org}/connectors/auth-configs",
        headers=_from_app(world["origin"], world["owner"]),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert _code(response) == APP_ROUTE_NOT_ALLOWED, response.text


async def test_H20_a_viewer_without_the_permission_gets_the_routes_own_refusal(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    browser: AsyncClient,
    world,
):
    """The app adds nothing: installing a connector for the organization is
    the viewer's to do or not, and the route says which."""
    member = await _member(
        authenticated_client,
        async_client,
        world["org_id"],
        world["pod_id"],
        "POD_USER",
    )
    path = f"/organizations/{world['org_id']}/connectors/auth-configs"
    body = {"connector_id": f"missing-{uuid4().hex[:6]}"}

    as_app = await browser.post(
        path, headers=_from_app(world["origin"], member), json=body
    )
    as_member = await browser.post(path, headers=_cookie(member), json=body)

    assert as_app.status_code in {403, 404}, as_app.text
    assert _code(as_app) != APP_ROUTE_NOT_ALLOWED, as_app.text
    assert (as_app.status_code, _code(as_app)) == (
        as_member.status_code,
        _code(as_member),
    )


# --- /users/me, trimmed ---


async def test_H20_an_app_learns_who_is_viewing_but_not_their_phone_or_birthday(
    async_client: AsyncClient, browser: AsyncClient, world
):
    owner = world["owner"]
    updated = await async_client.post(
        "/users/me/profile",
        headers=_bearer(owner),
        json={"mobile_number": "+14155550100", "date_of_birth": "1990-01-02"},
    )
    assert updated.status_code == status.HTTP_201_CREATED, updated.text

    for path in ("/users/me", "/users/me/profile"):
        as_app = await browser.get(path, headers=_from_app(world["origin"], owner))
        as_person = await browser.get(path, headers=_cookie(owner))

        assert as_app.status_code == status.HTTP_200_OK, as_app.text
        assert as_app.json()["email"] == owner["email"]
        assert as_app.json()["mobile_number"] is None
        assert as_app.json()["date_of_birth"] is None
        assert as_person.json()["mobile_number"] == "+14155550100"
        assert as_person.json()["date_of_birth"] == "1990-01-02"


# --- fail closed ---


@pytest.mark.parametrize(
    ("host", "prefix"), [("testserver", ""), (f"orders.{APP_DOMAIN}", "/_lemma")]
)
async def test_a_cookie_request_from_a_null_origin_changes_nothing(
    authenticated_client: AsyncClient,
    browser: AsyncClient,
    world,
    monkeypatch,
    host: str,
    prefix: str,
):
    """Not read by its Host: an opaque document names no host at all -- on the
    API's host, or through an app host's own ``/_lemma`` door."""
    monkeypatch.setattr(settings, "app_api_via_app_origin", True)
    table = await _table(authenticated_client, world["pod_id"])
    records = f"{prefix}/pods/{world['pod_id']}/datastore/tables/{table}/records"

    response = await browser.post(
        records,
        headers={**_cookie(world["owner"]), "Origin": "null", "Host": host},
        json={"data": {"note": "from nowhere"}},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert _code(response) == ORIGIN_NULL_NOT_ALLOWED, response.text


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


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
async def test_a_cookie_live_socket_from_an_unknown_origin_is_refused(
    backend_server, world, origin: str
):
    """The socket reads its cookie with no anti-CSRF check: any page could
    open it as the signed-in person."""
    outcome = await _socket_outcome(
        backend_server, world["pod_id"], {**_cookie(world["owner"]), "Origin": origin}
    )

    assert outcome == CLOSE_FORBIDDEN


async def test_an_apps_live_socket_opens_in_its_own_pod_only(backend_server, world):
    headers = _from_app(world["origin"], world["owner"])

    own = await _socket_outcome(backend_server, world["pod_id"], headers)
    other = await _socket_outcome(backend_server, world["other_pod_id"], headers)

    assert own == "ready"
    assert other == CLOSE_FORBIDDEN


async def test_the_workspace_still_opens_a_cookie_live_socket(backend_server, world):
    outcome = await _socket_outcome(
        backend_server,
        world["pod_id"],
        {**_cookie(world["owner"]), "Origin": settings.frontend_url.rstrip("/")},
    )

    assert outcome == "ready"


async def test_H20_an_app_calling_through_its_own_door_is_pinned_too(
    browser: AsyncClient, world, monkeypatch
):
    """``<app-host>/_lemma/...`` carries no Origin of its own; its Host names
    the app."""
    monkeypatch.setattr(settings, "app_api_via_app_origin", True)
    host = world["origin"].removeprefix("https://")
    headers = {**_cookie(world["owner"]), "Host": host}

    own = await browser.get(
        f"/_lemma/pods/{world['pod_id']}/datastore/tables", headers=headers
    )
    org = await browser.patch(
        f"/_lemma/organizations/{world['org_id']}",
        headers=headers,
        json={"name": "Renamed by an app"},
    )

    assert own.status_code == status.HTTP_200_OK, own.text
    assert org.status_code == status.HTTP_403_FORBIDDEN, org.text
    assert _code(org) == APP_ROUTE_NOT_ALLOWED, org.text
