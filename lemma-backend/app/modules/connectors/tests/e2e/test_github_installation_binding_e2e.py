"""A GitHub App installation belongs to the Lemma organization that bound it.

Audit H4: one App serves every organization that installs it, and an
installation used to bind to any Lemma account whose GitHub user could see one
repository in it. That account's organization then got App tokens with the
installation's full permissions, and the installation's webhooks ran its
schedules. Each test here is one of the doors that let it in:

- the setup callback naming the installation (``installation_id`` on the
  redirect, under a ``state`` the claiming organization minted);
- reconciling an account against ``/user/installations``;
- binding an installation to an account by hand;
- a schedule provisioned from an account, and the deliveries routed to it;
- the installation a token is minted for.

GitHub is answered at the HTTP boundary (`tests/support/fake_github.py`), so
the code that asks it -- the OAuth exchange, `/user/installations`, `/user`,
`/user/memberships/orgs/{org}` -- runs whole. Two GitHub people: `octo-admin`,
an admin of the GitHub organization `octo` the App is installed on, who is org
A's owner; and `mallory`, a plain member of `octo` who can see a repository in
the installation -- the attack is precisely that she can.

An installation nobody holds yet is bound only with proof the claimant
controls it on GitHub: it is installed on their own GitHub user, or on a
GitHub organization they are an admin of. Seeing it is not enough.
"""

from __future__ import annotations

import random
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connect_request import ConnectRequest
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.services.auth.github_bindings import (
    routable_external_ref,
)
from app.modules.connectors.tests.support.fake_github import FakeGitHub, answer_github
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e

CALLBACK = "/connectors/connect-requests/oauth/callback"
GITHUB_OAUTH = [
    {
        "kind": "http",
        "auth_scheme": "OAUTH2",
        "supports_org_custom_oauth": True,
        "oauth2_defaults": {
            "authorization_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
        },
    }
]


def _installation_id() -> str:
    """One per test: the binding is unique per installation across the
    worker's database, and tests share it."""
    return str(random.randint(10**9, 2 * 10**9))


@pytest.fixture
def github(monkeypatch) -> FakeGitHub:
    fake = answer_github(monkeypatch)
    fake.person("octo-admin", admin_of={"octo"})
    fake.person("mallory", member_of={"octo"})
    return fake


@pytest.fixture
def github_sees(github: FakeGitHub):
    """The App installed on `octo` as each id named, and both people able to
    see a repository in it."""

    def see(*installation_ids: str) -> None:
        for installation_id in installation_ids:
            github.install(installation_id, on="octo")
            for user in github.users.values():
                user.sees.add(installation_id)

    return see


async def _github_connector(db_session: AsyncSession) -> None:
    connector = await db_session.get(Connector, "github")
    if connector is None:
        db_session.add(
            Connector(
                id="github",
                title="GitHub",
                description="H4 e2e",
                kinds=GITHUB_OAUTH,
                is_active=True,
            )
        )
    else:
        connector.kinds = GITHUB_OAUTH
        connector.is_active = True
    await db_session.commit()


