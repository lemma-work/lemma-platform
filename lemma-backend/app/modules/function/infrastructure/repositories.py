"""Function repositories local to function module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
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
    FunctionRevisionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.domain.events import (
    FunctionCreatedEvent,
    FunctionRunFailedEvent,
)
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
    FunctionRevisionModel,
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

    # -- revision history -------------------------------------------------

    async def record_revision(
        self, entity: FunctionRevisionEntity
    ) -> FunctionRevisionEntity:
        """Insert one revision, or revive the existing row for its hash.

        Idempotent on ``(function_id, revision_hash)`` because the artifact is
        content-addressed: re-saving unchanged code rebuilds to the same hash and
        must not mint a second revision.

        ``DO UPDATE``, not ``DO NOTHING``, for two reasons. Re-saving code whose
        artifact retention had already deleted rewrites both the artifact and the
        source before this runs, so ``pruned_at`` has stopped being true -- left
        set, the revision reads as "build removed", refuses to promote or pin
        with a 410, and once superseded is skipped by ``select_prunable``
        forever, leaking its artifact. And ``DO UPDATE ... RETURNING`` yields a
        row on both paths, which removes a read-back whose ``assert`` would have
        fired as an AssertionError if the conflicting transaction rolled back in
        between.

        Deliberately NOT in the SET: ``revision_number``, ``created_at`` and
        ``created_by`` (updating them would reorder history), and ``code_path``
        and the schemas -- the hash covers the artifact, ``code_path`` derives
        from it, and schema extraction is deterministic per artifact, so the
        stored values are already the right ones. No ``where=`` either: a false
        predicate returns no row and would reinstate the read-back.
        """
        values = entity.model_dump(
            exclude={"id", "created_at", "code", "revision_number", "pruned_at"},
        )
        # Serializes the max+1 below against a concurrent save of DIFFERENT code,
        # which would otherwise compute the same number and violate
        # `uq_function_revision_number`. The callers happen to hold this lock
        # already via the `UPDATE functions` that precedes them in the same unit
        # of work; taking it here stops the numbering depending on an ordering
        # two layers up that a refactor could quietly remove.
        await self.session.execute(
            select(FunctionModel.id)
            .where(FunctionModel.id == entity.function_id)
            .with_for_update()
        )
        statement = (
            insert(FunctionRevisionModel)
            .values(
                id=uuid7(),
                created_at=datetime.now(timezone.utc),
                revision_number=select(
                    func.coalesce(func.max(FunctionRevisionModel.revision_number), 0)
                    + 1
                )
                .where(FunctionRevisionModel.function_id == entity.function_id)
                .scalar_subquery(),
                **values,
            )
            .on_conflict_do_update(
                constraint="uq_function_revision_hash",
                set_={"pruned_at": None},
            )
            .returning(FunctionRevisionModel)
        )
        return (await self.session.execute(statement)).scalar_one().to_entity()

    async def get_revision_by_hash(
        self, function_id: UUID, revision_hash: str
    ) -> FunctionRevisionEntity | None:
        statement = select(FunctionRevisionModel).where(
            FunctionRevisionModel.function_id == function_id,
            FunctionRevisionModel.revision_hash == revision_hash,
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_revision_by_number(
        self, function_id: UUID, revision_number: int
    ) -> FunctionRevisionEntity | None:
        statement = select(FunctionRevisionModel).where(
            FunctionRevisionModel.function_id == function_id,
            FunctionRevisionModel.revision_number == revision_number,
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

    async def list_revisions(self, function_id: UUID) -> list[FunctionRevisionEntity]:
        statement = (
            select(FunctionRevisionModel)
            .where(FunctionRevisionModel.function_id == function_id)
            .order_by(
                FunctionRevisionModel.revision_number.desc(),
            )
        )
        result = await self.session.execute(statement)
        return [model.to_entity() for model in result.scalars().all()]

    async def revision_hashes_with_runs_in_flight(
        self, function_id: UUID
    ) -> set[str]:
        """Revision hashes a PENDING or RUNNING run is pinned to.

        A run resolves its artifact from its OWN hash at execution time, so
        deleting the artifact under a dispatched run makes it fail with a
        digest error instead of running. Retention skips these.
        """
        statement = select(FunctionRunModel.revision_hash).where(
            FunctionRunModel.function_id == function_id,
            FunctionRunModel.revision_hash.is_not(None),
            FunctionRunModel.status.in_(
                [FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING]
            ),
        )
        result = await self.session.execute(statement)
        return {row for row in result.scalars().all() if row}

    async def mark_revisions_pruned(self, revision_ids: list[UUID]) -> None:
        if not revision_ids:
            return
        await self.session.execute(
            update(FunctionRevisionModel)
            .where(
                FunctionRevisionModel.id.in_(revision_ids),
                FunctionRevisionModel.pruned_at.is_(None),
            )
            .values(pruned_at=datetime.now(timezone.utc))
        )

    async def activate_revision(
        self, function_id: UUID, revision: FunctionRevisionEntity
    ) -> FunctionEntity | None:
        """Make ``revision`` the function's live one, contract included.

        The schemas move with the hash: they live on the function row, and every
        agent and workflow bound to this function reads them, so leaving the
        newest schemas next to older code would advertise a contract the code
        does not implement.
        """
        statement = (
            update(FunctionModel)
            .where(FunctionModel.id == function_id)
            .values(
                revision_hash=revision.revision_hash,
                code_path=revision.code_path,
                input_schema=revision.input_schema,
                output_schema=revision.output_schema,
                config_schema=revision.config_schema,
                status=FunctionStatus.READY,
            )
            .returning(FunctionModel)
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

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


def _expired_run_error(run, *, now: datetime) -> str:
    """Why a swept run failed, in the terms its reader can act on.

    The budget is stated because it is not visible anywhere else: it comes from
    a deployment-wide setting chosen by function type, so a reader looking at a
    failed run has no way to know whether it was given two minutes or ten.
    """

    started = getattr(run, "started_at", None)
    deadline = getattr(run, "deadline_at", None)
    if started is None or deadline is None:
        return "Function execution deadline exceeded; the runtime never reported a result"
    budget = round((deadline - started).total_seconds())
    ran_for = round((now - started).total_seconds())
    return (
        f"Function execution deadline exceeded: no result after {ran_for}s "
        f"against a {budget}s budget. The run was ended by the platform sweep, "
        "so either the function is still working or the runtime never reported "
        "back."
    )


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
        job_callback_grace_seconds: int = 0,
    ) -> int:
        """Terminalize runs whose one allowed execution window has elapsed.

        Note the sweep is deliberately late for job-backed runs: the deadline
        plus ``job_callback_grace_seconds``, and then only at the next tick of
        the once-a-minute cron. A run can therefore be marked failed a minute or
        two after its deadline, which is the grace window doing its job and not
        a stuck sweep.
        """

        statement = (
            select(FunctionRunModel)
            # The sweep needs the deadline arithmetic, the failure event's
            # fields, and nothing else. Without this it also dragged
            # ``input_data`` and ``output_data`` -- two JSONB columns, TOASTed
            # and detoasted per row -- through a query that never reads them.
            .options(
                load_only(
                    FunctionRunModel.id,
                    FunctionRunModel.function_id,
                    FunctionRunModel.status,
                    FunctionRunModel.error,
                    FunctionRunModel.logs,
                    FunctionRunModel.started_at,
                    FunctionRunModel.deadline_at,
                    FunctionRunModel.completed_at,
                )
            )
            .where(
                or_(
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.PENDING,
                        FunctionRunModel.deadline_at <= now,
                    ),
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.RUNNING,
                        FunctionRunModel.job_id.is_(None),
                        FunctionRunModel.deadline_at <= now,
                    ),
                    and_(
                        FunctionRunModel.status == FunctionRunStatus.RUNNING,
                        FunctionRunModel.job_id.is_not(None),
                        FunctionRunModel.deadline_at
                        <= now - timedelta(seconds=job_callback_grace_seconds),
                    ),
                ),
                FunctionRunModel.deadline_at.is_not(None),
            )
            .order_by(FunctionRunModel.deadline_at, FunctionRunModel.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        runs = list((await self.session.scalars(statement)).all())
        for run in runs:
            run.status = FunctionRunStatus.FAILED
            # Says which of the two timeouts this was, and what the budget
            # actually was. The dispatcher reports its own inline timeout as
            # "Function execution timed out (deadline exceeded)"; this one is
            # the sweeper finding a run whose result never came back at all.
            # The two read identically in a run list and have opposite
            # remedies — make the function faster, versus find out why the
            # runtime never reported — so 41 failures in one afternoon said
            # "timed out" and left the reader to guess which kind.
            run.error = _expired_run_error(run, now=now)
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
        return len(runs)

    async def delete_terminal_before(
        self,
        *,
        cutoff: datetime,
        batch_size: int,
    ) -> int:
        """Remove one batch of finished runs older than ``cutoff``.

        Only terminal runs are eligible: an unfinished run is either live work
        or something the deadline sweep still has to fail, and deleting it
        would strand whatever is waiting on the result.

        ``SKIP LOCKED`` keeps this off rows another statement is already
        holding, so the sweep never blocks the execution path -- it just leaves
        those rows for the next batch.
        """

        claimed = (
            select(FunctionRunModel.id)
            .where(
                FunctionRunModel.status.in_(
                    (
                        FunctionRunStatus.COMPLETED,
                        FunctionRunStatus.FAILED,
                        FunctionRunStatus.CANCELLED,
                    )
                ),
                FunctionRunModel.created_at < cutoff,
            )
            .order_by(FunctionRunModel.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("function_run_retention_batch")
        )
        result = await self.session.execute(
            delete(FunctionRunModel).where(
                FunctionRunModel.id.in_(select(claimed.c.id))
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

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
