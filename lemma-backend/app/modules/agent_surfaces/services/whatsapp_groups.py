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

from redis.exceptions import RedisError

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.redis.counters import incr_with_ttl
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.group_hello import hello_for

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


class GroupOpenLimitReached(RuntimeError):
    """This member has opened as many groups on this bot today as one may."""


class GroupsNotAvailable(RuntimeError):
    """Meta does not let this number create groups at all.

    The Groups API is open to eligible business numbers only; a test number,
    for one, is refused. Asking again changes nothing until Meta changes it.
    """


#: Meta's answer to a number that may not use the Groups API.
_NOT_ELIGIBLE_FOR_GROUPS = 131215


#: Groups one member may open on one bot in a day. A shared Meta number is
#: everybody's, and every group it creates is one more chat it answers in.
OPENS_PER_MEMBER_PER_DAY = 10
_DAY = 86_400


async def may_configure_bot(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, surface: AgentSurfaceEntity
) -> bool:
    """Whether this member may change the bot's setup -- the bar for opening groups.

    Asked of the member's own rights, as the Groups API asks it of the caller,
    not of the run's: the run acts for the member, and opening a group in their
    name is something the member must be able to do.
    """
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=surface.pod_id
    )
    if surface.agent_id is None or is_pod_default_agent(
        surface.agent_id, pod_id=surface.pod_id
    ):
        return await ctx.can(Permissions.AGENT_UPDATE)
    return await ctx.can(
        Permissions.AGENT_UPDATE,
        ResourceRef(
            resource_type=ResourceType.AGENT,
            resource_id=surface.agent_id,
            pod_id=surface.pod_id,
        ),
    )


class WhatsAppGroupOpener:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        wait_seconds: float = CONFIRMATION_WAIT_SECONDS,
        poll_seconds: float = _POLL_SECONDS,
        redis=None,
    ) -> None:
        self.uow_factory = uow_factory
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds
        self.redis = redis

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
            # The group's description is the bot's hello: how to ask it, and
            # that the pod keeps what is said there (see group_hello).
            description = await hello_for(uow, surface)
        if not await self._within_daily_limit(
            surface_id=surface_id, owner_user_id=owner_user_id
        ):
            raise GroupOpenLimitReached(
                f"One person can open {OPENS_PER_MEMBER_PER_DAY} groups a day here."
            )
        # Meta is called with no connection held, as every platform call is.
        client = WhatsAppClient.from_credentials(credentials)
        phone_number_id = str(credentials.get("phone_number_id") or "")
        try:
            request_id = await client.create_group(
                phone_number_id=phone_number_id, subject=name, description=description
            )
        except WhatsAppApiError as refused:
            if refused.meta_code == _NOT_ELIGIBLE_FOR_GROUPS:
                raise GroupsNotAvailable(
                    "WhatsApp doesn't let this number start groups. Meta opens "
                    "groups to eligible business numbers only."
                ) from refused
            raise
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

    async def _within_daily_limit(
        self, *, surface_id: UUID, owner_user_id: UUID
    ) -> bool:
        """Count this opening; fails closed, since a group not opened can be retried."""
        try:
            opened = await incr_with_ttl(
                self.redis or get_redis(),
                f"whatsapp:group_opens:{surface_id}:{owner_user_id}",
                _DAY,
            )
        except RedisError, OSError:
            return False
        return opened <= OPENS_PER_MEMBER_PER_DAY

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
