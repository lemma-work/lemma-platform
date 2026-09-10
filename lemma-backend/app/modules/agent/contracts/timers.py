"""The agent timer the schedule poller claims on each tick.

Snoozed agent runs come due on a clock, and the thing that owns a clock in this
codebase is the schedule poller — one loop, claiming with `FOR UPDATE SKIP
LOCKED` so every worker replica shares the work. Publishing the claimer here
lets `schedule` drive it without reaching into `agent`'s services, and lets the
poller move out of `app/core`, which used to compose the three claimers because
it was the composition root.
"""

from __future__ import annotations

from app.modules.agent.services.due_snooze_claimer import claim_due_snooze_waits

__all__ = ["claim_due_snooze_waits"]
