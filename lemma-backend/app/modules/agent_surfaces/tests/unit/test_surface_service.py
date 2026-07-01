from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    SurfaceConfig,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceAlreadyExistsError,
    AgentSurfaceNotFoundError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.services.surface_service import (
    AgentSurfaceService,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountInfo,
    SurfaceAuthConfigInfo,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType

pytestmark = pytest.mark.asyncio


def _surface_entity(**overrides) -> AgentSurfaceEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "name": "slack",
        "agent_id": uuid4(),
        "surface_type": SurfacePlatform.SLACK,
        "mode": SurfaceMode.DM,
        "account_id": uuid4(),
        "config": SurfaceConfig(),
    }
    payload.update(overrides)
    if payload.pop("is_active", True) is False:
        payload.setdefault("status", AgentSurfaceStatus.INACTIVE)
    return AgentSurfaceEntity(**payload)


async def test_create_surface(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    pod_id = uuid4()
    agent_id = uuid4()
    account_id = uuid4()
    config = SurfaceConfig()

    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, "T123", "U-BOT")

    result = await service.create_surface(
        platform=SurfacePlatform.SLACK,
        pod_id=pod_id,
        agent_id=agent_id,
        config=config,
        account_id=account_id,
    )

    assert result.pod_id == pod_id
    assert result.agent_id == agent_id
    assert result.surface_type == SurfacePlatform.SLACK
    assert result.account_id == account_id
    assert result.surface_identity_id == "U-BOT"
    repo.create.assert_awaited_once()
    enricher.resolve_binding.assert_awaited_once()


async def test_create_surface_name_defaults_and_is_pod_unique(monkeypatch):
    """A surface is addressed by its pod-unique name (defaults to the
    lowercased platform); several surfaces of the same platform can coexist
    under distinct names, but the same name cannot be reused in a pod."""
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )
    pod_id = uuid4()
    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, "T123", "U-BOT")

    # No surface with this name yet -> created; name defaults to the platform.
    repo.get_by_pod_and_name.return_value = None
    created = await service.create_surface(
        platform=SurfacePlatform.SLACK,
        pod_id=pod_id,
        agent_id=uuid4(),
        config=SurfaceConfig(),
        account_id=uuid4(),
    )
    assert created.name == "slack"
    repo.get_by_pod_and_name.assert_awaited_with(pod_id=pod_id, name="slack")

    # A surface with that name already exists in the pod -> rejected, even for
    # a different agent/account.
    repo.get_by_pod_and_name.return_value = _surface_entity(pod_id=pod_id)
    with pytest.raises(AgentSurfaceAlreadyExistsError):
        await service.create_surface(
            platform=SurfacePlatform.SLACK,
            pod_id=pod_id,
            agent_id=uuid4(),
            config=SurfaceConfig(),
            account_id=uuid4(),
        )

    # An explicit distinct name for a second Slack surface succeeds.
    repo.get_by_pod_and_name.return_value = None
    second = await service.create_surface(
        platform=SurfacePlatform.SLACK,
        pod_id=pod_id,
        agent_id=uuid4(),
        name="slack-support",
        config=SurfaceConfig(),
        account_id=uuid4(),
    )
    assert second.name == "slack-support"


async def test_create_telegram_surface_uses_built_in_credentials_without_account(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )

    pod_id = uuid4()
    agent_id = uuid4()
    config = SurfaceConfig()

    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.surface_settings.enable_telegram_polling_mode",
        True,
    )

    result = await service.create_surface(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=pod_id,
        agent_id=agent_id,
        config=config,
    )

    assert result.account_id is None
    assert result.surface_type is SurfacePlatform.TELEGRAM


async def test_create_telegram_webhook_surface_rejects_local_api_url(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    account_port = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        account_port=account_port,
    )

    account_id = uuid4()
    config = SurfaceConfig()
    repo.get_by_platform_and_account_id.return_value = None
    enricher.resolve_binding.return_value = (None, None, None)
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=uuid4(),
        connector_id="telegram",
        credentials={"bot_token": "telegram-token"},
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "http://localhost:8711",
    )

    with pytest.raises(AgentSurfaceValidationError, match="public HTTPS API URL"):
        await service.create_surface(
        platform=SurfacePlatform.TELEGRAM,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=config,
            account_id=account_id,
        )

    repo.create.assert_not_awaited()


