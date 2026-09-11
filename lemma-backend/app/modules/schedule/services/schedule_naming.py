"""What a schedule is called when the caller did not say.

Split out of `ScheduleService` because it reads nothing but the entity handed
to it: no repository, no unit of work, no authorization context. It is the
service's first act on a create, and keeping it here means the service file is
about the saga rather than about string handling.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.helpers.slug import normalize_resource_name
from app.modules.schedule.domain.schedule import ScheduleCreateEntity


def schedule_name_for(schedule_create: ScheduleCreateEntity) -> str | None:
    """The canonical name for a new schedule, generating one if it has none.

    An internal or pod-less schedule keeps whatever it was given (normalized),
    including nothing: those are not addressed by name. A pod schedule without
    one is named after its target, its type, and enough random hex to keep the
    pod-scoped uniqueness constraint from turning a second identical schedule
    into an error the caller cannot act on.
    """
    if schedule_create.is_internal or schedule_create.pod_id is None:
        return (
            normalize_resource_name(schedule_create.name)
            if schedule_create.name
            else schedule_create.name
        )
    if schedule_create.name:
        return normalize_resource_name(schedule_create.name)
    target_name = (
        schedule_create.workflow_name
        or schedule_create.agent_name
        or schedule_create.schedule_type.value.lower()
    )
    base = normalize_resource_name(
        f"{target_name}_{schedule_create.schedule_type.value.lower()}_schedule"
    )
    return f"{base}_{uuid4().hex[:8]}"
