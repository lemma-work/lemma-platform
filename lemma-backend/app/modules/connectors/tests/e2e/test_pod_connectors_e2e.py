"""Pod connectors and pod accounts, end to end through the HTTP API.

The journeys of design §3: a member brings their own account to a per-member
connector and the pod records it; a member with none is handed a connect link;
an owner shares an account and the pod runs as it until they withdraw it;
nobody but the owner can put an account in front of anyone (audit H3); a member
who leaves takes their accounts with them (M11); an agent runs only the
connectors it was granted (M8, M9); and a connect link completes only in the
browser of the person it was made for (H2).

The provider is the OpenAPI executor, patched: it reports back which account's
credential reached it, which is the whole question every test here asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.events.member_left_work import register_member_left_work
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connect_request import ConnectRequest
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.models.pod_connector import PodAccount
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.tests.support.fake_auth_provider import FakeAuthProvider
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e

_EXECUTOR = (
    "app.modules.connectors.infrastructure.adapters.openapi_http_executor."
    "OpenApiHttpExecutor.execute"
)


async def _echo_the_credential(_self, *, third_party_credentials, **_kwargs):
    return {"ran_as": third_party_credentials.get("api_key")}


@dataclass
class World:
    scenario: E2EScenario
    auth_config_id: str
    installation_name: str
    alice: dict
    bob: dict
    viewer: dict
    editor: dict
    seats: dict[str, dict]

    @property
    def pod_id(self) -> str:
        return self.scenario.pod_id

    def headers(self, user: dict) -> dict[str, str]:
        return auth_headers(user)

    async def call(self, method: str, path: str, user: dict | None, **kwargs):
        client = self.scenario.async_client
        headers = self.headers(user) if user is not None else None
        if user is None:
            client = self.scenario.owner_client
        return await client.request(method, path, headers=headers, **kwargs)

    async def run(self, slug: str, user: dict | None, *, headers=None):
        with patch(_EXECUTOR, new=_echo_the_credential):
            return await self.scenario.async_client.post(
                f"/pods/{self.pod_id}/connectors/{slug}/operations/echo/execute",
                headers=headers or self.headers(user),
                json={"payload": {}},
            )

    async def declare(self, slug: str, mode: str, **extra):
        return await self.scenario.owner_client.post(
            f"/pods/{self.pod_id}/connectors",
            json={
                "slug": slug,
                "auth_config_id": self.auth_config_id,
                "mode": mode,
                **extra,
            },
        )


async def _world(scenario: E2EScenario, db_session) -> World:
    await scenario.create_org_with_pod(name_prefix="Pod Connectors")
    connector_id = f"crm-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="CRM",
            description="pod connectors e2e",
            kinds=[{"kind": "http", "auth_scheme": "API_KEY"}],
            is_active=True,
        )
    )
    await db_session.flush()
    install = AuthConfig(
        organization_id=UUID(scenario.org_id),
        connector_id=connector_id,
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=connector_id,
    )
    db_session.add(install)
    db_session.add(
        ConnectorOperation(
            id=f"{connector_id}:echo",
            connector_id=connector_id,
            name="echo",
            provider_operation_name="echo",
            description="Echo",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
    )
    await db_session.commit()
    seats: dict[str, dict] = {}
    users: dict[str, dict] = {}
    for name, role in (
        ("alice", "POD_USER"),
        ("bob", "POD_USER"),
        ("viewer", "POD_VIEWER"),
        ("editor", "POD_EDITOR"),
    ):
        users[name] = await scenario.create_user(f"podconn-{name}")
        seats[name] = await scenario.add_user_to_pod(user=users[name], role=role)
    return World(
        scenario=scenario,
        auth_config_id=str(install.id),
        installation_name=install.name,
        seats=seats,
        **users,
    )


async def _connect(db_session, world: World, user: dict, secret: str) -> str:
    """An org account for ``user``, as the connect flow would have left it."""
    install = await db_session.get(AuthConfig, UUID(world.auth_config_id))
    created = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user["id"]),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=True,
            credentials={"api_key": secret},
        )
    )
    await db_session.commit()
    return str(created.id)


async def _pod_accounts(db_session, pod_id: str) -> list[PodAccount]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(PodAccount).where(PodAccount.pod_id == UUID(pod_id))
    )
    return list(rows.scalars())


async def test_a_member_brings_their_own_account_and_the_pod_records_it(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201

    ran = await world.run("crm", world.alice)

    assert ran.status_code == status.HTTP_200_OK, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}
    # Picked up on first use, as hers alone.
    mine = await world.call(
        "GET", f"/pods/{world.pod_id}/connector-accounts", world.alice
    )
    assert mine.status_code == 200, mine.text
    [recorded] = mine.json()["items"]
    assert recorded["account_id"] == alice_account
    assert (recorded["visibility"], recorded["source"]) == ("PERSONAL", "AUTO")
    assert recorded["last_used_at"] is not None
    # And she can see where it is used.
    used = await world.call(
        "GET", f"/me/connectors/accounts/{alice_account}/pods", world.alice
    )
    assert used.status_code == 200, used.text
    assert [item["pod_id"] for item in used.json()["items"]] == [world.pod_id]
    # Bob sees none of it: a PERSONAL pod account is its owner's business.
    theirs = await world.call(
        "GET", f"/pods/{world.pod_id}/connector-accounts", world.bob
    )
    assert theirs.json()["items"] == []


async def test_a_member_with_no_account_is_handed_a_connect_link(scenario, db_session):
    world = await _world(scenario, db_session)
    assert (await world.declare("crm", "PER_MEMBER", required=True)).status_code == 201

    refused = await world.run("crm", world.bob)

    assert refused.status_code == status.HTTP_409_CONFLICT, refused.text
    body = refused.json()
    assert body["code"] == "CONNECTION_REQUIRED"
    assert body["details"]["pod_connector"] == "crm"
    assert f"/pods/{world.pod_id}/connections" in body["details"]["connect_url"]

    await _connect(db_session, world, world.bob, "bob-key")
    assert (await world.run("crm", world.bob)).json()["result"] == {"ran_as": "bob-key"}


async def test_a_viewer_runs_no_connector(scenario, db_session):
    """A viewer holds no `connector.use`. The org-level route checked only org
    membership, so anyone in the org ran any installation (audit M8)."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.viewer, "viewer-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201

    refused = await world.run("crm", world.viewer)

    assert refused.status_code == status.HTTP_403_FORBIDDEN, refused.text


