"""One GitHub App installation, bound to several Lemma organizations.

GitHub allows one installation of an App per GitHub account, so a GitHub
organization worked on from two Lemma organizations is one installation. Each
Lemma organization binds it on its own proof -- its claimant is an admin of the
GitHub organization and may install apps for the Lemma one -- and from then on:

- each organization's accounts mint App tokens for it, and its APP connector
  may act on it;
- a delivery from it runs the schedules of every organization that holds it,
  and of no other;
- GitHub's ``installation.deleted`` lets go of it everywhere;
- an organization can let go of its own binding, and the others keep theirs.

What a plain member, a contractor who only sees a repository, or a forged
state can do is unchanged, and covered in ``test_github_installation_binding_e2e``.
GitHub is answered at the HTTP boundary (``tests/support/fake_github.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.services.auth.github_bindings import (
    claim_installation,
    routable_external_ref,
)
from app.modules.connectors.tests.e2e.test_github_installation_binding_e2e import (  # noqa: F401
    _account_in,
    _accounts_on,
    _holders,
    _installation_id,
    _org_a_binds,
    _org_of_someone_else,
    _verified_delivery,
    github,  # fixture
    github_sees,  # fixture
)
from app.modules.test_support.e2e_authz import auth_headers, invite_org_member

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    """Deliveries here are signed and verified by the real GitHub source."""
    from app.modules.connectors.config import connector_settings

    monkeypatch.setattr(
        connector_settings, "connector_github_app_webhook_secret", "h4-secret"
    )
    monkeypatch.setattr(
        connector_settings,
        "connector_github_app_webhook_secret_previous",
        None,
        raising=False,
    )


async def _second_org(scenario) -> str:
    """Another Lemma organization of the same owner -- the GitHub admin."""
    created = await scenario.owner_client.post(
        "/organizations", json={"name": f"Several B {uuid4().hex[:8]}"}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _both_hold(scenario, db_session, see, installation_id):
    """Org A binds through the setup callback; the same GitHub admin's second
    Lemma organization picks it up by reconciling, as the connectors page
    does. Returns org B and its account."""
    await _org_a_binds(scenario, db_session, see, installation_id)
    org_b = await _second_org(scenario)
    account_id = await _account_in(
        db_session,
        org_id=org_b,
        user_id=scenario.owner_user["id"],
        token="gho_octo-admin",
    )
    seen = await scenario.owner_client.get(
        f"/organizations/{org_b}/connectors/accounts/{account_id}/github/installations",
        params={"refresh": True},
    )
    assert seen.status_code == 200, seen.text
    assert seen.json()["install_state"] == "READY", seen.json()
    assert seen.json()["installation_id"] == installation_id
    return org_b, account_id


async def _routable(db_session, account_id) -> str | None:
    from app.core.crypto import get_secret_cipher

    db_session.expire_all()
    account = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).get(UUID(str(account_id)))
    return await routable_external_ref(db_session, account)


async def test_a_github_admin_uses_one_installation_from_two_orgs(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    """The journey that failed: an admin of the GitHub organization, whose
    first Lemma organization already holds the installation, connects GitHub
    in a second one. It is READY there, and both organizations' accounts mint
    App tokens for it."""
    installation_id = _installation_id()
    org_b, b_account = await _both_hold(
        scenario, db_session, github_sees, installation_id
    )

    assert await _holders(db_session, installation_id) == {
        UUID(scenario.org_id),
        UUID(org_b),
    }
    [a_account] = await _accounts_on(db_session, scenario.org_id)
    assert await _routable(db_session, a_account.id) == installation_id
    assert await _routable(db_session, b_account) == installation_id


async def test_a_delivery_runs_the_schedules_of_every_org_holding_it_and_no_other(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    """Org A and org B each proved the installation; org C did not, though a
    schedule there carries its id as a routing key. A pull request runs A's
    and B's schedules and not C's."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.schedule.services.webhook_handler import WebhookHandler

    installation_id = _installation_id()
    org_b, _ = await _both_hold(scenario, db_session, github_sees, installation_id)
    pod_a = await scenario.create_pod(name_prefix="Several A")
    _, c_headers, org_c = await _org_of_someone_else(scenario, "several-c")

    async def _pod(client_headers, org_id, prefix):
        created = await scenario.async_client.post(
            "/pods",
            headers=client_headers,
            json={"organization_id": org_id, "name": f"{prefix} {uuid4().hex[:6]}"},
        )
        assert created.status_code == 201, created.text
        return UUID(created.json()["id"])

    pod_b = await _pod(auth_headers(scenario.owner_user), org_b, "Several B")
    pod_c = await _pod(c_headers, org_c, "Several C")
    routing = {"source": "github", "installation_id": installation_id}
    schedules = {
        name: SimpleNamespace(
            id=uuid4(), pod_id=pod_id, config=routing, pod_connector_slug=None
        )
        for name, pod_id in (("a", UUID(pod_a["id"])), ("b", pod_b), ("c", pod_c))
    }

    class _Matcher:
        async def match_criteria(self, _criteria):
            return list(schedules.values())

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
    assert normalized.organization_ids == frozenset({scenario.org_id, org_b})

    await handler.handle_webhook(
        source="github", payload=normalized.payload, normalized=normalized
    )

    assert sorted(map(str, fired)) == sorted(
        [str(schedules["a"].id), str(schedules["b"].id)]
    ), "a pull request ran a schedule in an organization that does not hold it"


