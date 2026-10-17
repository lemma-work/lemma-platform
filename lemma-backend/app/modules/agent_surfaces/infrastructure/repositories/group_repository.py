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
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.channels.contracts import (
    GroupHome,
    Place,
    SourceKind,
    source_by_id,
    source_for,
)
from app.modules.channels.contracts.places import (
    change_place,
    confirm_group,
    find_place,
    forget_chat,
    forget_pending_group,
    open_pending_group,
    owned_place,
    places_hearing,
    shared_places_of_pod,
)

#: The settings page's ceiling. A bot in more groups than this has outgrown a
#: list, and the read has to stop somewhere.
_MAX_GROUPS_PAGE = 200


def _place(*, surface_id: UUID, platform: str, chat: str, title: str | None) -> Place:
    return Place(
        surface_id=surface_id,
        platform=platform,
        kind=SourceKind.SHARED,
        external_channel_id=chat,
        external_user_id=None,
        title=title,
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
            platform=None,
            external_channel_id=external_channel_id,
            surface_ids=[surface_id],
        )
        return SurfaceGroup.of(found[0]) if found else None

    async def get_by_id(self, group_id: UUID) -> SurfaceGroup | None:
        source = await source_by_id(self.uow, group_id)
        if source is None or source.kind is not SourceKind.SHARED:
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
            platform=None,
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
        surface_id: UUID,
        platform: str,
        external_channel_id: str,
        title: str | None = None,
    ) -> SurfaceGroup:
        """The group's place, made -- with its channel -- on first sight."""
        group, _ = await self.ensure_noting_creation(
            pod_id=pod_id,
            surface_id=surface_id,
            platform=platform,
            external_channel_id=external_channel_id,
            title=title,
        )
        return group

    async def ensure_noting_creation(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID,
        platform: str,
        external_channel_id: str,
        title: str | None = None,
    ) -> tuple[SurfaceGroup, bool]:
        """The group's place, and whether it was made now. A title the platform
        names it by now replaces the one it was known by."""
        place = _place(
            surface_id=surface_id,
            platform=platform,
            chat=external_channel_id,
            title=title,
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
        """The surfaces whose bots are in this chat."""
        found = await places_hearing(
            self.uow, platform=platform, external_channel_id=external_channel_id
        )
        return [source.surface_id for source in found if source.surface_id]

    async def create_pending(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID,
        platform: str,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool = True,
    ) -> SurfaceGroup:
        source = await open_pending_group(
            self.uow,
            pod_id=pod_id,
            surface_id=surface_id,
            platform=platform,
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
    ) -> SurfaceGroup | None:
        source = await confirm_group(
            self.uow,
            platform=platform,
            request_id=request_id,
            external_channel_id=external_channel_id,
            title=title,
            invite_link=invite_link,
        )
        return SurfaceGroup.of(source) if source else None

    async def set_invite_link(self, group_id: UUID, invite_link: str) -> None:
        await change_place(self.uow, group_id, {"invite_link": invite_link})

    async def forget_request(self, *, platform: str, request_id: str) -> None:
        """Drop a creation the platform refused: there is no group to keep."""
        await forget_pending_group(self.uow, platform=platform, request_id=request_id)

    async def forget_channel(self, *, platform: str, external_channel_id: str) -> None:
        """A group the platform deleted: nobody can speak there.

        Its channel stays, with everything said in it.
        """
        await forget_chat(
            self.uow, platform=platform, external_channel_id=external_channel_id
        )

    async def set_owner(self, group_id: UUID, owner_user_id: UUID | None) -> None:
        await change_place(self.uow, group_id, {"owner_user_id": owner_user_id})

    async def set_answers_outsiders(
        self, group_id: UUID, *, answers_outsiders: bool
    ) -> None:
        await change_place(self.uow, group_id, {"answers_outsiders": answers_outsiders})

    async def set_shared_externally(self, group_id: UUID, *, shared: bool) -> None:
        await change_place(self.uow, group_id, {"shared_externally": shared})
