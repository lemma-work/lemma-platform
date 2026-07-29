from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.entities import (
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.errors import (
    TelegramManagedBotSetupAlreadyInProgressError,
    TelegramManagedBotSetupNotFoundError,
)
from app.modules.agent_surfaces.services.telegram_manager_service import (
    TelegramManagedBotProvisioningClaim,
    TelegramManagedBotProvisioningInProgressError,
    TelegramManagedBotSetup,
    TelegramManagedBotSetupStore,
    TelegramManagedBotSetupStatus,
    TelegramManagerService,
)
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    TelegramMiniApp,
)
from app.modules.agent_surfaces.platforms.telegram.client import TelegramApiError

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self) -> None:
        self.by_id: dict[str, TelegramManagedBotSetup] = {}
        self.by_request: dict[int, str] = {}
        self.by_telegram_user: dict[int, str] = {}
        self.by_target: dict[tuple[UUID, str], str] = {}
        self.bot_by_setup: dict[str, int] = {}
        self.lease_by_setup: dict[str, str] = {}
        self.processed_updates: set[int] = set()

    async def create(self, setup: TelegramManagedBotSetup) -> bool:
        target = (setup.pod_id, setup.surface_name.casefold())
        if target in self.by_target:
            raise TelegramManagedBotSetupAlreadyInProgressError(setup.surface_name)
        self.by_id[setup.setup_id] = setup.model_copy(deep=True)
        self.by_request[setup.request_id] = setup.setup_id
        self.by_target[target] = setup.setup_id
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

    async def bind_telegram_user(
        self,
        *,
        setup_id: str,
        telegram_user_id: int,
    ) -> bool:
        current = self.by_telegram_user.get(telegram_user_id)
        if current is not None and current != setup_id:
            return False
        self.by_telegram_user[telegram_user_id] = setup_id
        return True

    async def save_if_status(
        self,
        setup: TelegramManagedBotSetup,
        *,
        expected: set[TelegramManagedBotSetupStatus],
        owner: str | None = None,
    ) -> bool:
        current = self.by_id.get(setup.setup_id)
        if (
            current is None
            or current.status not in expected
            or (
                owner is not None
                and self.lease_by_setup.get(setup.setup_id) != owner
            )
        ):
            return False
        await self.save(setup)
        return True

    async def claim_provisioning(
        self,
        *,
        setup_id: str,
        bot_id: int,
        owner: str,
    ) -> TelegramManagedBotProvisioningClaim:
        bound = self.bot_by_setup.get(setup_id)
        if bound is not None and bound != bot_id:
            return TelegramManagedBotProvisioningClaim.BOT_CONFLICT
        self.bot_by_setup[setup_id] = bot_id
        if setup_id in self.lease_by_setup:
            return TelegramManagedBotProvisioningClaim.IN_PROGRESS
        self.lease_by_setup[setup_id] = owner
        return TelegramManagedBotProvisioningClaim.ACQUIRED

    async def refresh_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> bool:
        return self.lease_by_setup.get(setup_id) == owner

    async def release_provisioning_lease(
        self,
        *,
        setup_id: str,
        owner: str,
    ) -> None:
        if self.lease_by_setup.get(setup_id) == owner:
            self.lease_by_setup.pop(setup_id)

    async def release_reservations(
        self,
        setup: TelegramManagedBotSetup,
    ) -> None:
        target = (setup.pod_id, setup.surface_name.casefold())
        if self.by_target.get(target) == setup.setup_id:
            self.by_target.pop(target)
        if (
            setup.telegram_user_id is not None
            and self.by_telegram_user.get(setup.telegram_user_id) == setup.setup_id
        ):
            self.by_telegram_user.pop(setup.telegram_user_id)

    async def is_update_processed(self, update_id: int) -> bool:
        return update_id in self.processed_updates

    async def mark_update_processed(self, update_id: int) -> None:
        self.processed_updates.add(update_id)


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
    pod_id: UUID | None = None,
    surface_name: str = "telegram-support",
) -> TelegramManagedBotSetup:
    return await service.start_setup(
        user_id=user_id or uuid4(),
        organization_id=uuid4(),
        pod_id=pod_id or uuid4(),
        surface_name=surface_name,
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
    setup.status = TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM
    await store.save(setup)
    await store.bind_telegram_user(
        setup_id=setup.setup_id,
        telegram_user_id=77,
    )
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
async def test_second_setup_cannot_replace_active_telegram_user_binding():
    store = _Store()
    service = _service(store)
    service._client.call = AsyncMock(return_value={"ok": True, "result": True})
    first = await _start(service, surface_name="telegram-first")
    second = await _start(service, surface_name="telegram-second")

    await service.handle_update(
        {
            "update_id": 1,
            "message": {
                "text": f"/start surface_{first.setup_id}",
                "chat": {"id": 99},
                "from": {"id": 77},
            },
        }
    )
    await service.handle_update(
        {
            "update_id": 2,
            "message": {
                "text": f"/start surface_{second.setup_id}",
                "chat": {"id": 99},
                "from": {"id": 77},
            },
        }
    )

    active = await store.get_by_telegram_user_id(77)
    rejected = await store.get(second.setup_id)
    assert active is not None
    assert active.setup_id == first.setup_id
    assert rejected is not None
    assert rejected.status is TelegramManagedBotSetupStatus.FAILED
    assert (second.pod_id, second.surface_name.casefold()) not in store.by_target


@pytest.mark.asyncio
async def test_concurrent_created_updates_provision_once_and_finish_complete():
    store = _Store()
    service = _service(store)
    service._client.call = AsyncMock(
        side_effect=lambda method, payload: (
            {"ok": True, "result": "child-token"}
            if method == "getManagedBotToken"
            else {"ok": True, "result": True}
        )
    )
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
    account_id = uuid4()
    surface_id = uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _persist(**kwargs):
        del kwargs
        entered.set()
        await release.wait()
        return account_id, surface_id

    service._persist_managed_bot = AsyncMock(side_effect=_persist)
    service._configure_managed_bot = AsyncMock()
    update = {
        "update_id": 12,
        "message": {
            "chat": {"id": 99},
            "from": {"id": 77},
            "managed_bot_created": {
                "bot": {"id": 1234, "username": "surface_bot"},
            },
        },
    }

    first_task = asyncio.create_task(service.handle_update(update))
    await entered.wait()
    second_result = await asyncio.gather(
        service.handle_update(update),
        return_exceptions=True,
    )
    release.set()
    assert await first_task is None

    assert isinstance(
        second_result[0],
        TelegramManagedBotProvisioningInProgressError,
    )
    saved = await store.get(setup.setup_id)
    assert saved is not None
    assert saved.status is TelegramManagedBotSetupStatus.COMPLETE
    service._persist_managed_bot.assert_awaited_once()
    service._configure_managed_bot.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_failures_can_replay_same_bot():
    store = _Store()
    service = _service(store)
    setup = await _start(service)
    service._client.call = AsyncMock(return_value={"ok": True})
    await service.handle_update(
        {
            "message": {
                "text": f"/start surface_{setup.setup_id}",
                "chat": {"id": 99},
                "from": {"id": 77},
            }
        }
    )
    account_id = uuid4()
    surface_id = uuid4()
    service._persist_managed_bot = AsyncMock(
        return_value=(account_id, surface_id)
    )
    service._configure_managed_bot = AsyncMock()
    service._client.call = AsyncMock(
        side_effect=TelegramApiError(
            method="getManagedBotToken",
            status_code=500,
            description="temporary",
        )
    )
    update = {
        "update_id": 21,
        "message": {
            "chat": {"id": 99},
            "from": {"id": 77},
            "managed_bot_created": {
                "bot": {"id": 1234, "username": "surface_bot"},
            },
        },
    }

    with pytest.raises(TelegramApiError):
        await service.handle_update(update)

    interrupted = await store.get(setup.setup_id)
    assert interrupted is not None
    assert interrupted.status is TelegramManagedBotSetupStatus.PROVISIONING
    assert 21 not in store.processed_updates

    service._client.call = AsyncMock(
        side_effect=lambda method, payload: (
            {"ok": True, "result": "child-token"}
            if method == "getManagedBotToken"
            else {"ok": True, "result": True}
        )
    )
    service._persist_managed_bot = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.handle_update(update)

    interrupted = await store.get(setup.setup_id)
    assert interrupted is not None
    assert interrupted.status is TelegramManagedBotSetupStatus.PROVISIONING
    assert 21 not in store.processed_updates

    service._persist_managed_bot = AsyncMock(
        return_value=(account_id, surface_id)
    )
    await service.handle_update(update)

    completed = await store.get(setup.setup_id)
    assert completed is not None
    assert completed.status is TelegramManagedBotSetupStatus.COMPLETE
    service._persist_managed_bot.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_update_id_is_not_processed_again():
    store = _Store()
    service = _service(store)
    service._client.call = AsyncMock(return_value={"ok": True})
    setup = await _start(service)
    update = {
        "update_id": 42,
        "message": {
            "text": f"/start surface_{setup.setup_id}",
            "chat": {"id": 99},
            "from": {"id": 77},
        },
    }

    await service.handle_update(update)
    await service.handle_update(update)

    assert service._client.call.await_count == 1


@pytest.mark.asyncio
async def test_target_is_reserved_while_setup_is_active():
    store = _Store()
    service = _service(store)
    pod_id = uuid4()

    await _start(service, pod_id=pod_id)

    with pytest.raises(TelegramManagedBotSetupAlreadyInProgressError):
        await _start(service, pod_id=pod_id)


@pytest.mark.asyncio
async def test_redis_store_enforces_atomic_state_and_claims():
    store = TelegramManagedBotSetupStore(ttl_seconds=60)
    service = _service(store)  # type: ignore[arg-type]
    setup = await _start(service)
    telegram_user_id = setup.request_id + 3_000_000_000
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    try:
        with pytest.raises(TelegramManagedBotSetupAlreadyInProgressError):
            await _start(service, pod_id=setup.pod_id)
        assert await store.get(setup.setup_id) is not None
        assert await store.bind_telegram_user(
            setup_id=setup.setup_id,
            telegram_user_id=telegram_user_id,
        )
        setup.telegram_user_id = telegram_user_id
        setup.status = TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM
        assert await store.save_if_status(
            setup,
            expected={TelegramManagedBotSetupStatus.PENDING},
        )
        assert not await store.save_if_status(
            setup,
            expected={TelegramManagedBotSetupStatus.PENDING},
        )

        assert (
            await store.claim_provisioning(
                setup_id=setup.setup_id,
                bot_id=1234,
                owner="owner-a",
            )
            is TelegramManagedBotProvisioningClaim.ACQUIRED
        )
        assert (
            await store.claim_provisioning(
                setup_id=setup.setup_id,
                bot_id=1234,
                owner="owner-b",
            )
            is TelegramManagedBotProvisioningClaim.IN_PROGRESS
        )
        assert (
            await store.claim_provisioning(
                setup_id=setup.setup_id,
                bot_id=9999,
                owner="owner-c",
            )
            is TelegramManagedBotProvisioningClaim.BOT_CONFLICT
        )
        setup.status = TelegramManagedBotSetupStatus.PROVISIONING
        assert not await store.save_if_status(
            setup,
            expected={TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM},
            owner="owner-b",
        )
        assert await store.save_if_status(
            setup,
            expected={TelegramManagedBotSetupStatus.WAITING_FOR_TELEGRAM},
            owner="owner-a",
        )
        await store.release_provisioning_lease(
            setup_id=setup.setup_id,
            owner="owner-a",
        )
        await store.release_reservations(setup)
        assert await store.get_by_telegram_user_id(telegram_user_id) is None
    finally:
        await redis.delete(
            store._setup_key(setup.setup_id),
            store._request_key(setup.request_id),
            store._target_key(setup.pod_id, setup.surface_name),
            store._telegram_user_key(telegram_user_id),
            store._bot_key(setup.setup_id),
            store._provisioning_key(setup.setup_id),
        )
        await redis.aclose()


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
    app_name = "support-queue"
    setup = await service.start_setup(
        user_id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        surface_name="telegram-support",
        agent_id=None,
        surface_config=SurfaceConfig(
            telegram=SurfaceTelegramConfig(app_name=app_name)
        ),
        is_enabled=True,
        pod_name="Customer Success",
    )
    child = SimpleNamespace(call=AsyncMock(), call_multipart=AsyncMock())
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator.TelegramClient",
        lambda **_: child,
    )
    mini_app_resolver = AsyncMock(
        return_value=TelegramMiniApp(
            app_id=app_id,
            name=app_name,
            url="https://support.apps.example.test",
        )
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator."
        "resolve_telegram_mini_app",
        mini_app_resolver,
    )

    await service._configure_managed_bot(
        setup=setup,
        bot_token="child-token",
    )

    mini_app_resolver.assert_awaited_once_with(
        uow=ANY,
        pod_id=setup.pod_id,
        app_name=app_name,
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
async def test_configure_managed_bot_propagates_transient_telegram_failure(
    monkeypatch,
):
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
    setup = await _start(service)
    child = SimpleNamespace(
        call=AsyncMock(
            side_effect=TelegramApiError(
                method="setMyDescription",
                status_code=500,
                description="temporary",
            )
        ),
        call_multipart=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator."
        "TelegramClient",
        lambda **_: child,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_configurator."
        "resolve_telegram_mini_app",
        AsyncMock(return_value=None),
    )

    with pytest.raises(TelegramApiError):
        await service._configure_managed_bot(
            setup=setup,
            bot_token="child-token",
        )


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
    surface_repository = SimpleNamespace(
        get_by_pod_and_name=AsyncMock(return_value=None),
    )
    surface_service = SimpleNamespace(
        surface_repository=surface_repository,
        create_surface=AsyncMock(
            return_value=SimpleNamespace(id=surface_id, is_active=True)
        ),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("surface_account_matches", [True, False])
async def test_persist_managed_bot_reuses_matching_account_and_surface(
    monkeypatch,
    surface_account_matches,
):
    store = _Store()
    setup = await _start(_service(store))
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
    auth_config = SimpleNamespace(id=uuid4())
    account = SimpleNamespace(
        id=account_id,
        credentials={"bot_token": "old-token"},
        display_name="@old_bot",
    )
    accounts = SimpleNamespace(
        get_by_user_auth_config_and_provider_account=AsyncMock(
            return_value=account
        ),
        update=AsyncMock(return_value=account),
    )
    surface = SimpleNamespace(
        id=surface_id,
        surface_type=SurfacePlatform.TELEGRAM,
        credential_mode=SurfaceCredentialMode.CUSTOM,
        account_id=account_id if surface_account_matches else uuid4(),
        is_active=False,
    )
    surface_service = SimpleNamespace(
        surface_repository=SimpleNamespace(
            get_by_pod_and_name=AsyncMock(return_value=surface)
        ),
        create_surface=AsyncMock(),
        update_surface=AsyncMock(return_value=surface),
    )
    external_users = SimpleNamespace(
        get_by_identity=AsyncMock(return_value=None),
        upsert=AsyncMock(),
    )

    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.managed_bot_persistence."
        "AuthConfigRepository",
        lambda **_: SimpleNamespace(
            get_active_by_org_and_app=AsyncMock(return_value=auth_config)
        ),
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

    if not surface_account_matches:
        with pytest.raises(DomainError) as exc_info:
            await service._persist_managed_bot(
                setup=setup,
                bot_id=1234,
                bot_username="surface_bot",
                bot_token="child-token",
            )
        assert exc_info.value.code == "TELEGRAM_MANAGED_BOT_SURFACE_CONFLICT"
        uow.commit.assert_not_awaited()
        return

    result = await service._persist_managed_bot(
        setup=setup,
        bot_id=1234,
        bot_username="surface_bot",
        bot_token="child-token",
    )
    assert result == (account_id, surface_id)
    assert account.credentials.model_dump() == {"bot_token": "child-token"}
    assert account.display_name == "@surface_bot"
    accounts.update.assert_awaited_once_with(account)
    surface_service.create_surface.assert_not_awaited()
    surface_service.update_surface.assert_awaited_once_with(
        surface_id=surface_id,
        is_active=True,
    )
    external_users.upsert.assert_awaited_once()
    uow.commit.assert_awaited_once()
