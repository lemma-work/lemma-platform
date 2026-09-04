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


def watch_for_exit(output, watchers: set, process_id: str, handle) -> None:
    """Record the outcome when an E2B process finishes.

    Nothing else can. E2B reports completion by resolving the handle, not by any
    state a later poll could read, so without this a finished command reads as
    still running forever: the caller sees no exit code, never treats it as
    complete, and polls until its deadline.

    The distinction that matters here is between the command failing and us
    losing the ability to watch it. Only the first carries an exit code.
    """
    import asyncio

    import anyio

    from app.core.request_context import create_inherited_task

    async def watch() -> None:
        try:
            outcome = await handle.wait()
            exit_code = getattr(outcome, "exit_code", None)
        except Exception as exc:
            # A command that exits non-zero raises in some SDK versions; the
            # exit code is still the thing the caller needs.
            exit_code = getattr(exc, "exit_code", None)
            if exit_code is None:
                # No exit code on the exception means this is not the command
                # reporting failure, it is us losing the stream. Recording it
                # as an exit reported a running build as failed and unpinned
                # its sandbox for the idle sweep.
                await output.record_unknown(process_id)
                return
        except asyncio.CancelledError:
            # `wait()` awaits an SDK-internal task, so a cancellation anywhere
            # in that chain (a disconnect, a sandbox release) arrives here --
            # and `except Exception` does not catch it. Skipping the record
            # leaves the process reading as "still running" for the rest of the
            # sandbox's life, and the agent polls a corpse until its own
            # deadline. Record what we know, then let the cancellation continue.
            with anyio.CancelScope(shield=True):
                await output.record_unknown(process_id)
            raise
        await output.record_exit(process_id, exit_code=exit_code)

    task = create_inherited_task(watch(), name=f"e2b-process-watch:{process_id}")
    # Held so the task is not garbage collected mid-flight, and discarded once
    # it has recorded the outcome.
    watchers.add(task)
    task.add_done_callback(watchers.discard)
