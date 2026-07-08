from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.ports import SurfaceAccountInfo
from app.modules.agent_surfaces.infrastructure.adapters.account_binding import (
    SurfaceAccountBindingResolver,
)

pytestmark = pytest.mark.asyncio


def _make_account_port(account_info: SurfaceAccountInfo | None) -> AsyncMock:
    port = AsyncMock()
    port.get_account.return_value = account_info
    return port


async def test_slack_binding_populates_bot_user_id_from_account_credentials():
    account_id = uuid4()
    account_port = _make_account_port(
        SurfaceAccountInfo(
            id=account_id,
            user_id=uuid4(),
            connector_id="slack",
            credentials={"raw_response": {"bot_user_id": "U0AGSSTQZLH"}},
        )
    )
    resolver = SurfaceAccountBindingResolver(account_port)

    tenant_id, workspace_id, identity_id = await resolver.resolve_binding(
        SurfacePlatform.SLACK, account_id=account_id
    )
    assert tenant_id is None
    assert workspace_id is None
    assert identity_id == "U0AGSSTQZLH"
    account_port.get_account.assert_awaited_once_with(account_id)


async def test_slack_binding_raises_when_bot_user_id_missing():
    account_id = uuid4()
    account_port = _make_account_port(
        SurfaceAccountInfo(
            id=account_id,
            user_id=uuid4(),
            connector_id="slack",
            credentials={"raw_response": {}},
        )
    )
    resolver = SurfaceAccountBindingResolver(account_port)

    with pytest.raises(AgentSurfaceValidationError):
        await resolver.resolve_binding(SurfacePlatform.SLACK, account_id=account_id)


async def test_slack_binding_raises_when_account_id_missing():
    resolver = SurfaceAccountBindingResolver(_make_account_port(None))

    with pytest.raises(AgentSurfaceValidationError, match="require account_id"):
        await resolver.resolve_binding(SurfacePlatform.SLACK)


async def test_teams_binding_extracts_tenant_id_from_account():
    account_id = uuid4()
    account_port = _make_account_port(
        SurfaceAccountInfo(
            id=account_id,
            user_id=uuid4(),
            connector_id="microsoft_teams",
            credentials={"user_data": {"tenant_id": "tenant-from-account"}},
        )
    )
    resolver = SurfaceAccountBindingResolver(account_port)

    tenant_id, workspace_id, identity_id = await resolver.resolve_binding(
        SurfacePlatform.TEAMS, account_id=account_id
    )
    assert tenant_id == "tenant-from-account"
    assert workspace_id is None
    assert identity_id is None
    account_port.get_account.assert_awaited_once_with(account_id)


async def test_binding_rejects_account_for_wrong_connector():
    account_id = uuid4()
    account_port = _make_account_port(
        SurfaceAccountInfo(
            id=account_id,
            user_id=uuid4(),
            connector_id="slack",
            credentials={},
        )
    )
    resolver = SurfaceAccountBindingResolver(account_port)

    with pytest.raises(AgentSurfaceValidationError, match="connected telegram account"):
        await resolver.resolve_binding(SurfacePlatform.TELEGRAM, account_id=account_id)


async def test_whatsapp_binding_allows_built_in_credentials_without_account():
    account_port = _make_account_port(None)
    resolver = SurfaceAccountBindingResolver(account_port)

    assert await resolver.resolve_binding(SurfacePlatform.WHATSAPP) == (None, None, None)
    account_port.get_account.assert_not_awaited()


async def test_telegram_binding_allows_built_in_credentials_without_account():
    account_port = _make_account_port(None)
    resolver = SurfaceAccountBindingResolver(account_port)

    assert await resolver.resolve_binding(SurfacePlatform.TELEGRAM) == (None, None, None)
    account_port.get_account.assert_not_awaited()
