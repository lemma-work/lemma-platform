"""Resolve the Slack app and surfaces eligible to receive a shared webhook."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.config import reveal_secret
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService
from app.modules.agent_surfaces.services.webhook_security_service import (
    SlackWebhookVerificationCandidate,
)


def slack_team_id(payload: dict) -> str | None:
    team = payload.get("team")
    if isinstance(team, dict):
        nested = str(team.get("id") or "").strip()
        if nested:
            return nested
    return str(payload.get("team_id") or "").strip() or None


def slack_api_app_id(payload: dict) -> str | None:
    return str(payload.get("api_app_id") or "").strip() or None


async def slack_candidates_for_workspace(
    *,
    service: AgentSurfaceService,
    team_id: str | None,
    is_managed: Callable[[str], Awaitable[bool]] | None = None,
    also_paused: bool = False,
) -> list[SlackWebhookVerificationCandidate]:
    # No resolver, no credentials to verify a signature against -- the same
    # reading `list_channels` gives it, and the reason this is `| None` at all.
    resolver = service.credential_resolver
    if not team_id or resolver is None:
        return []
    # The `team_id` names the workspace, and a workspace's surfaces are a
    # handful. This used to read every Slack surface in the deployment and then
    # apply exactly this predicate in Python -- on the path that runs *before*
    # the signature is checked, which `PS-SURF-010` says is the wrong side of
    # "verify every inbound message before acting on it". The credential resolve
    # below was in the same loop, so it ran per surface that happened to match
    # rather than per surface that could.
    # `also_paused` only for an app's removal: a paused surface hears nothing
    # else, but must hear that, or turning it back on reads a dead app as fine.
    surfaces = await service.surface_repository.list_active_for_routing(
        SurfacePlatform.SLACK.value,
        external_workspace_id=team_id,
        also_paused=also_paused,
    )
    grouped: dict[tuple[str, str], list[UUID]] = {}
    for surface in surfaces:
        credentials = await resolver.slack_webhook_credentials(surface)
        if not credentials.app_id or not credentials.signing_secret:
            continue
        grouped.setdefault((credentials.app_id, credentials.signing_secret), []).append(
            surface.id
        )
    # Lemma's own app as this workspace's manager: a candidate even with no
    # teammate answering through it, so its Home and its invites verify. Where
    # Lemma's app is still a pod's bot, that pod's surface is already in the
    # group, and the delivery goes to it as before.
    manager = await _manager_candidate(is_managed, team_id) if is_managed else None
    if manager is not None:
        grouped.setdefault(manager, [])
    return [
        SlackWebhookVerificationCandidate(
            app_id=app_id,
            signing_secret=signing_secret,
            receiver_surface_ids=tuple(surface_ids),
        )
        for (app_id, signing_secret), surface_ids in grouped.items()
    ]


async def _manager_candidate(
    is_managed: Callable[[str], Awaitable[bool]], team_id: str
) -> tuple[str, str] | None:
    """``(app_id, signing_secret)`` of Lemma's own app, when it manages this workspace."""
    app_id = surface_settings.slack_app_id
    secret = reveal_secret(surface_settings.slack_signing_secret)
    if not app_id or not secret:
        return None
    return (app_id, secret) if await is_managed(team_id) else None
