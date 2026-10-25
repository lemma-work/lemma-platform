"""The account a surface runs as, read through its pod connector.

A surface stores the slug of a SHARED pod connector, never an account. Every
step that needs the account behind it -- credentials, the installation a
verified identity was bound through, "is this bot already bound" -- asks here,
and the answer is re-read from the connector each time, so the account's owner
withdrawing it (or the connector being removed, the installation disabled, the
account needing reauth) stops the surface at its next step with the
connector's own typed error.
"""

from __future__ import annotations

import re
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.surface_connectors import surface_connector_id
from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorMode,
    PodConnectorNotFoundError,
    get_pod_connector,
    resolve_pod_connector,
    resolve_standing_pod_connector,
    shared_account_ids,
)
from app.modules.connectors.contracts.surfaces import SurfaceAccount, account


def connector_slug_for(platform: SurfacePlatform, surface_name: str) -> str:
    """A readable slug base for the connector a surface's own bot is shared as."""
    tail = re.sub(r"[^a-z0-9_-]+", "-", surface_name.lower()).strip("-_")
    base = platform.value.lower()
    if tail and tail != base:
        base = f"{base}-{tail}"
    return base[:50]


async def bound_account_id(uow, surface: AgentSurfaceEntity) -> UUID | None:
    """The account the surface runs as now, or ``None`` on system credentials.

    Raises the pod connector's typed refusal when it runs as nothing. A read
    with no write: it runs on every message, on paths that release their
    connection between reads.
    """
    if surface.pod_connector_slug is None:
        return None
    resolved = await resolve_standing_pod_connector(
        uow,
        pod_id=surface.pod_id,
        slug=surface.pod_connector_slug,
        record_use=False,
    )
    return resolved.account_id


async def installation_identity(uow, surface: AgentSurfaceEntity) -> UUID:
    """What a person's verified binding through this surface is keyed on.

    The bot's own account where the surface runs as one -- the binding is to
    that app, whichever surface reaches it -- and the surface itself on the
    deployment's shared credentials.
    """
    return await bound_account_id(uow, surface) or surface.id


async def bound_account(uow, surface: AgentSurfaceEntity) -> SurfaceAccount | None:
    """The account the surface runs as, credentials left out, or ``None``.

    For display -- a reach handle, a label -- where a connector that runs as
    nothing is an absence to show, not a failure to raise.
    """
    account_id = (await bound_account_ids(uow, [surface])).get(surface.id)
    return await account(uow, account_id) if account_id is not None else None


async def bound_account_ids(
    uow, surfaces: list[AgentSurfaceEntity]
) -> dict[UUID, UUID]:
    """Surface id -> the account it runs as, for surfaces that run as one now.

    A read for listings: no side effect and no error, so a surface whose
    connector runs as nothing is simply absent from the answer.
    """
    refs = {
        surface.id: (surface.pod_id, surface.pod_connector_slug)
        for surface in surfaces
        if surface.pod_connector_slug is not None
    }
    accounts = await shared_account_ids(uow, list(refs.values()))
    return {
        surface_id: accounts[ref] for surface_id, ref in refs.items() if ref in accounts
    }


async def account_for_binding(
    uow,
    ctx,
    *,
    pod_id: UUID,
    slug: str,
    platform: SurfacePlatform,
) -> UUID:
    """The account binding a surface to ``slug`` would run as, checked.

    The connector has to be SHARED: a surface answers with nobody at the
    keyboard, so there is no member whose own account a per-member connector
    could mean. And it has to be the platform's connector -- a Gmail account
    is not a Slack bot.

    With a person acting (``ctx``) it resolves as them, so binding needs the
    same ``connector.use`` and account access running an operation through
    the connector would; the flows that bind a surface on a person's behalf
    with no request context resolve the connector's standing instead.
    """
    connector = await get_pod_connector(uow, pod_id=pod_id, slug=slug)
    if connector is None:
        raise PodConnectorNotFoundError(slug)
    if connector.mode is not PodConnectorMode.SHARED:
        raise AgentSurfaceValidationError(
            f"'{slug}' uses each member's own account. A surface answers with "
            "nobody behind it, so it needs a shared connector: one whose "
            "account its owner shared with the pod."
        )
    resolved = (
        await resolve_pod_connector(uow, ctx, pod_id=pod_id, slug=slug)
        if ctx is not None
        else await resolve_standing_pod_connector(
            uow, pod_id=pod_id, slug=slug, record_use=False
        )
    )
    expected = surface_connector_id(platform)
    if resolved.connector_id.lower() != expected:
        raise AgentSurfaceValidationError(
            f"{platform.value.title()} surfaces need a {expected} connector; "
            f"'{slug}' is {resolved.connector_id}."
        )
    return resolved.account_id


__all__ = [
    "account_for_binding",
    "bound_account",
    "bound_account_id",
    "bound_account_ids",
    "connector_slug_for",
    "installation_identity",
]
