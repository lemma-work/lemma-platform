"""Lemma creating a pod's own Slack app from the org's configuration token."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    SlackAppCreationForbiddenError,
    SlackAppCreationNotSetUpError,
)
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    CreatedSlackApp,
    RotatedConfigurationToken,
)
from app.modules.agent_surfaces.services import slack_app_provisioning as provisioning

pytestmark = pytest.mark.asyncio

ORG = uuid4()
USER = uuid4()


class _Uow:
    def __init__(self):
        self.session = SimpleNamespace()
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Tokens:
    """The repository, with plaintext standing in for ciphertext."""

    def __init__(self):
        self.rows: dict[str, SimpleNamespace] = {}

    async def list_for_organization(self, organization_id):
        return list(self.rows.values())

    async def get(self, organization_id, team_id):
        return self.rows.get(team_id)

    async def save(
        self, *, organization_id, token, team_name=None, saved_by_user_id=None
    ):
        row = self.rows.get(token.team_id) or SimpleNamespace(
            team_id=token.team_id,
            team_name=None,
            created_at=datetime.now(timezone.utc),
        )
        row.access_token = token.access_token
        row.refresh_token = token.refresh_token
        row.access_expires_at = token.expires_at
        row.team_name = team_name or row.team_name
        self.rows[token.team_id] = row
        return row

    async def delete(self, organization_id, team_id):
        return self.rows.pop(team_id, None) is not None


def _rotated(n: int, *, hours: float = 12) -> RotatedConfigurationToken:
    return RotatedConfigurationToken(
        access_token=f"access-{n}",
        refresh_token=f"refresh-{n}",
        team_id="T1",
        slack_user_id="U_ADMIN",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


@pytest.fixture
def slack():
    """Slack's side and the connectors module's, recorded and injected. Nothing
    here reaches the network or another module."""
    calls = SimpleNamespace(rotated=[], created=[], deleted=[], installs=[])
    state = SimpleNamespace(manager=True, install_fails=False, n=0)

    async def rotate(refresh_token):
        calls.rotated.append(refresh_token)
        state.n += 1
        return _rotated(state.n)

    async def workspace_name(access_token):
        return "Acme"

    async def create_app(access_token, manifest):
        calls.created.append((access_token, manifest))
        return CreatedSlackApp(
            app_id="A_NEW", client_id="cid", client_secret="csec", signing_secret="ssec"
        )

    async def delete_app(access_token, app_id):
        calls.deleted.append(app_id)
        return True

    async def may_manage(uow, *, user_id, organization_id):
        return state.manager

    async def create_install(uow, **kwargs):
        if state.install_fails:
            raise AgentSurfaceValidationError("name taken")
        calls.installs.append(kwargs)
        return uuid4()

    state.calls = provisioning.SlackAppCalls(
        rotate=rotate,
        workspace_name=workspace_name,
        create_app=create_app,
        delete_app=delete_app,
    )
    state.installs = provisioning.OrgInstalls(
        may_manage=may_manage, create_install=create_install
    )
    return calls, state


def _provisioner(state) -> provisioning.SlackAppProvisioner:
    return provisioning.SlackAppProvisioner(
        _Uow(), calls=state.calls, installs=state.installs, tokens=_Tokens()
    )


async def test_saving_a_token_rotates_it_and_names_the_workspace(slack):
    calls, state = slack
    provisioner = _provisioner(state)

    saved = await provisioner.save_token(
        organization_id=ORG, user_id=USER, refresh_token="refresh-pasted"
    )

    assert (saved.team_id, saved.team_name) == ("T1", "Acme")
    assert calls.rotated == ["refresh-pasted"]
    # The pasted refresh token is spent; the pair it rotated to is what is kept.
    assert provisioner.tokens.rows["T1"].refresh_token == "refresh-1"
    assert [w.team_id for w in await provisioner.workspaces(ORG)] == ["T1"]


async def test_only_an_install_manager_may_save_a_token(slack):
    calls, state = slack
    state.manager = False

    with pytest.raises(SlackAppCreationForbiddenError):
        await _provisioner(state).save_token(
            organization_id=ORG, user_id=USER, refresh_token="refresh-pasted"
        )
    assert calls.rotated == []


async def test_creating_an_app_registers_its_credentials_as_the_orgs_own(slack):
    calls, state = slack
    provisioner = _provisioner(state)
    await provisioner.tokens.save(organization_id=ORG, token=_rotated(0))

    created = await provisioner.create_pod_app(
        organization_id=ORG, user_id=USER, team_id="T1", app_name="Ada"
    )

    assert created.app_id == "A_NEW"
    token, manifest = calls.created[0]
    assert token == "access-0"  # still fresh, so not rotated
    assert manifest["display_information"]["name"] == "Ada"
    install = calls.installs[0]
    assert install["connector_id"] == "slack"
    assert install["config"] == {
        "client_id": "cid",
        "client_secret": "csec",
        "signing_secret": "ssec",
    }
    assert "A_NEW" in install["name"]


async def test_a_token_near_expiry_is_rotated_before_use(slack):
    calls, state = slack
    provisioner = _provisioner(state)
    await provisioner.tokens.save(organization_id=ORG, token=_rotated(0, hours=0.05))

    await provisioner.create_pod_app(
        organization_id=ORG, user_id=USER, team_id="T1", app_name="Ada"
    )

    assert calls.rotated == ["refresh-0"]
    assert calls.created[0][0] == "access-1"
    assert provisioner.tokens.rows["T1"].refresh_token == "refresh-1"


async def test_an_app_lemma_could_not_register_is_deleted_again(slack):
    """Otherwise it sits in the workspace under the teammate's name, unconnectable."""
    calls, state = slack
    state.install_fails = True
    provisioner = _provisioner(state)
    await provisioner.tokens.save(organization_id=ORG, token=_rotated(0))

    with pytest.raises(AgentSurfaceValidationError):
        await provisioner.create_pod_app(
            organization_id=ORG, user_id=USER, team_id="T1", app_name="Ada"
        )

    assert calls.deleted == ["A_NEW"]
    assert provisioner.uow.rollbacks == 1


async def test_no_token_for_the_workspace_says_so(slack):
    _, state = slack
    with pytest.raises(SlackAppCreationNotSetUpError):
        await _provisioner(state).create_pod_app(
            organization_id=ORG, user_id=USER, team_id="T_OTHER", app_name="Ada"
        )
