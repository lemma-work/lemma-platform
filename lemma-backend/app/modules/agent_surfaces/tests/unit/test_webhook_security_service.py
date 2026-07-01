from __future__ import annotations

import pytest
from uuid import uuid4

from app.modules.agent_surfaces.config import surface_settings
from app.core.security import _is_surface_webhook_path
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
    SurfaceConfig,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
)

pytestmark = pytest.mark.asyncio


async def test_verify_platform_request_skips_checks_when_security_disabled(
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    service = SurfaceWebhookSecurityService()

    await service.verify_platform_request(
        platform="slack",
        headers={},
        raw_body=b'{"type":"event_callback"}',
    )


async def test_verify_surface_request_uses_surface_telegram_secret(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    service = SurfaceWebhookSecurityService()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(type="TELEGRAM"),
        webhook_secret="surface-secret",
    )

    await service.verify_surface_request(
        surface=surface,
        headers={"x-telegram-bot-api-secret-token": "surface-secret"},
        raw_body=b"{}",
    )


async def test_surface_webhook_auth_exclusion_matches_only_uuid_webhook_paths():
    surface_id = "019e7d94-44b9-75ba-8730-21821b4163f8"

    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook") is True
    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook/extra") is False
    assert _is_surface_webhook_path("/surfaces/not-a-uuid/webhook") is False
    assert _is_surface_webhook_path(f"/pods/{surface_id}/surfaces") is False
