"""Slack telling an app its tokens in a workspace are gone.

Two events say it. ``app_uninstalled`` arrives once the app is removed outright;
``tokens_revoked`` names the users whose tokens went, bots under ``tokens.bot``.
Slack does not promise their order, so each is read as complete on its own.

A revocation naming only user tokens is not this: the bot still has its token,
and the surface still answers. That one is left to the connector, which finds a
dead user token the first time a tool call is refused.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)


UNINSTALL_EVENT_TYPES = frozenset({"app_uninstalled", "tokens_revoked"})


def is_uninstall_delivery(payload: dict[str, object]) -> bool:
    """Whether a raw event callback is one of the two, read before verification
    so a paused surface's signing secret is among the candidates."""
    event = payload.get("event")
    return isinstance(event, dict) and event.get("type") in UNINSTALL_EVENT_TYPES


def parse_uninstall(
    event: dict[str, object], *, tenant_id: str | None, raw: dict[str, object]
) -> ParsedSurfaceLifecycleEvent | None:
    """An UNINSTALLED event, when this is the app losing its bot here."""
    event_type = str(event.get("type") or "")
    if event_type == "app_uninstalled":
        return ParsedSurfaceLifecycleEvent(
            platform=SurfacePlatform.SLACK,
            kind=SurfaceLifecycleKind.UNINSTALLED,
            tenant_id=tenant_id,
            raw_payload=raw,
        )
    if event_type != "tokens_revoked":
        return None
    tokens = event.get("tokens")
    bots = tokens.get("bot") if isinstance(tokens, dict) else None
    revoked = [
        str(user).strip()
        for user in (bots if isinstance(bots, list) else [])
        if str(user).strip()
    ]
    if not revoked:
        return None
    return ParsedSurfaceLifecycleEvent(
        platform=SurfacePlatform.SLACK,
        kind=SurfaceLifecycleKind.UNINSTALLED,
        tenant_id=tenant_id,
        revoked_identity_ids=revoked,
        raw_payload=raw,
    )