async def _install_github(client, org_id: str, headers=None) -> str:
    response = await client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        headers=headers,
        json={
            "connector_id": "github",
            "kind": "http",
            "name": f"github-{uuid4().hex[:8]}",
            "config_source": "ORG_CUSTOM",
            "config": {
                "oauth2_credentials": {"client_id": "id", "client_secret": "secret"}
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _org_of_someone_else(scenario: E2EScenario, prefix: str):
    """A second Lemma organization, owned by a different person."""
    person = await scenario.create_user(prefix)
    headers = auth_headers(person)
    created = await scenario.async_client.post(
        "/organizations",
        headers=headers,
        json={"name": f"{prefix} {uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    return person, headers, created.json()["id"]


async def _setup_callback(
    scenario: E2EScenario,
    db_session: AsyncSession,
    *,
    org_id: str,
    auth_config_id: str,
    headers,
    installation_id: str,
    code: str = "octo-admin",
):
    """Start a connect in ``org_id`` and come back from GitHub's install page
    naming ``installation_id``, as the install redirect does."""
    client = scenario.async_client
    minted = await client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        headers=headers,
        json={"connector_id": "github", "auth_config_id": auth_config_id},
    )
    assert minted.status_code == 200, minted.text
    row = await db_session.get(ConnectRequest, UUID(minted.json()["id"]))
    state = (row.attributes or {})["state"]
    return await client.get(
        CALLBACK,
        headers=headers,
        params={
            "state": state,
            "code": code,
            "installation_id": installation_id,
            "setup_action": "install",
            "format": "json",
        },
    )


async def _accounts_on(db_session: AsyncSession, org_id: str) -> list[Account]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(Account).where(
            Account.organization_id == UUID(org_id), Account.connector_id == "github"
        )
    )
    return list(rows.scalars())


async def _org_a_binds(scenario, db_session, github_sees, installation_id) -> str:
    """Org A installs the App and completes the setup callback. Returns its
    account id."""
    await scenario.create_org(name_prefix="H4 Org A")
    await _github_connector(db_session)
    auth_config_id = await _install_github(scenario.owner_client, scenario.org_id)
    github_sees(installation_id)
    bound = await _setup_callback(
        scenario,
        db_session,
        org_id=scenario.org_id,
        auth_config_id=auth_config_id,
        headers=auth_headers(scenario.owner_user),
        installation_id=installation_id,
    )
    assert bound.status_code == 200, bound.text
    [account] = await _accounts_on(db_session, scenario.org_id)
    assert account.external_ref == installation_id
    return bound.json()["id"]


async def test_another_org_cannot_adopt_an_installation_through_the_setup_callback(
    scenario, db_session, github_sees
):
    """Org B's person can see a repository in org A's installation and comes
    back from GitHub naming it, under a state org B minted itself."""
    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)

    _, b_headers, org_b = await _org_of_someone_else(scenario, "h4-mallory")
    b_install = await _install_github(scenario.async_client, org_b, b_headers)
    adopted = await _setup_callback(
        scenario,
        db_session,
        org_id=org_b,
        auth_config_id=b_install,
        headers=b_headers,
        installation_id=installation_id,
        code="mallory",
    )

    assert adopted.status_code == 409, adopted.text
    assert adopted.json()["code"] == "GITHUB_INSTALLATION_BOUND_ELSEWHERE"
    assert [a.external_ref for a in await _accounts_on(db_session, org_b)] == []


async def test_a_forged_or_another_orgs_state_completes_nothing(
    scenario, db_session, github_sees
):
    """The state is what ties a setup callback to an organization and a
    person. One nobody minted claims nothing; org A's, finished in org B's
    browser, is refused before anything is exchanged."""
    installation_id = _installation_id()
    await scenario.create_org(name_prefix="H4 Org A")
    await _github_connector(db_session)
    auth_config_id = await _install_github(scenario.owner_client, scenario.org_id)
    github_sees(installation_id)
    _, b_headers, org_b = await _org_of_someone_else(scenario, "h4-mallory")

    forged = await scenario.async_client.get(
        CALLBACK,
        headers=b_headers,
        params={
            "state": "forged-" + uuid4().hex,
            "code": "abc",
            "installation_id": installation_id,
            "setup_action": "install",
            "format": "json",
        },
    )
    assert forged.status_code == 404, forged.text
    assert forged.json()["code"] == "CONNECT_REQUEST_NOT_FOUND"

    minted = await scenario.owner_client.post(
        f"/organizations/{scenario.org_id}/connectors/connect-requests",
        json={"connector_id": "github", "auth_config_id": auth_config_id},
    )
    assert minted.status_code == 200, minted.text
    row = await db_session.get(ConnectRequest, UUID(minted.json()["id"]))
    other_orgs_state = (row.attributes or {})["state"]
    in_b = await scenario.async_client.get(
        CALLBACK,
        headers=b_headers,
        params={
            "state": other_orgs_state,
            "code": "abc",
            "installation_id": installation_id,
            "setup_action": "install",
            "format": "json",
        },
    )
    assert in_b.status_code == 403, in_b.text
    assert in_b.json()["code"] == "CONNECT_REQUEST_BROWSER_MISMATCH"

    assert await _accounts_on(db_session, scenario.org_id) == []
    assert await _accounts_on(db_session, org_b) == []


async def _account_in(
    db_session: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    external_ref=None,
    token: str = "gho_mallory",
) -> str:
    install = AuthConfig(
        organization_id=UUID(org_id),
        connector_id="github",
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=f"github-{uuid4().hex[:8]}",
    )
    db_session.add(install)
    await db_session.commit()
    from app.core.crypto import get_secret_cipher
    from app.modules.connectors.domain.account import AccountEntity

    created = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user_id),
            organization_id=UUID(org_id),
            auth_config_id=install.id,
            connector_id="github",
            is_default=True,
            credentials={"access_token": token},
            external_ref=external_ref,
        )
    )
    await db_session.commit()
    return str(created.id)