async def test_create_telegram_webhook_surface_registers_per_surface_webhook(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    account_port = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        account_port=account_port,
    )

    account_id = uuid4()
    config = SurfaceConfig()
    repo.get_by_platform_and_account_id.return_value = None
    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=uuid4(),
        connector_id="telegram",
        credentials={"bot_token": "telegram-token"},
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )
    register = AsyncMock()
    monkeypatch.setattr(service, "_register_telegram_webhook", register)

    result = await service.create_surface(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=config,
        account_id=account_id,
    )

    assert result.webhook_secret
    webhook_url = f"https://api.example.test/surfaces/{result.id}/webhook"
    register.assert_awaited_once_with(
        credentials={"bot_token": "telegram-token"},
        webhook_url=webhook_url,
        webhook_secret=result.webhook_secret,
    )


async def test_create_telegram_webhook_surface_rejects_duplicate_account(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    account_port = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        account_port=account_port,
    )

    account_id = uuid4()
    config = SurfaceConfig()
    repo.get_by_platform_and_account_id.return_value = _surface_entity(
        surface_type=SurfacePlatform.TELEGRAM,
        config=config,
        account_id=account_id,
    )
    enricher.resolve_binding.return_value = (None, None, None)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    with pytest.raises(AgentSurfaceValidationError, match="already connected"):
        await service.create_surface(
        platform=SurfacePlatform.TELEGRAM,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=config,
            account_id=account_id,
        )

    account_port.get_account.assert_not_awaited()
    repo.create.assert_not_awaited()


async def test_create_system_surface_rejects_org_level_credential_conflict(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )

    config = SurfaceConfig()
    repo.create.side_effect = lambda entity: entity
    repo.get_system_credential_conflict_in_org.return_value = _surface_entity(
        surface_type=SurfacePlatform.WHATSAPP,
        config=config,
        account_id=None,
    )
    enricher.resolve_binding.return_value = (None, None, None)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    with pytest.raises(AgentSurfaceValidationError, match="System WHATSAPP credentials"):
        await service.create_surface(
        platform=SurfacePlatform.WHATSAPP,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=config,
        )

    repo.create.assert_not_awaited()


async def test_create_account_surface_rejects_org_level_account_conflict(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )

    account_id = uuid4()
    config = SurfaceConfig()
    repo.create.side_effect = lambda entity: entity
    repo.get_account_conflict_in_org.return_value = _surface_entity(
        surface_type=SurfacePlatform.SLACK,
        config=config,
        account_id=account_id,
    )
    enricher.resolve_binding.return_value = (None, "T123", "U-BOT")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    with pytest.raises(AgentSurfaceValidationError, match="connected account"):
        await service.create_surface(
        platform=SurfacePlatform.SLACK,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=config,
            account_id=account_id,
        )

    repo.create.assert_not_awaited()


async def test_create_teams_surface_with_account_is_active(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )

    pod_id = uuid4()
    agent_id = uuid4()
    account_id = uuid4()
    config = SurfaceConfig()

    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = ("tenant-123", None, None)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    result = await service.create_surface(
        platform=SurfacePlatform.TEAMS,
        pod_id=pod_id,
        agent_id=agent_id,
        config=config,
        account_id=account_id,
    )

    assert result.status is AgentSurfaceStatus.ACTIVE
    assert result.external_tenant_id == "tenant-123"
    assert result.account_id == account_id


async def test_create_teams_requires_account_id():
    with pytest.raises(AgentSurfaceValidationError, match="require account_id"):
        AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.TEAMS,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=SurfaceConfig(),
        )


async def test_create_whatsapp_surface():
    config = SurfaceConfig()
    entity = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.WHATSAPP,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=config,
    )
    assert entity.surface_type == SurfacePlatform.WHATSAPP


async def test_create_telegram_surface():
    config = SurfaceConfig()
    entity = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.TELEGRAM,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=config,
    )
    assert entity.surface_type == SurfacePlatform.TELEGRAM


