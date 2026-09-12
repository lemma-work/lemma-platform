"""App repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.dialects.postgresql import Insert, insert

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.sql_text import starts_with
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.domain.events import AppCreatedEvent
from app.modules.apps.domain.ports import AppRepositoryPort
from app.modules.apps.infrastructure.models import AppModel, AppReleaseModel


def _record_release_statement(entity: AppReleaseEntity) -> Insert:
    """Allocate a deployment number while the caller holds the parent lock."""
    values = entity.model_dump(
        exclude_unset=True,
        exclude={"id", "created_at", "release_number", "pruned_at"},
    )
    statement = insert(AppReleaseModel).values(
        id=uuid7(),
        created_at=datetime.now(timezone.utc),
        # Allocated inside the INSERT, not read first. The caller holds a lock on
        # the parent app row, so max+1 is an allocation rather than a guess.
        release_number=select(
            func.coalesce(func.max(AppReleaseModel.release_number), 0) + 1
        )
        .where(AppReleaseModel.app_id == entity.app_id)
        .scalar_subquery(),
        **values,
    )
    return statement.returning(AppReleaseModel)


def release_prefix_statement(app_id: UUID, prefix: str):
    """Releases whose version starts with *prefix*, one row per distinct version.

    Ambiguity is a property of **distinct versions, not rows**. Redeploying
    identical bytes writes several releases carrying the same digest, and that
    is not ambiguous, so ``DISTINCT ON (version)`` collapses each version to its
    best row before the limit applies. "Best" is the resolver's own tiebreak --
    live before pruned, then newest -- resolved inside the version here rather
    than across the whole match set, which is the same answer because a prefix
    that resolves at all has only one version to sort within.

    Pruned rows stay in. They lose the ordering to live ones, but a caller left
    holding only a pruned match has to be able to say "removed by retention"
    instead of "no such release".

    A module-level builder so the statement a test explains is the statement the
    repository runs; a copy in the test would certify itself.
    """
    return (
        select(AppReleaseModel)
        .where(
            AppReleaseModel.app_id == app_id,
            starts_with(AppReleaseModel.version, prefix),
        )
        .distinct(AppReleaseModel.version)
        # DISTINCT ON keeps the first row of each `version` group, so the column
        # it distinguishes on has to lead the ordering and the tiebreak follows.
        # `release_number` is NOT NULL in this table, so DESC needs no NULLS
        # clause to match the Python it replaces.
        .order_by(
            AppReleaseModel.version,
            AppReleaseModel.pruned_at.is_not(None),
            desc(AppReleaseModel.release_number),
        )
        .limit(2)
    )


class AppRepository(AppRepositoryPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def create(self, entity: AppEntity) -> AppEntity:
        model = AppModel(
            **entity.model_dump(exclude_unset=True, exclude={"allowed_actions"})
        )
        self.session.add(model)
        await self.session.flush()
        # `AppEntity` is a plain BaseModel carrying its own `id`, so it cannot be
        # promoted to an AggregateRoot without a field collision. Collected here,
        # which is the single write path behind all three creation routes.
        self.uow.collect_events(
            [
                AppCreatedEvent(
                    app_id=model.id, pod_id=model.pod_id, user_id=model.user_id
                )
            ]
        )
        return model.to_entity()

    def _to_entity_with_allowed_actions(
        self,
        model: AppModel,
        allowed_actions: list[str] | tuple[str, ...] | None = None,
    ) -> AppEntity:
        entity = model.to_entity()
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity

    async def get(self, id: UUID, ctx: Context | None = None) -> AppEntity | None:
        if ctx is None:
            stmt = select(AppModel).where(AppModel.id == id)
            result = await self.session.execute(stmt)
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.APP,
            resource_id_col=AppModel.id,
            pod_id_col=AppModel.pod_id,
            owner_user_id_col=AppModel.user_id,
            visibility_col=AppModel.visibility,
        )
        stmt = select(AppModel, actions).where(AppModel.id == id)
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def get_by_name(
        self,
        pod_id: UUID,
        name: str,
        ctx: Context | None = None,
    ) -> AppEntity | None:
        if ctx is None:
            stmt = select(AppModel).where(
                AppModel.pod_id == pod_id, AppModel.name == name
            )
            result = await self.session.execute(stmt)
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.APP,
            resource_id_col=AppModel.id,
            pod_id_col=AppModel.pod_id,
            owner_user_id_col=AppModel.user_id,
            visibility_col=AppModel.visibility,
        )
        stmt = select(AppModel, actions).where(
            AppModel.pod_id == pod_id, AppModel.name == name
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def get_by_public_slug(self, public_slug: str) -> AppEntity | None:
        stmt = select(AppModel).where(AppModel.public_slug == public_slug)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[AppEntity], str | None]:
        statement = select(AppModel).where(AppModel.pod_id == pod_id)
        if cursor:
            statement = statement.where(AppModel.id > UUID(cursor))
        statement = statement.order_by(AppModel.id).limit(limit + 1)
        result = await self.session.execute(statement)
        models = list(result.scalars().all())
        next_cursor = None
        if len(models) > limit:
            next_cursor = str(models[limit - 1].id)
            models = models[:limit]

        return [m.to_entity() for m in models], next_cursor

    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[AppEntity], str | None]:
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.APP,
            resource_id_col=AppModel.id,
            pod_id_col=AppModel.pod_id,
            owner_user_id_col=AppModel.user_id,
            visibility_col=AppModel.visibility,
        )
        statement = select(AppModel, actions).where(
            AppModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.APP_READ),
        )
        if cursor:
            statement = statement.where(AppModel.id > UUID(cursor))
        statement = statement.order_by(AppModel.id).limit(limit + 1)
        result = await self.session.execute(statement)
        rows = list(result.all())
        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit - 1][0].id)
            rows = rows[:limit]

        return [
            self._to_entity_with_allowed_actions(model, actions)
            for model, actions in rows
        ], next_cursor

    async def update(self, app: AppEntity) -> AppEntity:
        model = await self.session.get(AppModel, app.id)
        if not model:
            raise AppNotFoundError(f"App {app.id} not found")

        model.public_slug = app.public_slug
        model.description = app.description
        model.source_archive_path = app.source_archive_path
        model.current_release_id = app.current_release_id
        model.status = app.status
        model.user_id = app.user_id
        previous_visibility = model.visibility
        model.visibility = app.visibility
        if (
            previous_visibility == ResourceVisibility.RESTRICTED.value
            and app.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=app.pod_id,
                resource_type=ResourceType.APP,
                resource_id=app.id,
            )

        await self.session.flush()
        return model.to_entity()

    async def delete(self, id: UUID) -> bool:
        stmt = delete(AppModel).where(AppModel.id == id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def get_for_update(self, app_id: UUID) -> AppEntity | None:
        model = (
            await self.session.execute(
                select(AppModel)
                .where(AppModel.id == app_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return model.to_entity() if model else None

    async def mark_releases_purged(self, release_ids: tuple[UUID, ...]) -> None:
        await self.session.execute(
            update(AppReleaseModel)
            .where(
                AppReleaseModel.id.in_(release_ids),
                AppReleaseModel.pruned_at.is_not(None),
            )
            .values(purged_at=datetime.now(timezone.utc))
        )

    async def record_release(self, entity: AppReleaseEntity) -> AppReleaseEntity:
        """Record a new deployment under the parent lock, allocating its number atomically."""
        await self.session.execute(
            select(AppModel.id).where(AppModel.id == entity.app_id).with_for_update()
        )
        result = await self.session.execute(_record_release_statement(entity))
        return result.scalar_one().to_entity()

    async def get_release(self, id: UUID) -> AppReleaseEntity | None:
        stmt = select(AppReleaseModel).where(AppReleaseModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_release_by_version(
        self, app_id: UUID, version: str
    ) -> AppReleaseEntity | None:
        stmt = select(AppReleaseModel).where(
            AppReleaseModel.app_id == app_id,
            AppReleaseModel.version == version,
        )
        stmt = stmt.order_by(AppReleaseModel.release_number.desc()).limit(1)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_release_by_number(
        self, app_id: UUID, release_number: int
    ) -> AppReleaseEntity | None:
        stmt = select(AppReleaseModel).where(
            AppReleaseModel.app_id == app_id,
            AppReleaseModel.release_number == release_number,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def attach_release_source(
        self,
        release_id: UUID,
        *,
        source_archive_path: str,
        source_digest: str | None,
    ) -> None:
        await self.session.execute(
            update(AppReleaseModel)
            .where(AppReleaseModel.id == release_id)
            .values(
                source_archive_path=source_archive_path,
                source_digest=source_digest,
            )
        )

    async def set_current_release(self, app_id: UUID, release_id: UUID) -> None:
        await self.session.execute(
            update(AppModel)
            .where(AppModel.id == app_id)
            .values(current_release_id=release_id)
        )

    async def mark_releases_pruned(self, release_ids: list[UUID]) -> None:
        """Stamp ``pruned_at`` before the bytes go, so a sweep that dies midway
        never leaves a release the UI still offers to promote."""
        if not release_ids:
            return
        await self.session.execute(
            update(AppReleaseModel)
            .where(
                AppReleaseModel.id.in_(release_ids),
                AppReleaseModel.pruned_at.is_(None),
            )
            .values(pruned_at=datetime.now(timezone.utc))
        )

    async def find_releases_by_digest_prefix(
        self, app_id: UUID, prefix: str
    ) -> list[AppReleaseEntity]:
        """The best release under each distinct version a prefix names, at most two.

        Callers want one release and a yes/no on ambiguity, and two rows answer
        both: one row means the prefix resolves, two means it does not. This
        replaces reading an app's entire release history to run ``startswith``
        over it in Python -- a read that grew with the app's lifetime deploy
        count to answer a single ref, on a table retention stamps rather than
        empties.
        """
        if not prefix:
            return []
        result = await self.session.execute(release_prefix_statement(app_id, prefix))
        return [model.to_entity() for model in result.scalars().all()]

    async def page_releases(
        self, app_id: UUID, *, limit: int, cursor: UUID | None
    ) -> tuple[list[AppReleaseEntity], UUID | None]:
        """One page of an app's history, newest first, plus the next cursor.

        Keyset on the id, which is also the ordering: these rows key on uuid7,
        so id order *is* creation order -- the same invariant `select_prunable`
        relies on to call the prunable set a suffix of the ranking. That is what
        lets a bare-UUID page token, the house contract, order by time.

        `list_releases` stays for deletion, which needs every row by definition.
        """
        statement = select(AppReleaseModel).where(AppReleaseModel.app_id == app_id)
        if cursor is not None:
            statement = statement.where(AppReleaseModel.id < cursor)
        rows = list(
            (
                await self.session.execute(
                    statement.order_by(desc(AppReleaseModel.id)).limit(limit + 1)
                )
            ).scalars()
        )
        next_cursor = rows[limit - 1].id if len(rows) > limit else None
        return [row.to_entity() for row in rows[:limit]], next_cursor

    async def list_unpurged_releases(self, app_id: UUID) -> list[AppReleaseEntity]:
        """The releases retention can still act on, newest first.

        A purged release's bytes are already gone: it is not a candidate, it is
        not an unfinished deletion, and because pruning only ever takes from the
        old end it cannot change where any surviving release ranks. It is a
        tombstone kept so the history stays legible ("v3 -- build removed"), and
        retention reading it achieved nothing.

        Which matters because retention runs after every deploy and these rows
        are never deleted -- so the plan's input grew with the app's whole
        lifetime while the set it can choose from stays at `max_keep`.
        """
        statement = (
            select(AppReleaseModel)
            .where(
                AppReleaseModel.app_id == app_id,
                AppReleaseModel.purged_at.is_(None),
            )
            .order_by(desc(AppReleaseModel.created_at), desc(AppReleaseModel.id))
        )
        result = await self.session.execute(statement)
        return [model.to_entity() for model in result.scalars().all()]

    async def list_releases(self, app_id: UUID) -> list[AppReleaseEntity]:
        stmt = (
            select(AppReleaseModel)
            .where(AppReleaseModel.app_id == app_id)
            .order_by(desc(AppReleaseModel.created_at), desc(AppReleaseModel.id))
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]
