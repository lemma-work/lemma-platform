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


SNOOZE_WAKE_SOURCE = "agent_snooze"


async def schedule_snooze_wake(
    *,
    conversation_id: UUID,
    user_id: UUID,
    wake_at: datetime,
) -> UUID:
    """Arm the timer that ends this snooze. Returns its per-wait token."""
    # Nothing to arm. The wait row the caller is about to persist carries
    # `scheduled_at` and `external_ref`, and the schedule poller claims from
    # those columns -- the row is the timer. This still mints the token the two
    # are joined by, and still returns it before the row is written, so the
    # ordering the caller relies on is unchanged.
    del conversation_id, user_id, wake_at
    return uuid4()


async def cancel_snooze_wake(timer_id: str) -> None:
    """Drop the timer for a snooze that will never be resumed.

    Nothing to drop, and nothing ever really depended on it. The wait row was
    always the guard -- a fired timer resolves through
    ``find_active_by_external_ref``, which ignores anything not ACTIVE -- and now
    the poller's due query filters on the same status, so a completed or
    cancelled wait is invisible to it.

    Kept as a call rather than deleted at the call sites: cancelling a snooze is
    a real domain event, and the day it needs to do something again, this is
    where it goes.
    """
    del timer_id
