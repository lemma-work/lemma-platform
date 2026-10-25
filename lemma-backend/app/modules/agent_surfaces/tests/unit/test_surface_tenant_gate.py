"""A tenant-scoped surface accepts its own tenant's events and nobody else's."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)

pytestmark = pytest.mark.unit


def _surface(platform: SurfacePlatform, *, tenant: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity.create(
        surface_type=platform,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
        pod_connector_slug="own-bot",
        external_tenant_id=tenant if platform is SurfacePlatform.TEAMS else None,
        external_workspace_id=tenant if platform is SurfacePlatform.SLACK else None,
    )


@pytest.mark.parametrize("platform", [SurfacePlatform.TEAMS, SurfacePlatform.SLACK])
def test_a_surface_that_never_recorded_its_tenant_accepts_no_tenant(platform):
    assert _surface(platform, tenant=None).matches_tenant("tenant-x") is False


@pytest.mark.parametrize("platform", [SurfacePlatform.TEAMS, SurfacePlatform.SLACK])
def test_an_event_naming_no_tenant_reaches_no_tenant_scoped_surface(platform):
    assert _surface(platform, tenant="tenant-a").matches_tenant(None) is False


@pytest.mark.parametrize("platform", [SurfacePlatform.TEAMS, SurfacePlatform.SLACK])
def test_only_the_recorded_tenant_matches(platform):
    surface = _surface(platform, tenant="tenant-a")

    assert surface.matches_tenant("tenant-a") is True
    assert surface.matches_tenant("tenant-b") is False


def test_platforms_without_tenants_are_not_asked():
    telegram = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.TELEGRAM,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
    )

    assert telegram.matches_tenant(None) is True
