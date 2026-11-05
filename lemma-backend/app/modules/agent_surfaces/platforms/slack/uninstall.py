"""Slack telling an app its tokens in a workspace are gone.

Two events say it. ``app_uninstalled`` arrives once the app is removed outright;
``tokens_revoked`` names the users whose tokens went, bots under ``tokens.bot``.
Slack does not promise their order, so each is read as complete on its own.

A revocation naming only user tokens is not this: the bot still has its token,
and the surface still answers. That one is left to the connector, which finds a
dead user token the first time a tool call is refused.

An Enterprise Grid org-wide install says the same thing with
``app_uninstalled_team``, under an enterprise rather than a workspace; it is
not read here.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)


#: Both event types, for the request-time classifier to send to the
#: platform-update path: anything it does not name there is dropped with a 200.
UNINSTALL_EVENT_TYPES = frozenset({"app_uninstalled", "tokens_revoked"})


def is_uninstall_delivery(payload: Mapping[str, object]) -> bool:
    """Whether a raw event callback is one of the two, read before verification
    so a surface in any status has its signing secret among the candidates.

    The body is unauthenticated here, so a ``type`` that is not a string -- a
    list, say, which cannot be looked up in a set -- is simply not one.
    """
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return False
    event_type = event.get("type")
    return isinstance(event_type, str) and event_type in UNINSTALL_EVENT_TYPES


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
