"""An APP pod connector, end to end: the organization's GitHub bot.

Declared by anyone who may manage the pod's connectors -- from an app's page
too, since it puts nobody's credential in play -- and used by anyone with
``connector.use``: every call is the App, on the installation the call's
repository owner has it on. Only installations proven to the organization and
on a GitHub organization count. Everything that needs an account refuses it,
and the deprecated installation route never lands on it.

GitHub is stood in for at two points only: minting an installation token
(``github_presenter.installation_token``) and the operation's HTTP call (the
OpenAPI executor), which reports back the token that reached it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.tests.e2e.test_pod_connectors_e2e import _EXECUTOR
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e

APP_DOMAIN = "apps.appconnector.test"
_GITHUB_MINT = (
    "app.modules.connectors.services.execution.github_presenter.installation_token"
)


async def _echo_token(_self, *, third_party_credentials, **_kwargs):
    return {"token": third_party_credentials.get("access_token")}


async def _minted(installation_id: str) -> str:
    return f"ghs_{installation_id}"


@pytest.fixture(autouse=True)
def _github_stood_in(monkeypatch):
    # GitHub's token endpoint, which is the network: the presenter itself runs.
    monkeypatch.setattr(_GITHUB_MINT, _minted)
    monkeypatch.setattr(settings, "app_base_domain", APP_DOMAIN)


def _installation_id() -> str:
    return str(random.randint(10**9, 10**10 - 1))


@dataclass
class GitHubWorld:
    scenario: E2EScenario
    auth_config_id: str
    installation_name: str
    #: An operation GitHub lets the App make, and one it keeps for people.
    app_operation: str
    person_operation: str
    alice: dict
    editor: dict
    viewer: dict

    @property
    def pod_id(self) -> str:
        return self.scenario.pod_id

    @property
    def org_id(self) -> UUID:
        return UUID(self.scenario.org_id)

    async def declare_bot(self, **extra: object):
        return await self.scenario.owner_client.post(
            f"/pods/{self.pod_id}/connectors",
            json={"auth_config_id": self.auth_config_id, "mode": "APP", **extra},
        )

    async def run(self, user: dict, operation: str, payload: dict, **body):
        with patch(_EXECUTOR, new=_echo_token):
            return await self.scenario.async_client.post(
                f"/pods/{self.pod_id}/connectors/github/operations/{operation}/execute",
                headers=auth_headers(user),
                json={"payload": payload, **body},
            )


async def _github_world(scenario: E2EScenario, db_session) -> GitHubWorld:
    await scenario.create_org_with_pod(name_prefix="App Connector")
    if await db_session.get(Connector, "github") is None:
        db_session.add(
            Connector(
                id="github",
                title="GitHub",
                kinds=[{"kind": "http", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
    install = AuthConfig(
        organization_id=UUID(scenario.org_id),
        connector_id="github",
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=f"github-{uuid4().hex[:8]}",
    )
    db_session.add(install)
    suffix = uuid4().hex[:8]
    app_operation, person_operation = f"issues_probe_{suffix}", f"gists_probe_{suffix}"
    for name, token_kind in (
        (app_operation, "installation_ok"),
        (person_operation, "user_only"),
    ):
        db_session.add(
            ConnectorOperation(
                id=f"github:http:{name}",
                connector_id="github",
                kind="http",
                name=name,
                provider_operation_name=name,
                description=name,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                execution={"github_token_kind": token_kind},
            )
        )
    await db_session.commit()
    users: dict[str, dict] = {}
    for name, role in (
        ("alice", "POD_USER"),
        ("editor", "POD_EDITOR"),
        ("viewer", "POD_VIEWER"),
    ):
        users[name] = await scenario.create_user(f"appconn-{name}")
        await scenario.add_user_to_pod(user=users[name], role=role)
    return GitHubWorld(
        scenario=scenario,
        auth_config_id=str(install.id),
        installation_name=install.name,
        app_operation=app_operation,
        person_operation=person_operation,
        **users,
    )


async def _bound(
    db_session,
    world: GitHubWorld,
    *,
    login: str,
    account_type: str = "Organization",
    proven: bool = True,
) -> str:
    """An App installation bound to the world's organization, already checked."""
    installation_id = _installation_id()
    db_session.add(
        ConnectorInstallation(
            connector_id="github",
            external_id=installation_id,
            organization_id=world.org_id,
            proven_at=datetime.now(UTC) if proven else None,
            details={
                "account": {
                    "login": login,
                    "type": account_type,
                    "repository_selection": "selected",
                    "checked_at": datetime.now(UTC).isoformat(),
                }
            },
        )
    )
    await db_session.commit()
    return installation_id


