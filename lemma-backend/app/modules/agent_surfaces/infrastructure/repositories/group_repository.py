"""The groups a pod's bots are in, read and written as channels' shared places.

A group is a shared place of a GROUP channel (``app.modules.channels``): the
place's row is the group, with its settings, and the place's id is the group's.
This keeps the group's own vocabulary -- a surface finds a group by its chat,
adopts one it was added to, waits on one it asked a platform to create -- and
says each in the channels' terms. Every read is bounded.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.external_accounts import account_of
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.channels.contracts import (
    GroupHome,
    Place,
    Principal,
    SourceKind,
    channel_by_id,
    join,
    source_by_id,
    source_for,
)
from app.modules.channels.contracts.places import (
    change_place,
    confirm_group,
    find_place,
    forget_chat,
    forget_pending_group,
    joined_place,
    left_place,
    open_pending_group,
    place_became_private,
    place_resumed,
    place_stopped,
    owned_place,
    places_hearing,
    shared_places_of_pod,
    shared_places_of_surfaces,
    surfaces_hearing,
)

#: The settings page's ceiling. A bot in more groups than this has outgrown a
#: list, and the read has to stop somewhere.
_MAX_GROUPS_PAGE = 200


def _place(
    surface: AgentSurfaceEntity,
    *,
    chat: str | None,
    title: str | None,
    is_private: bool = True,
) -> Place:
    return Place(
        surface_id=surface.id,
        platform=surface.surface_type.value,
        account=account_of(surface),
        kind=SourceKind.CHAT,
        external_channel_id=chat,
        external_user_id=None,
        title=title,
        is_private=is_private,
    )


class SurfaceGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.uow = SqlAlchemyUnitOfWork(session)

    async def get(
        self, *, surface_id: UUID, external_channel_id: str
    ) -> SurfaceGroup | None:
        """The group this surface is in on this chat, if it knows it."""
        found = await places_hearing(
            self.uow,
            external_channel_id=external_channel_id,
            surface_ids=[surface_id],
        )
        return SurfaceGroup.of(found[0]) if found else None

    async def get_by_id(self, group_id: UUID) -> SurfaceGroup | None:
        source = await source_by_id(self.uow, group_id)
        if source is None or source.kind is not SourceKind.CHAT:
            return None
        return SurfaceGroup.of(source) if source.surface_id else None

    async def list_for_channel(
        self, *, external_channel_id: str, surface_ids: Sequence[UUID]
    ) -> list[SurfaceGroup]:
        """This chat's groups among these surfaces -- at most one per surface."""
        if not surface_ids:
            return []
        found = await places_hearing(
            self.uow,
            external_channel_id=external_channel_id,
            surface_ids=surface_ids,
        )
        return [SurfaceGroup.of(source) for source in found]

    async def list_for_pod(self, pod_id: UUID) -> list[SurfaceGroup]:
        """Every group the pod's bots are in, most recently changed first."""
        found = await shared_places_of_pod(self.uow, pod_id, limit=_MAX_GROUPS_PAGE)
        return [SurfaceGroup.of(source) for source in found]

    async def ensure(
        self,
        *,
        pod_id: UUID,
        surface: AgentSurfaceEntity,
        external_channel_id: str,
        title: str | None = None,
        is_private: bool = True,
    ) -> SurfaceGroup:
        """The group's place, made -- with its channel -- on first sight."""
        group, _ = await self.ensure_noting_creation(
            pod_id=pod_id,
            surface=surface,
            external_channel_id=external_channel_id,
            title=title,
            is_private=is_private,
        )
        return group

    async def ensure_noting_creation(
        self,
        *,
        pod_id: UUID,
        surface: AgentSurfaceEntity,
        external_channel_id: str,
        title: str | None = None,
        is_private: bool = True,
    ) -> tuple[SurfaceGroup, bool]:
        """The group's place, and whether it was made now. A title the platform
        names it by now replaces the one it was known by. ``is_private``: the
        platform keeps it to its members, so its channel is private in Lemma."""
        place = _place(
            surface, chat=external_channel_id, title=title, is_private=is_private
        )
        known = await find_place(self.uow, pod_id=pod_id, place=place)
        if known is not None:
            if title and known.title != title:
                await change_place(self.uow, known.id, {"title": title})
                known = known.model_copy(update={"title": title})
            return SurfaceGroup.of(known), False
        source = await source_for(
            self.uow, pod_id=pod_id, place=place, home=GroupHome(title=title)
        )
        return SurfaceGroup.of(source), True

    async def surface_ids_for_channel(
        self, *, platform: str, external_channel_id: str
    ) -> list[UUID]:
        """The surfaces whose bots are in this chat, in any pod: routing only."""
        return await surfaces_hearing(
            self.uow, platform=platform, external_channel_id=external_channel_id
        )

    async def create_pending(
        self,
        *,
        pod_id: UUID,
        surface: AgentSurfaceEntity,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool = True,
    ) -> SurfaceGroup:
        source = await open_pending_group(
            self.uow,
            pod_id=pod_id,
            place=_place(surface, chat=None, title=title),
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )
        return SurfaceGroup.of(source)

    async def find_owned(
        self, *, surface_id: UUID, owner_user_id: UUID, title: str
    ) -> SurfaceGroup | None:
        """The group this member already has by this name here, if any."""
        source = await owned_place(
            self.uow, surface_id=surface_id, owner_user_id=owner_user_id, title=title
        )
        return SurfaceGroup.of(source) if source else None

    async def confirm_created(
        self,
        *,
        platform: str,
        request_id: str,
        external_channel_id: str,
        title: str | None,
        invite_link: str | None,
        surface_ids: Sequence[UUID] | None = None,
    ) -> SurfaceGroup | None:
        """The group the platform made. On the surfaces named, or -- since a
        platform's request ids are its own -- wherever it was asked for."""
        source = await confirm_group(
            self.uow,
            surface_ids=surface_ids,
            platform=None if surface_ids is not None else platform,
            request_id=request_id,
            external_channel_id=external_channel_id,
            title=title,
            invite_link=invite_link,
        )
        return SurfaceGroup.of(source) if source else None

    async def set_invite_link(self, group_id: UUID, invite_link: str) -> None:
        await change_place(self.uow, group_id, {"invite_link": invite_link})

    async def forget_request(
        self,
        *,
        platform: str,
        request_id: str,
        surface_ids: Sequence[UUID] | None = None,
    ) -> None:
        """Drop a creation the platform refused: there is no group to keep."""
        await forget_pending_group(
            self.uow,
            request_id=request_id,
            surface_ids=surface_ids,
            platform=None if surface_ids is not None else platform,
        )

    async def forget_channel(
        self,
        *,
        platform: str,
        external_channel_id: str,
        surface_ids: Sequence[UUID] | None = None,
    ) -> None:
        """A group the platform deleted: nobody can speak there.

        Its channel stays, with everything said in it.
        """
        await forget_chat(
            self.uow,
            external_channel_id=external_channel_id,
            surface_ids=surface_ids,
            platform=None if surface_ids is not None else platform,
        )

    async def set_owner(self, group_id: UUID, owner_user_id: UUID | None) -> None:
        """The member who answers for the group -- who is in its channel too,
        so they can read what they answer for."""
        await change_place(self.uow, group_id, {"owner_user_id": owner_user_id})
        if owner_user_id is None:
            return
        source = await source_by_id(self.uow, group_id)
        channel = await channel_by_id(self.uow, source.channel_id) if source else None
        if channel is not None:
            await join(
                self.uow, channel=channel, principal=Principal.member(owner_user_id)
            )

    async def set_answers_outsiders(
        self, group_id: UUID, *, answers_outsiders: bool
    ) -> None:
        await change_place(self.uow, group_id, {"answers_outsiders": answers_outsiders})

    async def set_shared_externally(self, group_id: UUID, *, shared: bool) -> None:
        await change_place(self.uow, group_id, {"shared_externally": shared})

    async def became_private(self, group_id: UUID) -> bool:
        """The platform now keeps the group to its members; so does Lemma."""
        return await place_became_private(self.uow, group_id)

    async def member_left(self, group_id: UUID, principal: Principal) -> bool:
        """Somebody the platform says left the group is out of its channel."""
        return await left_place(self.uow, group_id, principal)

    async def member_joined(
        self, group_id: UUID, principal: Principal, *, name: str | None = None
    ) -> bool:
        """Somebody the platform says joined the group is in its channel."""
        return await joined_place(self.uow, group_id, principal, name=name)

    async def bot_removed(self, group_id: UUID) -> bool:
        """The bot was taken out of the group: its channel is archived."""
        return await place_stopped(self.uow, group_id)

    async def bots_removed_everywhere(self, surface_ids: Sequence[UUID]) -> int:
        """These surfaces' bots taken out of every group at once -- their app
        removed from the workspace, which sends no leave per channel. Each
        group is archived as `bot_removed` archives one; returns how many were
        not already."""
        archived = 0
        after: UUID | None = None
        while page := await shared_places_of_surfaces(
            self.uow, surface_ids, after=after, limit=_MAX_GROUPS_PAGE
        ):
            for source in page:
                archived += await self.bot_removed(source.id)
            after = page[-1].id
        return archived

    async def bot_returned(self, group_id: UUID) -> bool:
        """The bot is back in the group: its channel is brought back."""
        return await place_resumed(self.uow, group_id)
