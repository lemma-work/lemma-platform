"""Boundary tests for the per-surface DM conversation reset window
(``_should_reset_dm_conversation``): a DM starts a fresh Lemma conversation
after N hours of inactivity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)


def _surface(*, mode: SurfaceMode = SurfaceMode.DM, reset_hours: int = 24):
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        mode=mode,
        config=SurfaceConfig(dm_conversation_reset_after_hours=reset_hours),
    )


def _link(*, updated_at: datetime) -> AgentSurfaceConversationLink:
    link = AgentSurfaceConversationLink(
        surface_id=uuid4(),
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_thread_id="chat-1",
    )
    link.updated_at = updated_at
    return link


def _should_reset(surface, link) -> bool:
    service = AgentSurfaceIngressService(uow_factory=lambda: None)
    return service._should_reset_dm_conversation(surface=surface, link=link)


def test_reset_when_inactive_beyond_window():
    surface = _surface(reset_hours=24)
    link = _link(updated_at=datetime.now(timezone.utc) - timedelta(hours=25))
    assert _should_reset(surface, link) is True


def test_no_reset_within_window():
    surface = _surface(reset_hours=24)
    link = _link(updated_at=datetime.now(timezone.utc) - timedelta(hours=23))
    assert _should_reset(surface, link) is False


def test_reset_disabled_when_hours_non_positive():
    surface = _surface(reset_hours=0)
    # Even a very old link never resets when the window is disabled.
    link = _link(updated_at=datetime.now(timezone.utc) - timedelta(days=30))
    assert _should_reset(surface, link) is False


def test_naive_updated_at_treated_as_utc():
    surface = _surface(reset_hours=24)
    # A naive timestamp (no tzinfo) must not raise and is treated as UTC.
    naive_old = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(tzinfo=None)
    link = _link(updated_at=naive_old)
    assert _should_reset(surface, link) is True


def test_never_resets_for_non_dm_mode():
    surface = _surface(mode=SurfaceMode.EMAIL, reset_hours=1)
    link = _link(updated_at=datetime.now(timezone.utc) - timedelta(days=7))
    assert _should_reset(surface, link) is False
