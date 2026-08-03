"""Scheduler timer adapter for agent snooze waits.

The agent module must not import the schedule module directly (the architecture
ratchet enforces this), so the one-shot timer a snooze needs is wired here, in
the composition layer, exactly as ``workflow_scheduler`` does it for workflow
``wait_until`` nodes.

The payload shape is the contract with ``ScheduleStartService.handle_schedule_fired``:
``conversation_id`` selects the snooze branch, and ``wait_ref`` is the per-wait
token that resolves the fired timer to exactly one ACTIVE wait, so sequential
snoozes in one conversation cannot cross-resume each other.
"""

from datetime import datetime
from uuid import UUID, uuid4

from app.modules.schedule.scheduler.api_client import SchedulerAPIClient

SNOOZE_WAKE_SOURCE = "agent_snooze"


async def schedule_snooze_wake(
    *,
    conversation_id: UUID,
    user_id: UUID,
    wake_at: datetime,
) -> UUID:
    """Arm the timer that ends this snooze. Returns its per-wait token."""
    timer_id = uuid4()
    await SchedulerAPIClient().schedule_once_job(
        schedule_id=timer_id,
        user_id=user_id,
        run_date=wake_at,
        payload={
            "conversation_id": str(conversation_id),
            "wait_ref": str(timer_id),
            "scheduled_at": wake_at.isoformat(),
            "source": SNOOZE_WAKE_SOURCE,
        },
        replace_existing=True,
    )
    return timer_id


async def cancel_snooze_wake(timer_id: str) -> None:
    """Drop the timer for a snooze that will never be resumed.

    Best effort by design: ``remove_job`` already treats a missing job as
    success, and the wait row is the real guard — a fired timer resolves through
    ``find_active_by_external_ref``, which ignores anything not ACTIVE. Removing
    the job just stops the scheduler doing pointless work.
    """
    await SchedulerAPIClient().remove_job(UUID(timer_id))
