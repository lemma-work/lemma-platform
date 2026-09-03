from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.account import AccountEntity, ComposioCredentials
from app.modules.connectors.domain.connector import ConnectorEntity, AuthProvider
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.composition.schedule_connectors import (
    ExternalScheduleWriterAdapter,
)
from app.composition import schedule_connectors as manager_factory


@pytest.mark.asyncio
async def test_create_provider_trigger_passes_connector_trigger_to_manager(monkeypatch):
    user_id = uuid4()
    account_id = uuid4()
    app_trigger = ConnectorTriggerEntity(
        id="gmail_new_email",
        connector_id="gmail",
        event_type="GMAIL_NEW_EMAIL",
    )
    account = AccountEntity(
        id=account_id,
        user_id=user_id,
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="gmail",
        credentials=ComposioCredentials(connection_id="ca_123"),
    )
    connector = ConnectorEntity(
        id="gmail",
        composio_toolkit_slug="gmail",
    )
    schedule = ScheduleEntity(
        user_id=user_id,
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=app_trigger.id,
        account_id=account_id,
        config={"labelIds": ["INBOX"]},
    )
    app_trigger_repository = AsyncMock()
    app_trigger_repository.get.return_value = app_trigger
    connector_service = AsyncMock()
    connector_service.get_account.return_value = account
    connector_service.get_connector.return_value = connector
    connector_service.auth_config_repository.get.return_value = AuthConfigEntity(
        id=account.auth_config_id,
        organization_id=account.organization_id,
        connector_id="gmail",
        provider=AuthProvider.COMPOSIO,
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name="gmail",
    )
    connector_service._build_effective_connector = MagicMock(return_value=connector)
    manager = AsyncMock()
    manager.create_schedule.return_value = "ti_123"
    monkeypatch.setattr(
        manager_factory.ManagersFactory,
        "get_manager",
        lambda *args, **kwargs: manager,
    )

    adapter = ExternalScheduleWriterAdapter(
        uow=AsyncMock(),
        connector_service=connector_service,
        connector_trigger_repository=app_trigger_repository,
    )

    provider_id = await adapter.create_provider_trigger(schedule)

    assert provider_id.provider_trigger_id == "ti_123"
    manager.create_schedule.assert_awaited_once()
    call_kwargs = manager.create_schedule.await_args.kwargs
    assert call_kwargs["account"] == account
    assert call_kwargs["app_trigger"] == app_trigger
    assert call_kwargs["config"] == schedule.config
    # The native /webhooks/schedules/{id} callback was removed; managers no longer
    # receive a callback_url (composio delivers via its own verified webhook).
    assert "callback_url" not in call_kwargs
