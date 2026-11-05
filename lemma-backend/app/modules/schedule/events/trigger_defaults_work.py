"""A pod's trigger defaults changed: its DATASTORE triggers resync their subscribers.

A trigger that leaves ``bulk_writes`` unset follows the pod's
``triggers.bulk_writes_default``, and its change-log subscriber records what
that made it want -- whether it batches, and whether bulk writes are logged
with their rows. A FAST item written by the pod update, so the resync commits
with the setting or not at all.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.pod.contracts.trigger_settings import PodTriggerDefaultsChanged
from app.modules.schedule.repositories.change_subscription import (
    resync_pod_triggers,
)

SCHEDULE_TRIGGER_DEFAULTS = "schedule.trigger_defaults"


async def run_trigger_defaults_changed(
    ctx: FastContext, payload: PodTriggerDefaultsChanged
) -> None:
    await resync_pod_triggers(ctx.uow, payload.pod_id)


def register_trigger_defaults_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SCHEDULE_TRIGGER_DEFAULTS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PodTriggerDefaultsChanged,
    )(run_trigger_defaults_changed)


register_trigger_defaults_work(default_registry)
facts.subscribe(
    PodTriggerDefaultsChanged,
    kind=SCHEDULE_TRIGGER_DEFAULTS,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"schedule-trigger-defaults:{fact.pod_id}",
)
