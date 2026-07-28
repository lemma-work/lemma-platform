from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    SurfaceConfig,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.errors import (
    TelegramManagedBotSetupNotFoundError,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagedBotSetup,
    TelegramManagedBotSetupStatus,
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    TelegramMiniApp,
)

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self) -> None:
        self.by_id: dict[str, TelegramManagedBotSetup] = {}
        self.by_request: dict[int, str] = {}
        self.by_telegram_user: dict[int, str] = {}

    async def create(self, setup: TelegramManagedBotSetup) -> bool:
        self.by_id[setup.setup_id] = setup.model_copy(deep=True)
        self.by_request[setup.request_id] = setup.setup_id
        return True

    async def get(self, setup_id: str) -> TelegramManagedBotSetup | None:
        setup = self.by_id.get(setup_id)
        return setup.model_copy(deep=True) if setup else None

    async def get_by_request_id(
        self, request_id: int
    ) -> TelegramManagedBotSetup | None:
        setup_id = self.by_request.get(request_id)
        return await self.get(setup_id) if setup_id else None

    async def get_by_telegram_user_id(
        self, telegram_user_id: int
    ) -> TelegramManagedBotSetup | None:
        setup_id = self.by_telegram_user.get(telegram_user_id)
        return await self.get(setup_id) if setup_id else None

    async def save(self, setup: TelegramManagedBotSetup) -> None:
        self.by_id[setup.setup_id] = setup.model_copy(deep=True)
        self.by_request[setup.request_id] = setup.setup_id
        if setup.telegram_user_id is not None:
            self.by_telegram_user[setup.telegram_user_id] = setup.setup_id


def _service(store: _Store) -> TelegramManagerService:
    return TelegramManagerService(
        uow_factory=lambda: None,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        manager_token="manager-token",
        manager_username="@lemma_manager_bot",
        api_base_url="http://telegram.test/bot",
    )


async def _start(
    service: TelegramManagerService,
    *,
    user_id: UUID | None = None,
) -> TelegramManagedBotSetup:
    return await service.start_setup(
        user_id=user_id or uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        surface_name="telegram-support",
        agent_id=None,
        surface_config=SurfaceConfig(),
        is_enabled=True,
        pod_name="Customer Success",
    )


@pytest.mark.asyncio
async def test_start_setup_builds_short_native_launch_and_suggestions():
    store = _Store()
    service = _service(store)

    setup = await _start(service)

    assert service.launch_url(setup).startswith(
        "https://t.me/lemma_manager_bot?start=surface_"
    )
    assert setup.suggested_bot_username.endswith("_bot")
    assert len(setup.suggested_bot_username) <= 32
    assert setup.status is TelegramManagedBotSetupStatus.PENDING


@pytest.mark.asyncio
async def test_start_message_sends_request_managed_bot_keyboard():
    store = _Store()
    service = _service(store)
    service._client.call = AsyncMock(return_value={"ok": True})
    setup = await _start(service)

    await service.handle_update(
        {
            "message": {
                "text": f"/start surface_{setup.setup_id}",
                "chat": {"id": 99},
                "from": {"id": 77},
            }
        }
    )

    saved = await store.get(setup.setup_id)
    assert saved is not None
    assert saved.status is TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM
    assert saved.telegram_user_id == 77
    payload = service._client.call.await_args.args[1]
    button = payload["reply_markup"]["keyboard"][0][0]
    assert button["request_managed_bot"]["request_id"] == setup.request_id
    assert (
        button["request_managed_bot"]["suggested_username"] + "bot"
        == setup.suggested_bot_username
    )


@pytest.mark.asyncio
async def test_created_managed_bot_persists_surface_and_finishes_setup():
    store = _Store()
    service = _service(store)
    setup = await _start(service)
    setup.telegram_user_id = 77
    await store.save(setup)
    account_id = uuid4()
    surface_id = uuid4()
    service._persist_managed_bot = AsyncMock(
        return_value=(account_id, surface_id)
    )
    service._configure_managed_bot = AsyncMock()

    async def _call(method, payload):
        if method == "getManagedBotToken":
            return {"ok": True, "result": "child-token"}
        return {"ok": True, "result": True}

    service._client.call = AsyncMock(side_effect=_call)
    await service.handle_update(
        {
            "message": {
                "chat": {"id": 99},
                "from": {"id": 77},
                "managed_bot_created": {
                    "bot": {"id": 1234, "username": "surface_bot"},
                },
            }
        }
    )

    saved = await store.get(setup.setup_id)
    assert saved is not None
    assert saved.status is TelegramManagedBotSetupStatus.COMPLETE
    assert saved.account_id == account_id
    assert saved.surface_id == surface_id
    assert saved.bot_username == "surface_bot"
    service._persist_managed_bot.assert_awaited_once()
    persist_args = service._persist_managed_bot.await_args.kwargs
    assert persist_args["setup"].setup_id == setup.setup_id
    assert persist_args["bot_id"] == 1234
    assert persist_args["bot_username"] == "surface_bot"
    assert persist_args["bot_token"] == "child-token"
    service._configure_managed_bot.assert_awaited_once()