async def test_a_shared_connector_runs_as_the_account_its_owner_shared(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": alice_account, "visibility": "POD"},
    )
    assert shared.status_code == 201, shared.text
    pod_account_id = shared.json()["id"]
    declared = await world.declare(
        "telemetry", "SHARED", shared_pod_account_id=pod_account_id
    )
    assert declared.status_code == 201, declared.text
    assert declared.json()["status"] == "READY"

    as_bob = await world.run("telemetry", world.bob)
    assert as_bob.status_code == 200, as_bob.text
    assert as_bob.json()["result"] == {"ran_as": "alice-key"}

    as_viewer = await world.run("telemetry", world.viewer)
    assert as_viewer.status_code == status.HTTP_403_FORBIDDEN, as_viewer.text

    withdrawn = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{pod_account_id}",
        world.alice,
        json={"visibility": "PERSONAL"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    after = await world.run("telemetry", world.bob)
    assert after.status_code == status.HTTP_409_CONFLICT, after.text
    assert after.json()["code"] == "CONNECTOR_NEEDS_ACCOUNT"
    # Bob is not the owner, but the fix -- someone sharing an account, an
    # editor pointing the connector at it -- is on the pod's connections page.
    assert after.json()["details"]["connect_url"].endswith(
        f"/pods/{world.pod_id}/connections?connector=telemetry"
    )


async def test_nobody_but_the_owner_can_put_an_account_in_front_of_others(
    scenario, db_session
):
    """Audit H3: a pod editor or admin put a colleague's account behind an
    agent or a role, and the pod ran as them without their knowing."""
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await world.run("crm", world.alice)).status_code == 200
    [personal] = await _pod_accounts(db_session, world.pod_id)

    # An editor cannot widen it...
    widened = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{personal.id}",
        world.editor,
        json={"visibility": "POD"},
    )
    assert widened.status_code == status.HTTP_403_FORBIDDEN, widened.text
    # ...nor add it to the pod as theirs...
    added = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.editor,
        json={"account_id": alice_account, "visibility": "POD"},
    )
    assert added.status_code == status.HTTP_404_NOT_FOUND, added.text
    # ...nor point a shared connector at it while it is still hers alone.
    pointed = await world.declare(
        "crm-shared", "SHARED", shared_pod_account_id=str(personal.id)
    )
    assert pointed.status_code == status.HTTP_400_BAD_REQUEST, pointed.text
    assert pointed.json()["details"]["reason"] == "account_not_shared"
    # And a pod admin -- the org owner here -- cannot grant it to a role.
    granted = await scenario.owner_client.put(
        f"/pods/{world.pod_id}/roles/POD_USER/permissions",
        json={
            "grants": [
                {
                    "resource_type": "connector_account",
                    "resource_name": str(personal.id),
                    "permission_ids": ["connector_account.use"],
                }
            ]
        },
    )
    assert granted.status_code == status.HTTP_403_FORBIDDEN, granted.text
    assert granted.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER"
    assert (await world.run("crm", world.bob)).json()["code"] == "CONNECTION_REQUIRED"

    # Even someone Alice shared it with -- who can use it -- cannot pass it on.
    owner_seat = await scenario.current_pod_member_id()
    restricted = await world.call(
        "PATCH",
        f"/pods/{world.pod_id}/connector-accounts/{personal.id}",
        world.alice,
        json={
            "visibility": "RESTRICTED",
            "share_with": [{"grantee_type": "POD_MEMBER", "grantee_id": owner_seat}],
        },
    )
    assert restricted.status_code == 200, restricted.text
    passed_on = await scenario.owner_client.put(
        f"/pods/{world.pod_id}/roles/POD_USER/permissions",
        json={
            "grants": [
                {
                    "resource_type": "connector_account",
                    "resource_name": str(personal.id),
                    "permission_ids": ["connector_account.use"],
                }
            ]
        },
    )
    assert passed_on.status_code == status.HTTP_403_FORBIDDEN, passed_on.text
    assert "connector_account.share" in passed_on.json()["message"]


