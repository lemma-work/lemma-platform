"""Schedule run-history and manual-redrive application service."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.domain.errors import BadRequestError
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.schedule.domain.errors import (
    ScheduleNotFoundError,
    ScheduleRunNotRetryableError,
)
from app.modules.schedule.domain.interfaces import (
    DatastoreSchedulePolicy,
    ScheduleRepository,
)
from app.modules.schedule.domain.schedule import (
    DatastoreScheduleConfig,
    ScheduleRunStatus,
    ScheduleType,
)
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.repositories.trigger_records import (
    SkipRecord,
    TriggerRecords,
)
from app.modules.schedule.services.trigger_fire import add_start_work


class ScheduleRunService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        schedule_repository: ScheduleRepository,
        datastore_policy: DatastoreSchedulePolicy,
    ) -> None:
        self.uow = uow
        self.schedule_repository = schedule_repository
        self.datastore_policy = datastore_policy
        self.run_repository = ScheduleRunRepository(uow)

    async def list_schedule_runs(
        self,
        *,
        pod_id: UUID,
        schedule_id: UUID,
        ctx: Context,
        limit: int,
        status: ScheduleRunStatus | None = None,
        skipped: bool | None = None,
    ):
        schedule = await self.schedule_repository.get(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            raise ScheduleNotFoundError()
        await ctx.require(
            Permissions.SCHEDULE_READ, ResourceRef.schedule(pod_id, schedule_id)
        )
        run_user_id = await self._visible_run_user_id(
            schedule=schedule, pod_id=pod_id, ctx=ctx
        )
        return await self.run_repository.list_for_schedule(
            schedule_id,
            limit=limit,
            user_id=run_user_id,
            status=status,
            skipped=skipped,
        )

    async def list_trigger_skips(
        self,
        *,
        pod_id: UUID,
        schedule_id: UUID,
        ctx: Context,
        limit: int,
        page_token: str | None,
    ) -> tuple[list[SkipRecord], str | None]:
        """What the trigger let pass without firing, newest first, a page at a time.

        Skip rows carry counts and log positions, never record contents, so
        anyone who may read the schedule may read them.
        """
        schedule = await self.schedule_repository.get(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            raise ScheduleNotFoundError()
        await ctx.require(
            Permissions.SCHEDULE_READ, ResourceRef.schedule(pod_id, schedule_id)
        )
        skips = await TriggerRecords(self.uow.session).list_skips(
            schedule_id, limit=limit + 1, before=decode_skip_page_token(page_token)
        )
        if len(skips) <= limit:
            return skips, None
        return skips[:limit], encode_skip_page_token(skips[limit - 1])

    async def _visible_run_user_id(
        self, *, schedule, pod_id: UUID, ctx: Context
    ) -> UUID | None:
        if schedule.schedule_type == ScheduleType.DATASTORE:
            try:
                config = DatastoreScheduleConfig(**schedule.config)
                can_view_all = await self.datastore_policy.can_view_all_runs(
                    pod_id=pod_id, table_name=config.table_name, ctx=ctx
                )
            except ValueError:
                can_view_all = False
            if not can_view_all:
                return ctx.user_id
        return None

    async def retry_schedule_run(
        self, *, pod_id: UUID, schedule_id: UUID, run_id: UUID, ctx: Context
    ):
        schedule = await self.schedule_repository.get(schedule_id, ctx=ctx)
        if schedule is None or schedule.pod_id != pod_id:
            raise ScheduleNotFoundError()
        await ctx.require(
            Permissions.SCHEDULE_UPDATE, ResourceRef.schedule(pod_id, schedule_id)
        )
        required_user_id = await self._visible_run_user_id(
            schedule=schedule, pod_id=pod_id, ctx=ctx
        )
        result = await self.run_repository.create_redrive(
            schedule_id=schedule_id,
            run_id=run_id,
            redriven_by_user_id=ctx.user_id,
            fallback_user_id=schedule.user_id,
            required_user_id=required_user_id,
        )
        if result is None:
            raise ScheduleRunNotRetryableError()
        schedule_run, created = result
        if created:
            await add_start_work(WorkWriter.of(self.uow), schedule, schedule_run.id)
        return schedule_run


def encode_skip_page_token(skip: SkipRecord) -> str:
    raw = f"{skip.at.isoformat()}|{skip.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_skip_page_token(page_token: str | None) -> tuple[datetime, UUID] | None:
    if not page_token:
        return None
    try:
        at, skip_id = (
            base64.urlsafe_b64decode(page_token.encode("ascii")).decode().split("|")
        )
        return datetime.fromisoformat(at), UUID(skip_id)
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise BadRequestError("Invalid page_token") from exc