async def test_create_gmail_surface_builds_inbox_filtered_trigger():
    from app.modules.agent_surfaces.domain.ports import SurfaceAccountInfo

    repo = AsyncMock()
    enricher = AsyncMock()
    schedule_service = AsyncMock()
    app_trigger_repo = AsyncMock()
    account_port = AsyncMock()
    auth_config_port = AsyncMock()
    pod_id = uuid4()
    agent_id = uuid4()
    account_id = uuid4()
    auth_config_id = uuid4()
    user_id = uuid4()
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=user_id,
        auth_config_id=auth_config_id,
        email="assistant@gmail.test",
        connector_id="gmail",
        credentials={},
    )
    auth_config_port.get_auth_config.return_value = SurfaceAuthConfigInfo(
        id=auth_config_id,
        provider=AuthProvider.COMPOSIO.value,
        connector_id="gmail",
    )
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        schedule_service=schedule_service,
        connector_trigger_repository=app_trigger_repo,
        account_port=account_port,
        auth_config_port=auth_config_port,
    )

    config = SurfaceConfig()

    repo.create.side_effect = lambda entity: entity
    repo.update.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    app_trigger_repo.get_by_app_name_and_event_type.return_value = [
        AsyncMock(id="gmail:gmail_new_gmail_message")
    ]
    schedule_service.create_schedule.return_value = ScheduleEntity(
        id=uuid4(),
        user_id=user_id,
        pod_id=pod_id,
        schedule_type=ScheduleType.WEBHOOK,
        account_id=account_id,
        connector_trigger_id="gmail:gmail_new_gmail_message",
        config={},
    )

    result = await service.create_surface(
        platform=SurfacePlatform.GMAIL,
        pod_id=pod_id,
        agent_id=agent_id,
        config=config,
        mode=SurfaceMode.EMAIL,
        account_id=account_id,
    )

    assert result.surface_identity_email == "assistant@gmail.test"
    schedule_payload = schedule_service.create_schedule.await_args.args[0]
    assert schedule_payload.connector_trigger_id == "gmail:gmail_new_gmail_message"
    assert schedule_payload.config["labelIds"] == "INBOX"
    assert schedule_payload.config["query"] == "label:inbox -from:assistant@gmail.test"
    assert schedule_payload.config["userId"] == "me"


async def test_create_outlook_surface_keeps_trigger_config_minimal():
    from app.modules.agent_surfaces.domain.ports import SurfaceAccountInfo

    repo = AsyncMock()
    enricher = AsyncMock()
    schedule_service = AsyncMock()
    app_trigger_repo = AsyncMock()
    account_port = AsyncMock()
    auth_config_port = AsyncMock()
    pod_id = uuid4()
    agent_id = uuid4()
    account_id = uuid4()
    auth_config_id = uuid4()
    user_id = uuid4()
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=user_id,
        auth_config_id=auth_config_id,
        email="assistant@outlook.test",
        connector_id="outlook",
        credentials={},
    )
    auth_config_port.get_auth_config.return_value = SurfaceAuthConfigInfo(
        id=auth_config_id,
        provider=AuthProvider.COMPOSIO.value,
        connector_id="outlook",
    )
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        schedule_service=schedule_service,
        connector_trigger_repository=app_trigger_repo,
        account_port=account_port,
        auth_config_port=auth_config_port,
    )

    config = SurfaceConfig()

    repo.create.side_effect = lambda entity: entity
    repo.update.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    app_trigger_repo.get_by_app_name_and_event_type.return_value = [
        AsyncMock(id="outlook:outlook_message_trigger")
    ]
    schedule_service.create_schedule.return_value = ScheduleEntity(
        id=uuid4(),
        user_id=user_id,
        pod_id=pod_id,
        schedule_type=ScheduleType.WEBHOOK,
        account_id=account_id,
        connector_trigger_id="outlook:outlook_message_trigger",
        config={},
    )

    result = await service.create_surface(
        platform=SurfacePlatform.OUTLOOK,
        pod_id=pod_id,
        agent_id=agent_id,
        config=config,
        mode=SurfaceMode.EMAIL,
        account_id=account_id,
    )

    assert result.surface_identity_email == "assistant@outlook.test"
    schedule_payload = schedule_service.create_schedule.await_args.args[0]
    assert schedule_payload.connector_trigger_id == "outlook:outlook_message_trigger"
    assert "query" not in schedule_payload.config
    assert "labelIds" not in schedule_payload.config


