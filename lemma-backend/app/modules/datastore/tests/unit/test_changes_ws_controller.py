"""Unit tests for the datastore changes WebSocket controller.

Focuses on error-handling paths that are hard to reproduce with the full
E2E stack: the client-disconnect race that surfaces as a RuntimeError when
uvicorn's state machine rejects websocket.accept(), and the rule that every
rejection is a close *after* accept — a pre-accept close reaches a browser as
1006, which hides the 4401 that tells the client to refresh its token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from app.modules.datastore.api.controllers.changes_controller import (
    CLOSE_UNAUTHENTICATED,
    datastore_changes_ws,
)
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_MODULE = "app.modules.datastore.api.controllers.changes_controller"


def _mock_session(user_id=None):
    s = MagicMock()
    s.get_user_id.return_value = str(user_id or uuid4())
    return s


def _mock_uow_factory(uow):
    """Return a SessionUnitOfWorkFactory-shaped mock that yields *uow*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=uow)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory_instance = MagicMock()
    factory_instance.return_value = cm

    return MagicMock(return_value=factory_instance)


def _make_ws(**accept_kwargs):
    ws = MagicMock()
    ws.headers.get.return_value = None
    ws.cookies.get.return_value = None
    ws.query_params.get.return_value = "test-token"
    ws.accept = AsyncMock(**accept_kwargs)
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive = AsyncMock(side_effect=Exception("disconnected"))
    return ws


def _auth_patches(uow):
    mock_ctx = MagicMock()
    mock_ctx.require = AsyncMock()

    mock_table_svc = AsyncMock()
    mock_table_svc.list_tables = AsyncMock(return_value=([], None))

    mock_auth_svc_instance = MagicMock()
    mock_auth_svc_instance.build_user_context = AsyncMock(return_value=mock_ctx)

    mock_auth_svc_class = MagicMock(return_value=mock_auth_svc_instance)

    mock_build_ts = MagicMock(return_value=mock_table_svc)

    return mock_ctx, mock_auth_svc_class, mock_build_ts


async def test_accept_runtimeerror_does_not_crash():
    """RuntimeError during websocket.accept() must not propagate to the ASGI layer.

    Production uvicorn raises RuntimeError when the client disconnects between
    routing and accept() — the state machine has already moved past the accept
    window.  The handler must catch it and return cleanly.
    """
    pod_id = uuid4()
    ws = _make_ws(side_effect=RuntimeError("Expected ASGI message 'websocket.send'"))

    uow = MagicMock()
    uow.session = MagicMock()
    _, mock_auth_svc, mock_build_ts = _auth_patches(uow)
    mock_factory = _mock_uow_factory(uow)

    with (
        patch(f"{_MODULE}._resolve_session", AsyncMock(return_value=_mock_session())),
        patch(f"{_MODULE}.SessionUnitOfWorkFactory", mock_factory),
        patch(f"{_MODULE}.AuthorizationDataService", mock_auth_svc),
        patch(f"{_MODULE}.build_table_service", mock_build_ts),
    ):
        # Must return None — not raise, not propagate the RuntimeError.
        result = await datastore_changes_ws(ws, pod_id=pod_id, table=None, since=None)

    assert result is None
    ws.accept.assert_awaited_once()
    ws.send_json.assert_not_awaited()  # streaming never started


async def test_accept_runtimeerror_specific_table_does_not_crash():
    """Same race with a table= filter in the query params."""
    pod_id = uuid4()
    ws = _make_ws(side_effect=RuntimeError("websocket state machine"))

    uow = MagicMock()
    uow.session = MagicMock()
    mock_ctx, mock_auth_svc, mock_build_ts = _auth_patches(uow)

    mock_table_entity = MagicMock()
    mock_table_entity.table_name = "notes"
    mock_table_svc = mock_build_ts.return_value
    mock_table_svc.get_table = AsyncMock(return_value=mock_table_entity)

    mock_factory = _mock_uow_factory(uow)

    with (
        patch(f"{_MODULE}._resolve_session", AsyncMock(return_value=_mock_session())),
        patch(f"{_MODULE}.SessionUnitOfWorkFactory", mock_factory),
        patch(f"{_MODULE}.AuthorizationDataService", mock_auth_svc),
        patch(f"{_MODULE}.build_table_service", mock_build_ts),
    ):
        result = await datastore_changes_ws(
            ws, pod_id=pod_id, table="notes", since=None
        )

    assert result is None
    ws.accept.assert_awaited_once()


