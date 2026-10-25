"""Deterministic guards for idempotent Telegram webhook registration (Bug C)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.infrastructure.db.session_uow import SESSION_UOW_KEY
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfaceConfig,
    SurfacePlatform,
)
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


class _Telegram:
    """The Bot API, as far as registering a webhook goes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        #: Bot API methods and the registration's commits, as they happened.
        self.order: list[str] = []
        self.reported_url = WEBHOOK_URL
        self.refuses_set = False

    async def answer(
        self, method: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.calls.append((method, dict(payload)))
        self.order.append(method)
        if method == "setWebhook" and self.refuses_set:
            raise TelegramApiError(
                method=method, status_code=400, description="bad webhook"
            )
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": self.reported_url}}
        return {"ok": True, "result": True}


@pytest.fixture
def telegram(monkeypatch) -> _Telegram:
    bot = _Telegram()

    async def call(_self, method, payload, client=None):
        return await bot.answer(method, payload)

    monkeypatch.setattr(TelegramClient, "call", call)
    return bot


class _Uow:
    """Records the commits the registration makes, in order with the calls."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def commit(self) -> None:
        self._order.append("commit")


def _committing_service(order: list[str]) -> AgentSurfaceService:
    repository = AsyncMock()
    repository.session = SimpleNamespace(info={SESSION_UOW_KEY: _Uow(order)})
    service = AgentSurfaceService(
        surface_repository=repository, account_binding_resolver=AsyncMock()
    )
    service._build_public_surface_webhook_url = lambda _id: WEBHOOK_URL
    return service


async def test_an_unconfirmed_webhook_is_reported_not_refused(telegram, caplog):
    """``setWebhook`` was accepted, so Telegram is posting with this secret.

    Raising here rolled the surface back, and Telegram went on posting to a
    surface that no longer existed. The row now stays, and the mismatch is said.
    """
    telegram.reported_url = "https://stale.example/webhook"

    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="s",
    )

    assert "telegram_webhook_mismatch" in caplog.text


def _surface_with_secret() -> AgentSurfaceEntity:
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
        pod_connector_slug="own-bot",
    )
    surface.configure_webhook_secret(secret="the-secret")
    return surface


async def test_the_row_is_committed_before_telegram_is_called(telegram):
    service = _committing_service(telegram.order)

    await service._register_committed_telegram_webhook(
        _surface_with_secret(),
        credentials={"bot_token": "T"},
        drop_pending_updates=False,
    )

    # The commit comes first: Telegram posts to the secret as soon as it is set.
    assert telegram.order[0] == "commit"
    assert telegram.order.index("commit") < telegram.order.index("setWebhook")


async def test_a_refused_registration_is_recorded_on_the_committed_row(telegram):
    telegram.refuses_set = True
    service = _committing_service(telegram.order)
    surface = _surface_with_secret()

    with pytest.raises(AgentSurfacePlatformError):
        await service._register_committed_telegram_webhook(
            surface, credentials={"bot_token": "T"}, drop_pending_updates=False
        )

    saved = service.surface_repository.update.await_args.args[0]
    assert saved.status is AgentSurfaceStatus.ERROR
    assert saved.webhook_secret.get_secret_value() == "the-secret"
    # Once before the call, and again so the ERROR is not rolled back with it.
    assert telegram.order[0] == telegram.order[-1] == "commit"


async def test_re_registering_keeps_the_messages_telegram_is_holding(telegram):
    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="s",
        drop_pending_updates=False,
    )

    assert all(
        payload["drop_pending_updates"] is False
        for method, payload in telegram.calls
        if method in {"deleteWebhook", "setWebhook"}
    )
