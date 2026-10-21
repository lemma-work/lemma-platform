"""``ConnectorAccountConnected``: what the OAuth callback announces, and when.

Through the real connect-request route and callback, with the provider faked:
a new grant and a renewed one each announce the fact in the callback's own
transaction; it carries the purpose its starter recorded; and it says whether
the browser that came back carried the starter's own session.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.contracts.facts import ConnectorAccountConnected
from app.modules.connectors.contracts.own_apps import (
    grant_is_browser_bound,
    start_authorization,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connect_request import (
    ConnectRequest,
)
from app.modules.connectors.services import oauth_callback
from app.modules.connectors.tests.e2e.test_accounts_connect_requests_e2e import (
    _install_fake_auth_provider,
    _live_state,
    _oauth_install,
)
from app.modules.connectors.tests.support.fake_auth_provider import FakeAuthProvider
from app.modules.test_support.e2e_base import _sign_up_verified_user

pytestmark = pytest.mark.e2e


class _Announced:
    """Stands in for the fact bus at the callback: records what it is told."""

    def __init__(self) -> None:
        self.facts: list[ConnectorAccountConnected] = []

    async def emit(self, uow, fact, *, writer=None) -> int:
        assert isinstance(fact, ConnectorAccountConnected)
        self.facts.append(fact)
        return 0


@pytest.fixture
def announced(monkeypatch) -> _Announced:
    recorder = _Announced()
    monkeypatch.setattr(oauth_callback, "facts", recorder)
    return recorder


async def _install_id(db_session, org_id: str, connector_id: str) -> UUID:
    found = await db_session.scalar(
        select(AuthConfig.id).where(
            AuthConfig.organization_id == UUID(org_id),
            AuthConfig.connector_id == connector_id,
        )
    )
    assert found is not None
    return found


async def test_a_new_and_a_renewed_grant_each_announce_the_account(
    authenticated_client, fixed_test_org, db_session, monkeypatch, announced
):
    org_id = fixed_test_org["id"]
    connector_id = await _oauth_install(authenticated_client, db_session, org_id)
    _install_fake_auth_provider(monkeypatch, FakeAuthProvider())

    async def connect() -> dict:
        started = await authenticated_client.post(
            f"/organizations/{org_id}/connectors/connect-requests",
            json={"connector_id": connector_id},
        )
        assert started.status_code == 200, started.text
        state = await _live_state(db_session, started.json())
        back = await authenticated_client.get(
            "/connectors/connect-requests/oauth/callback",
            params={"state": state, "code": "same-person", "format": "json"},
        )
        assert back.status_code == 200, back.text
        return back.json()

    first = await connect()
    await db_session.execute(
        Account.__table__.update()
        .where(Account.id == UUID(first["id"]))
        .values(status="REAUTH_REQUIRED")
    )
    await db_session.commit()
    again = await connect()

    assert again["id"] == first["id"]
    assert [(str(f.account_id), f.renewed) for f in announced.facts] == [
        (first["id"], False),
        (first["id"], True),
    ]
    fact = announced.facts[0]
    assert fact.connector_id == connector_id
    assert str(fact.organization_id) == org_id
    assert fact.purpose is None
    # The person who started it came back with their own session.
    assert fact.browser_bound is True
    uow = SqlAlchemyUnitOfWork(db_session)
    assert await grant_is_browser_bound(uow, account_id=UUID(first["id"]))


async def test_a_purpose_is_carried_and_another_browser_is_not_bound(
    authenticated_client,
    async_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
    monkeypatch,
    announced,
):
    """The consent link sent to someone else: their browser, the starter's state."""
    org_id = fixed_test_org["id"]
    connector_id = await _oauth_install(authenticated_client, db_session, org_id)
    _install_fake_auth_provider(monkeypatch, FakeAuthProvider())
    install_id = await _install_id(db_session, org_id, connector_id)
    url = await start_authorization(
        SqlAlchemyUnitOfWork(db_session),
        user_id=UUID(fixed_test_user["id"]),
        organization_id=UUID(org_id),
        connector_id=connector_id,
        auth_config_id=install_id,
        purpose="slack_teammate_app",
    )
    assert url
    state = await db_session.scalar(
        select(ConnectRequest.attributes["state"].astext)
        .where(ConnectRequest.auth_config_id == install_id)
        .limit(1)
    )
    someone_else = await _sign_up_verified_user(async_client)

    back = await async_client.get(
        "/connectors/connect-requests/oauth/callback",
        params={"state": state, "code": f"c-{uuid4().hex}", "format": "json"},
        headers={"Authorization": f"Bearer {someone_else['token']}"},
    )

    assert back.status_code == 200, back.text
    (fact,) = announced.facts
    assert fact.purpose == "slack_teammate_app"
    assert str(fact.user_id) == fixed_test_user["id"]
    assert fact.browser_bound is False
    uow = SqlAlchemyUnitOfWork(db_session)
    assert not await grant_is_browser_bound(uow, account_id=fact.account_id)
