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
    if not team_id:
        return []
    surfaces = await service.surface_repository.list_active_by_type(
        SurfacePlatform.SLACK.value
    )
    grouped: dict[tuple[str, str], list[UUID]] = {}
    for surface in surfaces:
        if str(surface.external_workspace_id or "").strip() != team_id:
            continue
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
