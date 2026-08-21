"""Unit tests for the user-scoped surface listing + default-surface preference."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceNotFoundError,
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.services.user_surfaces_service import (
    UserSurfacesService,
)
from app.modules.identity.domain.user_preferences import UserPreferences

pytestmark = pytest.mark.asyncio


def _surface(pod_id, platform=SurfacePlatform.WHATSAPP, *, created_offset=0):
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(
            second=created_offset
        ),
    )


def _service(*, pod_ids, surfaces_by_pod, preferences=None, get_surface=None):
    async def _list_by_pod(pod_id, **_kwargs):
        return list(surfaces_by_pod.get(pod_id, [])), None

    surfaces = SimpleNamespace(
        list_by_pod=AsyncMock(side_effect=_list_by_pod),
        get=AsyncMock(
            side_effect=lambda sid: get_surface(sid) if get_surface else None
        ),
    )
    membership = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=list(pod_ids)),
    )
    user = SimpleNamespace(preferences=preferences)
    users = SimpleNamespace(
        get=AsyncMock(return_value=user),
        set_preferences=AsyncMock(),
    )
    service = UserSurfacesService(
        surface_repository=surfaces,
        pod_membership_port=membership,
        user_repository=users,
    )
    return service, users


async def test_list_groups_by_platform_and_flags_conflict():
    pod_a, pod_b = uuid4(), uuid4()
    wa_a = _surface(pod_a, SurfacePlatform.WHATSAPP, created_offset=1)
    wa_b = _surface(pod_b, SurfacePlatform.WHATSAPP, created_offset=2)
    tg_a = _surface(pod_a, SurfacePlatform.TELEGRAM)
    prefs = UserPreferences(default_surfaces={"WHATSAPP": wa_b.id})
    service, _ = _service(
        pod_ids=[pod_a, pod_b],
        surfaces_by_pod={pod_a: [wa_a, tg_a], pod_b: [wa_b]},
        preferences=prefs,
    )

    groups = await service.list_user_surfaces(uuid4())
    by_platform = {g.platform: g for g in groups}

    wa = by_platform[SurfacePlatform.WHATSAPP]
    assert wa.conflict is True
    assert wa.default_surface_id == wa_b.id
    assert {s.id for s in wa.surfaces} == {wa_a.id, wa_b.id}

    tg = by_platform[SurfacePlatform.TELEGRAM]
    assert tg.conflict is False
    assert tg.default_surface_id is None


async def test_set_default_writes_preference_for_in_pod_surface():
    pod_a = uuid4()
    wa = _surface(pod_a, SurfacePlatform.WHATSAPP)
    service, users = _service(
        pod_ids=[pod_a],
        surfaces_by_pod={pod_a: [wa]},
        preferences=UserPreferences(),
        get_surface=lambda sid: wa if sid == wa.id else None,
    )
    user_id = uuid4()

    updated = await service.set_default_surface(
        user_id=user_id, platform=SurfacePlatform.WHATSAPP, surface_id=wa.id
    )
    assert updated.default_surface_for("WHATSAPP") == wa.id
    users.set_preferences.assert_awaited_once()


async def test_set_default_rejects_surface_outside_user_pods():
    pod_a, other_pod = uuid4(), uuid4()
    foreign = _surface(other_pod, SurfacePlatform.WHATSAPP)
    service, users = _service(
        pod_ids=[pod_a],  # user is NOT in other_pod
        surfaces_by_pod={pod_a: []},
        preferences=UserPreferences(),
        get_surface=lambda sid: foreign if sid == foreign.id else None,
    )

    with pytest.raises(AgentSurfaceNotFoundError):
        await service.set_default_surface(
            user_id=uuid4(),
            platform=SurfacePlatform.WHATSAPP,
            surface_id=foreign.id,
        )
    users.set_preferences.assert_not_awaited()


async def test_set_default_rejects_platform_mismatch():
    pod_a = uuid4()
    tg = _surface(pod_a, SurfacePlatform.TELEGRAM)
    service, users = _service(
        pod_ids=[pod_a],
        surfaces_by_pod={pod_a: [tg]},
        preferences=UserPreferences(),
        get_surface=lambda sid: tg if sid == tg.id else None,
    )

    with pytest.raises(AgentSurfaceValidationError):
        await service.set_default_surface(
            user_id=uuid4(),
            platform=SurfacePlatform.WHATSAPP,  # mismatch: surface is TELEGRAM
            surface_id=tg.id,
        )
    users.set_preferences.assert_not_awaited()


async def test_set_default_rejects_unknown_surface():
    pod_a = uuid4()
    service, _ = _service(
        pod_ids=[pod_a],
        surfaces_by_pod={pod_a: []},
        preferences=UserPreferences(),
        get_surface=lambda sid: None,
    )
    with pytest.raises(AgentSurfaceNotFoundError):
        await service.set_default_surface(
            user_id=uuid4(),
            platform=SurfacePlatform.WHATSAPP,
            surface_id=uuid4(),
        )
