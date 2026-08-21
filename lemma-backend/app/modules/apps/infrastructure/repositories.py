"""App repositories."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, desc, select

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity
from app.modules.apps.domain.errors import AppNotFoundError
from app.modules.apps.domain.events import AppCreatedEvent
from app.modules.apps.domain.ports import AppRepositoryPort
from app.modules.apps.infrastructure.models import AppModel, AppReleaseModel


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

    async def create_release(self, entity: AppReleaseEntity) -> AppReleaseEntity:
        model = AppReleaseModel(**entity.model_dump(exclude_unset=True))
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

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
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def list_releases(self, app_id: UUID) -> list[AppReleaseEntity]:
        stmt = (
            select(AppReleaseModel)
            .where(AppReleaseModel.app_id == app_id)
            .order_by(desc(AppReleaseModel.created_at))
        )
        result = await self.session.execute(stmt)
        return [model.to_entity() for model in result.scalars().all()]
