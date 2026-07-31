"""Unit tests for deterministic surface selection (the multi-pod / shared-bot
disambiguation): pod membership → user default (authoritative) → continuity →
tiebreak."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)

pytestmark = pytest.mark.asyncio


def _surface(pod_id, surface_id) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=surface_id,
        pod_id=pod_id,
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
    )


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="chat-1",
        external_thread_id="chat-1",
        sender_external_user_id="tg-user-1",
        message_text="hi",
        is_dm=True,
    )


def _service(*, continuity_id, member_pod_ids, default_surface_id):
    link_repo = SimpleNamespace(
        find_surface_id_for_external_thread=AsyncMock(return_value=continuity_id)
    )
    membership = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=list(member_pod_ids)),
        get_user_default_surface_id=AsyncMock(return_value=default_surface_id),
        clear_user_default_surface_id=AsyncMock(return_value=None),
    )
    service = AgentSurfaceIngressService(
        uow_factory=lambda: None,
        conversation_link_repository=link_repo,
        pod_membership_port=membership,
    )
    # Expose the membership double for assertions.
    service._test_membership = membership  # type: ignore[attr-defined]
    return service


async def test_continuity_reuses_prior_surface_over_ordering():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    # An existing conversation for this chat lives on surface B; the sender is a
    # member of both pods. B must win even though A is first in the list.
    service = _service(
        continuity_id=surf_b.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_user_default_wins_when_multiple_member_pods():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=surf_b.id,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_deterministic_tiebreak_when_no_default():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=None,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    # First candidate wins (list is ordered by created_at, id upstream).
    assert chosen is surf_a


async def test_single_member_candidate_selected():
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_b},  # only member of pod B
        default_surface_id=None,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b


async def test_none_when_sender_not_a_member():
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=None,
        member_pod_ids=set(),
        default_surface_id=None,
    )
    chosen = await service._select_surface(
        candidates=[surf_a],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is None


async def test_none_when_unresolved_user_and_no_continuity():
    pod_a = uuid4()
    surf_a = _surface(pod_a, uuid4())
    service = _service(
        continuity_id=None,
        member_pod_ids={pod_a},
        default_surface_id=None,
    )
    chosen = await service._select_surface(
        candidates=[surf_a],
        resolved_user=ResolvedSurfaceUser(internal_user_id=None),
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is None


async def test_valid_default_wins_over_continuity():
    """The user set a default; an older conversation lives on a different pod.
    The valid default is authoritative and must win (issue 3)."""
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    # Continuity says A, but the user's default is B and they're a member of both.
    service = _service(
        continuity_id=surf_a.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=surf_b.id,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_b
    service._test_membership.clear_user_default_surface_id.assert_not_awaited()


async def test_stale_default_is_cleared_and_falls_back_to_continuity():
    """A default pointing at a pod the user left is stale: it must be cleared and
    routing falls through to continuity (not silently honored)."""
    pod_a, pod_b = uuid4(), uuid4()
    surf_a = _surface(pod_a, uuid4())
    surf_b = _surface(pod_b, uuid4())
    stale_surface_id = uuid4()  # a surface the user is no longer a member of
    user = ResolvedSurfaceUser(internal_user_id=uuid4(), external_user_id="tg-user-1")
    service = _service(
        continuity_id=surf_a.id,
        member_pod_ids={pod_a, pod_b},
        default_surface_id=stale_surface_id,
    )
    chosen = await service._select_surface(
        candidates=[surf_a, surf_b],
        resolved_user=user,
        parsed=_event(),
        platform="TELEGRAM",
    )
    assert chosen is surf_a  # continuity, since the stale default is ignored
    service._test_membership.clear_user_default_surface_id.assert_awaited_once()
