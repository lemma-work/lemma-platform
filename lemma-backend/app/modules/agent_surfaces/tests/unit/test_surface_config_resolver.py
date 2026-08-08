from __future__ import annotations

from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.api.schemas import (
    SurfaceBehaviorConfigInput,
    SurfaceChannelRouteInput,
)
from app.modules.agent_surfaces.api.surface_config_resolver import (
    merge_surface_config,
    resolve_slack_config,
    resolve_telegram_config,
)
from app.modules.agent_surfaces.domain.entities import (
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfacePlatform,
    SurfaceSlackConfig,
)
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


async def test_saving_settings_keeps_an_explicit_pod_assistant_route():
    """A channel someone pointed at the pod assistant from inside Slack must
    still say so after a save from the web UI.

    Dropping the flag leaves `agent_name` empty, which is "nobody has said" —
    and that resolves to the surface default, i.e. a different agent answering.
    """
    existing = SurfaceConfig(
        channels=[SurfaceChannelRoute(channel_id="C1", use_pod_assistant=True)],
    )

    merged = await merge_surface_config(
        uow=object(),
        existing=existing,
        pod_id=uuid4(),
        platform=SurfacePlatform.SLACK,
        config_input=SurfaceBehaviorConfigInput(
            channels=[
                SurfaceChannelRouteInput(channel_id="C1", use_pod_assistant=True)
            ]
        ),
        agent_service=AsyncMock(),
        ctx=object(),
    )

    assert merged.channels[0].use_pod_assistant is True
    assert merged.channels[0].agent_name is None


async def test_saving_settings_keeps_everyones_dm_choices():
    """`dm_agent_by_user` is written from inside Slack, one person at a time.
    A settings save carries no one's choices and must not clear them."""
    existing = SurfaceConfig(
        slack=SurfaceSlackConfig(dm_agent_by_user={"U1": "researcher"}),
    )

    merged = await merge_surface_config(
        uow=object(),
        existing=existing,
        pod_id=uuid4(),
        platform=SurfacePlatform.SLACK,
        config_input=SurfaceBehaviorConfigInput(slack={"app_name": None}),
        agent_service=AsyncMock(),
        ctx=object(),
    )

    assert merged.slack.dm_agent_by_user == {"U1": "researcher"}


async def test_resolve_slack_config_rejects_an_app_on_another_platform():
    with pytest.raises(
        AgentSurfaceValidationError,
        match="only be featured on a Slack surface",
    ):
        await resolve_slack_config(
            uow=object(),
            pod_id=uuid4(),
            platform=SurfacePlatform.TEAMS,
            app_name="support-desk",
            ctx=object(),
        )