async def test_reconciling_an_account_does_not_adopt_another_orgs_installation(
    scenario, db_session, github_sees
):
    """The connectors page asks GitHub what a fresh account reaches and records
    an unambiguous answer. Org A's installation is not an answer for org B."""
    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)
    mallory, b_headers, org_b = await _org_of_someone_else(scenario, "h4-mallory")
    account_id = await _account_in(db_session, org_id=org_b, user_id=mallory["id"])

    seen = await scenario.async_client.get(
        f"/organizations/{org_b}/connectors/accounts/{account_id}/github/installations",
        headers=b_headers,
        params={"refresh": True},
    )

    assert seen.status_code == 200, seen.text
    assert seen.json()["install_state"] == "INSTALL_REQUIRED", seen.json()
    assert seen.json()["choices"] == []
    [account] = await _accounts_on(db_session, org_b)
    assert account.external_ref is None


async def test_binding_by_hand_refuses_another_orgs_installation(
    scenario, db_session, github_sees
):
    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)
    mallory, b_headers, org_b = await _org_of_someone_else(scenario, "h4-mallory")
    account_id = await _account_in(db_session, org_id=org_b, user_id=mallory["id"])

    bound = await scenario.async_client.post(
        f"/organizations/{org_b}/connectors/accounts/{account_id}/github/installations",
        headers=b_headers,
        json={"installation_id": installation_id},
    )

    assert bound.status_code == 409, bound.text
    assert bound.json()["code"] == "GITHUB_INSTALLATION_BOUND_ELSEWHERE"
    [account] = await _accounts_on(db_session, org_b)
    assert account.external_ref is None


async def test_a_legacy_account_naming_another_orgs_installation_mints_nothing(
    scenario, db_session, github_sees
):
    """An account bound before the binding existed, to an installation another
    organization holds, is not handed that installation's App token: the
    execution plan names no installation for it."""
    installation_id = _installation_id()
    a_account_id = await _org_a_binds(
        scenario, db_session, github_sees, installation_id
    )
    mallory, _b_headers, org_b = await _org_of_someone_else(scenario, "h4-mallory")
    b_account_id = await _account_in(
        db_session,
        org_id=org_b,
        user_id=mallory["id"],
        external_ref=installation_id,
    )

    from app.core.crypto import get_secret_cipher

    repository = AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    )
    theirs = await repository.get(UUID(a_account_id))
    mine = await repository.get(UUID(b_account_id))
    assert await routable_external_ref(db_session, theirs) == installation_id
    assert await routable_external_ref(db_session, mine) is None


