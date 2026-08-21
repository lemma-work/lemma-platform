"""Deterministic guards for idempotent Telegram webhook registration (Bug C)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
)
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService

pytestmark = pytest.mark.asyncio

WEBHOOK_URL = "https://surface-e2e.test/surfaces/abc/webhook"


def _service() -> AgentSurfaceService:
    return AgentSurfaceService(
        surface_repository=AsyncMock(),
        account_binding_resolver=AsyncMock(),
    )


async def test_register_webhook_deletes_then_sets_then_verifies_with_retry(monkeypatch):
    state = {"calls": [], "set_attempts": 0}

    async def fake_call(self, method, payload, client=None):
        state["calls"].append((method, dict(payload)))
        if method == "setWebhook":
            state["set_attempts"] += 1
            if state["set_attempts"] == 1:
                # Transient first failure — registration must retry.
                raise TelegramApiError(method=method, status_code=429, retry_after=0.01)
            return {"ok": True, "result": True}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": WEBHOOK_URL}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T", "api_base_url": "http://fake/bot"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="the-secret",
    )

    methods = [m for m, _ in state["calls"]]
    # deleteWebhook (with drop_pending_updates) precedes setWebhook precedes verify.
    assert methods[0] == "deleteWebhook"
    assert state["calls"][0][1]["drop_pending_updates"] is True
    assert methods.index("setWebhook") < methods.index("getWebhookInfo")
    assert state["set_attempts"] == 2  # retried once after the 429

    set_payload = next(p for m, p in state["calls"] if m == "setWebhook")
    assert set_payload["secret_token"] == "the-secret"
    assert set_payload["url"] == WEBHOOK_URL
    assert set_payload["allowed_updates"]
    assert set_payload["drop_pending_updates"] is True


async def test_register_webhook_raises_with_telegram_description(monkeypatch):
    async def fake_call(self, method, payload, client=None):
        if method == "setWebhook":
            raise TelegramApiError(
                method=method,
                status_code=400,
                description="Bad Request: bad webhook: HTTPS url must be provided",
            )
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    with pytest.raises(AgentSurfacePlatformError) as exc_info:
        await _service()._register_telegram_webhook(
            credentials={"bot_token": "T"},
            webhook_url=WEBHOOK_URL,
            webhook_secret="s",
        )
    # The real Telegram description is preserved (no longer a generic error).
    assert "HTTPS url must be provided" in str(exc_info.value)


async def test_register_webhook_raises_when_url_not_confirmed(monkeypatch):
    async def fake_call(self, method, payload, client=None):
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": "https://stale.example/webhook"}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    with pytest.raises(AgentSurfacePlatformError) as exc_info:
        await _service()._register_telegram_webhook(
            credentials={"bot_token": "T"},
            webhook_url=WEBHOOK_URL,
            webhook_secret="s",
        )
    assert "did not confirm" in str(exc_info.value)
