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
    SurfaceCredentialMode,
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
    """A surface on the deployment's shared bot/number (the default)."""
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        agent_id=pod_id,
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(
            second=created_offset
        ),
    )


def _own_bot_surface(pod_id, platform=SurfacePlatform.TELEGRAM, *, created_offset=0):
    """A surface on a bot the pod brought itself — its own handle, its own address."""
    surface = _surface(pod_id, platform, created_offset=created_offset)
    surface.account_id = uuid4()
    surface.credential_mode = SurfaceCredentialMode.CUSTOM
    return surface


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
    users = SimpleNamespace(
        preferences=AsyncMock(return_value=preferences or UserPreferences()),
        set_preferences=AsyncMock(),
    )
    service = UserSurfacesService(
        surface_repository=surfaces,
        pod_membership_port=membership,
        user_directory=users,
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


async def test_own_bot_surfaces_never_conflict():
    """Connecting your own Telegram bot in eleven pods is eleven addresses.

    Each bot answers on its own token, so a message can only ever land on the
    bot it was sent to — asking which pod should hear it invents a choice and
    implies the message might go somewhere else."""
    pods = [uuid4() for _ in range(3)]
    surfaces = {
        pod: [_own_bot_surface(pod, created_offset=index)]
        for index, pod in enumerate(pods)
    }
    service, _ = _service(
        pod_ids=pods,
        surfaces_by_pod=surfaces,
        preferences=UserPreferences(),
    )

    groups = await service.list_user_surfaces(uuid4())
    telegram = next(g for g in groups if g.platform is SurfacePlatform.TELEGRAM)

    assert len(telegram.surfaces) == 3
    assert telegram.contended == set()
    assert telegram.conflict is False


async def test_conflict_covers_only_the_shared_bot_surfaces():
    """A mix: the shared bot in two pods, plus a pod running its own bot."""
    shared_a, shared_b, own = uuid4(), uuid4(), uuid4()
    tg_a = _surface(shared_a, SurfacePlatform.TELEGRAM, created_offset=1)
    tg_b = _surface(shared_b, SurfacePlatform.TELEGRAM, created_offset=2)
    tg_own = _own_bot_surface(own, created_offset=3)
    service, _ = _service(
        pod_ids=[shared_a, shared_b, own],
        surfaces_by_pod={shared_a: [tg_a], shared_b: [tg_b], own: [tg_own]},
        preferences=UserPreferences(),
    )

    groups = await service.list_user_surfaces(uuid4())
    telegram = next(g for g in groups if g.platform is SurfacePlatform.TELEGRAM)

    assert telegram.conflict is True
    assert telegram.contended == {tg_a.id, tg_b.id}


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


def _shared_pods(pods, *, attached_surface_id=None):
    from app.modules.agent_surfaces.domain.available_pods import AvailablePod

    return SimpleNamespace(
        member_pods=AsyncMock(
            return_value=[
                AvailablePod(pod, f"Pod {i}", "Org") for i, pod in enumerate(pods)
            ]
        ),
        attach=AsyncMock(return_value=attached_surface_id or uuid4()),
    )


async def test_a_shared_bot_platform_gets_a_group_even_with_no_surface(monkeypatch):
    """The person with no WhatsApp surface yet is exactly who needs the picker."""
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "telegram_bot_token", None)
    pod_a, pod_b = uuid4(), uuid4()
    service, _ = _service(pod_ids=[pod_a, pod_b], surfaces_by_pod={})

    groups = await service.list_user_surfaces(
        uuid4(), shared_pods=_shared_pods([pod_a, pod_b])
    )

    by_platform = {g.platform: g for g in groups}
    assert set(by_platform) == {SurfacePlatform.WHATSAPP}
    whatsapp = by_platform[SurfacePlatform.WHATSAPP]
    assert whatsapp.surfaces == []
    assert {pod.pod_id for pod in whatsapp.available_pods} == {pod_a, pod_b}
    assert whatsapp.default_pod_id is None


async def test_default_pod_is_the_pod_of_the_default_surface():
    pod_a, pod_b = uuid4(), uuid4()
    wa_a, wa_b = _surface(pod_a, created_offset=1), _surface(pod_b, created_offset=2)
    service, _ = _service(
        pod_ids=[pod_a, pod_b],
        surfaces_by_pod={pod_a: [wa_a], pod_b: [wa_b]},
        preferences=UserPreferences(default_surfaces={"WHATSAPP": wa_b.id}),
    )

    groups = await service.list_user_surfaces(
        uuid4(), shared_pods=_shared_pods([pod_a, pod_b])
    )

    whatsapp = next(g for g in groups if g.platform is SurfacePlatform.WHATSAPP)
    assert whatsapp.default_pod_id == pod_b


async def test_choosing_a_pod_writes_the_surface_it_was_given():
    pod, surface_id = uuid4(), uuid4()
    service, users = _service(pod_ids=[pod], surfaces_by_pod={})
    shared = _shared_pods([pod], attached_surface_id=surface_id)

    updated = await service.set_default_pod(
        user_id=uuid4(),
        platform=SurfacePlatform.WHATSAPP,
        pod_id=pod,
        shared_pods=shared,
    )

    shared.attach.assert_awaited_once()
    assert updated.default_surface_for("WHATSAPP") == surface_id
    users.set_preferences.assert_awaited_once()


async def test_choosing_a_pod_on_a_platform_without_a_shared_bot_is_refused():
    service, users = _service(pod_ids=[], surfaces_by_pod={})
    with pytest.raises(AgentSurfaceValidationError):
        await service.set_default_pod(
            user_id=uuid4(),
            platform=SurfacePlatform.SLACK,
            pod_id=uuid4(),
            shared_pods=_shared_pods([]),
        )
    users.set_preferences.assert_not_awaited()