async def test_a_schedule_routing_key_is_never_another_orgs_installation(
    scenario, db_session, github_sees
):
    """A GitHub schedule's routing key is provisioned from its account
    (`resolve_trigger_binding`). An account still carrying another
    organization's installation provisions none, so the schedule is refused."""
    from app.modules.connectors.contracts.triggers import resolve_trigger_binding
    from app.modules.schedule.tests.e2e.test_schedule_e2e import (
        _seed_connector_trigger,
    )

    installation_id = _installation_id()
    a_account = await _org_a_binds(scenario, db_session, github_sees, installation_id)
    org_b = await scenario.owner_client.post(
        "/organizations", json={"name": f"H4 Org B {uuid4().hex[:8]}"}
    )
    assert org_b.status_code == 201, org_b.text
    legacy = await _account_in(
        db_session,
        org_id=org_b.json()["id"],
        user_id=scenario.owner_user["id"],
        external_ref=installation_id,
    )
    trigger_id = "github:http:pull_request"
    await _seed_connector_trigger(
        db_session,
        connector_id="github",
        trigger_id=trigger_id,
        event_type="pull_request",
    )
    owner = UUID(scenario.owner_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)

    ours = await resolve_trigger_binding(
        uow, trigger_id=trigger_id, account_id=UUID(a_account), user_id=owner
    )
    theirs = await resolve_trigger_binding(
        uow, trigger_id=trigger_id, account_id=UUID(legacy), user_id=owner
    )

    assert ours.installation_id == installation_id
    assert theirs.installation_id is None


async def _verified_delivery(installation_id: str):
    """A signed pull_request delivery, verified by the real GitHub source --
    which is where the installation's binding is looked up."""
    import hashlib
    import hmac
    import json

    from app.modules.connectors.config import connector_settings
    from app.modules.connectors.infrastructure.webhook_sources.github import (
        GitHubWebhookSource,
    )
    from app.modules.schedule.contracts.webhook_source import WebhookDelivery

    secret = connector_settings.connector_github_app_webhook_secret
    raw = json.dumps(
        {
            "action": "opened",
            "pull_request": {"id": 1, "head": {"sha": "abc", "repo": {"id": 7}}},
            "repository": {"id": 7, "name": "api", "owner": {"login": "octo"}},
            "installation": {"id": int(installation_id)},
        }
    ).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    source = GitHubWebhookSource()
    verified = await source.verify(
        WebhookDelivery(
            source="github",
            raw_body=raw,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "pull_request",
            },
        )
    )
    return source.normalize(verified)


async def test_a_delivery_runs_only_schedules_in_the_installations_org(
    scenario, db_session, github_sees, monkeypatch
):
    """A schedule in org B still routing on org A's installation -- written
    before the binding, or by any path that skipped it -- does not run on org
    A's events; org A's own does. And an installation bound to nobody routes
    to no schedule at all.

    The matcher is handed both schedules, as `config @> criteria` would find
    them; the source's binding lookup and the handler's organization filter
    are the real ones, against Postgres."""
    from types import SimpleNamespace

    from app.core.api.dependencies import get_uow_factory
    from app.modules.connectors.config import connector_settings
    from app.modules.schedule.services.webhook_handler import WebhookHandler

    monkeypatch.setattr(
        connector_settings, "connector_github_app_webhook_secret", "h4-secret"
    )
    monkeypatch.setattr(
        connector_settings,
        "connector_github_app_webhook_secret_previous",
        None,
        raising=False,
    )
    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)
    pod_a = await scenario.create_pod(name_prefix="H4 A")
    org_b = await scenario.owner_client.post(
        "/organizations", json={"name": f"H4 Org B {uuid4().hex[:8]}"}
    )
    assert org_b.status_code == 201, org_b.text
    pod_b = await scenario.owner_client.post(
        "/pods",
        json={"organization_id": org_b.json()["id"], "name": f"H4 B {uuid4().hex[:6]}"},
    )
    assert pod_b.status_code == 201, pod_b.text
    routing = {"source": "github", "installation_id": installation_id}
    # No pod connector to resolve: what is under test is the organization.
    ours = SimpleNamespace(
        id=uuid4(),
        pod_id=UUID(pod_a["id"]),
        config=routing,
        pod_connector_slug=None,
    )
    theirs = SimpleNamespace(
        id=uuid4(),
        pod_id=UUID(pod_b.json()["id"]),
        config=routing,
        pod_connector_slug=None,
    )

    class _Matcher:
        async def match_criteria(self, _criteria):
            return [ours, theirs]

    fired: list[object] = []

    async def _fire(_uow, schedule, _event):
        fired.append(schedule.id)

    handler = WebhookHandler(
        matcher_factory=lambda _uow: _Matcher(),
        uow_factory=get_uow_factory(),
        fire=_fire,
    )

    normalized = await _verified_delivery(installation_id)
    assert normalized is not None
    assert normalized.organization_id == scenario.org_id
    await handler.handle_webhook(
        source="github", payload=normalized.payload, normalized=normalized
    )
    assert fired == [ours.id], "org A's pull request started a run in org B"

    assert await _verified_delivery(_installation_id()) is None, (
        "an installation bound to no organization routed a delivery"
    )


