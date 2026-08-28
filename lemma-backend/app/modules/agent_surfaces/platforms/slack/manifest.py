"""The Slack app manifest a workspace pastes to run its own Slack app.

Served rather than copied out of the repo so the URLs always match the
deployment answering the request, and the scopes always match the code that will
consume the events. The committed manifest is the single source of truth; only
the URLs and the app's name are substituted — the URLs because they depend on
where Lemma is running, the name because one Slack app is one bot user, so an
agent that wants a bot of its own wants that bot to arrive already called by its
own name.
"""

from __future__ import annotations

import json
import re
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

# The name the committed manifest carries, and what any other name replaces.
DEFAULT_APP_NAME = "Lemma"
# Slack's own manifest limits. It validates on paste, so a name that overruns
# either does not fail in review — it fails as an error page in front of the
# person who just clicked "make my app", with nothing naming the cause.
_MAX_APP_NAME = 35
_MAX_BOT_DISPLAY_NAME = 80


def slack_app_name(agent_name: str | None) -> str:
    """The Slack app name for an agent, normalized to what Slack accepts.

    Periods go because Slack rejects them in a bot's display name, and a name it
    rejects takes the whole manifest with it. Everything else is left alone:
    this is the agent's name as its own workspace will read it, and quietly
    rewriting it into something else would defeat the point of asking.

    Falls back to the default for anything that normalizes to nothing, so a
    whitespace-only or all-period agent name still produces a usable app.
    """
    normalized = re.sub(r"\s+", " ", str(agent_name or "").replace(".", " ")).strip()
    return normalized or DEFAULT_APP_NAME


def build_slack_app_manifest(*, agent_name: str | None = None) -> dict[str, Any]:
    """Return the manifest with this deployment's URLs and the app's name filled in.

    Takes no surface. Both URLs it substitutes are deployment-wide — the shared
    Slack endpoint and the OAuth callback — so the manifest can be handed over
    before any surface exists, which is the order people actually work in: you
    need the app before you can supply the client id that creates the account
    the surface is built on.

    ``agent_name`` is the same shape of thing: the caller names the agent this
    app will be, and gets back a manifest that already says so. Nothing is read
    from a pod to do it, which is what keeps this endpoint free of pod scope.

    What a manifest cannot carry is the app's *icon* — Slack's
    ``display_information`` has no field for one — so a bot made from this
    arrives with a generic avatar until somebody uploads one. Its messages do
    not: those already go out under the agent's own name and face, through
    ``chat:write.customize``.
    """
    manifest = json.loads(MANIFEST_PATH.read_text())
    name = slack_app_name(agent_name)
    if name != DEFAULT_APP_NAME:
        manifest["display_information"]["name"] = name[:_MAX_APP_NAME]
        manifest["features"]["bot_user"]["display_name"] = name[:_MAX_BOT_DISPLAY_NAME]
        # The committed description is written around the default name, so this
        # renames it rather than rewriting it — there is one occurrence, and it
        # is the subject of the sentence.
        agent_view = manifest["features"].get("agent_view") or {}
        description = agent_view.get("agent_description")
        if description:
            agent_view["agent_description"] = description.replace(
                DEFAULT_APP_NAME, name
            )
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
