"""Cross-cutting validation for schedule mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.authorization.context import Context
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.time_schedule_policy import (
    validate_time_schedule_config,
)


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
) -> None:
    if existing.schedule_type == ScheduleType.TIME and (
        "config" in update_data or update_data.get("is_active") is True
    ):
        validate_time_schedule_config(update_data.get("config", existing.config))
    if existing.schedule_type == ScheduleType.DATASTORE:
        await require_datastore_update(existing.model_copy(update=update_data), ctx)
