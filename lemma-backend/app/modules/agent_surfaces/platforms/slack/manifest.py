"""The Slack app manifest a workspace pastes to run its own Slack app.

Served rather than copied out of the repo so the URLs always match the
deployment answering the request, and the scopes always match the code that will
consume the events. The committed manifest is the single source of truth; only
the URLs are substituted, because they are the only part that depends on where
Lemma is running.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.platforms.common import computed_webhook_url

MANIFEST_PATH = (
    Path(__file__).resolve().parents[4] / "manifests" / "slack" / "manifest.json"
)


def build_slack_app_manifest(surface: AgentSurfaceEntity) -> dict[str, Any]:
    """Return the manifest with this surface's URLs filled in."""
    if surface.surface_type is not SurfacePlatform.SLACK:
        raise AgentSurfaceValidationError("Only Slack surfaces have an app manifest.")
    manifest = json.loads(MANIFEST_PATH.read_text())
    webhook_url = computed_webhook_url(surface)
    if webhook_url:
        manifest["settings"]["event_subscriptions"]["request_url"] = webhook_url
        manifest["settings"]["interactivity"]["request_url"] = webhook_url
    base = str(settings.api_url or "").rstrip("/")
    if base:
        manifest["oauth_config"]["redirect_urls"] = [
            f"{base}/connectors/connect-requests/oauth/callback"
        ]
    return manifest
