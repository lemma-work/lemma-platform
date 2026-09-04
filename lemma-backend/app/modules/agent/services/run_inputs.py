"""Reading the two things a run needs off what it was handed.

Neither belongs to `AgentRunnerService`: one reads a message list and one reads
a runtime-profile snapshot, and both are pure. They live here rather than at the
top of the runner because the runner is a file whose length is ratcheted, and a
free function that never touches `self` is the cheapest thing to move out of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    JsonObject,
    MessageKind,
    MessageRole,
)


def run_input_text(messages: Sequence[Message]) -> str | None:
    """The prompt this run is answering: the last thing the user said.

    The harness is handed the whole selected history, but a trace's input is the
    turn, not the transcript -- the earlier turns are already their own traces in
    the same session. Tool returns and thinking blocks are skipped for the same
    reason: they are rows in the run, not the thing that started it.
    """
    for message in reversed(messages):
        if message.role is not MessageRole.USER:
            continue
        if message.kind is not MessageKind.TEXT:
            continue
        text = (message.text or "").strip()
        if text:
            return text
    return None


def profile_model_settings(
    runtime_profile_snapshot: dict[str, object | None] | None,
) -> JsonObject | None:
    """Pull the model_settings dict out of a resolved runtime profile snapshot."""
    if not isinstance(runtime_profile_snapshot, dict):
        return None
    config = runtime_profile_snapshot.get("config")
    if not isinstance(config, dict):
        return None
    model_settings = config.get("model_settings")
    return (
        model_settings if isinstance(model_settings, dict) and model_settings else None
    )
