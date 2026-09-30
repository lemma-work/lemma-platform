"""Opening a WhatsApp group for a member, from their chat with the bot.

A WhatsApp business number cannot be added to anybody's group. It can create
one, and people join it by its invite link. So what is "add the bot to the
group" on Telegram is, on WhatsApp, "ask the bot to open one": a member asks in
their own chat, the bot creates the group with them as the person who answers
for it (``SurfaceGroup.owner_user_id``), and hands back the link to pass on.

Meta creates the group asynchronously. The call answers with a ``request_id``
only; the group's id and link arrive in a webhook moments later
(``services/group_updates``). So the request is recorded as a pending group and
then waited on briefly, which is almost always long enough. When it is not, the
member is told the link is on its way, and asking again for the same group
answers from the row instead of creating a second one.

Groups hold at most eight people, and the bot answers there only when somebody
addresses it -- by name, by an ``@`` of its number, or by replying to it.
"""

from __future__ import annotations

import asyncio
from time import monotonic
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
    WhatsAppClient,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)

#: How long the member's turn waits for Meta to confirm the group. Confirmation
#: is usually a second or two; past this the turn moves on and says so.
CONFIRMATION_WAIT_SECONDS = 12.0
_POLL_SECONDS = 1.0


class OpenedGroup(BaseModel):
    """What the member is told about the group they asked for."""

    title: str
    invite_link: str | None = None
    #: True while Meta has not yet confirmed the group.
    pending: bool = False
    #: True when the member already had this group and nothing new was made.
    already_open: bool = False


class GroupNotOpened(RuntimeError):
    """Meta took the request but named no request to wait on."""


class WhatsAppGroupOpener:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        wait_seconds: float = CONFIRMATION_WAIT_SECONDS,
        poll_seconds: float = _POLL_SECONDS,
    ) -> None:
        self.uow_factory = uow_factory
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    async def open(
        self, *, surface_id: UUID, owner_user_id: UUID, title: str
    ) -> OpenedGroup:
        """The group, as the member asking for it in chat is told about it."""
        group, already_open = await self.request_group(
            surface_id=surface_id, owner_user_id=owner_user_id, title=title
        )
        return _told(group, already_open=already_open)

    async def request_group(
        self,
        *,
        surface_id: UUID,
        owner_user_id: UUID,
        title: str,
        answers_outsiders: bool = True,
    ) -> tuple[SurfaceGroup, bool]:
        """The group this member asked for, and whether they already had it."""
        name = " ".join(title.split())[:GROUP_SUBJECT_MAX_CHARS]
        if not name:
            raise ValueError("A group needs a name")
        async with self.uow_factory() as uow:
            existing = await SurfaceGroupRepository(uow.session).find_owned(
                surface_id=surface_id, owner_user_id=owner_user_id, title=name
            )
            if existing is not None:
                return existing, True
            surface = await SurfaceRepository(uow).get(surface_id)
            if surface is None:
                raise LookupError("This bot is no longer connected to WhatsApp")
            credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
        # Meta is called with no connection held, as every platform call is.
        client = WhatsAppClient.from_credentials(credentials)
        phone_number_id = str(credentials.get("phone_number_id") or "")
        request_id = await client.create_group(
            phone_number_id=phone_number_id, subject=name
        )
        if not request_id:
            raise GroupNotOpened("WhatsApp accepted the request but named none")
        async with self.uow_factory() as uow:
            group = await SurfaceGroupRepository(uow.session).create_pending(
                pod_id=surface.pod_id,
                surface_id=surface.id,
                platform=surface.surface_type.value,
                title=name,
                owner_user_id=owner_user_id,
                request_id=request_id,
                answers_outsiders=answers_outsiders,
            )
            await uow.commit()
        return await self._confirmed(group), False

    async def _confirmed(self, group: SurfaceGroup) -> SurfaceGroup:
        """The group once Meta has confirmed it, or as it stands at the deadline."""
        deadline = monotonic() + self.wait_seconds
        while group.invite_link is None and monotonic() < deadline:
            await asyncio.sleep(self.poll_seconds)
            async with self.uow_factory() as uow:
                group = (
                    await SurfaceGroupRepository(uow.session).get_by_id(group.id)
                    or group
                )
        return group


def _told(group: SurfaceGroup, *, already_open: bool = False) -> OpenedGroup:
    return OpenedGroup(
        title=group.title or "",
        invite_link=group.invite_link,
        pending=group.invite_link is None,
        already_open=already_open,
    )