# -- no claim without proof ---------------------------------------------------


async def _org_with_github(scenario: E2EScenario, db_session, prefix: str):
    """A Lemma organization of someone else's, with GitHub installed in it."""
    await _github_connector(db_session)
    person, headers, org_id = await _org_of_someone_else(scenario, prefix)
    auth_config_id = await _install_github(scenario.async_client, org_id, headers)
    return person, headers, org_id, auth_config_id


async def _held_by(db_session: AsyncSession, installation_id: str):
    from app.modules.connectors.infrastructure.models.connector_installation import (
        ConnectorInstallation,
    )

    db_session.expire_all()
    return await db_session.scalar(
        select(ConnectorInstallation.organization_id).where(
            ConnectorInstallation.connector_id == "github",
            ConnectorInstallation.external_id == installation_id,
        )
    )


async def test_seeing_an_unbound_installation_connects_github_but_claims_nothing(
    scenario, db_session, github, github_sees
):
    """Nobody holds it yet, and Mallory can see a repository in it. That is
    not control of it: her organization would get the App's token on every
    repository there. Her own GitHub sign-in is still hers to connect -- the
    account is made, without the installation, waiting on an admin."""
    installation_id = _installation_id()
    github_sees(installation_id)
    mallory, headers, org_id, auth_config_id = await _org_with_github(
        scenario, db_session, "h4-mallory"
    )

    claimed = await _setup_callback(
        scenario,
        db_session,
        org_id=org_id,
        auth_config_id=auth_config_id,
        headers=headers,
        installation_id=installation_id,
        code="mallory",
    )

    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "CONNECTED"
    assert claimed.json()["install_state"] == "PENDING_APPROVAL"
    assert await _held_by(db_session, installation_id) is None
    [account] = await _accounts_on(db_session, org_id)
    assert account.external_ref is None

    # An admin of `octo` binds it to this organization -- here Mallory once
    # GitHub vouches for her, since she also manages her org's installations
    # in Lemma; her next reconcile picks it up, no proof of her own needed.
    from app.modules.connectors.services.auth.github_bindings import (
        claim_installation,
    )

    await claim_installation(
        db_session,
        installation_id=installation_id,
        organization_id=UUID(org_id),
        user_id=UUID(mallory["id"]),
        administered=True,
    )
    await db_session.commit()
    seen = await scenario.async_client.get(
        f"/organizations/{org_id}/connectors/accounts/{account.id}/github/installations",
        headers=headers,
        params={"refresh": True},
    )
    assert seen.status_code == 200, seen.text
    assert seen.json()["install_state"] == "READY", seen.json()
    assert seen.json()["installation_id"] == installation_id


async def test_reconciling_does_not_claim_an_unbound_installation_without_proof(
    scenario, db_session, github, github_sees
):
    installation_id = _installation_id()
    github_sees(installation_id)
    mallory, headers, org_id, _ = await _org_with_github(
        scenario, db_session, "h4-mallory"
    )
    account_id = await _account_in(db_session, org_id=org_id, user_id=mallory["id"])

    seen = await scenario.async_client.get(
        f"/organizations/{org_id}/connectors/accounts/{account_id}/github/installations",
        headers=headers,
        params={"refresh": True},
    )

    assert seen.status_code == 200, seen.text
    # Waiting on the installation's owner, not "install it": it is installed.
    assert seen.json()["install_state"] == "PENDING_APPROVAL", seen.json()
    assert await _held_by(db_session, installation_id) is None
    [account] = await _accounts_on(db_session, org_id)
    assert account.external_ref is None


