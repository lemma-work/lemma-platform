"""When a surface thread stops being the conversation it was.

Two reasons, and only one of them is a clock. A cold DM becomes a fresh
conversation because one thread id there carries every conversation you will
ever have; a channel or email thread does not, because the platform already
bounded it to one topic.
"""

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


def _link(
    *, updated_at: datetime, conversation_kind: str = "DM"
) -> AgentSurfaceConversationLink:
    link = AgentSurfaceConversationLink(
        surface_id=uuid4(),
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_thread_id="chat-1",
        conversation_kind=conversation_kind,
    )
    link.updated_at = updated_at
    return link


def _should_reset(surface, link) -> bool:
    service = AgentSurfaceIngressService(uow_factory=lambda: None)
    return service._should_start_a_new_conversation(surface=surface, link=link)


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


def test_a_channel_thread_is_never_cut_by_the_clock():
    """The bug this check moved for.

    ``SurfaceMode`` has no CHANNEL member, so a Slack or Teams surface is
    necessarily DM and a channel thread inherited the DM window. Reply in a
    thread a day later and the agent got a fresh conversation with no history --
    while Slack showed the person the whole thread above it.
    """
    surface = _surface(reset_hours=1)
    stale = _link(
        updated_at=datetime.now(timezone.utc) - timedelta(days=7),
        conversation_kind="CHANNEL",
    )
    assert _should_reset(surface, stale) is False


def test_an_email_thread_is_never_cut_by_the_clock():
    surface = _surface(reset_hours=1)
    stale = _link(
        updated_at=datetime.now(timezone.utc) - timedelta(days=365),
        conversation_kind="EMAIL",
    )
    assert _should_reset(surface, stale) is False


def test_a_link_written_before_routing_set_a_kind_keeps_the_old_behaviour():
    """The column defaults to DM, so a stale row degrades to what already
    happened -- no change, rather than a new behaviour on old data."""
    surface = _surface(reset_hours=1)
    stale = _link(
        updated_at=datetime.now(timezone.utc) - timedelta(days=7),
        conversation_kind="",
    )
    assert _should_reset(surface, stale) is True


def test_a_different_agent_starts_a_new_conversation_on_every_shape():
    """This check used to sit inside the DM guard, so an email or channel thread
    re-routed to another agent kept the old one indefinitely."""
    from types import SimpleNamespace

    surface = _surface(reset_hours=0)
    service = AgentSurfaceIngressService(uow_factory=lambda: None)
    for kind in ("DM", "CHANNEL", "EMAIL"):
        link = _link(updated_at=datetime.now(timezone.utc), conversation_kind=kind)
        link.routed_agent_id = uuid4()
        route = SimpleNamespace(agent_id=uuid4(), conversation_kind=kind)
        assert service._should_start_a_new_conversation(
            surface=surface, link=link, route=route
        ) is True, kind