async def test_create_outlook_surface_allows_account_without_email():
    repo = AsyncMock()
    enricher = AsyncMock()
    schedule_service = AsyncMock()
    app_trigger_repo = AsyncMock()
    account_port = AsyncMock()
    auth_config_port = AsyncMock()
    pod_id = uuid4()
    account_id = uuid4()
    auth_config_id = uuid4()
    user_id = uuid4()
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=user_id,
        auth_config_id=auth_config_id,
        email=None,
        connector_id="outlook",
        credentials={},
    )
    auth_config_port.get_auth_config.return_value = SurfaceAuthConfigInfo(
        id=auth_config_id,
        provider=AuthProvider.COMPOSIO.value,
        connector_id="outlook",
    )
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        schedule_service=schedule_service,
        connector_trigger_repository=app_trigger_repo,
        account_port=account_port,
        auth_config_port=auth_config_port,
    )

    repo.create.side_effect = lambda entity: entity
    repo.update.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    app_trigger_repo.get_by_app_name_and_event_type.return_value = [
        AsyncMock(id="outlook:outlook_message_trigger")
    ]
    schedule_service.create_schedule.return_value = ScheduleEntity(
        id=uuid4(),
        user_id=user_id,
        pod_id=pod_id,
        schedule_type=ScheduleType.WEBHOOK,
        account_id=account_id,
        connector_trigger_id="outlook:outlook_message_trigger",
        config={},
    )

    result = await service.create_surface(
        platform=SurfacePlatform.OUTLOOK,
        pod_id=pod_id,
        agent_id=uuid4(),
        config=SurfaceConfig(),
        mode=SurfaceMode.EMAIL,
        account_id=account_id,
    )

    assert result.surface_identity_email is None
    schedule_service.create_schedule.assert_awaited()


async def test_create_gmail_surface_requires_account_email():
    repo = AsyncMock()
    enricher = AsyncMock()
    schedule_service = AsyncMock()
    app_trigger_repo = AsyncMock()
    account_port = AsyncMock()
    auth_config_port = AsyncMock()
    account_id = uuid4()
    auth_config_id = uuid4()
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=uuid4(),
        auth_config_id=auth_config_id,
        email=None,
        connector_id="gmail",
        credentials={},
    )
    auth_config_port.get_auth_config.return_value = SurfaceAuthConfigInfo(
        id=auth_config_id,
        provider=AuthProvider.COMPOSIO.value,
        connector_id="gmail",
    )
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        schedule_service=schedule_service,
        connector_trigger_repository=app_trigger_repo,
        account_port=account_port,
        auth_config_port=auth_config_port,
    )

    repo.create.side_effect = lambda entity: entity
    repo.update.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)

    with pytest.raises(AgentSurfaceValidationError, match="email address"):
        await service.create_surface(
            platform=SurfacePlatform.GMAIL,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=SurfaceConfig(),
            mode=SurfaceMode.EMAIL,
            account_id=account_id,
        )


async def test_create_gmail_surface_requires_composio_account():
    repo = AsyncMock()
    enricher = AsyncMock()
    schedule_service = AsyncMock()
    app_trigger_repo = AsyncMock()
    account_port = AsyncMock()
    auth_config_port = AsyncMock()
    account_id = uuid4()
    auth_config_id = uuid4()
    config = SurfaceConfig()
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=uuid4(),
        auth_config_id=auth_config_id,
        email="assistant@gmail.test",
        connector_id="gmail",
        credentials={},
    )
    auth_config_port.get_auth_config.return_value = SurfaceAuthConfigInfo(
        id=auth_config_id,
        provider=AuthProvider.LEMMA.value,
        connector_id="gmail",
    )
    repo.create.side_effect = lambda entity: entity
    enricher.resolve_binding.return_value = (None, None, None)
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
        schedule_service=schedule_service,
        connector_trigger_repository=app_trigger_repo,
        account_port=account_port,
        auth_config_port=auth_config_port,
    )

    with pytest.raises(AgentSurfaceValidationError, match="Composio-backed"):
        await service.create_surface(
        platform=SurfacePlatform.GMAIL,
            pod_id=uuid4(),
            agent_id=uuid4(),
            config=config,
            mode=SurfaceMode.EMAIL,
            account_id=account_id,
        )

    schedule_service.create_schedule.assert_not_awaited()