@pytest.mark.asyncio
async def test_configure_managed_bot_sets_selected_app_as_web_app(monkeypatch):
    store = _Store()

    class _Uow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    service = TelegramManagerService(
        uow_factory=lambda: _Uow(),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        manager_token="manager-token",
        manager_username="@lemma_manager_bot",
    )
    app_id = uuid4()
    setup = await service.start_setup(
        user_id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        surface_name="telegram-support",
        agent_id=None,
        surface_config=SurfaceConfig(
            telegram=SurfaceTelegramConfig(app_id=app_id)
        ),
        is_enabled=True,
        pod_name="Customer Success",
    )
    child = SimpleNamespace(call=AsyncMock(), call_multipart=AsyncMock())
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator.TelegramClient",
        lambda **_: child,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator."
        "resolve_telegram_mini_app",
        AsyncMock(
            return_value=TelegramMiniApp(
                app_id=app_id,
                name="support-queue",
                url="https://support.apps.example.test",
            )
        ),
    )

    await service._configure_managed_bot(
        setup=setup,
        bot_token="child-token",
    )

    calls = {call.args[0]: call.args[1] for call in child.call.await_args_list}
    assert calls["setChatMenuButton"]["menu_button"] == {
        "type": "web_app",
        "text": "Open Support Queue",
        "web_app": {"url": "https://support.apps.example.test"},
    }
    commands = calls["setMyCommands"]["commands"]
    assert {command["command"] for command in commands} == {"help", "retry"}


@pytest.mark.asyncio
async def test_get_setup_is_scoped_to_requesting_user_and_pod():
    store = _Store()
    service = _service(store)
    setup = await _start(service)

    with pytest.raises(TelegramManagedBotSetupNotFoundError):
        await service.get_setup(
            setup_id=setup.setup_id,
            user_id=uuid4(),
            pod_id=setup.pod_id,
        )


@pytest.mark.asyncio
async def test_persist_managed_bot_bootstraps_native_auth_config_and_commits(
    monkeypatch,
):
    store = _Store()
    setup_service = _service(store)
    setup = await _start(setup_service)
    setup.telegram_user_id = 77
    account_id = uuid4()
    surface_id = uuid4()

    class _Uow:
        def __init__(self):
            self.session = object()
            self.commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    uow = _Uow()
    service = TelegramManagerService(
        uow_factory=lambda: uow,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        manager_token="manager-token",
        manager_username="@lemma_manager_bot",
    )
    auth_configs = SimpleNamespace(
        get_active_by_org_and_app=AsyncMock(return_value=None),
        create=AsyncMock(side_effect=lambda entity: entity),
    )
    accounts = SimpleNamespace(
        get_by_user_auth_config_and_provider_account=AsyncMock(return_value=None),
        get_by_user_and_auth_config=AsyncMock(return_value=None),
        create=AsyncMock(
            side_effect=lambda entity: entity.model_copy(update={"id": account_id})
        ),
    )
    surface_service = SimpleNamespace(
        create_surface=AsyncMock(return_value=SimpleNamespace(id=surface_id)),
    )
    external_users = SimpleNamespace(
        get_by_identity=AsyncMock(return_value=None),
        upsert=AsyncMock(),
    )

    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_persistence."
        "AuthConfigRepository",
        lambda **_: auth_configs,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_persistence."
        "AccountRepository",
        lambda **_: accounts,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.api.dependencies.get_surface_service",
        lambda _: surface_service,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_identity."
        "ExternalSurfaceUserRepository",
        lambda _: external_users,
    )

    persisted_account_id, persisted_surface_id = (
        await service._persist_managed_bot(
            setup=setup,
            bot_id=1234,
            bot_username="surface_bot",
            bot_token="child-token",
        )
    )

    assert persisted_account_id == account_id
    assert persisted_surface_id == surface_id
    auth_config = auth_configs.create.await_args.args[0]
    assert auth_config.connector_id == "telegram"
    assert auth_config.organization_id == setup.organization_id
    assert auth_config.name == "telegram"
    assert accounts.create.await_args.args[0].credentials.bot_token == "child-token"
    assert external_users.upsert.await_args.kwargs["external_user_id"] == "77"
    assert external_users.upsert.await_args.kwargs["resolved_user_id"] == setup.user_id
    uow.commit.assert_awaited_once()
