"""Request/response models for the snooze toolset."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse

# A day. Two independent reasons, and the second is the one that bites:
# every wake replays full conversation history, and `reply_window_hours` in
# platform_capabilities means an agent that sleeps past a platform's reply window
# (WhatsApp's 24h customer-service rule) cannot deliver its own result on the
# surface it was asked on. A snooze that outlives its ability to answer is not
# useful. Requests above the cap are clamped, not rejected.
MAX_SNOOZE_SECONDS = 24 * 60 * 60

# Below this a snooze costs more than it saves: waking replays the whole
# conversation, so a tight poll loop is a token bonfire rather than a wait.
MIN_SNOOZE_SECONDS = 30


class SnoozeRequest(BaseModel):
    reason: str = Field(
        description=(
            "One short line, shown to the user while you sleep. Say what you are "
            "waiting on, specifically: 'waiting for the nightly build' beats "
            "'waiting'. They read this to understand what you're doing without "
            "having to ask."
        )
    )
    seconds: int = Field(
        description=(
            "How long to sleep. Choose it from what you are actually waiting for, "
            "not out of habit — a build that takes about eight minutes deserves "
            f"one ~500s check, not eight 60s ones. Under {MIN_SNOOZE_SECONDS}s is "
            f"rejected, not clamped; over {MAX_SNOOZE_SECONDS}s ({MAX_SNOOZE_SECONDS // 3600}h) "
            "is clamped down."
        )
    )
    note_to_self: str | None = Field(
        default=None,
        description="Handed back verbatim on wake. What you intended to do next.",
    )


class SnoozeResponse(BaseToolResponse):
    """What the agent sees when it wakes."""

    woke_because: Literal["TIMER", "CANCELLED"] | None = Field(
        default=None,
        description=(
            "TIMER: your time elapsed — this says nothing about whether what you "
            "were waiting for actually happened, so check. CANCELLED: the wait "
            "was ended early."
        ),
    )
    slept_seconds: int | None = Field(default=None, description="Actual time asleep.")
    note_to_self: str | None = Field(
        default=None, description="Whatever you passed in note_to_self."
    )
    # Set when a runtime cannot pause. Mirrors ask_user's contract so the model
    # gets guidance instead of a dead end.
    interaction_fallback: bool = False


# The two messages the model can wake to. TIMER leans on what it does *not* say:
# the failure this exists to prevent is an agent reading "I woke up" as "the
# thing I was waiting for happened".
_WAKE_MESSAGES = {
    "TIMER": (
        "Your time elapsed. That is all this means — check whatever you "
        "were waiting for before acting as though it happened."
    ),
    "CANCELLED": (
        "This wait was cancelled and your turn was stopped. You did not sleep "
        "the full duration and nothing you were waiting for is known to have "
        "happened."
    ),
}


def build_snooze_result(
    *, woke_because: str, slept_seconds: int, note_to_self: str | None
) -> dict:
    """The tool return replayed for a resolved snooze, whatever resolved it."""
    return SnoozeResponse(
        success=woke_because != "CANCELLED",
        woke_because=woke_because,
        slept_seconds=slept_seconds,
        note_to_self=note_to_self,
        message=_WAKE_MESSAGES[woke_because],
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
