"""An app's page on machines: its own pod's, as its viewer -- never anybody's own.

An app's page acts as its viewer inside its own pod, for machines as for
every other resource (``core/app_pin``). A pod machine is a pod resource, so
the page reaches its processes, files and previews when its viewer could.
A person's own machine (``me``) is not a pod resource: no app reaches it,
and nothing an app or its viewer does changes that -- there is no consent
to give. Managing a machine -- creating, changing, deleting, granting,
resetting, the browser and its logins -- stays the person's own.

Nothing here starts a machine: every route asked is answered from rows
(``previews``) or refused before a sandbox would be reached.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.api.actor_policy import APP_PERSON_ONLY, APP_ROUTE_NOT_ALLOWED
from app.core.authorization.tests.e2e import test_app_pin_e2e as pin

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

app_hosts = pin.app_hosts
browser = pin.browser
world = pin.world


async def _pod_machine(client: AsyncClient, pod_id: str, name: str = "shared") -> str:
    created = await client.post(f"/pods/{pod_id}/machines", json={"name": name})
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["ref"]


async def test_an_app_never_reaches_its_viewers_own_machine(
    browser: AsyncClient, world
):
    as_app = pin._from_app(world["origin"], world["owner"])

    refused = await browser.get("/machines/me/previews", headers=as_app)

    assert refused.status_code == status.HTTP_404_NOT_FOUND, refused.text
    assert pin._code(refused) == "MACHINE_NOT_FOUND", refused.text
    # The same person, in Lemma, has it.
    own = await browser.get(
        "/machines/me/previews", headers=pin._cookie(world["owner"])
    )
    assert own.status_code == status.HTTP_200_OK, own.text


async def test_an_app_uses_a_machine_of_its_own_pod_as_its_viewer(
    authenticated_client: AsyncClient, browser: AsyncClient, world
):
    ref = await _pod_machine(authenticated_client, world["pod_id"])
    as_app = pin._from_app(world["origin"], world["owner"])

    previews = await browser.get(f"/machines/{ref}/previews", headers=as_app)

    assert previews.status_code == status.HTTP_200_OK, previews.text
    assert previews.json() == {"previews": []}
    listed = await browser.get(
        "/machines", headers=as_app, params={"pod_id": world["pod_id"]}
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    refs = {m["ref"]: m for m in listed.json()["machines"]}
    assert "me" not in refs, "an app is never shown its viewer's own machine"
    # As its viewer, but never past use: managing is the person's own.
    assert refs[ref]["access"] == "use", refs[ref]


async def test_an_app_does_not_reach_another_pods_machine(
    authenticated_client: AsyncClient, browser: AsyncClient, world
):
    ref = await _pod_machine(authenticated_client, world["other_pod_id"])

    refused = await browser.get(
        f"/machines/{ref}/previews",
        headers=pin._from_app(world["origin"], world["owner"]),
    )

    assert refused.status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ), refused.text


async def test_an_app_does_not_manage_its_pods_machines(
    authenticated_client: AsyncClient, browser: AsyncClient, world
):
    pod_id = world["pod_id"]
    ref = await _pod_machine(authenticated_client, pod_id)
    as_app = pin._from_app(world["origin"], world["owner"])

    for method, path, body in (
        ("POST", f"/pods/{pod_id}/machines", {"name": "from-app"}),
        ("PATCH", f"/pods/{pod_id}/machines/shared", {"run_slots": 2}),
        ("GET", f"/pods/{pod_id}/machines/shared/grants", None),
        ("PUT", f"/pods/{pod_id}/machines/shared/grants", {"agents": []}),
        ("DELETE", f"/pods/{pod_id}/machines/shared", None),
    ):
        response = await browser.request(method, path, headers=as_app, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"{method} {path}: {response.text}"
        )
        assert pin._code(response) == APP_PERSON_ONLY, f"{method} {path}"

    for method, path, body in (
        ("POST", f"/machines/{ref}:release", None),
        ("POST", f"/machines/{ref}:restart", None),
        ("POST", f"/machines/{ref}:reset", {"scopes": ["home"], "confirm": True}),
        ("GET", f"/machines/{ref}/browser", None),
    ):
        response = await browser.request(method, path, headers=as_app, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"{method} {path}: {response.text}"
        )
        assert pin._code(response) == APP_ROUTE_NOT_ALLOWED, f"{method} {path}"

    still = await authenticated_client.get(f"/machines/{ref}")
    assert still.status_code == status.HTTP_200_OK, still.text


async def test_the_browser_logins_and_reset_stay_the_persons(
    browser: AsyncClient, world
):
    as_app = pin._from_app(world["origin"], world["owner"])

    for method, path, body in (
        ("GET", "/machines/me/browser", None),
        ("POST", "/machines/me/browser:access", {"ttl_seconds": 60}),
        ("POST", "/machines/me/browser:navigate", {"url": "https://example.com"}),
        ("GET", "/machines/me/browser/logins", None),
        ("DELETE", "/machines/me/browser/logins/example.com", None),
        ("POST", "/machines/me:reset", {"scopes": ["home"], "confirm": True}),
        ("POST", "/machines/me:release", None),
    ):
        response = await browser.request(method, path, headers=as_app, json=body)
        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"{method} {path}: {response.text}"
        )
        assert pin._code(response) == APP_ROUTE_NOT_ALLOWED, f"{method} {path}"