def _from_app(origin: str, user: dict) -> dict[str, str]:
    return {
        "Cookie": f"sAccessToken={user['token']}",
        "rid": "session",
        "Origin": origin,
    }


# -- declaring the bot ---------------------------------------------------------


async def test_an_editor_adds_the_bot_from_an_apps_page(scenario, db_session, test_app):
    """It puts nobody's credential in play, so it is not a person-only act: a
    plugin sets itself up from inside its app. Managing connectors is still
    what it takes."""
    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login=f"octo-{uuid4().hex[:6]}")
    made = await world.scenario.owner_client.post(
        f"/pods/{world.pod_id}/apps",
        json={"name": "setup_app", "public_slug": f"setup-{uuid4().hex[:6]}"},
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    origin = f"https://{made.json()['public_slug']}.{APP_DOMAIN}"

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as browser:
        as_member = await browser.post(
            f"/pods/{world.pod_id}/connectors",
            headers=_from_app(origin, world.alice),
            json={"auth_config_id": world.auth_config_id, "mode": "APP"},
        )
        as_editor = await browser.post(
            f"/pods/{world.pod_id}/connectors",
            headers=_from_app(origin, world.editor),
            json={"auth_config_id": world.auth_config_id, "mode": "APP"},
        )

    assert as_member.status_code == status.HTTP_403_FORBIDDEN, as_member.text
    assert as_editor.status_code == status.HTTP_201_CREATED, as_editor.text
    declared = as_editor.json()
    assert (declared["slug"], declared["mode"]) == ("github", "APP")
    assert declared["status"] == "READY"
    assert declared["shared_pod_account_id"] is None


async def test_only_github_can_be_the_bot(scenario, db_session):
    world = await _github_world(scenario, db_session)
    other = AuthConfig(
        organization_id=world.org_id,
        connector_id=f"crm-{uuid4().hex[:6]}",
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=f"crm-{uuid4().hex[:6]}",
    )
    db_session.add(
        Connector(id=other.connector_id, title="CRM", kinds=[{"kind": "http"}])
    )
    db_session.add(other)
    await db_session.commit()

    refused = await world.scenario.owner_client.post(
        f"/pods/{world.pod_id}/connectors",
        json={"auth_config_id": str(other.id), "mode": "APP"},
    )

    assert refused.json()["code"] == "POD_CONNECTOR_INVALID", refused.text
    assert refused.json()["details"]["reason"] == "app_mode_github_only"


# -- calling as the bot --------------------------------------------------------


async def test_a_member_calls_as_the_bot_on_the_owners_installation(
    scenario, db_session
):
    """Whatever ``act_as`` says, and asked by a person rather than a function:
    on an APP connector every call is the App, and nobody's token is used."""
    world = await _github_world(scenario, db_session)
    login = f"octo-{uuid4().hex[:6]}"
    octo = await _bound(db_session, world, login=login)
    other = await _bound(db_session, world, login=f"acme-{uuid4().hex[:6]}")
    assert (await world.declare_bot()).status_code == status.HTTP_201_CREATED

    ran = await world.run(
        world.alice,
        world.app_operation,
        {"repository": f"{login.upper()}/widgets"},
        act_as="app",
    )
    plain = await world.run(
        world.alice, world.app_operation, {"owner": login}, act_as="user"
    )

    assert ran.status_code == status.HTTP_200_OK, ran.text
    assert ran.json()["result"] == {"token": f"ghs_{octo}"}
    assert plain.json()["result"] == {"token": f"ghs_{octo}"}
    assert other != octo


async def test_the_bot_refuses_what_it_cannot_decide_or_do(scenario, db_session):
    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login=f"octo-{uuid4().hex[:6]}")
    await _bound(db_session, world, login=f"acme-{uuid4().hex[:6]}")
    assert (await world.declare_bot()).status_code == status.HTTP_201_CREATED

    ambiguous = await world.run(world.alice, world.app_operation, {})
    elsewhere = await world.run(world.alice, world.app_operation, {"owner": "globex"})
    person_only = await world.run(world.alice, world.person_operation, {})
    viewer = await world.run(world.viewer, world.app_operation, {"owner": "globex"})

    assert ambiguous.status_code == status.HTTP_409_CONFLICT, ambiguous.text
    assert ambiguous.json()["code"] == "APP_INSTALLATION_AMBIGUOUS"
    assert len(ambiguous.json()["details"]["logins"]) == 2
    assert elsewhere.json()["code"] == "APP_NOT_INSTALLED", elsewhere.text
    assert elsewhere.json()["details"]["owner"] == "globex"
    assert person_only.json()["code"] == "APP_IDENTITY_UNAVAILABLE", person_only.text
    assert person_only.json()["details"]["reason"] == "user_only_operation"
    assert viewer.status_code == status.HTTP_403_FORBIDDEN, viewer.text


