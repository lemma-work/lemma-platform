"""A pod app's page is its viewer, but not for their own accounts (H20).

An app's page calls the API with its viewer's cookie, from the app's host. In
its own pod it has the viewer's access -- but what a person does with an
account they connected, and which pod the deprecated execute runs in, are not
the app author's to decide:

- sharing or adding the viewer's account to the pod, and letting it into
  sandboxes, is ``require_person``: an editor's app, opened by a member, would
  otherwise move the member's credential in front of the pod without them ever
  seeing the question;
- the deprecated org-level execute takes its pod from the body, where the
  pin's path check cannot see it, so an app's page runs in its own pod.

The requests here are a browser's: a cookie, an ``Origin``, no bearer token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette import status

from app.core.api.actor_policy import APP_PERSON_ONLY, APP_ROUTE_NOT_ALLOWED
from app.core.config import settings
from app.modules.connectors.infrastructure.models.pod_connector import PodAccount
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    _EXECUTOR,
    World,
    _connect,
    _echo_the_credential,
    _world,
)

pytestmark = pytest.mark.e2e

APP_DOMAIN = "apps.podaccounts.test"


@pytest.fixture(autouse=True)
def _app_hosts(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", APP_DOMAIN)


@pytest.fixture
async def browser(test_app) -> AsyncIterator[AsyncClient]:
    """A client with no credentials of its own: each request names its cookie.

    The scenario's clients carry a bearer header, which would win over any
    cookie and is never read as an app.
    """
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as client:
        yield client


def _from_app(origin: str, user: dict) -> dict[str, str]:
    # ``rid`` as the SDK sends it, so SuperTokens' anti-CSRF check is satisfied
    # and the refusal under test is the only one.
    return {
        "Cookie": f"sAccessToken={user['token']}",
        "rid": "session",
        "Origin": origin,
    }


def _code(response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


async def _app(world: World, pod_id: str, maker: dict | None) -> str:
    """An app in ``pod_id`` made by ``maker`` (the org owner when None), and
    the origin its page runs on."""
    name = f"app_{uuid4().hex[:8]}"
    created = await world.call(
        "POST",
        f"/pods/{pod_id}/apps",
        maker,
        json={"name": name, "public_slug": name.replace("_", "-")},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return f"https://{created.json()['public_slug']}.{APP_DOMAIN}"


async def _pod_accounts(db_session, pod_id: str) -> list[PodAccount]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(PodAccount).where(PodAccount.pod_id == UUID(pod_id))
    )
    return list(rows.scalars())


async def _alice_shares(world: World, db_session) -> str:
    """Alice's account, shared with the pod by Alice herself; `crm` runs as it."""
    account_id = await _connect(db_session, world, world.alice, "alice-key")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": account_id, "visibility": "POD"},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    pod_account_id = shared.json()["id"]
    declared = await world.declare(
        "crm", "SHARED", shared_pod_account_id=pod_account_id
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    return pod_account_id


# --- the viewer's own accounts ---


async def test_an_editors_app_cannot_share_a_members_account_into_the_pod(
    scenario, db_session, browser
):
    world = await _world(scenario, db_session)
    origin = await _app(world, world.pod_id, world.editor)
    bob_account = await _connect(db_session, world, world.bob, "bob-key")

    refused = await browser.post(
        f"/pods/{world.pod_id}/connector-accounts",
        headers=_from_app(origin, world.bob),
        json={"account_id": bob_account, "visibility": "POD"},
    )

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert _code(refused) == APP_PERSON_ONLY, refused.text
    assert await _pod_accounts(db_session, world.pod_id) == []


async def test_an_app_cannot_let_its_viewers_account_into_sandboxes(
    scenario, db_session, browser
):
    world = await _world(scenario, db_session)
    origin = await _app(world, world.pod_id, world.editor)
    pod_account_id = await _alice_shares(world, db_session)
    path = f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}"

    toggled = await browser.patch(
        path,
        headers=_from_app(origin, world.alice),
        json={"allow_sandbox_use": True},
    )
    widened = await browser.patch(
        path,
        headers=_from_app(origin, world.alice),
        json={"visibility": "RESTRICTED", "share_with": [world.editor["id"]]},
    )

    for response in (toggled, widened):
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
        assert _code(response) == APP_PERSON_ONLY, response.text
    (row,) = await _pod_accounts(db_session, world.pod_id)
    assert row.allow_sandbox_use is False
    # Alice herself, outside the app, still decides.
    as_alice = await world.call(
        "PATCH", path, world.alice, json={"allow_sandbox_use": True}
    )
    assert as_alice.status_code == status.HTTP_200_OK, as_alice.text
    assert as_alice.json()["allow_sandbox_use"] is True


