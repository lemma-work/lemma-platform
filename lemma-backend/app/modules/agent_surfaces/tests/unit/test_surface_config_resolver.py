from __future__ import annotations

from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.api.surface_config_resolver import (
    resolve_telegram_config,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.apps.contracts import ReadyPodApp

pytestmark = pytest.mark.asyncio


async def test_resolve_telegram_config_keeps_pod_scoped_app_name(monkeypatch):
    pod_id = uuid4()
    app_name = "support-desk"
    resolver = AsyncMock(
        return_value=ReadyPodApp(
            id=uuid4(),
            pod_id=pod_id,
            name=app_name,
            public_slug="support-desk-public",
        )
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.api.surface_config_resolver."
        "get_ready_pod_app_by_name",
        resolver,
    )
    ctx = object()

    result = await resolve_telegram_config(
        uow=object(),
        pod_id=pod_id,
        platform=SurfacePlatform.TELEGRAM,
        app_name=app_name,
        ctx=ctx,
    )

    assert result.app_name == app_name
    resolver.assert_awaited_once_with(
        uow=ANY,
        pod_id=pod_id,
        app_name=app_name,
        ctx=ctx,
    )


async def test_resolve_telegram_config_rejects_missing_or_undeployed_app(monkeypatch):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.api.surface_config_resolver."
        "get_ready_pod_app_by_name",
        AsyncMock(return_value=None),
    )

    with pytest.raises(
        AgentSurfaceValidationError,
        match="must belong to this pod and be deployed",
    ):
        await resolve_telegram_config(
            uow=object(),
            pod_id=uuid4(),
            platform=SurfacePlatform.TELEGRAM,
            app_name="missing-app",
            ctx=object(),
        )