async def test_get_surface_raises_not_found():
    repo = AsyncMock()
    repo.get.return_value = None
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )

    with pytest.raises(AgentSurfaceNotFoundError):
        await service.get_surface(uuid4())


async def test_toggle_surface():
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )

    entity = _surface_entity(is_active=True)
    repo.get.return_value = entity
    repo.update.return_value = entity

    result = await service.update_surface(surface_id=entity.id, is_active=False)

    assert result.is_active is False
    assert result.status is AgentSurfaceStatus.INACTIVE
    repo.update.assert_awaited_once()


async def test_toggle_telegram_webhook_surface_deletes_provider_webhook(monkeypatch):
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )
    account_id = uuid4()
    entity = _surface_entity(
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
        event_mode=SurfaceEventMode.WEBHOOK,
        account_id=account_id,
        webhook_secret="surface-secret",
        is_active=True,
    )
    repo.get.return_value = entity
    repo.update.side_effect = lambda updated: updated
    delete_webhook = AsyncMock()
    monkeypatch.setattr(service, "_delete_telegram_webhook", delete_webhook)

    result = await service.update_surface(surface_id=entity.id, is_active=False)

    assert result.is_active is False
    assert result.status is AgentSurfaceStatus.INACTIVE
    delete_webhook.assert_awaited_once()


async def test_resume_telegram_webhook_surface_registers_provider_webhook(monkeypatch):
    repo = AsyncMock()
    account_port = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
        account_port=account_port,
    )
    account_id = uuid4()
    entity = _surface_entity(
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
        event_mode=SurfaceEventMode.WEBHOOK,
        account_id=account_id,
        webhook_secret="old-secret",
        is_active=False,
    )
    repo.get.return_value = entity
    repo.update.side_effect = lambda updated: updated
    repo.get_by_platform_and_account_id.return_value = None
    account_port.get_account.return_value = SurfaceAccountInfo(
        id=account_id,
        user_id=uuid4(),
        connector_id="telegram",
        credentials={"bot_token": "telegram-token"},
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )
    register = AsyncMock()
    monkeypatch.setattr(service, "_register_telegram_webhook", register)

    result = await service.update_surface(surface_id=entity.id, is_active=True)

    assert result.is_active is True
    assert result.webhook_secret and result.webhook_secret != "old-secret"
    register.assert_awaited_once_with(
        credentials={"bot_token": "telegram-token"},
        webhook_url=f"https://api.example.test/surfaces/{entity.id}/webhook",
        webhook_secret=result.webhook_secret,
    )


async def test_delete_telegram_webhook_surface_deletes_provider_webhook(monkeypatch):
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )
    entity = _surface_entity(
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
        event_mode=SurfaceEventMode.WEBHOOK,
        account_id=uuid4(),
        webhook_secret="surface-secret",
        is_active=False,
    )
    repo.get.return_value = entity
    delete_webhook = AsyncMock()
    monkeypatch.setattr(service, "_delete_telegram_webhook", delete_webhook)

    await service.delete_surface(entity.id)

    delete_webhook.assert_awaited_once_with(entity)
    repo.delete.assert_awaited_once_with(entity.id)


async def test_list_surfaces_by_pod():
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )

    pod_id = uuid4()
    entity = _surface_entity(pod_id=pod_id)
    repo.list_by_pod.return_value = ([entity], None)

    surfaces, next_cursor = await service.list_surfaces_by_pod(pod_id)

    assert len(surfaces) == 1
    assert surfaces[0].pod_id == pod_id
    assert next_cursor is None
    repo.list_by_pod.assert_awaited_once_with(
        pod_id, platform=None, agent_id=None, match_agent=False, cursor=None, limit=100
    )


async def test_delete_all_surfaces_for_pod_paginates_and_deletes():
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )

    pod_id = uuid4()
    page1 = [_surface_entity(pod_id=pod_id), _surface_entity(pod_id=pod_id)]
    page2_cursor = page1[-1].id
    page2 = [_surface_entity(pod_id=pod_id)]
    repo.list_by_pod.side_effect = [(page1, page2_cursor), (page2, None)]
    service.delete_surface = AsyncMock()  # type: ignore[method-assign]

    deleted = await service.delete_all_surfaces_for_pod(pod_id)

    assert deleted == 3
    assert repo.list_by_pod.await_count == 2
    assert service.delete_surface.await_count == 3


