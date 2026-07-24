"""Function repositories local to function module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import load_only

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import (
    delete_grantee_grants,
    delete_resource_grants,
    delete_resource_sharing_grants,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.domain.events import FunctionRunFailedEvent
from app.modules.function.domain.errors import (
    FunctionNotFoundError,
    FunctionRunNotFoundError,
)
from app.modules.function.domain.ports import (
    FunctionRepositoryPort,
    FunctionRunRepositoryPort,
)
from app.modules.function.infrastructure.models import (
    FunctionModel,
    FunctionRunModel,
)


class FunctionRepository(FunctionRepositoryPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def create(self, entity: FunctionEntity) -> FunctionEntity:
        payload = entity.model_dump(exclude_unset=True, exclude={"allowed_actions"})
        model = FunctionModel(**payload)
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    def _to_entity_with_allowed_actions(
        self,
        model: FunctionModel,
        allowed_actions: list[str] | tuple[str, ...] | None = None,
    ) -> FunctionEntity:
        entity = model.to_entity()
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity

    async def get(self, id: UUID, ctx: Context | None = None) -> FunctionEntity | None:
        if ctx is None:
            stmt = select(FunctionModel).where(FunctionModel.id == id)
            result = await self.session.execute(stmt)
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.FUNCTION,
            resource_id_col=FunctionModel.id,
            pod_id_col=FunctionModel.pod_id,
            owner_user_id_col=FunctionModel.user_id,
            visibility_col=FunctionModel.visibility,
        )
        stmt = select(FunctionModel, actions).where(FunctionModel.id == id)
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def get_by_name(
        self,
        pod_id: UUID,
        name: str,
        ctx: Context | None = None,
    ) -> FunctionEntity | None:
        if ctx is None:
            stmt = select(FunctionModel).where(
                FunctionModel.pod_id == pod_id, FunctionModel.name == name
            )
            result = await self.session.execute(stmt)
            model = result.scalar_one_or_none()
            return model.to_entity() if model else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.FUNCTION,
            resource_id_col=FunctionModel.id,
            pod_id_col=FunctionModel.pod_id,
            owner_user_id_col=FunctionModel.user_id,
            visibility_col=FunctionModel.visibility,
        )
        stmt = select(FunctionModel, actions).where(
            FunctionModel.pod_id == pod_id,
            FunctionModel.name == name,
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionEntity], str | None]:
        statement = select(FunctionModel).where(FunctionModel.pod_id == pod_id)
        if cursor:
            statement = statement.where(FunctionModel.id < UUID(cursor))
        statement = statement.order_by(FunctionModel.id.desc()).limit(limit + 1)
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
    ) -> tuple[list[FunctionEntity], str | None]:
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.FUNCTION,
            resource_id_col=FunctionModel.id,
            pod_id_col=FunctionModel.pod_id,
            owner_user_id_col=FunctionModel.user_id,
            visibility_col=FunctionModel.visibility,
        )
        statement = select(FunctionModel, actions).where(
            FunctionModel.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.FUNCTION_READ),
        )
        if cursor:
            statement = statement.where(FunctionModel.id < UUID(cursor))
        statement = statement.order_by(FunctionModel.id.desc()).limit(limit + 1)
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

    async def update(self, function: FunctionEntity) -> FunctionEntity:
        if function.id is None:
            raise FunctionNotFoundError("Cannot update a function without an id")
        model = await self.session.get(FunctionModel, function.id)
        if not model:
            raise FunctionNotFoundError(f"Function {function.id} not found")

        model.description = function.description
        model.icon_url = function.icon_url
        model.input_schema = function.input_schema
        model.output_schema = function.output_schema
        model.config_schema = function.config_schema
        model.code_path = function.code_path
        model.revision_hash = function.revision_hash
        model.config = function.config
        model.user_id = function.user_id
        model.pod_id = function.pod_id
        previous_visibility = model.visibility
        model.visibility = function.visibility
        if (
            previous_visibility == ResourceVisibility.RESTRICTED.value
            and function.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=function.pod_id,
                resource_type=ResourceType.FUNCTION,
                resource_id=function.id,
            )
        model.status = function.status
        model.type = function.type

        await self.session.flush()
        return model.to_entity()

    async def activate_revision_if_missing(
        self,
        function_id: UUID,
        *,
        expected_code_path: str,
        revision_hash: str,
        code_path: str,
    ) -> FunctionEntity | None:
        """Atomically activate one legacy source revision.

        A first-run backfill may race with another invocation or a user update.
        The compare-and-set protects the newer definition: only the row that
        still points at the exact legacy source and has no active revision can
        be changed.
        """

        statement = (
            update(FunctionModel)
            .where(
                FunctionModel.id == function_id,
                FunctionModel.revision_hash.is_(None),
                FunctionModel.code_path == expected_code_path,
            )
            .values(
                revision_hash=revision_hash,
                code_path=code_path,
                status=FunctionStatus.READY,
            )
            .returning(FunctionModel)
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        if model is not None:
            return model.to_entity()
        return await self.get(function_id)

    async def delete(self, id: UUID) -> bool:
        pod_id = (
            await self.session.execute(
                select(FunctionModel.pod_id).where(FunctionModel.id == id)
            )
        ).scalar_one_or_none()
        if pod_id is not None:
            await delete_resource_grants(
                self.session,
                pod_id=pod_id,
                resource_type=ResourceType.FUNCTION,
                resource_id=id,
            )
            await delete_grantee_grants(
                self.session,
                pod_id=pod_id,
                grantee_type="FUNCTION",
                grantee_id=id,
            )
        stmt = (
            delete(FunctionModel)
            .where(FunctionModel.id == id)
            .returning(FunctionModel.id)
        )
        deleted_id = (await self.session.execute(stmt)).scalar_one_or_none()
        return deleted_id is not None


class FunctionRunRepository(FunctionRunRepositoryPort):
    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: FunctionRunEntity) -> None:
        events = entity.collect_events()
        if events:
            self.uow.collect_events(events)

    async def create_run(self, entity: FunctionRunEntity) -> FunctionRunEntity:
        payload = entity.model_dump(exclude_unset=True)
        model = FunctionRunModel(**payload)
        self.session.add(model)
        await self.session.flush()
        self._collect_events(entity)
        return model.to_entity()

    async def update_run_and_collect(
        self, run: FunctionRunEntity, **kwargs
    ) -> FunctionRunEntity:
        """Field-update a run (like ``update_run``) and collect the entity's
        domain events into the UoW so they publish on commit.

        Used for terminal transitions so ``FunctionRunCompletedEvent`` /
        ``FunctionRunFailedEvent`` added to ``run`` are emitted after the row is
        committed. Plain ``update_run`` deliberately stays event-free for the
        many non-terminal status updates.
        """
        if run.id is None:
            raise FunctionRunNotFoundError("Cannot update a run without an id")
        model = await self.session.get(FunctionRunModel, run.id)
        if not model:
            raise FunctionRunNotFoundError(f"Run {run.id} not found")

        for key, value in kwargs.items():
            if hasattr(model, key):
                setattr(model, key, value)

        await self.session.flush()
        self._collect_events(run)
        return model.to_entity()

    async def get_run(self, run_id: UUID) -> FunctionRunEntity | None:
        stmt = select(FunctionRunModel).where(FunctionRunModel.id == run_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return model.to_entity() if model else None

    async def fail_expired(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> int:
        """Terminalize runs whose one allowed execution window has elapsed."""

        return len(await self.fail_expired_runs(now=now, limit=limit))

    async def fail_expired_runs(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[FunctionRunEntity]:
        """Terminalize and return expired runs for post-commit telemetry."""

        statement = (
            select(FunctionRunModel)
            .where(
                FunctionRunModel.status.in_(
                    (FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING)
                ),
                FunctionRunModel.deadline_at.is_not(None),
                FunctionRunModel.deadline_at <= now,
            )
            .order_by(FunctionRunModel.deadline_at, FunctionRunModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        runs = list((await self.session.scalars(statement)).all())
        for run in runs:
            run.status = FunctionRunStatus.FAILED
            run.error = "Function execution deadline exceeded"
            run.completed_at = now
            self.uow.collect_events(
                [
                    FunctionRunFailedEvent(
                        run_id=run.id,
                        function_id=run.function_id,
                        error=run.error,
                        logs=run.logs,
                        completed_at=now,
                    )
                ]
            )
        if runs:
            await self.session.flush()
        return [run.to_entity() for run in runs]

    async def list_pending_async_runs(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[UUID]:
        """Return asynchronous runs still waiting for their one execution.

        ``job_id`` is the durable asynchronous-dispatch intent. Queue publication
        happens after this UoW closes; republishing a queued run is safe because
        Streaq atomically deduplicates the deterministic task identity.
        """

        statement = (
            select(FunctionRunModel.id)
            .where(
                FunctionRunModel.status == FunctionRunStatus.PENDING,
                FunctionRunModel.job_id.is_not(None),
                FunctionRunModel.deadline_at.is_not(None),
                FunctionRunModel.deadline_at > now,
            )
            .order_by(FunctionRunModel.created_at, FunctionRunModel.id)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def list_runs_by_function(
        self, function_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[FunctionRunEntity], str | None]:
        statement = (
            select(FunctionRunModel)
            .options(
                load_only(
                    FunctionRunModel.id,
                    FunctionRunModel.function_id,
                    FunctionRunModel.user_id,
                    FunctionRunModel.status,
                    FunctionRunModel.started_at,
                    FunctionRunModel.completed_at,
                    FunctionRunModel.created_at,
                )
            )
            .where(FunctionRunModel.function_id == function_id)
        )
        if cursor:
            statement = statement.where(FunctionRunModel.id < UUID(cursor))
        statement = statement.order_by(FunctionRunModel.id.desc()).limit(limit + 1)

        result = await self.session.execute(statement)
        models = list(result.scalars().all())

        next_cursor = None
        if len(models) > limit:
            next_cursor = str(models[limit - 1].id)
            models = models[:limit]

        return [
            FunctionRunEntity(
                id=m.id,
                function_id=m.function_id,
                user_id=m.user_id,
                status=m.status,
                started_at=m.started_at,
                completed_at=m.completed_at,
                created_at=m.created_at,
            )
            for m in models
        ], next_cursor
