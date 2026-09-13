"""Resolve the Slack app and surfaces eligible to receive a shared webhook."""

from __future__ import annotations

from uuid import UUID

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
    *, service: AgentSurfaceService, team_id: str | None
) -> list[SlackWebhookVerificationCandidate]:
    # No resolver, no credentials to verify a signature against -- the same
    # reading `list_channels` gives it, and the reason this is `| None` at all.
    if not team_id or service._credential_resolver is None:
        return []
    # The `team_id` names the workspace, and a workspace's surfaces are a
    # handful. This used to read every Slack surface in the deployment and then
    # apply exactly this predicate in Python -- on the path that runs *before*
    # the signature is checked, which `PS-SURF-010` says is the wrong side of
    # "verify every inbound message before acting on it". The credential resolve
    # below was in the same loop, so it ran per surface that happened to match
    # rather than per surface that could.
    surfaces = await service.surface_repository.list_active_for_routing(
        SurfacePlatform.SLACK.value, external_workspace_id=team_id
    )
    grouped: dict[tuple[str, str], list[UUID]] = {}
    for surface in surfaces:
        credentials = await service._credential_resolver.slack_webhook_credentials(
            surface
        )
        if not credentials.app_id or not credentials.signing_secret:
            continue
        grouped.setdefault((credentials.app_id, credentials.signing_secret), []).append(
            surface.id
        )
    return [
        SlackWebhookVerificationCandidate(
            app_id=app_id,
            signing_secret=signing_secret,
            receiver_surface_ids=tuple(surface_ids),
        )
        for (app_id, signing_secret), surface_ids in grouped.items()
    ]