async def test_an_account_is_shared_only_with_people_of_its_own_pod(
    scenario, db_session
):
    """A role or member id from somewhere else was stored as a grant that
    matched nobody and stayed behind. It is refused, and nothing is written."""
    from app.core.authorization.models import ResourcePermissionGrantModel

    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    assert (await world.run("crm", world.alice)).status_code == 200
    [personal] = await _pod_accounts(db_session, world.pod_id)

    for grantee_type in ("POD_MEMBER", "ROLE"):
        refused = await world.call(
            "PATCH",
            f"/pods/{world.pod_id}/connector-accounts/{personal.id}",
            world.alice,
            json={
                "visibility": "RESTRICTED",
                "share_with": [
                    {"grantee_type": grantee_type, "grantee_id": str(uuid4())}
                ],
            },
        )
        assert refused.status_code == status.HTTP_404_NOT_FOUND, refused.text

    stored = await db_session.scalars(
        select(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.resource_id == personal.id
        )
    )
    assert list(stored) == []


async def test_an_agent_runs_only_the_connectors_it_was_granted(scenario, db_session):
    """A grant names one pod connector, not a catalog id that reached every
    installation of it in the org (audit M9); and the agent runs as the person
    it acts for."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    agent = await scenario.create_agent()
    headers = await scenario.delegated_agent_headers(user=world.alice, agent=agent)

    ungranted = await world.run("crm", None, headers=headers)
    assert ungranted.status_code == status.HTTP_403_FORBIDDEN, ungranted.text

    await scenario.replace_agent_resource_grants(
        agent_name=agent["name"],
        grants=[
            {
                "resource_type": "connector",
                "resource_name": "crm",
                "permission_ids": ["connector.use"],
            }
        ],
    )
    granted = await world.run("crm", None, headers=headers)
    assert granted.status_code == 200, granted.text
    assert granted.json()["result"] == {"ran_as": "alice-key"}


@pytest.mark.drives_work
async def test_leaving_the_pod_takes_a_members_accounts_with_them(scenario, db_session):
    """Audit M11: a departed owner's account kept powering what it was pinned
    to. Now their pod accounts go when they do, and a shared connector on one
    needs another account."""
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": alice_account, "visibility": "POD"},
    )
    assert shared.status_code == 201, shared.text
    declared = await world.declare(
        "telemetry", "SHARED", shared_pod_account_id=shared.json()["id"]
    )
    assert declared.status_code == 201, declared.text

    left = await scenario.owner_client.delete(
        f"/pods/{world.pod_id}/members/{world.seats['alice']['pod_member_id']}"
    )
    assert left.status_code == 204, left.text
    registry = WorkRegistry()
    register_member_left_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )

    assert await _pod_accounts(db_session, world.pod_id) == []
    after = await world.run("telemetry", world.bob)
    assert after.json()["code"] == "CONNECTOR_NEEDS_ACCOUNT", after.text
    # The org account itself is untouched: leaving a pod is not leaving the org.
    assert await db_session.get(Account, UUID(alice_account)) is not None


async def test_a_connect_link_completes_only_in_its_own_persons_browser(
    scenario, db_session, monkeypatch
):
    """Audit H2: Mallory minted a connect link and sent it to Alice; Alice's
    consent landed her provider account on Mallory's Lemma account."""
    await scenario.create_org()
    connector_id = f"oauth-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="OAuth App",
            description="H2 e2e",
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
    fake = FakeAuthProvider(credentials=OAuthCredentials(access_token="victim"))
    for name in ("get_authorization_url", "exchange_code_for_credentials"):

        async def _delegate(self, *args, _bound=getattr(fake, name), **kwargs):
            return await _bound(*args, **kwargs)

        monkeypatch.setattr(LemmaAuthProvider, name, _delegate)

    # Mallory (the org owner here) mints the link.
    minted = await scenario.owner_client.post(
        f"/organizations/{scenario.org_id}/connectors/connect-requests",
        json={"connector_id": connector_id},
    )
    assert minted.status_code == 200, minted.text
    row = await db_session.get(ConnectRequest, UUID(minted.json()["id"]))
    state = (row.attributes or {})["state"]
    callback = "/connectors/connect-requests/oauth/callback"
    params = {"state": state, "code": "abc", "format": "json"}

    alice = await scenario.create_user("h2-alice")
    in_alices_browser = await scenario.async_client.get(
        callback, params=params, headers=auth_headers(alice)
    )
    assert in_alices_browser.status_code == 403, in_alices_browser.text
    assert in_alices_browser.json()["code"] == "CONNECT_REQUEST_BROWSER_MISMATCH"

    # A browser with no Lemma session at all: a client with an empty jar.
    async with AsyncClient(
        transport=scenario.async_client._transport,  # noqa: SLF001
        base_url=scenario.async_client.base_url,
    ) as browser:
        signed_out = await browser.get(callback, params=params)
    assert signed_out.status_code == 401, signed_out.text
    assert signed_out.json()["code"] == "CONNECT_REQUEST_SIGN_IN_REQUIRED"

    db_session.expire_all()
    accounts = await db_session.execute(
        select(Account).where(Account.connector_id == connector_id)
    )
    assert list(accounts.scalars()) == [], "nothing was connected by a refusal"

    # The person who minted it still completes it in their own browser: the
    # refusal spent neither the state nor the code.
    in_own_browser = await scenario.owner_client.get(callback, params=params)
    assert in_own_browser.status_code == 200, in_own_browser.text
    assert in_own_browser.json()["user_id"] == scenario.owner_user["id"]


