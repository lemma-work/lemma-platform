"""What a run ended by its deadline tells the person reading it."""

from __future__ import annotations

from datetime import datetime


def expired_run_error(
    *, started_at: datetime | None, deadline_at: datetime | None, now: datetime
) -> str:
    """Why a run failed at its deadline, in the terms its reader can act on.

    The budget is stated because it is not visible anywhere else: it comes from
    a deployment-wide setting chosen by function type, so a reader looking at a
    failed run has no way to know whether it was given two minutes or ten. It
    must not read like the dispatcher's own inline timeout either: that one
    means "make the function faster", this one "find out why the runtime never
    reported back".
    """
    if started_at is None or deadline_at is None:
        # A PENDING run's deadline is only its deadline to start.
        return (
            "Function never started before its start deadline. Nothing ran, so "
            "it is safe to retry."
        )
    budget = round((deadline_at - started_at).total_seconds())
    ran_for = round((now - started_at).total_seconds())
    return (
        f"Function execution deadline exceeded: no result after {ran_for}s "
        f"against a {budget}s budget. The run was ended by the platform, so "
        "either the function is still working or the runtime never reported "
        "back."
    )