async def test_binding_by_hand_does_not_claim_an_unbound_installation_without_proof(
    scenario, db_session, github, github_sees
):
    installation_id = _installation_id()
    github_sees(installation_id)
    mallory, headers, org_id, _ = await _org_with_github(
        scenario, db_session, "h4-mallory"
    )
    account_id = await _account_in(db_session, org_id=org_id, user_id=mallory["id"])

    bound = await scenario.async_client.post(
        f"/organizations/{org_id}/connectors/accounts/{account_id}/github/installations",
        headers=headers,
        json={"installation_id": installation_id},
    )

    assert bound.status_code == 403, bound.text
    assert bound.json()["code"] == "GITHUB_INSTALLATION_NOT_ADMINISTERED"
    assert await _held_by(db_session, installation_id) is None


async def test_an_installation_on_your_own_github_user_is_yours_to_claim(
    scenario, db_session, github
):
    """No GitHub organization to be an admin of: the installation is on the
    claimant's own GitHub user, which GitHub's `/user` confirms."""
    installation_id = _installation_id()
    github.person("solo")
    github.install(installation_id, on="solo", account_type="User")
    github.users["gho_solo"].sees.add(installation_id)
    _, headers, org_id, auth_config_id = await _org_with_github(
        scenario, db_session, "h4-solo"
    )

    claimed = await _setup_callback(
        scenario,
        db_session,
        org_id=org_id,
        auth_config_id=auth_config_id,
        headers=headers,
        installation_id=installation_id,
        code="solo",
    )

    assert claimed.status_code == 200, claimed.text
    assert await _held_by(db_session, installation_id) == UUID(org_id)


async def test_once_held_anyone_in_the_organization_connects_through_it(
    scenario, db_session, github, github_sees
):
    """Proof is for the first claim only. After an admin has bound it, a
    member who merely sees it reaches it -- it is their organization's."""
    from app.modules.test_support.e2e_authz import invite_org_member

    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)
    member = await scenario.create_user("h4-member")
    await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=member,
    )
    account_id = await _account_in(
        db_session, org_id=scenario.org_id, user_id=member["id"]
    )

    seen = await scenario.async_client.get(
        f"/organizations/{scenario.org_id}/connectors/accounts/{account_id}"
        "/github/installations",
        headers=auth_headers(member),
        params={"refresh": True},
    )

    assert seen.status_code == 200, seen.text
    assert seen.json()["install_state"] == "READY", seen.json()
    assert seen.json()["installation_id"] == installation_id


async def test_controlling_it_on_github_is_not_enough_to_install_it_for_the_org(
    scenario, db_session, github
):
    """Binding installs the App for the whole Lemma organization, so the first
    claim also takes `org.installation.manage` -- the rule Slack's binding
    already had. A member who owns the installation on GitHub still connects
    their sign-in; the installation waits for someone who manages installs."""
    from app.modules.test_support.e2e_authz import invite_org_member

    installation_id = _installation_id()
    github.person("solo")
    github.install(installation_id, on="solo", account_type="User")
    github.users["gho_solo"].sees.add(installation_id)
    await scenario.create_org(name_prefix="H4 member claim")
    await _github_connector(db_session)
    auth_config_id = await _install_github(scenario.owner_client, scenario.org_id)
    member = await scenario.create_user("h4-plain-member")
    await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=member,
    )

    claimed = await _setup_callback(
        scenario,
        db_session,
        org_id=scenario.org_id,
        auth_config_id=auth_config_id,
        headers=auth_headers(member),
        installation_id=installation_id,
        code="solo",
    )

    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["install_state"] == "PENDING_APPROVAL"
    assert await _held_by(db_session, installation_id) is None
