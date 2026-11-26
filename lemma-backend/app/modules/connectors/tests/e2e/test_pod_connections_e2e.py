"""Connecting from a Lemma adds the app to it, and one read says where I stand.

Two promises the Connections page rests on:

- **connecting from a pod** -- through an OAuth connect request that names the
  pod, or the credential form -- leaves the pod with a per-member connector on
  that installation and the new account as the person's default there; and
  "use it here" (adding an account they already have) does the same;
- ``GET /pods/{pod_id}/connections`` answers, per connector and for whoever
  asks, whose account a call runs as and what they can do about it, in every
  mode -- read-only.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from starlette import status

import pytest

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connect_request import ConnectRequest
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.pod_connector import PodConnector
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.tests.e2e.test_app_connector_e2e import (
    _bound,
    _github_world,
)
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import (
    World,
    _connect,
    _pod_accounts,
    _world,
)
from app.modules.connectors.tests.support.fake_auth_provider import FakeAuthProvider
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e


async def _connections(world: World, user: dict | None) -> dict:
    response = await world.call("GET", f"/pods/{world.pod_id}/connections", user)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def _row(body: dict, slug: str) -> dict:
    (row,) = [item for item in body["items"] if item["slug"] == slug]
    return row


async def _declared(db_session, pod_id: str) -> list[PodConnector]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(PodConnector).where(PodConnector.pod_id == UUID(pod_id))
    )
    return list(rows.scalars())


# -- where I stand, per mode ---------------------------------------------------


async def test_a_per_member_connector_says_connect_use_existing_then_ready(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201

    nothing = _row(await _connections(world, world.alice), "crm")
    assert nothing["me"] == {"standing": "connect", "account": None, "existing": None}
    assert nothing["runs_as"]["kind"] == "me"
    assert nothing["auth_config_id"] == world.auth_config_id
    assert nothing["installation_name"] == world.installation_name
    assert nothing["coverage"] is None, "a member is not shown who connected"

    account_id = await _connect(db_session, world, world.alice, "alice-key")
    elsewhere = _row(await _connections(world, world.alice), "crm")
    assert elsewhere["me"]["standing"] == "use_existing"
    assert elsewhere["me"]["existing"]["account_id"] == account_id

    used = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": account_id},
    )
    assert used.status_code == status.HTTP_201_CREATED, used.text
    assert used.json()["is_member_default"] is True
    ready = _row(await _connections(world, world.alice), "crm")
    assert ready["me"]["standing"] == "ready"
    assert ready["me"]["account"]["id"] == account_id
    assert ready["me"]["account"]["pod_account_id"] == used.json()["id"]

    viewer = _row(await _connections(world, world.viewer), "crm")
    assert viewer["me"]["standing"] == "not_allowed"
    editor = await _connections(world, world.editor)
    assert editor["may_manage"] is True
    assert _row(editor, "crm")["coverage"]["connected"] >= 1


async def test_a_shared_connector_waits_for_an_account_then_runs_as_its_owner(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    assert (await world.declare("crm", "SHARED")).status_code == 201

    waiting = _row(await _connections(world, world.bob), "crm")
    assert waiting["status"] == "NEEDS_ACCOUNT"
    assert waiting["me"]["standing"] == "waiting"
    assert waiting["runs_as"]["kind"] == "nobody"

    account_id = await _connect(db_session, world, world.alice, "alice-key")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": account_id, "visibility": "POD"},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    pointed = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connectors/crm",
        None,
        json={"shared_pod_account_id": shared.json()["id"]},
    )
    assert pointed.status_code == status.HTTP_200_OK, pointed.text

    for_bob = _row(await _connections(world, world.bob), "crm")
    assert for_bob["me"]["standing"] == "ready"
    assert for_bob["runs_as"]["kind"] == "person"
    assert for_bob["runs_as"]["owner_user_id"] == world.alice["id"]
    assert for_bob["me"]["account"] is None
    for_alice = _row(await _connections(world, world.alice), "crm")
    assert for_alice["me"]["account"]["id"] == account_id


async def test_the_bot_is_ready_when_installed_and_waits_when_not(scenario, db_session):
    world = await _github_world(scenario, db_session)
    declared = await world.declare_bot()
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    path = f"/pods/{world.pod_id}/connections"
    client = world.scenario.async_client

    before = await client.get(path, headers=auth_headers(world.alice))
    assert before.status_code == status.HTTP_200_OK, before.text
    assert before.json()["github_bot"] is None
    row = _row(before.json(), "github")
    assert (row["status"], row["me"]["standing"]) == ("NEEDS_INSTALLATION", "waiting")

    login = f"octo-{uuid4().hex[:6]}"
    await _bound(db_session, world, login=login)
    after = (await client.get(path, headers=auth_headers(world.alice))).json()
    assert after["github_bot"] == {
        "installations": [{"login": login, "repository_selection": "selected"}]
    }
    row = _row(after, "github")
    assert row["mode"] == "APP"
    assert (row["status"], row["me"]["standing"]) == ("READY", "ready")
    assert row["runs_as"] == {
        "kind": "app",
        "label": login,
        "owner_user_id": None,
        "owner_name": None,
    }
    viewer = (await client.get(path, headers=auth_headers(world.viewer))).json()
    assert _row(viewer, "github")["me"]["standing"] == "not_allowed"


async def test_reading_connections_declares_nothing(scenario, db_session):
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")

    body = await _connections(world, world.alice)

    assert body["items"] == []
    assert await _declared(db_session, world.pod_id) == []
    assert await _pod_accounts(db_session, world.pod_id) == []


# -- changing a connector's mode, and "mine, for everyone" ---------------------


async def test_a_connector_can_become_the_bot_and_back(scenario, db_session):
    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login=f"octo-{uuid4().hex[:6]}")
    declared = await world.scenario.owner_client.post(
        f"/pods/{world.pod_id}/connectors",
        json={"slug": "github", "auth_config_id": world.auth_config_id},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    path = f"/pods/{world.pod_id}/connectors/github"

    as_member = await world.scenario.async_client.patch(
        path, headers=auth_headers(world.alice), json={"mode": "APP"}
    )
    to_app = await world.scenario.owner_client.patch(path, json={"mode": "APP"})
    back = await world.scenario.owner_client.patch(path, json={"mode": "PER_MEMBER"})

    assert as_member.status_code == status.HTTP_403_FORBIDDEN, as_member.text
    assert to_app.status_code == status.HTTP_200_OK, to_app.text
    assert (to_app.json()["mode"], to_app.json()["status"]) == ("APP", "READY")
    assert back.json()["mode"] == "PER_MEMBER", back.text


async def test_sharing_mine_for_everyone_switches_the_pods_connector(
    scenario, db_session
):
    """One call: the account shared with the pod, and the per-member
    connector the pod already uses running as it."""
    world = await _world(scenario, db_session)
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    account_id = await _connect(db_session, world, world.editor, "editor-key")
    added = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.editor,
        json={"account_id": account_id},
    )
    assert added.status_code == status.HTTP_201_CREATED, added.text

    switched = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts/{added.json()['id']}/share-as",
        world.editor,
        json={"slug": "crm"},
    )

    assert switched.status_code == status.HTTP_200_OK, switched.text
    assert switched.json()["mode"] == "SHARED"
    assert switched.json()["shared_pod_account_id"] == added.json()["id"]
    assert switched.json()["status"] == "READY"


# -- connecting from a pod -----------------------------------------------------


async def test_the_credential_form_from_a_pod_adds_the_app_to_it(scenario, db_session):
    world = await _world(scenario, db_session)
    org = world.scenario.org_id

    outsider_pod = await world.call(
        "POST",
        f"/organizations/{org}/connectors/accounts",
        world.alice,
        json={
            "auth_config_id": world.auth_config_id,
            "credentials": {"api_key": "x"},
            "pod_id": str(uuid4()),
        },
    )
    connected = await world.call(
        "POST",
        f"/organizations/{org}/connectors/accounts",
        world.alice,
        json={
            "auth_config_id": world.auth_config_id,
            "credentials": {"api_key": "alice-key"},
            "pod_id": world.pod_id,
        },
    )

    assert outsider_pod.status_code == status.HTTP_403_FORBIDDEN, outsider_pod.text
    assert outsider_pod.json()["code"] == "CONNECT_REQUEST_POD_NOT_MEMBER"
    assert connected.status_code == status.HTTP_200_OK, connected.text
    (connector,) = await _declared(db_session, world.pod_id)
    assert (connector.mode, connector.source) == ("PER_MEMBER", "AUTO")
    slug = connector.slug
    (mine,) = await _pod_accounts(db_session, world.pod_id)
    assert str(mine.account_id) == connected.json()["id"]
    assert mine.is_member_default is True
    row = _row(await _connections(world, world.alice), slug)
    assert row["me"]["standing"] == "ready"


async def test_an_oauth_connect_from_a_pod_adds_the_app_to_it(
    scenario, db_session, monkeypatch
):
    await scenario.create_org_with_pod(name_prefix="Connect From Pod")
    connector_id = f"oauth-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="OAuth App",
            kinds=[
                {
                    "kind": "http",
                    "auth_scheme": "OAUTH2",
                    "supports_org_custom_oauth": True,
                    "oauth2_defaults": {
                        "authorization_url": "https://mock.example.com/auth",
                        "token_url": "https://mock.example.com/token",
                    },
                }
            ],
            is_active=True,
        )
    )
    await db_session.commit()
    installed = await scenario.owner_client.post(
        f"/organizations/{scenario.org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "kind": "http",
            "config_source": "ORG_CUSTOM",
            "config": {
                "oauth2_credentials": {"client_id": "id", "client_secret": "secret"}
            },
        },
    )
    assert installed.status_code == 200, installed.text
    fake = FakeAuthProvider(credentials=OAuthCredentials(access_token="mine"))
    for name in ("get_authorization_url", "exchange_code_for_credentials"):

        async def _delegate(self, *args, _bound=getattr(fake, name), **kwargs):
            return await _bound(*args, **kwargs)

        monkeypatch.setattr(LemmaAuthProvider, name, _delegate)

    minted = await scenario.owner_client.post(
        f"/organizations/{scenario.org_id}/connectors/connect-requests",
        json={"connector_id": connector_id, "pod_id": scenario.pod_id},
    )
    assert minted.status_code == 200, minted.text
    row = await db_session.get(ConnectRequest, UUID(minted.json()["id"]))
    assert (row.attributes or {})["pod_id"] == scenario.pod_id
    completed = await scenario.owner_client.get(
        "/connectors/connect-requests/oauth/callback",
        params={"state": row.attributes["state"], "code": "abc", "format": "json"},
    )
    assert completed.status_code == 200, completed.text

    (connector,) = await _declared(db_session, scenario.pod_id)
    install = await db_session.get(AuthConfig, connector.auth_config_id)
    assert install is not None and install.connector_id == connector_id
    assert connector.mode == "PER_MEMBER"
    (mine,) = await _pod_accounts(db_session, scenario.pod_id)
    assert str(mine.account_id) == completed.json()["id"]
    assert mine.is_member_default is True
