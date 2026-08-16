"""The CLI session endpoints, which had no coverage at all.

They were moved out of `auth_controller` and switched from a request-scoped unit
of work to a factory, so that minting or refreshing a SuperTokens session -- an
HTTP round trip -- stops running with a pooled connection checked out. That
change was made against zero tests, which is worth fixing on its own.

The ordering assertions are the interesting ones. Whether a connection is held
across the token exchange is not something a unit test can see directly, but the
*shape* that guarantees it is: the unit-of-work scope must be closed before the
SuperTokens call starts. Asserting on that ordering catches a future edit that
pulls the exchange back inside the scope, which is exactly how this got here.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.identity.api.controllers import cli_auth_controller as ctrl


@pytest.fixture
def wiring(monkeypatch):
    """Record the order of scope open/close against the SuperTokens calls."""
    events: list[str] = []
    user_id = uuid4()

    def _uow_factory():
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _scope():
            events.append("scope-open")
            try:
                yield SimpleNamespace(session=None)
            finally:
                events.append("scope-close")

        return _scope()

    async def _get_user(_user_id):
        events.append("db-get-user")
        return SimpleNamespace(email="cli@example.com")

    monkeypatch.setattr(
        ctrl, "get_user_service", lambda _uow: SimpleNamespace(get_user=_get_user)
    )

    async def _create(user, **_kwargs):
        events.append("supertokens-mint")
        return {
            "access_token": "at",
            "refresh_token": "rt",
            "access_token_expires_at": 1,
            "session_handle": "sh",
            "user_id": user,
        }

    async def _refresh(_token):
        events.append("supertokens-refresh")
        return {
            "access_token": "at2",
            "refresh_token": "rt2",
            "access_token_expires_at": 2,
            "session_handle": "sh2",
            "user_id": str(user_id),
        }

    monkeypatch.setattr(ctrl, "create_cli_session_tokens", _create)
    monkeypatch.setattr(ctrl, "refresh_cli_session_tokens", _refresh)

    return SimpleNamespace(events=events, user_id=user_id, uow_factory=_uow_factory)


@pytest.mark.asyncio
async def test_minting_returns_the_tokens_and_the_owner_email(wiring) -> None:
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=wiring.user_id)))

    response = await ctrl.cli_session_tokens(request, uow_factory=wiring.uow_factory)

    assert response.access_token == "at"
    assert response.email == "cli@example.com"
    assert response.user_id == wiring.user_id


@pytest.mark.asyncio
async def test_the_mint_happens_after_the_scope_closes(wiring) -> None:
    """The connection must be back in the pool before the HTTP call starts."""
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=wiring.user_id)))

    await ctrl.cli_session_tokens(request, uow_factory=wiring.uow_factory)

    assert wiring.events == [
        "scope-open",
        "db-get-user",
        "scope-close",
        "supertokens-mint",
    ], f"the SuperTokens mint ran inside the unit-of-work scope: {wiring.events}"


@pytest.mark.asyncio
async def test_refreshing_returns_the_new_tokens_and_the_owner_email(wiring) -> None:
    body = SimpleNamespace(refresh_token="rt")

    response = await ctrl.cli_refresh_session(body, uow_factory=wiring.uow_factory)

    assert response.access_token == "at2"
    assert response.email == "cli@example.com"


@pytest.mark.asyncio
async def test_the_refresh_happens_before_any_scope_opens(wiring) -> None:
    """The database is not consulted until the token has been accepted.

    So holding a connection across the refresh was pure waste -- there was
    nothing to hold it for yet.
    """
    body = SimpleNamespace(refresh_token="rt")

    await ctrl.cli_refresh_session(body, uow_factory=wiring.uow_factory)

    assert wiring.events == [
        "supertokens-refresh",
        "scope-open",
        "db-get-user",
        "scope-close",
    ], f"a connection was open across the refresh: {wiring.events}"


@pytest.mark.asyncio
async def test_a_rejected_refresh_token_is_a_401_not_a_500(wiring, monkeypatch) -> None:
    async def _refuse(_token):
        raise ValueError("nope")

    monkeypatch.setattr(ctrl, "refresh_cli_session_tokens", _refuse)

    with pytest.raises(HTTPException) as exc:
        await ctrl.cli_refresh_session(
            SimpleNamespace(refresh_token="bad"), uow_factory=wiring.uow_factory
        )

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "INVALID_REFRESH_TOKEN"
    # The reason is reported by type, not by leaking the exception's text.
    assert exc.value.detail["details"]["error_type"] == "ValueError"
