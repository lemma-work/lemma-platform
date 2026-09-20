"""Request/response models for the waiting toolset.

One tool, one way to wait. The three targets differ in what resolves them, not
in what the agent does, so they share a request, a response and a resume path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason
from app.modules.agent.tools.context import BaseToolResponse

# A day. Two independent reasons, and the second is the one that bites: every
# wake replays full conversation history, and `reply_window_hours` in
# platform_capabilities means an agent that sleeps past a platform's reply window
# (WhatsApp's 24h customer-service rule) cannot deliver its own result on the
# surface it was asked on. A wait that outlives its ability to answer is not
# useful. Requests above the cap are clamped, not rejected.
MAX_WAIT_SECONDS = 24 * 60 * 60

# Below this a wait costs more than it saves: waking replays the whole
# conversation, so a tight loop is a token bonfire rather than a wait.
MIN_WAIT_SECONDS = 30

# Default ceiling for a wait on something else's progress. Matches the sandbox
# runtime's own `process_max_lifetime_seconds`, so a process wait cannot outlive
# the process it is watching by design rather than by coincidence.
DEFAULT_TARGET_DEADLINE_SECONDS = 60 * 60

# How long between two checks of a target nobody publishes an event for. Starts
# responsive because a fast finish is the common case, then backs off, because a
# two-hour wait does not need a check every ten seconds.
POLL_START_SECONDS = 10
POLL_CAP_SECONDS = 60


def next_poll_delay(attempt: int) -> int:
    """Seconds until check number ``attempt`` (0-based), doubling to the cap."""
    return min(POLL_CAP_SECONDS, POLL_START_SECONDS * (2 ** max(0, attempt)))


class WaitForRequest(BaseModel):
    reason: str = Field(
        description=(
            "One line shown to the person while you wait. Be specific: "
            "'waiting for the test suite' beats 'waiting'."
        )
    )
    seconds: int | None = Field(
        default=None,
        description=(
            "Wait this long, then carry on. For a gap with nothing to watch — "
            f"something that needs time to settle. Under {MIN_WAIT_SECONDS}s is "
            f"rejected; over {MAX_WAIT_SECONDS // 3600}h is clamped."
        ),
    )
    process_id: str | None = Field(
        default=None,
        description=(
            "Wait for a sandbox process to finish, from `exec_command`'s "
            "`process_id`. You wake when it exits, with its exit code."
        ),
    )
    subagent_run_id: str | None = Field(
        default=None,
        description=(
            "Wait for a sub-agent run to finish, from `spawn_subagent`'s "
            "`run_id`. You wake with its result."
        ),
    )
    max_seconds: int | None = Field(
        default=None,
        description=(
            "Give up waiting after this long and wake anyway, for "
            "`process_id` / `subagent_run_id`. Defaults to "
            f"{DEFAULT_TARGET_DEADLINE_SECONDS // 60} minutes."
        ),
    )
    note_to_self: str | None = Field(
        default=None,
        description="Handed back verbatim on wake. What you intended to do next.",
    )


class WaitForResponse(BaseToolResponse):
    """What the agent sees when it wakes."""

    woke_because: (
        Literal[
            "TIMER",
            "TARGET_FINISHED",
            "TARGET_GONE",
            "DEADLINE",
            "ANSWERED",
            "CANCELLED",
        ]
        | None
    ) = Field(default=None, description="Why this wait ended.")
    waited_seconds: int | None = Field(default=None, description="Time actually spent.")
    note_to_self: str | None = Field(
        default=None, description="Whatever you passed in note_to_self."
    )
    exit_code: int | None = Field(
        default=None,
        description="Exit code of the process you waited on, when there was one.",
    )


# What the model is told on each wake. The discipline these inherit from the
# tool they replace is worth keeping: a wake says only what is actually known,
# and the wording works hardest at the places where "I woke up" would otherwise
# be read as "the thing I was waiting for happened".
_WAKE_MESSAGES: dict[tuple[AgentWaitType, AgentWaitWakeReason], str] = {
    (AgentWaitType.TIME, AgentWaitWakeReason.TIMER): (
        "Your time elapsed. That is all this means — check whatever you were "
        "waiting for before acting as though it happened."
    ),
    (AgentWaitType.PROCESS, AgentWaitWakeReason.TARGET_FINISHED): (
        "The process ended. That is not the same as it succeeding — read its "
        "exit code and its output before building on it."
    ),
    (AgentWaitType.PROCESS, AgentWaitWakeReason.DEADLINE): (
        "You stopped waiting because your own limit ran out. The process was "
        "still running when you last looked, and nothing is known to have "
        "finished. Check it before deciding anything."
    ),
    (AgentWaitType.PROCESS, AgentWaitWakeReason.TARGET_GONE): (
        "The process can no longer be read — its sandbox was reclaimed, or the "
        "runtime stopped tracking it. Its outcome is unknown, not bad. Anything "
        "it wrote to the pod survives; anything in the sandbox may not."
    ),
    (AgentWaitType.PROCESS, AgentWaitWakeReason.ANSWERED): (
        "Everyone you reached with message_user has now replied, which is why "
        "you woke early. Read them with check_messages. Your process is still "
        "running — nothing about it has changed."
    ),
    (AgentWaitType.SUBAGENT, AgentWaitWakeReason.TARGET_FINISHED): (
        "The sub-agent run finished. Read its result with "
        "query_subagents(mode='messages') before building on it."
    ),
    (AgentWaitType.SUBAGENT, AgentWaitWakeReason.DEADLINE): (
        "You stopped waiting because your own limit ran out. The sub-agent may "
        "still be running — check with query_subagents before deciding it "
        "failed."
    ),
    (AgentWaitType.SUBAGENT, AgentWaitWakeReason.TARGET_GONE): (
        "That sub-agent run can no longer be found, so its outcome is unknown."
    ),
    (AgentWaitType.SUBAGENT, AgentWaitWakeReason.ANSWERED): (
        "Everyone you reached with message_user has now replied, which is why "
        "you woke early. Read them with check_messages. The sub-agent is still "
        "running — nothing about it has changed."
    ),
    (AgentWaitType.TIME, AgentWaitWakeReason.ANSWERED): (
        "Everyone you reached with message_user has now replied, which is why "
        "you woke before your time was up. Read them with check_messages. "
        "Nothing else you were waiting on is known to have happened."
    ),
}

_CANCELLED_MESSAGE = (
    "This wait was cancelled and your turn was stopped. You did not wait the "
    "full duration and nothing you were waiting for is known to have happened."
)

_UNKNOWN_MESSAGE = (
    "This wait ended, and nothing more than that is known. Check whatever you "
    "were waiting for rather than assuming either way."
)


def wake_message(wait_type: AgentWaitType, reason: AgentWaitWakeReason) -> str:
    """What to tell the model, for any pairing the system can produce.

    A lookup with a truthful fallback rather than a `KeyError`: `ANSWERED` and
    `CANCELLED` can land on any wait type, and a pairing nobody anticipated must
    still wake the agent with something honest rather than leaving a suspended
    conversation with no way back.
    """
    if reason is AgentWaitWakeReason.CANCELLED:
        return _CANCELLED_MESSAGE
    return _WAKE_MESSAGES.get((wait_type, reason), _UNKNOWN_MESSAGE)


def build_wait_result(
    *,
    wait_type: AgentWaitType,
    reason: AgentWaitWakeReason,
    waited_seconds: int,
    note_to_self: str | None,
    exit_code: int | None = None,
) -> dict:
    """The tool return replayed for a resolved wait, whatever resolved it."""
    return WaitForResponse(
        success=reason is not AgentWaitWakeReason.CANCELLED,
        woke_because=reason.value,
        waited_seconds=waited_seconds,
        note_to_self=note_to_self,
        exit_code=exit_code,
        message=wake_message(wait_type, reason),
    ).model_dump(mode="json")


def elapsed_seconds(started_at: object) -> int:
    """Seconds since a spec's ISO ``started_at``; 0 when it is unusable."""
    if not isinstance(started_at, str):
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
