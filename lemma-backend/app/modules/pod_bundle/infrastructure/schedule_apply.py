"""Applying one schedule out of a bundle.

Split out of ``applier.py`` for the same reason ``surface_apply.py`` was: that
file sits at the 600-line ceiling the architecture ratchet sets, and a schedule
step shares nothing with its neighbours beyond the account-binding rule, which
lives in ``account_binding.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from lemma_pod_bundle.apply_fields import (
    SCHEDULE_APPLY_FIELDS,
    without_time_schedule_filter,
)

from app.modules.pod_bundle.domain.state import PlanStep
from app.modules.pod_bundle.infrastructure.account_binding import (
    validate_account_binding,
)


async def apply_schedule(
    step: PlanStep,
    *,
    uow,
    ctx,
    pod_id: UUID,
    user_id: UUID,
    load: Callable[[str, str], Mapping[str, object]],
    warnings: list[str],
) -> None:
    """Create a bundled schedule once, by name.

    A time schedule's filter is left out and said so: it was never asked, and
    the server refuses one on a new time schedule, so an older bundle carrying
    it would otherwise fail to import.
    """
    from app.modules.schedule.contracts import ScheduleCreateEntity, ScheduleType
    from app.modules.schedule.contracts.provisioning import (
        create_schedule,
        get_schedule_by_name,
    )

    payload, dropped_filter = without_time_schedule_filter(load("schedules", step.name))
    if dropped_filter:
        warnings.append(
            f"Schedule '{step.name}' is a time schedule; its filter was left "
            "out, because nothing would ever ask it."
        )
    existing = await get_schedule_by_name(uow, pod_id=pod_id, name=step.name, ctx=ctx)
    if existing is not None:
        # Create-once by name. The plan says SKIP for this case, so reaching
        # here means the pod grew the schedule between plan and apply.
        return
    # Build from the shared allow-list (also used by lemma-cli's direct
    # import) so the two importers can't silently drift on which exported
    # fields survive — this is what previously dropped account_id,
    # connector_trigger_id, filter_instruction and filter_output_schema.
    fields = {
        key: value for key, value in payload.items() if key in SCHEDULE_APPLY_FIELDS
    }
    fields["name"] = step.name
    fields["schedule_type"] = ScheduleType(str(payload.get("schedule_type")))
    fields["config"] = payload.get("config") or {}
    await validate_account_binding(
        uow,
        account_id=fields.get("account_id"),
        expected_connector=payload.get("connector_id"),
        expected_kind=payload.get("connector_kind") or payload.get("provider"),
        resource_label=f"Schedule '{step.name}'",
    )
    entity = ScheduleCreateEntity(user_id=user_id, pod_id=pod_id, **fields)
    await create_schedule(uow, entity, ctx=ctx)