async def test_uninstalling_lets_go_of_it_in_every_org(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    from app.modules.connectors.infrastructure.webhook_sources.github import (
        _retire_installation,
    )

    installation_id = _installation_id()
    org_b, b_account = await _both_hold(
        scenario, db_session, github_sees, installation_id
    )

    await _retire_installation(installation_id, action="deleted")

    assert await _holders(db_session, installation_id) == set()
    assert await _routable(db_session, b_account) is None
    assert await _verified_delivery(installation_id) is None


async def test_an_org_lets_go_of_its_own_binding_and_the_other_keeps_its(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    installation_id = _installation_id()
    org_b, b_account = await _both_hold(
        scenario, db_session, github_sees, installation_id
    )
    url = f"/organizations/{org_b}/connectors/github/installations/{installation_id}"

    released = await scenario.owner_client.delete(url)

    assert released.status_code == 200, released.text
    assert await _holders(db_session, installation_id) == {UUID(scenario.org_id)}
    [b] = await _accounts_on(db_session, org_b)
    assert str(b.id) == b_account
    assert b.external_ref is None
    [a] = await _accounts_on(db_session, scenario.org_id)
    assert await _routable(db_session, a.id) == installation_id
    normalized = await _verified_delivery(installation_id)
    assert normalized is not None
    assert normalized.organization_ids == frozenset({scenario.org_id})

    again = await scenario.owner_client.delete(url)
    assert again.status_code == 404, again.text
    assert again.json()["code"] == "GITHUB_INSTALLATION_NOT_HELD"


async def test_only_someone_who_manages_installations_may_let_go(
    scenario,
    db_session,
    github_sees,  # noqa: F811
):
    """Letting go stops every schedule and account in the organization on it:
    it takes `org.installation.manage`, and a person outside the organization
    learns nothing about what it holds."""
    installation_id = _installation_id()
    await _org_a_binds(scenario, db_session, github_sees, installation_id)
    member = await scenario.create_user("several-member")
    await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=member,
    )
    outsider, outsider_headers, _ = await _org_of_someone_else(
        scenario, "several-outsider"
    )
    url = (
        f"/organizations/{scenario.org_id}/connectors/github/installations/"
        f"{installation_id}"
    )

    for headers in (auth_headers(member), outsider_headers):
        refused = await scenario.async_client.delete(url, headers=headers)
        assert refused.status_code in {403, 404}, refused.text
        assert refused.json()["code"] != "GITHUB_INSTALLATION_NOT_HELD"

    assert await _holders(db_session, installation_id) == {UUID(scenario.org_id)}


async def test_the_bot_acts_in_each_org_only_on_a_binding_proven_there(
    scenario, db_session
):
    """APP mode reads the organization's own proven bindings. Two
    organizations that each proved the installation both have it; one holding
    it only on a binding migration 0063 derived does not."""
    from app.modules.connectors.infrastructure.models.connector_installation import (
        ConnectorInstallation,
    )
    from app.modules.connectors.services.auth.github_app_installations import (
        bound_app_installations,
    )

    installation_id = _installation_id()
    await scenario.create_org(name_prefix="Several bot A")
    org_a = scenario.org_id
    org_b = await _second_org(scenario)
    _, _, org_derived = await _org_of_someone_else(scenario, "several-derived")
    db_session.add(
        ConnectorInstallation(
            connector_id="github",
            external_id=installation_id,
            organization_id=UUID(org_derived),
            proven_at=None,
        )
    )
    await db_session.commit()
    for org_id in (org_a, org_b):
        await claim_installation(
            db_session,
            installation_id=installation_id,
            organization_id=UUID(org_id),
            user_id=UUID(scenario.owner_user["id"]),
            administered=True,
        )
    await db_session.commit()

    async def _bot_sees(org_id):
        return [
            one.installation_id
            for one in await bound_app_installations(db_session, UUID(org_id))
        ]

    assert await _bot_sees(org_a) == [installation_id]
    assert await _bot_sees(org_b) == [installation_id]
    assert await _bot_sees(org_derived) == []