async def test_listing_the_bots_operations_leaves_out_person_only_ones(
    scenario, db_session
):
    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login=f"octo-{uuid4().hex[:6]}")
    assert (await world.declare_bot()).status_code == status.HTTP_201_CREATED

    listed = await world.scenario.async_client.get(
        f"/pods/{world.pod_id}/connectors/github/operations",
        headers=auth_headers(world.alice),
        params={"query": "probe"},
    )

    assert listed.status_code == status.HTTP_200_OK, listed.text
    names = {item["name"] for item in listed.json()["items"]}
    assert world.app_operation in names
    assert world.person_operation not in names


async def test_unproven_and_personal_installations_do_not_count(scenario, db_session):
    """An unproven binding may have been squatted on; a personal installation
    would hand one person's own repositories to the whole Lemma."""
    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login="squatted", proven=False)
    await _bound(db_session, world, login="ada-lovelace", account_type="User")
    declared = await world.declare_bot()
    assert declared.status_code == status.HTTP_201_CREATED, declared.text

    assert declared.json()["status"] == "NEEDS_INSTALLATION"
    refused = await world.run(world.alice, world.app_operation, {"owner": "ada-lovelace"})
    assert refused.json()["code"] == "APP_NOT_INSTALLED", refused.text


# -- nothing that needs an account gets the bot --------------------------------


async def test_what_needs_an_account_refuses_the_bot_and_the_old_route_avoids_it(
    scenario, db_session
):
    """A surface, the sandbox's git, bundle publishing all ask the contract for
    the account; the deprecated installation route serves a person's own."""
    from app.core.authorization.factory import create_authorization_data_service
    from app.modules.connectors.contracts.pod_connectors import (
        AppConnectorUnsupportedError,
        resolve_pod_connector,
    )
    from app.modules.connectors.services.pod_connector_composition import (
        build_pod_connector_resolver,
    )

    world = await _github_world(scenario, db_session)
    await _bound(db_session, world, login=f"octo-{uuid4().hex[:6]}")
    assert (await world.declare_bot()).status_code == status.HTTP_201_CREATED
    db_session.add(
        Account(
            user_id=UUID(world.alice["id"]),
            organization_id=world.org_id,
            connector_id="github",
            auth_config_id=UUID(world.auth_config_id),
            credentials={"access_token": "gho_alice"},
            is_default=True,
        )
    )
    await db_session.commit()
    uow = SqlAlchemyUnitOfWork(db_session)
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=UUID(world.alice["id"]), pod_id=UUID(world.pod_id)
    )

    with pytest.raises(AppConnectorUnsupportedError):
        await resolve_pod_connector(uow, ctx, pod_id=UUID(world.pod_id), slug="github")
    resolver = build_pod_connector_resolver(uow)
    auth_config = await resolver.auth_configs.get(UUID(world.auth_config_id))
    assert auth_config is not None
    served = await resolver.serve_installation(
        ctx, pod_id=UUID(world.pod_id), auth_config=auth_config, account_id=None
    )

    assert served.pod_connector.mode.value == "PER_MEMBER"
    assert served.account.credentials is not None
