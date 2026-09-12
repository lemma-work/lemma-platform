"""Function repositories local to function module."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select, update

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
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.domain.events import (
    FunctionCreatedEvent,
)
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
)
from app.modules.function.domain.ports import (
    FunctionRepositoryPort,
)
from app.modules.function.infrastructure.run_repository import (
    FunctionRunRepository as FunctionRunRepository,
)
from app.modules.function.infrastructure.revision_repository import (
    FunctionRevisionRepositoryMixin,
)
from app.modules.function.infrastructure.models import (
    FunctionModel,
    FunctionRunModel,
)

#: Run states that have not settled. A function cannot be deleted while any of
#: its runs is in one of them.
_NON_TERMINAL_RUN_STATUSES = (
    FunctionRunStatus.PENDING,
    FunctionRunStatus.RUNNING,
)


class FunctionRepository(FunctionRevisionRepositoryMixin, FunctionRepositoryPort):
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
        # `FunctionEntity` is a plain BaseModel with its own `id` field, so it
        # cannot become an AggregateRoot without a field collision. Collect here
        # instead: this is the single write path behind every creation route, and
        # the row exists by this point, so an event that fires is an event that
        # committed.
        self.uow.collect_events(
            [
                FunctionCreatedEvent(
                    function_id=model.id,
                    pod_id=model.pod_id,
                    user_id=getattr(model, "user_id", None),
                )
            ]
        )
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

    async def get_many(self, ids) -> list[FunctionEntity]:
        """Several functions in one statement, in whatever order they come back.

        No authorization variant: the one caller is turning an agent's own
        grants into tools, and the grant is the decision -- the `ctx` overload
        above exists for a person asking about a function, which is a different
        question.
        """
        wanted = {id for id in ids if id is not None}
        if not wanted:
            return []
        result = await self.session.execute(
            select(FunctionModel).where(FunctionModel.id.in_(wanted))
        )
        return [model.to_entity() for model in result.scalars().all()]

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
        # Refuse while anything is still executing. `function_id` is SET NULL
        # now, so history survives the delete -- but a PENDING/RUNNING run
        # detached from its definition can never finish, and whatever suspended
        # on it (a workflow FUNCTION node) waits until its own deadline sweep
        # notices. Better to say so than to strand it.
        in_flight = await self.session.scalar(
            select(func.count())
            .select_from(FunctionRunModel)
            .where(
                FunctionRunModel.function_id == id,
                FunctionRunModel.status.in_(_NON_TERMINAL_RUN_STATUSES),
            )
        )
        if in_flight:
            raise FunctionConflictError(
                f"{in_flight} run(s) of this function are still executing. "
                "Wait for them to finish, then delete it."
            )
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
