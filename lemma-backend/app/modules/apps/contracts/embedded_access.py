"""Opening a private app inside an AI tool's conversation.

There the app is framed in someone else's page -- ChatGPT's -- and the access
cookie its host sets on a normal visit is ``SameSite=Lax``, which no browser
sends from a frame inside another site. So the frame asks the Lemma view around
it for a ticket instead of minting one from the person's Lemma session, which it
cannot reach either, and trades it for a cookie made for that place: partitioned
to the page it is framed in, and bound to the AI tool's connection so it ends
when the connection does.

See ``services/app_access.py`` for the ticket and the cookie themselves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.api.dependencies import build_app_use_cases
from app.modules.apps.domain.entities import public_app_url
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.services.app_access import (
    TICKET_TTL_SECONDS,
    AppAccessClaims,
    AppAccessPurpose,
    mint_app_access_token,
)


def app_host_origin(slug: str) -> str | None:
    """The origin the app at ``slug`` is served from, or ``None`` where this
    deployment serves no app host -- what a frame around it may load."""
    return public_app_url(slug)


@dataclass(frozen=True, slots=True)
class EmbeddedAppTicket:
    ticket: str
    origin: str
    """The app host the ticket is good for, and the only one."""
    expires_in_seconds: int


async def mint_embedded_app_ticket(
    uow_factory: UnitOfWorkFactory,
    *,
    slug: str,
    user_id: UUID,
    session_handle: str,
    grant_id: UUID,
) -> EmbeddedAppTicket:
    """A one-minute ticket for the app at ``slug``, for ``user_id``.

    Refused (``AppNotFoundError``) when the deployment serves no app host or the
    person may not open the app -- the same answer for both, as on the app host.
    """
    origin = public_app_url(slug)
    if origin is None:
        raise AppNotFoundError()
    app = await build_app_use_cases(uow_factory).authorize_person_for_app(
        slug=slug, user_id=user_id
    )
    if app.id is None:
        raise AppNotFoundError()
    claims = AppAccessClaims(
        user_id=user_id,
        app_id=app.id,
        origin=origin,
        session_handle=session_handle,
        expires_at=int(time.time()) + TICKET_TTL_SECONDS,
        grant_id=grant_id,
    )
    return EmbeddedAppTicket(
        ticket=mint_app_access_token(AppAccessPurpose.TICKET, claims),
        origin=origin,
        expires_in_seconds=TICKET_TTL_SECONDS,
    )


__all__ = ["EmbeddedAppTicket", "app_host_origin", "mint_embedded_app_ticket"]
