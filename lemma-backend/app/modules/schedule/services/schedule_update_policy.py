"""Cross-cutting validation for schedule mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.authorization.context import Context
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    TIME_SCHEDULE_FILTER_REFUSED,
    ScheduleEntity,
    ScheduleType,
    refuses_filter,
)
from app.modules.schedule.services.time_schedule_policy import (
    validated_time_schedule_config,
)
from app.modules.schedule.services.triage_policy import apply_triage_update


DatastoreUpdateCheck = Callable[[ScheduleEntity, Context | None], Awaitable[None]]


def is_explicit_reactivation(existing, updated, update_data: dict) -> bool:
    return bool(
        updated and update_data.get("is_active") is True and not existing.is_active
    )


async def validate_schedule_update_policies(
    existing: ScheduleEntity,
    update_data: dict,
    *,
    ctx: Context | None,
    require_datastore_update: DatastoreUpdateCheck,
    session: object | None = None,
) -> None:
    """Refuse an update the schedule's type does not allow.

    ``session`` is forwarded so the TIME branch can hand the pooled connection
    back across its cron walk; see `validated_time_schedule_config`. The triage
    step also rewrites ``update_data`` into the columns a triage is stored as.
    """
    if refuses_filter(
        existing.schedule_type,
        filter_instruction=update_data.get("filter_instruction"),
        filter_output_schema=update_data.get("filter_output_schema"),
    ):
        raise ScheduleValidationError(TIME_SCHEDULE_FILTER_REFUSED)
    await apply_triage_update(existing, update_data, ctx=ctx, session=session)
    if existing.schedule_type == ScheduleType.TIME and (
        "config" in update_data or update_data.get("is_active") is True
    ):
        await validated_time_schedule_config(
            update_data.get("config", existing.config), session=session
        )
    if existing.schedule_type == ScheduleType.DATASTORE:
        await require_datastore_update(existing.model_copy(update=update_data), ctx)
