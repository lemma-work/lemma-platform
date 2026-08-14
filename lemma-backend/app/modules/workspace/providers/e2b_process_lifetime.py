"""Translating this platform's process deadline into E2B's timeout argument.

Its own module because the two systems disagree by default and the disagreement
is expensive. Every sandbox operation here carries a `deadline_at`, and the
contract above it is that a build may outlive the call that started it —
`process_max_lifetime_seconds` is an hour. E2B's `commands.run` and `pty.create`
instead take a `timeout` at which they kill the command, and default it to 60
seconds.

Passing nothing therefore does not mean "no limit", it means "one minute". That
is how every install, build and test suite came to be killed mid-flight while
the agent was still polling it.
"""

from __future__ import annotations

from datetime import datetime, timezone

# E2B reads a non-positive timeout as "no timeout", so an already-expired
# deadline must floor to something small and positive rather than pass through:
# the caller asked for less time, not for an immortal process.
MINIMUM_PROCESS_SECONDS = 1.0


def seconds_until(deadline_at: datetime, *, now: datetime | None = None) -> float:
    """How long E2B should let a process live, from the deadline we were given."""

    moment = now or datetime.now(timezone.utc)
    remaining = (deadline_at - moment).total_seconds()
    return max(MINIMUM_PROCESS_SECONDS, remaining)
