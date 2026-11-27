"""The Connections read says GitHub is ready only when it is.

A connected GitHub account reaches nothing until an installation of the App
that its Lemma organization holds is behind it. ``GET /pods/{pod_id}/connections``
used to judge GitHub like any app -- the account is connected, so ``ready`` --
while the account page said the installation was missing, and a plugin's own
Connect said a third thing. Now the row is ``install`` until an installation is
usable, and ``me.github`` says why, by who can fix it:

- ``install``: the App is installed on nothing the account can see;
- ``github_admin``: it is, on a GitHub organization this person is not an admin
  of -- whichever Lemma organizations hold it elsewhere;
- ``github_permission``: the installation never granted the App Members: read,
  so GitHub cannot say who its admins are;
- ``lemma_admin``: they are its GitHub admin, and may not install apps here.

And a GitHub admin who may install apps here is simply ready: the read binds
the installation for this organization, on their proof, as the account page's
reconcile does -- also when another organization holds it already.

GitHub is answered at the HTTP boundary (``tests/support/fake_github.py``).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func
from starlette import status

from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)
from app.modules.connectors.tests.e2e.test_app_connector_e2e import (
    GitHubWorld,
    _github_world,
)
from app.modules.connectors.tests.e2e.test_github_installation_binding_e2e import (
    _account_in,
    _holders,
    _installation_id,
)
from app.modules.connectors.tests.support.fake_github import FakeGitHub, answer_github
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e


@pytest.fixture
def github(monkeypatch) -> FakeGitHub:
    return answer_github(monkeypatch)


async def _per_member(world: GitHubWorld) -> None:
    declared = await world.scenario.owner_client.post(
        f"/pods/{world.pod_id}/connectors",
        json={"slug": "github", "auth_config_id": world.auth_config_id},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text


async def _mine_here(db_session, world: GitHubWorld, user: dict, token: str) -> str:
    """``user``'s GitHub account, signed in as ``token``, in use in the pod."""
    account_id = await _account_in(
        db_session,
        org_id=str(world.org_id),
        user_id=user["id"],
        token=token,
        auth_config_id=UUID(world.auth_config_id),
    )
    used = await world.scenario.async_client.post(
        f"/pods/{world.pod_id}/connector-accounts",
        headers=auth_headers(user),
        json={"account_id": account_id},
    )
    assert used.status_code == status.HTTP_201_CREATED, used.text
    return account_id


async def _me(world: GitHubWorld, user: dict) -> dict:
    response = await world.scenario.async_client.get(
        f"/pods/{world.pod_id}/connections", headers=auth_headers(user)
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    (row,) = [one for one in response.json()["items"] if one["slug"] == "github"]
    return row["me"]


async def _held_elsewhere(db_session, scenario, installation_id: str) -> UUID:
    """Another Lemma organization holds the installation, proven."""
    created = await scenario.owner_client.post(
        "/organizations", json={"name": f"Elsewhere {uuid4().hex[:8]}"}
    )
    assert created.status_code == 201, created.text
    other = UUID(created.json()["id"])
    db_session.add(
        ConnectorInstallation(
            connector_id="github",
            external_id=installation_id,
            organization_id=other,
            proven_at=func.now(),
        )
    )
    await db_session.commit()
    return other


async def test_connected_but_installed_on_nothing_is_install_not_ready(
    scenario, db_session, github
):
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("alice-gh")
    await _mine_here(db_session, world, world.alice, token)

    me = await _me(world, world.alice)

    assert me["standing"] == "install"
    assert me["github"]["wait"] == "install"


async def test_seeing_another_orgs_installation_waits_on_its_github_admin(
    scenario, db_session, github
):
    """The Connections page said READY here, the account said install, and a
    plugin said a third thing. One answer now: an admin of `octo` has to."""
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("alice-gh", member_of={"octo"})
    installation_id = _installation_id()
    github.install(installation_id, on="octo")
    github.users[token].sees.add(installation_id)
    await _held_elsewhere(db_session, scenario, installation_id)
    await _mine_here(db_session, world, world.alice, token)

    me = await _me(world, world.alice)

    assert me["standing"] == "install"
    assert me["github"]["wait"] == "github_admin"
    assert me["github"]["login"] == "octo"
    assert me["github"]["manage_url"].endswith(
        f"/organizations/octo/settings/installations/{installation_id}"
    )
    assert UUID(scenario.org_id) not in await _holders(db_session, installation_id)


async def test_an_installation_without_members_read_waits_on_accepting_it(
    scenario, db_session, github
):
    """Without Members: read GitHub cannot say who the GitHub organization's
    admins are, so even its admin is told what unblocks it, not that they
    are not an admin."""
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("owner-gh", admin_of={"octo"})
    installation_id = _installation_id()
    github.install(installation_id, on="octo", members=False)
    github.users[token].sees.add(installation_id)
    await _mine_here(db_session, world, scenario.owner_user, token)

    me = await _me(world, scenario.owner_user)

    assert me["standing"] == "install"
    assert me["github"]["wait"] == "github_permission"
    assert me["github"]["login"] == "octo"
    assert await _holders(db_session, installation_id) == set()


async def test_a_github_admin_who_may_not_install_apps_here_waits_on_one_who_may(
    scenario, db_session, github
):
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("alice-gh", admin_of={"octo"})
    installation_id = _installation_id()
    github.install(installation_id, on="octo")
    github.users[token].sees.add(installation_id)
    await _mine_here(db_session, world, world.alice, token)

    me = await _me(world, world.alice)

    assert me["standing"] == "install"
    assert me["github"]["wait"] == "lemma_admin"
    assert await _holders(db_session, installation_id) == set()


async def test_a_github_admin_who_installs_apps_here_is_ready_beside_another_org(
    scenario, db_session, github
):
    """The journey that looped: the installation is another Lemma
    organization's already, and this organization's owner administers it on
    GitHub. Reading the Connections page binds it here too -- ready, and both
    organizations hold it."""
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("owner-gh", admin_of={"octo"})
    installation_id = _installation_id()
    github.install(installation_id, on="octo")
    github.users[token].sees.add(installation_id)
    other = await _held_elsewhere(db_session, scenario, installation_id)
    await _mine_here(db_session, world, scenario.owner_user, token)

    me = await _me(world, scenario.owner_user)

    assert me["standing"] == "ready"
    assert me.get("github") is None
    assert await _holders(db_session, installation_id) == {other, world.org_id}


async def test_a_github_that_does_not_answer_cannot_hold_the_page(
    scenario, db_session, github, monkeypatch
):
    """The page renders rows it already has; GitHub gets a budget for the one
    account it has to ask about. Past it the row says GitHub did not answer
    -- not ready, check again -- instead of the page hanging."""
    import asyncio

    from app.modules.connectors.api import dependencies

    async def _hangs(_path: str) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(dependencies, "GITHUB_PAGE_BUDGET_SECONDS", 0.05)
    github.before_api_request = _hangs
    world = await _github_world(scenario, db_session)
    await _per_member(world)
    token = github.person("alice-gh")
    await _mine_here(db_session, world, world.alice, token)

    me = await asyncio.wait_for(_me(world, world.alice), timeout=10)

    assert me["standing"] == "install"
    assert me["github"]["wait"] == "check"
