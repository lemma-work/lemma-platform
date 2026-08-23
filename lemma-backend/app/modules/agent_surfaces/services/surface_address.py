"""Which of a user's surfaces answer at the *same* address.

Two surfaces only compete for a sender when a message to one of them could
land on either — that is, when both answer at the same inbound identity. That
happens on the deployment's shared bot/number, and it is the whole point of
it: one Telegram bot, one WhatsApp number, one Slack app, fronting pods in
several organizations, so a DM has to pick which pod hears it.

A bot a pod brought itself is the opposite case. Telegram delivers by token and
a token belongs to one surface across every org (``ensure_unique_telegram_account``);
a connected mailbox has its own address. Messaging that bot is already
unambiguous, so there is no choice to offer and asking for one only suggests
the message might go somewhere else.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)


def _tenant_scope(surface: AgentSurfaceEntity) -> str:
    """The workspace/tenant an inbound event has to match (``matches_tenant``).

    One Microsoft bot app or Slack app serves many tenants, and an event only
    ever reaches the surfaces installed in the tenant it came from — so the
    shared credential is one address *per tenant*, not one overall.
    """
    if surface.surface_type is SurfacePlatform.TEAMS:
        return surface.external_tenant_id or ""
    if surface.surface_type is SurfacePlatform.SLACK:
        return surface.external_workspace_id or ""
    return ""


def inbound_address_key(surface: AgentSurfaceEntity) -> str:
    """An opaque key equal for two surfaces exactly when they share an address.

    The deployment's system credential is one identity per platform (see
    ``system_credential_is_identity``), so every surface riding it shares a key,
    narrowed by the workspace/tenant inbound events are matched against.
    Everything else is keyed on the identity it actually owns — its mailbox
    address, its connected account, its resolved handle — and falls back to the
    surface's own id, which can never collide, when none of those is known yet.
    """
    platform = surface.surface_type.value
    capabilities = get_platform_capabilities(platform)
    if (
        surface.account_id is None
        and surface.credential_mode is SurfaceCredentialMode.SYSTEM
        and (capabilities is None or capabilities.system_credential_is_identity)
    ):
        return f"system:{platform}:{_tenant_scope(surface)}"
    if surface.surface_identity_email:
        return f"email:{surface.surface_identity_email.strip().lower()}"
    if surface.account_id is not None:
        return f"account:{platform}:{surface.account_id}"
    if surface.surface_identity_username:
        return f"handle:{platform}:{surface.surface_identity_username.strip().lower()}"
    return f"surface:{surface.id}"


def contended_surface_ids(surfaces: Iterable[AgentSurfaceEntity]) -> set[UUID]:
    """The ids of surfaces that share their address with another in ``surfaces``.

    Only these are a choice for the user; the rest each own their address.
    """
    by_address: dict[str, list[UUID]] = {}
    for surface in surfaces:
        by_address.setdefault(inbound_address_key(surface), []).append(surface.id)
    return {
        surface_id for ids in by_address.values() if len(ids) > 1 for surface_id in ids
    }
