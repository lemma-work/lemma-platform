from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.connectors.domain.connector import AuthProvider, ConnectorKind
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.domain.errors import ConnectorTriggerNotFoundError
from app.modules.connectors.services.trigger_service import ConnectorTriggerService

pytestmark = pytest.mark.asyncio


def _service(*, trigger_repository, kind=ConnectorKind.COMPOSIO, connector_id="slack"):
    connector_service = AsyncMock()
    connector_service.get_auth_config_by_name = AsyncMock(
        return_value=SimpleNamespace(
            id=uuid4(),
            kind=kind,
            connector_id=connector_id,
        )
    )
    return ConnectorTriggerService(
        trigger_repository=trigger_repository,
        connector_repository=AsyncMock(),
        connector_service=connector_service,
    )


def _trigger(kind: ConnectorKind, connector_id: str = "slack") -> ConnectorTriggerEntity:
    return ConnectorTriggerEntity(
        id=f"{connector_id}:{kind.value}:new_message",
        connector_id=connector_id,
        kind=kind,
        event_type="new_message",
        description="New message",
        config_schema={"type": "object"},
    )


async def test_list_triggers_for_auth_config_passes_kind_to_repo():
    trigger_repository = AsyncMock()
    trigger_repository.list_by_connector_kind.return_value = [
        _trigger(ConnectorKind.COMPOSIO)
    ]
    service = _service(trigger_repository=trigger_repository, kind=ConnectorKind.COMPOSIO)

    triggers = await service.list_triggers_for_auth_config(
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_name="slack-composio",
        search_query="msg",
        limit=25,
    )

    assert [t.kind for t in triggers] == [ConnectorKind.COMPOSIO]
    # the deprecated provider view still maps correctly
    assert [t.provider for t in triggers] == [AuthProvider.COMPOSIO]
    trigger_repository.list_by_connector_kind.assert_awaited_once_with(
        "slack",
        "composio",
        search_query="msg",
        limit=25,
    )


async def test_get_trigger_for_auth_config_uses_kind_lookup():
    trigger_repository = AsyncMock()
    trigger_repository.get_by_connector_kind_and_name.return_value = _trigger(
        ConnectorKind.PACKAGE
    )
    service = _service(trigger_repository=trigger_repository, kind=ConnectorKind.PACKAGE)

    trigger = await service.get_trigger_for_auth_config(
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_name="slack-lemma",
        trigger_name="new_message",
    )

    assert trigger.kind == ConnectorKind.PACKAGE
    assert trigger.provider == AuthProvider.LEMMA
    trigger_repository.get_by_connector_kind_and_name.assert_awaited_once_with(
        "slack",
        "package",
        "new_message",
    )


async def test_get_trigger_for_auth_config_raises_when_missing():
    trigger_repository = AsyncMock()
    trigger_repository.get_by_connector_kind_and_name.return_value = None
    service = _service(trigger_repository=trigger_repository)

    with pytest.raises(ConnectorTriggerNotFoundError):
        await service.get_trigger_for_auth_config(
            user_id=uuid4(),
            organization_id=uuid4(),
            auth_config_name="slack-composio",
            trigger_name="missing",
        )