async def test_accept_succeeds_normally_starts_streaming():
    """Sanity: when accept() succeeds the forwarder task is started."""
    pod_id = uuid4()
    ws = _make_ws()

    uow = MagicMock()
    uow.session = MagicMock()
    _, mock_auth_svc, mock_build_ts = _auth_patches(uow)
    mock_factory = _mock_uow_factory(uow)

    async def _fake_forward_changes(
        websocket, *, pod_id, user_id, allowed_tables, since
    ):
        await websocket.send_json({"type": "ready", "since": "0-0"})

    with (
        patch(f"{_MODULE}._resolve_session", AsyncMock(return_value=_mock_session())),
        patch(f"{_MODULE}.SessionUnitOfWorkFactory", mock_factory),
        patch(f"{_MODULE}.AuthorizationDataService", mock_auth_svc),
        patch(f"{_MODULE}.build_table_service", mock_build_ts),
        patch(f"{_MODULE}._forward_changes", _fake_forward_changes),
    ):
        await datastore_changes_ws(ws, pod_id=pod_id, table=None, since=None)

    ws.accept.assert_awaited_once()
    ws.send_json.assert_awaited_once_with({"type": "ready", "since": "0-0"})


def _handshake_calls(ws) -> list:
    """The accept/close calls on *ws*, in the order the handler made them."""
    return [c for c in ws.mock_calls if c[0] in ("accept", "close")]


async def test_missing_token_accepts_then_closes_4401():
    """No token at all: accept, then close 4401 — never a pre-accept close."""
    ws = _make_ws()
    ws.query_params.get.return_value = None

    await datastore_changes_ws(ws, pod_id=uuid4(), table=None, since=None)

    assert _handshake_calls(ws) == [
        call.accept(),
        call.close(
            code=CLOSE_UNAUTHENTICATED,
            reason="Unauthorized datastore changes websocket.",
        ),
    ]
    ws.send_json.assert_not_awaited()


async def test_expired_token_accepts_then_closes_4401():
    """An expired access token is the case the client can fix by refreshing."""
    ws = _make_ws()

    with patch(
        f"{_MODULE}._resolve_session",
        AsyncMock(side_effect=TryRefreshTokenError("expired")),
    ):
        await datastore_changes_ws(ws, pod_id=uuid4(), table=None, since=None)

    assert _handshake_calls(ws) == [
        call.accept(),
        call.close(
            code=CLOSE_UNAUTHENTICATED,
            reason="Access token expired. Refresh your session and reconnect.",
        ),
    ]


async def test_forbidden_pod_accepts_then_closes_4403():
    """Authorization failures are also post-accept, so 4403 reaches the client."""
    ws = _make_ws()

    uow = MagicMock()
    uow.session = MagicMock()
    mock_ctx, mock_auth_svc, mock_build_ts = _auth_patches(uow)
    mock_ctx.require = AsyncMock(side_effect=DatastoreAccessDeniedError())

    with (
        patch(f"{_MODULE}._resolve_session", AsyncMock(return_value=_mock_session())),
        patch(f"{_MODULE}.SessionUnitOfWorkFactory", _mock_uow_factory(uow)),
        patch(f"{_MODULE}.AuthorizationDataService", mock_auth_svc),
        patch(f"{_MODULE}.build_table_service", mock_build_ts),
    ):
        await datastore_changes_ws(ws, pod_id=uuid4(), table=None, since=None)

    assert _handshake_calls(ws) == [
        call.accept(),
        call.close(code=4403, reason="Access denied"),
    ]
    ws.send_json.assert_not_awaited()
