"""Unit tests for the datastore changes WebSocket controller.

Focuses on error-handling paths that are hard to reproduce with the full
E2E stack: specifically the client-disconnect race that surfaces as a
RuntimeError when uvicorn's state machine rejects websocket.accept().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.datastore.api.controllers.changes_controller import (
    datastore_changes_ws,
)

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

    factory_class = MagicMock(return_value=factory_instance)
    return factory_class


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
