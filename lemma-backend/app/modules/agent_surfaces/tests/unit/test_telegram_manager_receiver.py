from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agent_surfaces.module import _telegram_manager_webhook_lifespan
from app.modules.agent_surfaces.services.telegram_manager_receiver import (
    TelegramManagerPollingReceiver,
    run_telegram_manager_webhook_registration,
)
from app.modules.agent_surfaces.platforms.telegram.client import TelegramApiError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_api_lifespan_yields_while_webhook_registration_is_pending(
    monkeypatch,
):
    started = asyncio.Event()

    async def _pending_registration():
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver."
        "run_telegram_manager_webhook_registration",
        _pending_registration,
    )

    async with _telegram_manager_webhook_lifespan(None):
        await asyncio.wait_for(started.wait(), timeout=1)


def _polling_receiver():
    receiver = TelegramManagerPollingReceiver(
        uow_factory=lambda: None,  # type: ignore[arg-type]
        store=AsyncMock(),
        manager_token="manager-token",
        manager_username="manager_bot",
    )
    receiver.should_start = lambda: True
    receiver._store.load_offset = AsyncMock(return_value=None)
    receiver._store.save_offset = AsyncMock()
    receiver._service.handle_update = AsyncMock()
    return receiver


@pytest.mark.asyncio
async def test_polling_saves_offset_only_after_successful_handling():
    receiver = _polling_receiver()
    updates_returned = False

    async def _call(method, payload):
        nonlocal updates_returned
        del payload
        if method == "deleteWebhook":
            return {"ok": True, "result": True}
        if not updates_returned:
            updates_returned = True
            return {"ok": True, "result": [{"update_id": 17, "message": {}}]}
        raise asyncio.CancelledError

    receiver._client.call = AsyncMock(side_effect=_call)

    with pytest.raises(asyncio.CancelledError):
        await receiver.run()

    receiver._service.handle_update.assert_awaited_once()
    receiver._store.save_offset.assert_awaited_once_with(18)


@pytest.mark.asyncio
async def test_polling_keeps_offset_when_handling_fails(monkeypatch):
    receiver = _polling_receiver()
    receiver._client.call = AsyncMock(
        side_effect=[
            {"ok": True, "result": True},
            {"ok": True, "result": [{"update_id": 17, "message": {}}]},
        ]
    )
    receiver._service.handle_update = AsyncMock(side_effect=RuntimeError("transient"))
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await receiver.run()

    receiver._store.save_offset.assert_not_awaited()


@pytest.mark.asyncio
async def test_polling_keeps_offset_when_persisting_offset_fails(monkeypatch):
    receiver = _polling_receiver()
    payloads = []
    update_calls = 0

    async def _call(method, payload):
        nonlocal update_calls
        if method == "deleteWebhook":
            return {"ok": True, "result": True}
        payloads.append(payload)
        update_calls += 1
        if update_calls == 1:
            return {"ok": True, "result": [{"update_id": 17, "message": {}}]}
        raise asyncio.CancelledError

    receiver._client.call = AsyncMock(side_effect=_call)
    receiver._store.save_offset = AsyncMock(
        side_effect=RuntimeError("redis unavailable")
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver.asyncio.sleep",
        AsyncMock(),
    )

    with pytest.raises(asyncio.CancelledError):
        await receiver.run()

    assert payloads == [
        {
            "timeout": 30,
            "allowed_updates": ["message", "managed_bot"],
        },
        {
            "timeout": 30,
            "allowed_updates": ["message", "managed_bot"],
        },
    ]


@pytest.mark.asyncio
async def test_webhook_registration_retries_network_failure(monkeypatch):
    request = httpx.Request("POST", "https://api.telegram.org")
    register = AsyncMock(
        side_effect=[httpx.ConnectError("offline", request=request), None]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver."
        "register_telegram_manager_webhook",
        register,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver.asyncio.sleep",
        sleep,
    )

    await run_telegram_manager_webhook_registration()

    assert register.await_count == 2
    sleep.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500])
async def test_webhook_registration_retries_transient_telegram_error(
    monkeypatch,
    status_code,
):
    register = AsyncMock(
        side_effect=[
            TelegramApiError(
                method="setWebhook",
                status_code=status_code,
                description="temporary",
                retry_after=7 if status_code == 429 else None,
            ),
            None,
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver."
        "register_telegram_manager_webhook",
        register,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver.asyncio.sleep",
        sleep,
    )

    await run_telegram_manager_webhook_registration()

    assert register.await_count == 2
    sleep.assert_awaited_once_with(7 if status_code == 429 else 5.0)


@pytest.mark.asyncio
async def test_webhook_registration_stops_on_permanent_telegram_error(
    monkeypatch,
):
    register = AsyncMock(
        side_effect=TelegramApiError(
            method="setWebhook",
            status_code=400,
            description="bad webhook",
        )
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver."
        "register_telegram_manager_webhook",
        register,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_manager_receiver.asyncio.sleep",
        sleep,
    )

    await run_telegram_manager_webhook_registration()

    register.assert_awaited_once()
    sleep.assert_not_awaited()
