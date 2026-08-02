"""Request/response models for the snooze toolset."""

from __future__ import annotations

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
            "one ~500s check, not eight 60s ones. Clamped to "
            f"[{MIN_SNOOZE_SECONDS}, {MAX_SNOOZE_SECONDS}]."
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
