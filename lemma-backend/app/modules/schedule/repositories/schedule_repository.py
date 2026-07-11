"""Schedule repository implementation."""

from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.schedule.domain.interfaces import (
    ScheduleRepository as ScheduleRepositoryInterface,
)
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.domain.value_objects import (
    DatastoreOperation,
    parse_datastore_operation,
)
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleRepository(ScheduleRepositoryInterface):
    """Schedule repository implementation."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork | None = None,
        session: AsyncSession | None = None,
        message_bus: MessageBus | None = None,
    ):
        # Backward compatibility: first positional arg may be AsyncSession.
        if isinstance(uow, AsyncSession):
            session = uow
            uow = None
        if uow is None:
            if session is None:
                raise ValueError("Either uow or session must be provided")
            uow = SqlAlchemyUnitOfWork(session)
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def create(self, entity: ScheduleEntity) -> ScheduleEntity:
        """Create a new schedule."""
        schedule = Schedule(
            id=entity.id,
            user_id=entity.user_id,
            pod_id=entity.pod_id,
            name=entity.name,
            schedule_type=entity.schedule_type,
            agent_id=entity.agent_id,
            workflow_id=entity.workflow_id,
            config=entity.config,
            filter_instruction=entity.filter_instruction,
            filter_output_schema=entity.filter_output_schema,
            account_id=entity.account_id,
            connector_trigger_id=entity.connector_trigger_id,
            visibility=entity.visibility,
            is_active=entity.is_active,
            is_internal=entity.is_internal,
        )
        self.session.add(schedule)
        await self.session.flush()
        created = await self.get(schedule.id)
        assert created is not None
        return created

    def _to_entity_with_allowed_actions(
        self,
        model: Schedule,
        allowed_actions: list[str] | tuple[str, ...] | None = None,
    ) -> ScheduleEntity:
        entity = model.to_entity()
        if allowed_actions is not None:
            entity.allowed_actions = list(allowed_actions)
        return entity

    async def get(
        self,
        schedule_id: UUID,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a schedule by ID."""
        if ctx is None:
            stmt = (
                select(Schedule)
                .options(selectinload(Schedule.agent), selectinload(Schedule.workflow))
                .where(Schedule.id == schedule_id)
            )
            result = await self.session.execute(stmt)
            schedule = result.scalar_one_or_none()
            return schedule.to_entity() if schedule else None
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.SCHEDULE,
            resource_id_col=Schedule.id,
            pod_id_col=Schedule.pod_id,
            owner_user_id_col=Schedule.user_id,
            visibility_col=Schedule.visibility,
        )
        stmt = (
            select(Schedule, actions)
            .options(selectinload(Schedule.agent), selectinload(Schedule.workflow))
            .where(Schedule.id == schedule_id)
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def get_by_name(
        self,
        *,
        pod_id: UUID,
        name: str,
        ctx: Context | None = None,
    ) -> Optional[ScheduleEntity]:
        """Get a non-internal schedule by pod-scoped name."""
        if ctx is None:
            stmt = (
                select(Schedule)
                .options(selectinload(Schedule.agent), selectinload(Schedule.workflow))
                .where(
                    Schedule.pod_id == pod_id,
                    Schedule.name == name,
                    Schedule.is_internal.is_(False),
                )
            )
            result = await self.session.execute(stmt)
            schedule = result.scalar_one_or_none()
            return schedule.to_entity() if schedule else None

        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.SCHEDULE,
            resource_id_col=Schedule.id,
            pod_id_col=Schedule.pod_id,
            owner_user_id_col=Schedule.user_id,
            visibility_col=Schedule.visibility,
        )
        stmt = (
            select(Schedule, actions)
            .options(selectinload(Schedule.agent), selectinload(Schedule.workflow))
            .where(
                Schedule.pod_id == pod_id,
                Schedule.name == name,
                Schedule.is_internal.is_(False),
                allowed_actions_contains(actions, Permissions.SCHEDULE_READ),
            )
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return self._to_entity_with_allowed_actions(row[0], row[1]) if row else None

    async def update(self, schedule_id: UUID, **kwargs) -> Optional[ScheduleEntity]:
        """Update a schedule."""
        valid_columns = {c.name for c in Schedule.__table__.columns}
        update_data = {k: v for k, v in kwargs.items() if k in valid_columns}

        if not update_data:
            return await self.get(schedule_id)

        if (
            "visibility" in update_data
            and update_data["visibility"] != ResourceVisibility.RESTRICTED.value
        ):
            existing = await self.session.get(Schedule, schedule_id)
            if (
                existing is not None
                and existing.pod_id is not None
                and existing.visibility == ResourceVisibility.RESTRICTED.value
            ):
                await delete_resource_sharing_grants(
                    self.session,
                    pod_id=existing.pod_id,
                    resource_type=ResourceType.SCHEDULE,
                    resource_id=schedule_id,
                )

        stmt = (
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(**update_data)
            .returning(Schedule)
        )
        result = await self.session.execute(stmt)
        schedule = result.scalar_one_or_none()
        await self.session.flush()
        return await self.get(schedule.id) if schedule else None

    async def delete(self, schedule_id: UUID) -> bool:
        """Delete a schedule."""
        stmt = delete(Schedule).where(Schedule.id == schedule_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def list(
        self,
        schedule_type: Optional[ScheduleType] = None,
        is_active: Optional[bool] = None,
        pod_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        agent_id: Optional[UUID] = None,
        workflow_id: Optional[UUID] = None,
        name: str | None = None,
        ctx: Context | None = None,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> tuple[List[ScheduleEntity], UUID | None]:
        """List schedules with filters."""
        actions = None
        if ctx is not None:
            actions = allowed_actions_expr(
                ctx=ctx,
                resource_type=ResourceType.SCHEDULE,
                resource_id_col=Schedule.id,
                pod_id_col=Schedule.pod_id,
                owner_user_id_col=Schedule.user_id,
                visibility_col=Schedule.visibility,
            )
            stmt = select(Schedule, actions).where(
                Schedule.is_internal.is_(False),
                allowed_actions_contains(actions, Permissions.SCHEDULE_READ),
            )
        else:
            stmt = select(Schedule).where(Schedule.is_internal.is_(False))

        if schedule_type:
            stmt = stmt.where(Schedule.schedule_type == schedule_type)
        if is_active is not None:
            stmt = stmt.where(Schedule.is_active == is_active)
        if pod_id:
            stmt = stmt.where(Schedule.pod_id == pod_id)
        if user_id:
            stmt = stmt.where(Schedule.user_id == user_id)
        if agent_id:
            stmt = stmt.where(Schedule.agent_id == agent_id)
        if workflow_id:
            stmt = stmt.where(Schedule.workflow_id == workflow_id)
        if name:
            stmt = stmt.where(Schedule.name == name)
        if cursor is not None:
            stmt = stmt.where(Schedule.id > cursor)

        stmt = (
            stmt.options(selectinload(Schedule.agent), selectinload(Schedule.workflow))
            .order_by(Schedule.id)
            .limit(limit + 1)
        )

        result = await self.session.execute(stmt)
        rows = list(result.all())

        next_cursor = None
        if len(rows) > limit:
            first = rows[limit - 1][0] if ctx is not None else rows[limit - 1][0]
            next_cursor = first.id
            rows = rows[:limit]

        if ctx is not None:
            return [
                self._to_entity_with_allowed_actions(schedule, allowed_actions)
                for schedule, allowed_actions in rows
            ], next_cursor
        return [row[0].to_entity() for row in rows], next_cursor

    async def list_all_by_pod(self, pod_id: UUID) -> List[ScheduleEntity]:
        """List every schedule in a pod without RBAC filtering.

        System-level query for pod-deletion cleanup; includes internal
        schedules (unlike ``list``, which excludes ``is_internal`` rows).
        """
        stmt = select(Schedule).where(Schedule.pod_id == pod_id)
        result = await self.session.execute(stmt)
        return [row.to_entity() for row in result.scalars().all()]

    async def find_schedules_by_config(
        self,
        schedule_type: ScheduleType,
        connector_trigger_id: str | None = None,
        **kwargs,
    ) -> List[ScheduleEntity]:
        """Find active schedules by app trigger and config JSON fields."""
        if connector_trigger_id is None:
            raise ValueError("schedule_type and connector_trigger_id are required")
        stmt = select(Schedule).where(
            Schedule.schedule_type == schedule_type,
            Schedule.connector_trigger_id == connector_trigger_id,
            Schedule.is_active.is_(True),
            Schedule.config.op("@>")(kwargs),
        )
        result = await self.session.execute(stmt)
        return [t.to_entity() for t in result.scalars().all()]

    async def find_by_config(
        self,
        schedule_type: ScheduleType,
        criteria: dict[str, Any] | None = None,
    ) -> List[ScheduleEntity]:
        """Find schedules matching criteria using JSONB contains operator."""
        criteria = criteria or {}
        stmt = select(Schedule).where(
            Schedule.schedule_type == schedule_type,
            Schedule.is_active.is_(True),
            Schedule.config.op("@>")(criteria),
        )
        result = await self.session.execute(stmt)
        return [t.to_entity() for t in result.scalars().all()]

    async def find_active_by_workflow(
        self,
        *,
        pod_id: UUID,
        workflow_id: UUID,
        user_id: UUID | None = None,
    ) -> List[ScheduleEntity]:
        """Find active schedules for a pod workflow, optionally scoped to an owner."""
        stmt = select(Schedule).where(
            Schedule.pod_id == pod_id,
            Schedule.workflow_id == workflow_id,
            Schedule.is_active.is_(True),
            Schedule.is_internal.is_(False),
        )
        if user_id is not None:
            stmt = stmt.where(Schedule.user_id == user_id)
        result = await self.session.execute(stmt)
        return [t.to_entity() for t in result.scalars().all()]

    async def find_by_webhook_config(
        self, source: str, criteria: dict[str, Any]
    ) -> List[ScheduleEntity]:
        """Find webhook schedules by source and config criteria."""
        full_criteria = {"source": source, **criteria}
        return await self.find_by_config(
            schedule_type=ScheduleType.WEBHOOK,
            criteria=full_criteria,
        )

    async def find_by_pod_table_event(
        self,
        pod_id: UUID,
        table_name: str,
        operation: DatastoreOperation | str,
    ) -> List[ScheduleEntity]:
        """Find pod table schedules matching the event properties.

        Operation matching happens in Python on the typed entity. Operations
        are required: a schedule that declares none matches nothing.
        """

        operation_value = parse_datastore_operation(operation)

        table_match = or_(
            ~func.jsonb_exists(Schedule.config, "table_name"),
            func.jsonb_extract_path_text(Schedule.config, "table_name") == table_name,
        )

        stmt = select(Schedule).where(
            Schedule.schedule_type == ScheduleType.DATASTORE,
            Schedule.is_active.is_(True),
            Schedule.pod_id == pod_id,
            table_match,
        )

        result = await self.session.execute(stmt)
        matched: list[ScheduleEntity] = []
        for model in result.scalars().all():
            entity = model.to_entity()
            try:
                config = entity.datastore_config
            except ValueError as exc:
                logger.warning(
                    "DATASTORE schedule %s has invalid config, skipping: %s",
                    entity.id,
                    exc,
                )
                continue
            if config is None or not config.operations:
                logger.warning(
                    "DATASTORE schedule %s declares no operations; operations are "
                    "required and the schedule will never fire. Update it with "
                    "explicit operations (INSERT, UPDATE, DELETE).",
                    entity.id,
                )
                continue
            if operation_value in config.operations:
                matched.append(entity)
        return matched

    async def record_fire(
        self,
        schedule_id: UUID,
        *,
        status: ScheduleFireStatus,
        fired_at: datetime | None = None,
        run_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record the outcome of a fire attempt for debuggability.

        Best-effort telemetry: failures here must never break event handling.
        """
        values: dict[str, Any] = {
            "last_fired_at": fired_at or datetime.now(timezone.utc),
            "last_fire_status": status,
            "last_error": error[:2000] if error else None,
        }
        if run_id is not None:
            values["last_run_id"] = run_id
        stmt = (
            update(Schedule).where(Schedule.id == schedule_id).values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def increment_consecutive_failures(self, schedule_id: UUID) -> int:
        """Atomically increment the durable circuit-breaker counter."""
        count = await self.session.scalar(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(consecutive_failures=Schedule.consecutive_failures + 1)
            .returning(Schedule.consecutive_failures)
        )
        return int(count or 0)

    async def reset_consecutive_failures(self, schedule_id: UUID) -> None:
        """Reset the durable circuit-breaker counter after success/reactivation."""
        await self.session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(consecutive_failures=0)
        )

    async def set_consecutive_failures(self, schedule_id: UUID, count: int) -> None:
        """Set the streak derived from distinct terminal schedule runs."""
        await self.session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(consecutive_failures=max(0, count))
        )