async def test_delete_all_surfaces_for_pod_continues_past_failure():
    repo = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=AsyncMock(),
    )

    pod_id = uuid4()
    surfaces = [_surface_entity(pod_id=pod_id), _surface_entity(pod_id=pod_id)]
    repo.list_by_pod.return_value = (surfaces, None)
    service.delete_surface = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("telegram down"), None]
    )

    deleted = await service.delete_all_surfaces_for_pod(pod_id)

    assert deleted == 1
    assert service.delete_surface.await_count == 2


async def test_update_surface_updates_account_metadata(monkeypatch):
    repo = AsyncMock()
    enricher = AsyncMock()
    service = AgentSurfaceService(
        surface_repository=repo,
        account_binding_resolver=enricher,
    )

    entity = _surface_entity()
    repo.get.return_value = entity
    repo.update.return_value = entity
    enricher.resolve_binding.return_value = (None, "T999", "U-BOT-NEW")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_service.settings.api_url",
        "https://api.example.test",
    )

    result = await service.update_surface(
        surface_id=entity.id,
        account_id=uuid4(),
    )
    assert result.external_workspace_id == "T999"
    assert result.surface_identity_id == "U-BOT-NEW"
    repo.update.assert_awaited_once()


async def test_slack_surface_matches_workspace_from_connected_account():
    surface = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.SLACK,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
        account_id=uuid4(),
        external_workspace_id="T123",
    )

    assert surface.matches_tenant("T123") is True
    assert surface.matches_tenant("T999") is False


async def test_surface_event_mode_defaults_and_validation():
    gmail = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.GMAIL,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
        account_id=uuid4(),
    )
    # Email platforms default to EMAIL mode + COMPOSIO_TRIGGER without explicit args.
    assert gmail.mode is SurfaceMode.EMAIL
    assert gmail.event_mode is SurfaceEventMode.COMPOSIO_TRIGGER

    telegram = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.TELEGRAM,
        pod_id=uuid4(),
        agent_id=uuid4(),
    )
    assert telegram.mode is SurfaceMode.DM
    assert telegram.event_mode is SurfaceEventMode.WEBHOOK

    with pytest.raises(AgentSurfaceValidationError, match="COMPOSIO_TRIGGER"):
        AgentSurfaceEntity.create(
            surface_type=SurfacePlatform.TELEGRAM,
            pod_id=uuid4(),
            agent_id=uuid4(),
            event_mode=SurfaceEventMode.COMPOSIO_TRIGGER,
        )

    with pytest.raises(AgentSurfaceValidationError, match="EMAIL mode"):
        AgentSurfaceEntity.create(
            surface_type=SurfacePlatform.SLACK,
            pod_id=uuid4(),
            agent_id=uuid4(),
            account_id=uuid4(),
            mode=SurfaceMode.EMAIL,
        )


async def test_surface_platform_from_source():
    assert SurfacePlatform.from_source("slack") == SurfacePlatform.SLACK
    assert SurfacePlatform.from_source("teams") == SurfacePlatform.TEAMS
    assert SurfacePlatform.from_source("whatsapp") == SurfacePlatform.WHATSAPP
    assert SurfacePlatform.from_source("telegram") == SurfacePlatform.TELEGRAM
    assert SurfacePlatform.from_source("gmail") == SurfacePlatform.GMAIL
    assert SurfacePlatform.from_source("outlook") == SurfacePlatform.OUTLOOK
    assert SurfacePlatform.from_source("unknown") is None


async def test_get_platform_setup_guide():
    service = AgentSurfaceService(
        surface_repository=AsyncMock(),
        account_binding_resolver=AsyncMock(),
    )

    guide = service.get_platform_setup_guide("teams")

    assert guide.platform is SurfacePlatform.TEAMS
    assert guide.docs_path == "docs/surfaces/teams.md"
    assert any(connector.mode.value == "CONNECTED_ACCOUNT" for connector in guide.connectors)


async def test_get_platform_setup_guide_raises_for_invalid_platform():
    service = AgentSurfaceService(
        surface_repository=AsyncMock(),
        account_binding_resolver=AsyncMock(),
    )

    with pytest.raises(AgentSurfaceValidationError):
        service.get_platform_setup_guide("not-a-platform")
