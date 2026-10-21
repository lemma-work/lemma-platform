"""``account_of``: which account a surface's places belong to.

Two agents' Slack apps (or Teams bots) in one workspace are two accounts --
one between them handed each app the other's places and token. The same app
installed again is the same account, so its places keep their history; so is
a WhatsApp number's surface made again.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.agent_surfaces.domain.surface_config import SurfaceConfig
from app.modules.channels.contracts import AccountKey


def _surface(platform: SurfacePlatform, *, bot: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=f"{platform.value.lower()}-{uuid4().hex[:6]}",
        agent_id=uuid4(),
        surface_type=platform,
        account_id=uuid4(),
        external_workspace_id="T123" if platform is SurfacePlatform.SLACK else None,
        external_tenant_id="tenant-1" if platform is SurfacePlatform.TEAMS else None,
        surface_identity_id=bot,
        config=SurfaceConfig(),
        is_active=True,
    )


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
def test_two_agents_apps_in_one_workspace_are_two_accounts(platform):
    reviewer = _surface(platform, bot="B-REVIEWER")
    support = _surface(platform, bot="B-SUPPORT")

    assert account_of(reviewer) != account_of(support)
    assert account_of(reviewer).workspace == account_of(support).workspace


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
def test_the_same_app_installed_again_is_the_same_account(platform):
    first = _surface(platform, bot="B-REVIEWER")
    reinstalled = _surface(platform, bot="B-REVIEWER")

    assert first.id != reinstalled.id
    assert account_of(first) == account_of(reinstalled)


def test_a_surface_that_never_recorded_its_bot_names_only_its_workspace():
    assert account_of(_surface(SurfacePlatform.SLACK, bot=None)) == AccountKey(
        workspace="T123"
    )


def test_a_whatsapp_number_is_its_account_whichever_surface_reaches_it():
    first = _surface(SurfacePlatform.WHATSAPP, bot="PHONE-ID-1")
    again = _surface(SurfacePlatform.WHATSAPP, bot="PHONE-ID-1")

    assert account_of(first) == account_of(again) == AccountKey(workspace="PHONE-ID-1")
