"""Lemma creating a teammate's own Slack app, against the real database,
routes, cipher and connectors module, with Slack faked at ``SlackAppCalls``.

The double does what matters about Slack: a refresh token works once, a second
use is refused, and every app it makes gets credentials of its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.api.controllers.slack_app_creation_controller import (
    get_slack_app_calls,
)
from app.modules.agent_surfaces.domain.errors import (
    SlackConfigurationTokenRefusedError,
)
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    CreatedSlackApp,
    RotatedConfigurationToken,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    SlackConfigurationTokenRepository,
)
from app.modules.agent_surfaces.services import slack_app_provisioning as provisioning
from app.modules.agent_surfaces.services import slack_app_sweep
from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector
from app.modules.test_support.e2e_authz import (
    auth_headers,
    create_role_visibility_context,
    signup_user,
)

pytestmark = pytest.mark.e2e

TEAM = "T0E2E"
SLACK_KIND: dict[str, object] = {
    "kind": "http",
    "auth_scheme": "OAUTH2",
    "supports_org_custom_oauth": True,
    "oauth2_defaults": {
        "authorization_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
    },
}
PASTED = "xoxe-1-pasted"


@dataclass
class FakeSlack:
    valid: set[str] = field(default_factory=lambda: {PASTED})
    rotated: list[str] = field(default_factory=list)
    created: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    rotate_delay: float = 0.0
    refuse_delete: bool = False
    n: int = 0

    async def rotate(self, refresh_token: SecretStr) -> RotatedConfigurationToken:
        value = refresh_token.get_secret_value()
        self.rotated.append(value)
        await asyncio.sleep(self.rotate_delay)
        if value not in self.valid:
            raise SlackConfigurationTokenRefusedError("invalid_refresh_token")
        self.valid.discard(value)  # single-use
        self.n += 1
        refresh = f"xoxe-1-refresh-{self.n}"
        self.valid.add(refresh)
        return RotatedConfigurationToken(
            access_token=SecretStr(f"xoxe.xoxp-access-{self.n}"),
            refresh_token=SecretStr(refresh),
            team_id=TEAM,
            slack_user_id="U0ADMIN",
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )

    async def workspace_name(self, access_token: SecretStr) -> str | None:
        return "Acme"

    async def create_app(
        self, access_token: SecretStr, manifest: dict[str, object]
    ) -> CreatedSlackApp:
        display = manifest["display_information"]
        assert isinstance(display, dict)
        app_id = f"A0E2E{len(self.created) + 1}"
        self.created.append((app_id, str(display["name"])))
        return CreatedSlackApp(
            app_id=app_id,
            client_id=f"cid-{app_id}",
            client_secret=SecretStr(f"csecret-{app_id}"),
            signing_secret=SecretStr(f"ssecret-{app_id}"),
        )

    async def delete_app(self, access_token: SecretStr, app_id: str) -> bool:
        if self.refuse_delete:
            return False
        self.deleted.append(app_id)
        return True

    def calls(self) -> provisioning.SlackAppCalls:
        return provisioning.SlackAppCalls(
            rotate=self.rotate,
            workspace_name=self.workspace_name,
            create_app=self.create_app,
            delete_app=self.delete_app,
        )


@pytest.fixture
async def fake(test_app, db_session) -> FakeSlack:
    connector = await _ensure_connector(db_session, "slack")
    # As the catalog declares Slack: an org may bring its own app.
    connector.kinds = [SLACK_KIND]
    await db_session.commit()
    slack = FakeSlack()
    test_app.dependency_overrides[get_slack_app_calls] = slack.calls
    yield slack
    test_app.dependency_overrides.pop(get_slack_app_calls, None)


async def _pod(client: AsyncClient, org_id: str, **headers: str) -> str:
    created = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Slack apps {uuid4().hex[:8]}",
            "description": "Slack app creation e2e",
            "type": "HYBRID",
        },
        headers=headers or None,
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    return created.json()["id"]


def _base(pod_id: str) -> str:
    return f"/pods/{pod_id}/surface-setup/slack"


async def _save(client: AsyncClient, pod_id: str, token: str = PASTED):
    return await client.put(
        f"{_base(pod_id)}/app-creation", json={"refresh_token": token}
    )


async def _scalar(db_session, sql: str, **params: object) -> object:
    return (await db_session.execute(text(sql), params)).scalar()


def _uows(db_manager) -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(db_manager.session_factory)


async def test_save_then_create_makes_the_orgs_own_install_and_a_retry_reuses_it(
    authenticated_client: AsyncClient, fixed_test_org, db_session, fake: FakeSlack
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])

    saved = await _save(authenticated_client, pod_id)
    assert saved.status_code == status.HTTP_200_OK, saved.text
    listed = await authenticated_client.get(f"{_base(pod_id)}/app-creation")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    body = listed.json()
    assert body["can_manage"] is True
    assert [
        (i["team_id"], i["team_name"], i["needs_new_token"]) for i in body["items"]
    ] == [(TEAM, "Acme", False)]
    assert "xoxe" not in listed.text and "xoxe" not in saved.text
    # The pasted token is spent; the pair it rotated to is kept, encrypted.
    stored = await _scalar(
        db_session,
        "SELECT access_token || refresh_token FROM slack_configuration_tokens "
        "WHERE team_id = :team",
        team=TEAM,
    )
    assert isinstance(stored, str) and "xoxe" not in stored

    created = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}
    )
    assert created.status_code == status.HTTP_200_OK, created.text
    made = created.json()
    assert made["reused"] is False
    assert fake.created[0][0] == made["app_id"]
    install = (
        await db_session.execute(
            text(
                "SELECT config_source, is_default, config::text "
                "FROM auth_configs WHERE id = :id"
            ),
            {"id": made["auth_config_id"]},
        )
    ).one()
    assert install[0] == "ORG_CUSTOM"
    assert install[1] is False, "a teammate's app is never the org's default"
    assert f"csecret-{made['app_id']}" not in install[2], "stored encrypted"
    assert (
        await _scalar(
            db_session,
            "SELECT source FROM slack_pending_app_bindings WHERE auth_config_id = :id",
            id=made["auth_config_id"],
        )
        == "WEB"
    )

    # The reach sheet starts the authorisation after the create; when that
    # fails and the person tries again, the same app comes back.
    again = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}
    )
    assert again.status_code == status.HTTP_200_OK, again.text
    assert again.json() == {**made, "reused": True}
    assert len(fake.created) == 1

    forgot = await authenticated_client.delete(f"{_base(pod_id)}/app-creation/{TEAM}")
    assert forgot.status_code == status.HTTP_204_NO_CONTENT, forgot.text
    after = await authenticated_client.get(f"{_base(pod_id)}/app-creation")
    assert after.json()["items"] == []


async def test_the_app_is_named_after_the_agent_the_server_reads(
    authenticated_client: AsyncClient, fixed_test_org, fake: FakeSlack
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "support_desk", "instruction": "Answer briefly."},
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    other_pod = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)

    made = await authenticated_client.post(
        f"{_base(pod_id)}/apps",
        json={"team_id": TEAM, "agent_id": agent.json()["id"], "app_name": "Evil"},
    )
    elsewhere = await authenticated_client.post(
        f"{_base(other_pod)}/apps",
        json={"team_id": TEAM, "agent_id": agent.json()["id"]},
    )

    assert made.status_code == status.HTTP_200_OK, made.text
    assert fake.created == [(made.json()["app_id"], "Support Desk")]
    assert elsewhere.status_code == status.HTTP_404_NOT_FOUND, elsewhere.text


async def test_a_member_who_cannot_manage_installs_sees_no_workspace_and_cannot_act(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fake: FakeSlack,
):
    ctx = await create_role_visibility_context(
        authenticated_client, async_client, fixed_test_org, pod_name_prefix="slack-apps"
    )
    pod_id = ctx["pod_id"]
    await _save(authenticated_client, pod_id)
    rotations = len(fake.rotated)
    editor = ctx["editor_headers"]  # pod editor, organization member

    listed = await async_client.get(f"{_base(pod_id)}/app-creation", headers=editor)
    saved = await async_client.put(
        f"{_base(pod_id)}/app-creation", json={"refresh_token": "x"}, headers=editor
    )
    created = await async_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}, headers=editor
    )

    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert listed.json() == {"can_manage": False, "items": []}
    assert "Acme" not in listed.text
    assert saved.status_code == status.HTTP_403_FORBIDDEN, saved.text
    assert created.status_code == status.HTTP_403_FORBIDDEN, created.text
    assert len(fake.rotated) == rotations and fake.created == []


async def test_another_organization_neither_sees_nor_uses_the_workspace(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fake: FakeSlack,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)
    stranger = await signup_user(async_client, "slack-apps-stranger")
    headers = auth_headers(stranger)
    org = await async_client.post(
        "/organizations", json={"name": f"Other {uuid4().hex[:6]}"}, headers=headers
    )
    assert org.status_code == status.HTTP_201_CREATED, org.text
    their_pod = await _pod(async_client, org.json()["id"], **headers)

    listed = await async_client.get(f"{_base(their_pod)}/app-creation", headers=headers)
    created = await async_client.post(
        f"{_base(their_pod)}/apps", json={"team_id": TEAM}, headers=headers
    )

    assert listed.json() == {"can_manage": True, "items": []}
    assert created.status_code == status.HTTP_409_CONFLICT, created.text
    assert created.json()["code"] == "SLACK_APP_CREATION_NOT_SET_UP"
    assert fake.created == []


async def test_an_idempotency_key_answers_with_its_app_after_the_row_is_gone(
    authenticated_client: AsyncClient, fixed_test_org, db_session, fake: FakeSlack
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)
    key = {"Idempotency-Key": uuid4().hex}

    first = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}, headers=key
    )
    # As if authorised and bound: the pending row is gone.
    await db_session.execute(text("DELETE FROM slack_pending_app_bindings"))
    await db_session.commit()
    retried = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}, headers=key
    )

    assert first.status_code == retried.status_code == status.HTTP_200_OK
    assert retried.json()["app_id"] == first.json()["app_id"]
    assert len(fake.created) == 1


async def test_two_creates_near_expiry_rotate_once_and_both_succeed(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session,
    db_manager,
    fake: FakeSlack,
):
    """A refresh token works once. Two creates that both find the access token
    expired must not both spend it: one rotates, the other waits for its pair."""
    pod_a = await _pod(authenticated_client, fixed_test_org["id"])
    pod_b = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_a)
    await db_session.execute(
        text(
            "UPDATE slack_configuration_tokens "
            "SET access_expires_at = now() - interval '1 minute'"
        )
    )
    await db_session.commit()
    fake.rotate_delay = 0.4
    fake.rotated.clear()

    async def create(pod_id: str) -> provisioning.ProvisionedSlackApp:
        async with _uows(db_manager)() as uow:
            return await provisioning.SlackAppProvisioner(
                uow, calls=fake.calls(), lease_wait_seconds=0.2
            ).create_app(
                organization_id=UUID(fixed_test_org["id"]),
                pod_id=UUID(pod_id),
                agent_id=UUID(pod_id),
                user_id=UUID(fixed_test_user["id"]),
                team_id=TEAM,
            )

    made = await asyncio.gather(create(pod_a), create(pod_b))

    assert len(fake.rotated) == 1, fake.rotated
    assert {app.app_id for app in made} == {app_id for app_id, _ in fake.created}
    assert len(made) == len(fake.created) == 2
    row = (
        await db_session.execute(
            text(
                "SELECT generation, rotation_claimed_until, refused_at "
                "FROM slack_configuration_tokens"
            )
        )
    ).one()
    assert row == (1, None, None), "one paste, one rotation; lease given back"


async def test_a_rotation_cancelled_after_slack_answers_still_keeps_the_pair(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
    db_manager,
    fake: FakeSlack,
    monkeypatch,
):
    """Slack has spent the old refresh token once it answers. A request
    cancelled while the new pair is being stored must still store it, or the
    next use sends the spent token and the org has to paste a new one."""
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)
    await db_session.execute(
        text(
            "UPDATE slack_configuration_tokens "
            "SET access_expires_at = now() - interval '1 minute'"
        )
    )
    await db_session.commit()
    fake.rotated.clear()  # saving rotated the paste once already
    storing = asyncio.Event()
    store = SlackConfigurationTokenRepository.store_rotation

    async def slow_store(self, *args, **kwargs):
        stored = await store(self, *args, **kwargs)
        # The row is written and not yet committed: cancel here.
        storing.set()
        await asyncio.sleep(0.3)
        return stored

    monkeypatch.setattr(SlackConfigurationTokenRepository, "store_rotation", slow_store)

    async def fresh() -> SecretStr:
        async with _uows(db_manager)() as uow:
            return await provisioning.SlackAppProvisioner(
                uow, calls=fake.calls()
            ).fresh_access_token(UUID(fixed_test_org["id"]), TEAM)

    request = asyncio.create_task(fresh())
    await asyncio.wait_for(storing.wait(), timeout=5)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        # Raised only once the store has committed: the session is not closed
        # under it.
        await request
    monkeypatch.undo()

    assert len(fake.rotated) == 1
    assert (await fresh()).get_secret_value() == "xoxe.xoxp-access-2"
    assert len(fake.rotated) == 1, "the stored pair was used, not rotated again"


async def test_a_stored_token_slack_refuses_asks_for_a_new_paste(
    authenticated_client: AsyncClient, fixed_test_org, db_session, fake: FakeSlack
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)
    await db_session.execute(
        text(
            "UPDATE slack_configuration_tokens "
            "SET access_expires_at = now() - interval '1 minute'"
        )
    )
    await db_session.commit()
    fake.valid.clear()  # revoked at Slack
    fake.rotated.clear()

    refused = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}
    )
    again = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}
    )
    listed = await authenticated_client.get(f"{_base(pod_id)}/app-creation")

    assert refused.status_code == status.HTTP_409_CONFLICT, refused.text
    assert refused.json()["code"] == "SLACK_APP_CREATION_TOKEN_EXPIRED"
    assert "paste" in refused.json()["message"]
    assert again.json()["code"] == "SLACK_APP_CREATION_TOKEN_EXPIRED"
    assert len(fake.rotated) == 1, "a refused token is not sent to Slack again"
    assert listed.json()["items"][0]["needs_new_token"] is True

    fake.valid.add("xoxe-1-new-paste")
    repasted = await _save(authenticated_client, pod_id, "xoxe-1-new-paste")
    assert repasted.status_code == status.HTTP_200_OK, repasted.text
    assert repasted.json()["needs_new_token"] is False
    made = await authenticated_client.post(
        f"{_base(pod_id)}/apps", json={"team_id": TEAM}
    )
    assert made.status_code == status.HTTP_200_OK, made.text


async def test_a_pasted_token_slack_refuses_says_so_without_saving(
    authenticated_client: AsyncClient, fixed_test_org, db_session, fake: FakeSlack
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])

    refused = await _save(authenticated_client, pod_id, "xoxe-1-wrong")

    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, refused.text
    assert refused.json()["code"] == "SLACK_CONFIGURATION_TOKEN_REFUSED"
    assert (
        await _scalar(db_session, "SELECT count(*) FROM slack_configuration_tokens")
        == 0
    )


async def test_two_saves_of_one_workspace_both_succeed(
    fixed_test_org, fixed_test_user, db_session, db_manager, fake: FakeSlack
):
    fake.valid.add("xoxe-1-second")

    async def save(token: str):
        async with _uows(db_manager)() as uow:
            return await provisioning.SlackAppProvisioner(
                uow, calls=fake.calls()
            ).save_token(
                organization_id=UUID(fixed_test_org["id"]),
                user_id=UUID(fixed_test_user["id"]),
                refresh_token=SecretStr(token),
            )

    saved = await asyncio.gather(save(PASTED), save("xoxe-1-second"))

    assert [one.team_id for one in saved] == [TEAM, TEAM]
    row = (
        await db_session.execute(
            text("SELECT count(*), max(generation) FROM slack_configuration_tokens")
        )
    ).one()
    assert row == (1, 1), "one row; the later paste replaced the first"


@dataclass
class _FailingInstalls:
    """The real connectors module, failing -- or cancelled -- after its install
    has committed, which is where a rollback can no longer undo it."""

    error: BaseException

    async def create_install(self, uow, **kwargs) -> UUID:
        await provisioning.create_own_app_install(uow, **kwargs)
        raise self.error

    def installs(self) -> provisioning.OrgInstalls:
        return provisioning.OrgInstalls(create_install=self.create_install)


@pytest.mark.parametrize(
    "error", [RuntimeError("after the commit"), asyncio.CancelledError()]
)
async def test_a_create_interrupted_after_the_install_committed_undoes_both(
    error: BaseException,
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session,
    db_manager,
    fake: FakeSlack,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, pod_id)

    with pytest.raises(type(error)):
        async with _uows(db_manager)() as uow:
            await provisioning.SlackAppProvisioner(
                uow, calls=fake.calls(), installs=_FailingInstalls(error).installs()
            ).create_app(
                organization_id=UUID(fixed_test_org["id"]),
                pod_id=UUID(pod_id),
                agent_id=UUID(pod_id),
                user_id=UUID(fixed_test_user["id"]),
                team_id=TEAM,
            )

    assert fake.deleted == [fake.created[0][0]]
    assert (
        await _scalar(
            db_session,
            "SELECT count(*) FROM auth_configs WHERE organization_id = :org "
            "AND connector_id = 'slack'",
            org=fixed_test_org["id"],
        )
        == 0
    )
    assert (
        await _scalar(db_session, "SELECT count(*) FROM slack_pending_app_bindings")
        == 0
    )


@pytest.mark.asyncio
async def test_an_app_slack_will_not_delete_is_kept_for_the_next_sweep(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
    db_manager,
    fake: FakeSlack,
):
    """A failed delete must not forget an app that is still in Slack: its row
    stays so a later sweep asks again -- until the retry window is over."""
    retrying_pod = await _pod(authenticated_client, fixed_test_org["id"])
    abandoned_pod = await _pod(authenticated_client, fixed_test_org["id"])
    await _save(authenticated_client, retrying_pod)
    retrying = (
        await authenticated_client.post(
            f"{_base(retrying_pod)}/apps", json={"team_id": TEAM}
        )
    ).json()
    abandoned = (
        await authenticated_client.post(
            f"{_base(abandoned_pod)}/apps", json={"team_id": TEAM}
        )
    ).json()
    for auth_config_id, days in (
        (retrying["auth_config_id"], 8),
        (abandoned["auth_config_id"], 40),
    ):
        await db_session.execute(
            text(
                "UPDATE slack_pending_app_bindings SET created_at = now() - "
                "make_interval(days => :days) WHERE auth_config_id = :id"
            ),
            {"days": days, "id": auth_config_id},
        )
    await db_session.commit()
    fake.refuse_delete = True

    settled = await slack_app_sweep.sweep_unauthorised_apps(
        _uows(db_manager), calls=fake.calls()
    )

    assert settled == 1
    pending = {
        str(row)
        for row in (
            await db_session.execute(
                text("SELECT auth_config_id FROM slack_pending_app_bindings")
            )
        ).scalars()
    }
    assert retrying["auth_config_id"] in pending
    assert abandoned["auth_config_id"] not in pending


async def test_the_sweep_deletes_apps_nobody_authorised_and_keeps_authorised_ones(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session,
    db_manager,
    fake: FakeSlack,
):
    pod_id = await _pod(authenticated_client, fixed_test_org["id"])
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents", json={"name": "kept_one", "instruction": "Hi."}
    )
    await _save(authenticated_client, pod_id)
    stale = (
        await authenticated_client.post(f"{_base(pod_id)}/apps", json={"team_id": TEAM})
    ).json()
    authorised = (
        await authenticated_client.post(
            f"{_base(pod_id)}/apps",
            json={"team_id": TEAM, "agent_id": agent.json()["id"]},
        )
    ).json()
    fresh_pod = await _pod(authenticated_client, fixed_test_org["id"])
    fresh = (
        await authenticated_client.post(
            f"{_base(fresh_pod)}/apps", json={"team_id": TEAM}
        )
    ).json()
    await db_session.execute(
        text(
            "UPDATE slack_pending_app_bindings "
            "SET created_at = now() - interval '8 days' WHERE auth_config_id <> :fresh"
        ),
        {"fresh": fresh["auth_config_id"]},
    )
    await db_session.commit()

    async def is_authorised(uow, auth_config_id: UUID) -> bool:
        return str(auth_config_id) == authorised["auth_config_id"]

    settled = await slack_app_sweep.sweep_unauthorised_apps(
        _uows(db_manager),
        calls=fake.calls(),
        installs=provisioning.OrgInstalls(is_authorised=is_authorised),
    )

    assert settled == 2
    assert fake.deleted == [stale["app_id"]]
    remaining = {
        str(row)
        for row in (
            await db_session.execute(
                text(
                    "SELECT id FROM auth_configs WHERE organization_id = :org "
                    "AND connector_id = 'slack'"
                ),
                {"org": fixed_test_org["id"]},
            )
        ).scalars()
    }
    assert remaining == {authorised["auth_config_id"], fresh["auth_config_id"]}
    pending = {
        str(row)
        for row in (
            await db_session.execute(
                text("SELECT auth_config_id FROM slack_pending_app_bindings")
            )
        ).scalars()
    }
    assert pending == {fresh["auth_config_id"]}
