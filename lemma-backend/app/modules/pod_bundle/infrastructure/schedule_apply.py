"""Applying one schedule out of a bundle.

Split out of ``applier.py`` for the same reason ``surface_apply.py`` was: that
file sits at the 600-line ceiling the architecture ratchet sets, and a schedule
step shares nothing with its neighbours.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from lemma_pod_bundle.apply_fields import (
    SCHEDULE_APPLY_FIELDS,
    is_past_one_time_schedule,
    without_time_schedule_filter,
)

from app.modules.pod_bundle.domain.state import PlanStep


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
    if is_past_one_time_schedule(payload):
        # It fired (or was missed) in the pod it came from and can never fire
        # again; creating it is refused, and failing here would leave the
        # import half applied.
        warnings.append(
            f"Schedule '{step.name}' runs once, at a time that has passed; it "
            "was not imported."
        )
        return
    if "account_id" in payload and not payload.get("pod_connector"):
        # A bundle exported before pod connectors. The account, if the
        # importer gave one, becomes the pod connector serving it to them;
        # without one the schedule cannot listen, and is left out and said so
        # rather than failing the whole import.
        account_id = _as_uuid(payload.get("account_id"))
        if account_id is None:
            warnings.append(
                f"Schedule '{step.name}' named a connected account, which "
                "bundles no longer carry; it was not imported. Create it with "
                "the pod connector it should listen through."
            )
            return
        from app.modules.connectors.contracts.pod_connectors import (
            pod_connector_for_account,
        )

        binding = await pod_connector_for_account(
            uow, ctx, pod_id=pod_id, account_id=account_id
        )
        payload = {**payload, "pod_connector": binding.slug}
        warnings.append(
            f"Schedule '{step.name}' named an account (deprecated); it listens "
            f"through the pod connector '{payload['pod_connector']}' instead."
        )
    existing = await get_schedule_by_name(uow, pod_id=pod_id, name=step.name, ctx=ctx)
    if existing is not None:
        # Create-once by name. The plan says SKIP for this case, so reaching
        # here means the pod grew the schedule between plan and apply.
        return
    # Build from the shared allow-list (also used by lemma-cli's direct
    # import) so the two importers can't silently drift on which exported
    # fields survive — this is what previously dropped connector_trigger_id,
    # filter_instruction and filter_output_schema.
    fields = {
        key: value for key, value in payload.items() if key in SCHEDULE_APPLY_FIELDS
    }
    fields["name"] = step.name
    fields["schedule_type"] = ScheduleType(str(payload.get("schedule_type")))
    fields["config"] = payload.get("config") or {}
    # The pod connector, by slug: whether the importer may listen through it
    # -- and which account that means for them -- is asked when the schedule
    # is created, exactly as for one made by hand.
    fields["pod_connector_slug"] = fields.pop("pod_connector", None)
    entity = ScheduleCreateEntity(user_id=user_id, pod_id=pod_id, **fields)
    await create_schedule(uow, entity, ctx=ctx)


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None
