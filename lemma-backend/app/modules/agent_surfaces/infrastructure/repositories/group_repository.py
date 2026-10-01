"""Reading and writing the groups a pod's bots are in, and their log.

Every read is bounded. A group is found by its key (surface, chat) or listed a
page at a time for its surface's settings, and the log is only ever read as
"the last N lines", newest first -- which is what the index serves and all that
a run's background context can use.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import (
    GROUP_LOG_RETENTION,
    GroupLine,
    SurfaceGroup,
)
from app.modules.agent_surfaces.infrastructure.group_models import (
    AgentSurfaceGroupMessageModel,
    AgentSurfaceGroupModel,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser

#: The settings page's ceiling. A bot in more groups than this has outgrown a
#: list, and the read has to stop somewhere.
_MAX_GROUPS_PAGE = 200

#: How many pods' bots one chat is looked up for. A WhatsApp group has one; a
#: Telegram group could hold a few pods' surfaces on the shared bot.
_MAX_SURFACES_PER_CHAT = 20

#: Longest line kept. The log is background for a model, not an archive, and one
#: pasted document should not crowd out everything said around it.
MAX_LINE_CHARS = 2000


class SurfaceGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, *, surface_id: UUID, external_channel_id: str
    ) -> SurfaceGroup | None:
        stmt = select(AgentSurfaceGroupModel).where(
            AgentSurfaceGroupModel.surface_id == surface_id,
            AgentSurfaceGroupModel.external_channel_id == external_channel_id,
        )
        # `first()` on a unique key: at most one row can match.
        model = (await self.session.execute(stmt)).scalars().first()
        return model.to_entity() if model else None

    async def get_by_id(self, group_id: UUID) -> SurfaceGroup | None:
        model = await self.session.get(AgentSurfaceGroupModel, group_id)
        return model.to_entity() if model else None

    async def list_for_channel(
        self, *, external_channel_id: str, surface_ids: Sequence[UUID]
    ) -> list[SurfaceGroup]:
        """This chat's rows among these surfaces -- at most one per surface."""
        if not surface_ids:
            return []
        stmt = (
            select(AgentSurfaceGroupModel)
            .where(
                AgentSurfaceGroupModel.external_channel_id == external_channel_id,
                AgentSurfaceGroupModel.surface_id.in_(list(surface_ids)),
            )
            .limit(len(surface_ids))
        )
        models = (await self.session.execute(stmt)).scalars().all()
        return [model.to_entity() for model in models]

    async def list_for_pod(self, pod_id: UUID) -> list[SurfaceGroup]:
        """Every group the pod's bots are in, most recently changed first."""
        stmt = (
            select(AgentSurfaceGroupModel)
            .where(AgentSurfaceGroupModel.pod_id == pod_id)
            .order_by(AgentSurfaceGroupModel.updated_at.desc())
            .limit(_MAX_GROUPS_PAGE)
        )
        models = (await self.session.execute(stmt)).scalars().all()
        return [model.to_entity() for model in models]

    async def latest_line_at(self, group_ids: Sequence[UUID]) -> dict[UUID, datetime]:
        """When each of these groups last had something said in it."""
        if not group_ids:
            return {}
        stmt = (
            select(
                AgentSurfaceGroupMessageModel.group_id,
                func.max(AgentSurfaceGroupMessageModel.created_at),
            )
            .where(AgentSurfaceGroupMessageModel.group_id.in_(list(group_ids)))
            .group_by(AgentSurfaceGroupMessageModel.group_id)
            .limit(len(group_ids))
        )
        rows = (await self.session.execute(stmt)).all()
        return {group_id: at for group_id, at in rows if at is not None}

    async def ensure(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID,
        platform: str,
        external_channel_id: str,
        title: str | None = None,
    ) -> SurfaceGroup:
        """The group's row, created on first sight."""
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
        """The group's row, and whether this call is what created it.

        ``ON CONFLICT DO NOTHING`` then a read, rather than read-then-insert:
        two first messages in a new group arrive together often enough, and the
        unique index is the only thing both of them can agree on -- which is
        also what makes "created" true for exactly one of them.
        """
        inserted = await self.session.execute(
            insert(AgentSurfaceGroupModel)
            .values(
                pod_id=pod_id,
                surface_id=surface_id,
                platform=platform,
                external_channel_id=external_channel_id,
                title=title,
            )
            .on_conflict_do_nothing(
                index_elements=["surface_id", "external_channel_id"]
            )
            .returning(AgentSurfaceGroupModel.id)
        )
        created = inserted.scalars().first() is not None
        group = await self.get(
            surface_id=surface_id, external_channel_id=external_channel_id
        )
        if group is None:  # pragma: no cover - the insert above made it
            raise LookupError("The group row vanished after it was ensured")
        if title and group.title != title:
            await self._update(group.id, title=title)
            group = group.model_copy(update={"title": title})
        return group, created

    async def surface_ids_for_channel(
        self, *, platform: str, external_channel_id: str
    ) -> list[UUID]:
        """The surfaces that know this chat, asked by the chat alone.

        Bounded by the pods one chat can plausibly hold a bot for; a WhatsApp
        group has exactly one, the pod that created it.
        """
        stmt = (
            select(AgentSurfaceGroupModel.surface_id)
            .where(
                AgentSurfaceGroupModel.platform == platform,
                AgentSurfaceGroupModel.external_channel_id == external_channel_id,
            )
            .limit(_MAX_SURFACES_PER_CHAT)
        )
        return list((await self.session.execute(stmt)).scalars().all())

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
        """A group the bot has asked the platform to create, not yet confirmed."""
        model = AgentSurfaceGroupModel(
            pod_id=pod_id,
            surface_id=surface_id,
            platform=platform,
            external_channel_id=None,
            title=title,
            owner_user_id=owner_user_id,
            answers_outsiders=answers_outsiders,
            request_id=request_id,
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def find_owned(
        self, *, surface_id: UUID, owner_user_id: UUID, title: str
    ) -> SurfaceGroup | None:
        """The group this member already has by this name here, if any."""
        stmt = (
            select(AgentSurfaceGroupModel)
            .where(
                AgentSurfaceGroupModel.surface_id == surface_id,
                AgentSurfaceGroupModel.owner_user_id == owner_user_id,
                AgentSurfaceGroupModel.title == title,
            )
            .order_by(AgentSurfaceGroupModel.created_at.desc())
            .limit(1)
        )
        model = (await self.session.execute(stmt)).scalars().first()
        return model.to_entity() if model else None

    async def confirm_created(
        self,
        *,
        request_id: str,
        external_channel_id: str,
        title: str | None,
        invite_link: str | None,
    ) -> SurfaceGroup | None:
        """Record the platform's confirmation; None when no request matches.

        A replayed confirmation finds the row already confirmed and writes the
        same values again, which is what makes replaying it harmless.
        """
        stmt = select(AgentSurfaceGroupModel).where(
            AgentSurfaceGroupModel.request_id == request_id
        )
        model = (await self.session.execute(stmt)).scalars().first()
        if model is None:
            return None
        model.external_channel_id = external_channel_id
        if title:
            model.title = title
        if invite_link:
            model.invite_link = invite_link
        await self.session.flush()
        return model.to_entity()

    async def set_invite_link(self, group_id: UUID, invite_link: str) -> None:
        await self._update(group_id, invite_link=invite_link)

    async def forget_request(self, request_id: str) -> None:
        """Drop a creation the platform refused: there is no group to keep."""
        await self.session.execute(
            delete(AgentSurfaceGroupModel).where(
                AgentSurfaceGroupModel.request_id == request_id,
                AgentSurfaceGroupModel.external_channel_id.is_(None),
            )
        )

    async def forget_channel(self, *, platform: str, external_channel_id: str) -> None:
        """A group the platform deleted, with its log: nobody can speak there."""
        await self.session.execute(
            delete(AgentSurfaceGroupModel).where(
                AgentSurfaceGroupModel.platform == platform,
                AgentSurfaceGroupModel.external_channel_id == external_channel_id,
            )
        )

    async def set_owner(self, group_id: UUID, owner_user_id: UUID | None) -> None:
        await self._update(group_id, owner_user_id=owner_user_id)

    async def set_answers_outsiders(
        self, group_id: UUID, *, answers_outsiders: bool
    ) -> None:
        await self._update(group_id, answers_outsiders=answers_outsiders)

    async def set_shared_externally(self, group_id: UUID, *, shared: bool) -> None:
        await self._update(group_id, shared_externally=shared)

    async def _update(self, group_id: UUID, **values: object) -> None:
        await self.session.execute(
            update(AgentSurfaceGroupModel)
            .where(AgentSurfaceGroupModel.id == group_id)
            .values(**values)
        )

    async def append_line(
        self,
        *,
        group_id: UUID,
        body: str,
        external_message_id: str | None = None,
        author_external_id: str | None = None,
        author_name: str | None = None,
        from_agent: bool = False,
        answered_name: str | None = None,
        answered_from_public: bool = False,
        answered_user_id: UUID | None = None,
    ) -> None:
        """Add one line; a message already logged is left as it was.

        Lines past the log's retention go as each new one arrives: one indexed
        range per group, which finds nothing almost every time.
        """
        text = body.strip()
        if not text:
            return
        await self.session.execute(
            delete(AgentSurfaceGroupMessageModel).where(
                AgentSurfaceGroupMessageModel.group_id == group_id,
                AgentSurfaceGroupMessageModel.created_at
                < datetime.now(timezone.utc) - GROUP_LOG_RETENTION,
            )
        )
        await self.session.execute(
            insert(AgentSurfaceGroupMessageModel)
            .values(
                group_id=group_id,
                body=text[:MAX_LINE_CHARS],
                external_message_id=external_message_id,
                author_external_id=author_external_id,
                author_name=author_name,
                from_agent=from_agent,
                answered_name=(answered_name or "")[:255] or None,
                answered_from_public=answered_from_public,
                answered_user_id=answered_user_id,
            )
            .on_conflict_do_nothing(
                index_elements=["group_id", "external_message_id"],
                index_where=AgentSurfaceGroupMessageModel.external_message_id.is_not(
                    None
                ),
            )
        )

    async def recent_lines(self, *, group: SurfaceGroup, limit: int) -> list[GroupLine]:
        """The last ``limit`` lines, oldest first, with who each author proved to be.

        The author's Lemma user comes from ``agent_surface_external_users`` --
        the same proof routing uses -- read at the moment the lines are read, so
        a stranger who has since signed up and verified reads as who they are
        now. One more bounded read rather than a join: a join would repeat a
        line for every tenant an id appears under.
        """
        stmt = (
            select(AgentSurfaceGroupMessageModel)
            .where(AgentSurfaceGroupMessageModel.group_id == group.id)
            .order_by(AgentSurfaceGroupMessageModel.created_at.desc())
            .limit(limit)
        )
        models = list((await self.session.execute(stmt)).scalars().all())
        models.reverse()
        authors = {
            model.author_external_id
            for model in models
            if model.author_external_id and not model.from_agent
        }
        proven = await self._proven_users(platform=group.platform, ids=authors)
        return [
            GroupLine(
                author_external_id=model.author_external_id,
                author_name=model.author_name,
                author_user_id=(
                    proven.get(model.author_external_id)
                    if model.author_external_id
                    else None
                ),
                from_agent=model.from_agent,
                text=model.body,
                created_at=model.created_at,
                answered_name=model.answered_name,
                answered_from_public=model.answered_from_public,
                answered_user_id=model.answered_user_id,
            )
            for model in models
        ]

    async def _proven_users(self, *, platform: str, ids: set[str]) -> dict[str, UUID]:
        if not ids:
            return {}
        stmt = (
            select(
                AgentSurfaceExternalUser.external_user_id,
                AgentSurfaceExternalUser.resolved_user_id,
            )
            .where(
                AgentSurfaceExternalUser.platform == platform,
                AgentSurfaceExternalUser.external_user_id.in_(sorted(ids)),
                AgentSurfaceExternalUser.resolved_user_id.is_not(None),
            )
            .limit(len(ids) * 4)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            external_id: user_id
            for external_id, user_id in rows
            if external_id and user_id is not None
        }