# -- the deprecated org-level execute ---------------------------------------
#
# Code written before pod connectors names the installation and, sometimes, an
# account. It keeps running -- through the pod's connectors, with the same
# checks -- and every response says it is deprecated.


async def _run_installation(world: World, user: dict | None, *, headers=None, **body):
    org_id = world.scenario.org_id
    with patch(_EXECUTOR, new=_echo_the_credential):
        return await world.scenario.async_client.post(
            f"/organizations/{org_id}/connectors/{world.installation_name}"
            "/operations/echo/execute",
            headers=headers or world.headers(user),
            json={"payload": {}, **body},
        )


def _says_deprecated(response) -> None:
    assert response.headers["deprecation"] == "true"
    assert (
        "connector.operation.execute is deprecated"
        in (response.headers["x-lemma-deprecation"])
    )
    assert response.headers["sunset"]


async def test_the_deprecated_execute_runs_through_the_pods_connector(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    bob_account = await _connect(db_session, world, world.bob, "bob-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201

    ran = await _run_installation(world, world.alice, pod_id=world.pod_id)
    assert ran.status_code == 200, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}
    _says_deprecated(ran)

    # Her own account by id is hers to name.
    named = await _run_installation(
        world, world.alice, pod_id=world.pod_id, account_id=alice_account
    )
    assert named.json()["result"] == {"ran_as": "alice-key"}
    # A colleague's is not, which is what an account id used to allow.
    colleague = await _run_installation(
        world, world.alice, pod_id=world.pod_id, account_id=bob_account
    )
    assert colleague.status_code == status.HTTP_403_FORBIDDEN, colleague.text

    viewer = await _run_installation(world, world.viewer, pod_id=world.pod_id)
    assert viewer.status_code == status.HTTP_403_FORBIDDEN, viewer.text


async def test_the_deprecated_execute_reaches_a_shared_account_only_as_shared(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    alice_account = await _connect(db_session, world, world.alice, "alice-key")
    shared = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connector-accounts",
        world.alice,
        json={"account_id": alice_account, "visibility": "POD"},
    )
    assert shared.status_code == 201, shared.text
    declared = await world.declare(
        "telemetry", "SHARED", shared_pod_account_id=shared.json()["id"]
    )
    assert declared.status_code == 201, declared.text

    as_bob = await _run_installation(
        world, world.bob, pod_id=world.pod_id, account_id=alice_account
    )
    assert as_bob.status_code == 200, as_bob.text
    assert as_bob.json()["result"] == {"ran_as": "alice-key"}


async def test_an_agent_on_the_deprecated_execute_needs_its_grant(scenario, db_session):
    """The pod comes from the agent's token, and its grant on the pod
    connector is what lets it run -- as on the pod route."""
    world = await _world(scenario, db_session)
    await _connect(db_session, world, world.alice, "alice-key")
    assert (await world.declare("crm", "PER_MEMBER")).status_code == 201
    agent = await scenario.create_agent()
    headers = await scenario.delegated_agent_headers(user=world.alice, agent=agent)

    ungranted = await _run_installation(world, None, headers=headers)
    assert ungranted.status_code == status.HTTP_403_FORBIDDEN, ungranted.text

    await scenario.replace_agent_resource_grants(
        agent_name=agent["name"],
        grants=[
            {
                "resource_type": "connector",
                "resource_name": "crm",
                "permission_ids": ["connector.use"],
            }
        ],
    )
    granted = await _run_installation(world, None, headers=headers)
    assert granted.status_code == 200, granted.text
    assert granted.json()["result"] == {"ran_as": "alice-key"}


async def test_a_person_outside_any_pod_still_runs_their_own_account(
    scenario, db_session
):
    world = await _world(scenario, db_session)
    bob_account = await _connect(db_session, world, world.bob, "bob-key")
    await _connect(db_session, world, world.alice, "alice-key")

    ran = await _run_installation(world, world.alice)
    assert ran.status_code == 200, ran.text
    assert ran.json()["result"] == {"ran_as": "alice-key"}
    _says_deprecated(ran)

    theirs = await _run_installation(world, world.alice, account_id=bob_account)
    assert theirs.status_code == status.HTTP_403_FORBIDDEN, theirs.text
