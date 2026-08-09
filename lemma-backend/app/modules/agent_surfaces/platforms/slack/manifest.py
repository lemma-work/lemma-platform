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
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.common import platform_webhook_url

# parents[5] is the backend root: .../app/modules/agent_surfaces/platforms/slack
# → slack, platforms, agent_surfaces, modules, app, lemma-backend. Off by one
# and this reads app/manifests/, which does not exist — every call 500s.
MANIFEST_PATH = (
    Path(__file__).resolve().parents[5] / "manifests" / "slack" / "manifest.json"
)


def build_slack_app_manifest() -> dict[str, Any]:
    """Return the manifest with this deployment's URLs filled in.

    Takes no surface. Both URLs it substitutes are deployment-wide — the shared
    Slack endpoint and the OAuth callback — so the manifest can be handed over
    before any surface exists, which is the order people actually work in: you
    need the app before you can supply the client id that creates the account
    the surface is built on.
    """
    manifest = json.loads(MANIFEST_PATH.read_text())
    webhook_url = platform_webhook_url(SurfacePlatform.SLACK)
    if webhook_url:
        manifest["settings"]["event_subscriptions"]["request_url"] = webhook_url
        manifest["settings"]["interactivity"]["request_url"] = webhook_url
    base = str(settings.api_url or "").rstrip("/")
    if base:
        manifest["oauth_config"]["redirect_urls"] = [
            f"{base}/connectors/connect-requests/oauth/callback"
        ]
    return manifest