# --- the deprecated execute's pod, from the body ---


async def _execute(client: AsyncClient, world: World, headers, **body):
    org_id = world.scenario.org_id
    with patch(_EXECUTOR, new=_echo_the_credential):
        return await client.post(
            f"/organizations/{org_id}/connectors/{world.installation_name}"
            "/operations/echo/execute",
            headers=headers,
            json={"payload": {}, **body},
        )


async def _second_pod(world: World) -> str:
    created = await world.scenario.owner_client.post(
        "/pods",
        json={
            "organization_id": world.scenario.org_id,
            "name": f"Another pod {uuid4().hex[:8]}",
            "type": "HYBRID",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["id"]


async def test_an_app_naming_no_pod_runs_in_its_own(scenario, db_session, browser):
    """Before, no pod meant "a person outside any pod" and their own account;
    an app's page is inside its pod, so it runs through that pod's connector."""
    world = await _world(scenario, db_session)
    await _alice_shares(world, db_session)
    origin = await _app(world, world.pod_id, None)
    owner = world.scenario.owner_user

    ran = await _execute(browser, world, _from_app(origin, owner))

    assert ran.status_code == status.HTTP_200_OK, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}


async def test_an_app_naming_another_pod_in_the_body_is_refused(
    scenario, db_session, browser, monkeypatch
):
    """The pin checked only the path: an app in one pod named another in the
    body and ran that pod's SHARED connector as its viewer."""
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", True)
    world = await _world(scenario, db_session)
    await _alice_shares(world, db_session)
    elsewhere = await _second_pod(world)
    origin = await _app(world, elsewhere, None)
    owner = world.scenario.owner_user

    refused = await _execute(
        browser, world, _from_app(origin, owner), pod_id=world.pod_id
    )
    own = await _execute(browser, world, _from_app(origin, owner), pod_id=elsewhere)

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text
    assert _code(refused) == APP_ROUTE_NOT_ALLOWED, refused.text
    assert _code(own) != APP_ROUTE_NOT_ALLOWED, own.text
    # The owner, outside the app, still runs it in the pod they name.
    direct = await _execute(
        browser,
        world,
        {"Authorization": f"Bearer {owner['token']}"},
        pod_id=world.pod_id,
    )
    assert direct.json()["result"] == {"ran_as": "alice-key"}, direct.text


async def test_another_pod_in_the_body_is_served_until_the_pin_is_enforced(
    scenario, db_session, browser, monkeypatch
):
    """The same switch as a path's pod: observed, and served as before."""
    monkeypatch.setattr(settings, "app_pin_cross_pod_enforced", False)
    world = await _world(scenario, db_session)
    await _alice_shares(world, db_session)
    elsewhere = await _second_pod(world)
    origin = await _app(world, elsewhere, None)

    served = await _execute(
        browser,
        world,
        _from_app(origin, world.scenario.owner_user),
        pod_id=world.pod_id,
    )

    assert served.status_code == status.HTTP_200_OK, served.text
    assert served.json()["result"] == {"ran_as": "alice-key"}
