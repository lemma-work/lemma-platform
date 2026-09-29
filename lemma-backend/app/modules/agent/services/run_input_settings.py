"""Pure helpers the runner applies to a run's input and model settings.

Split from ``agent_runner_service`` to keep it under the size ratchet; none of
them touch the runner's state.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.agent.domain.entities import Message, MessageKind, MessageRole
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.context_budget import ContextBudget


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


def with_reply_budget(
    model_settings: JsonObject | None, budget: ContextBudget
) -> JsonObject | None:
    """Tell the model how much room it has to answer in.

    Nothing set `max_tokens`, so every request used whatever the provider
    defaults to. That is survivable on a model that answers in prose and fatal
    on one that thinks first: thinking tokens are output tokens, a small
    default is spent on them before any content exists, and the provider stops
    the response at the cap. pydantic-ai treats a length-stopped response with
    no actionable part as `UnexpectedModelBehavior` and ends the run, so the
    work is lost rather than shortened.

    The number is the window minus the ceiling everything else is held under,
    which is the room the budget had already set aside for exactly this and
    never spent.

    An operator who set `max_tokens` on the runtime profile outranks this: they
    know something about their model that a fraction of a window does not. Any
    value they set counts, including a zero -- a provider will reject that and
    say so, which is a better answer than quietly substituting a number they
    did not choose and leaving them to wonder why their setting did nothing.
    Absent and explicitly null both mean unset, and are filled.
    """
    if model_settings and model_settings.get("max_tokens") is not None:
        return model_settings
    reply = budget.reply_token_budget
    if reply <= 0:
        return model_settings
    return {**(model_settings or {}), "max_tokens": reply}
