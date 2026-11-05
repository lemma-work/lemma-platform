"""Pure helpers the runner applies to a run's input and model settings.

Split from ``agent_runner_service`` to keep it under the size ratchet; none of
them touch the runner's state.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.domain.generation import LIGHT_REASONING_EFFORTS, lighter_effort
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

    A cap somebody chose -- on the model's catalog entry, the profile, the
    agent or the workflow step, merged before this -- is kept when it fits and
    clamped when it does not: they know something about their model that a
    fraction of a window does not, but a cap past the room the window leaves
    is a request the provider refuses. Any value they set counts, including a
    zero -- a provider will reject that and say so, which is a better answer
    than quietly substituting a number they did not choose. Absent and
    explicitly null both mean unset, and are filled.
    """
    reply = budget.reply_token_budget
    chosen = (model_settings or {}).get("max_tokens")
    if chosen is not None:
        if reply > 0 and isinstance(chosen, int) and chosen > reply:
            return {**(model_settings or {}), "max_tokens": reply}
        return model_settings
    if reply <= 0:
        return model_settings
    return {**(model_settings or {}), "max_tokens": reply}


#: Never ask for less than this, however a halving lands: an answer budget too
#: small to hold a tool call is a guaranteed failure, not a shorter answer.
MIN_REPLY_TOKENS = 1_024


#: What a compaction summary may spend. A summary is a page or two of prose;
#: the cap keeps a reasoning model from thinking its way through the whole
#: window before it writes one.
COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS = 8_192

_EFFORT_KEY = "openai_reasoning_effort"
_THINKING_KEY = "anthropic_thinking"
#: Anthropic's smallest extended-thinking budget.
_MIN_THINKING_BUDGET = 1_024


def summary_only_settings() -> JsonObject:
    """Settings for a summary on a model nothing else is known about.

    Only the cap: a separately configured summarization model, or a run whose
    compaction settings were never resolved, gives no grounds for any other
    parameter. A run's own model gets the ``compaction_model_settings`` that
    ``generation_settings.run_generation_settings`` resolved for it.
    """
    return {"max_tokens": COMPACTION_SUMMARY_MAX_OUTPUT_TOKENS}


def with_light_reasoning(
    model_settings: JsonObject, lighter_reasoning: JsonObject | None
) -> JsonObject:
    """``model_settings`` with an OpenAI-compatible reasoning effort turned down.

    ``lighter_reasoning`` is what ``generation_settings`` translated a light
    effort into for this model. It names an effort only where the model's
    catalog entry declares REASONING, which is what makes sending one safe on
    a run that was not sending one. Without it an effort is only ever lowered
    (an operator's raw wire switch, say), never introduced. An effort that is
    already light is never raised.
    """
    settings: JsonObject = dict(model_settings)
    current = settings.get(_EFFORT_KEY)
    if isinstance(current, str) and current in LIGHT_REASONING_EFFORTS:
        return settings
    target = (lighter_reasoning or {}).get(_EFFORT_KEY)
    if target is None and isinstance(current, str):
        target = lighter_effort(current).value
    if target is not None:
        settings[_EFFORT_KEY] = target
    return settings


def shorter_answer_settings(
    model_settings: JsonObject | None,
    *,
    lighter_reasoning: JsonObject | None = None,
) -> JsonObject:
    """Settings for re-asking a request whose answer ran past the time budget.

    Sending the same request again takes the same time and expires again, so
    the retry asks for less: half the output budget and a light reasoning
    effort, on a model that declares REASONING (``lighter_reasoning``) or
    wherever the run already sends one. An extended-thinking budget is halved
    with the cap, and dropped when what is left is under Anthropic's minimum.
    """
    settings: JsonObject = dict(model_settings or {})
    current = settings.get("max_tokens")
    if isinstance(current, int) and not isinstance(current, bool) and current > 0:
        settings["max_tokens"] = min(current, max(MIN_REPLY_TOKENS, current // 2))
    settings = with_light_reasoning(settings, lighter_reasoning)
    _halve_thinking(settings)
    return settings


def _halve_thinking(settings: JsonObject) -> None:
    """Halve a thinking budget in place, keeping it inside the answer's cap.

    Thinking tokens are output tokens: a budget at or above ``max_tokens`` is a
    request the provider refuses, and halving the cap alone can make one.
    """
    thinking = settings.get(_THINKING_KEY)
    if not isinstance(thinking, dict):
        return
    budget = thinking.get("budget_tokens")
    if not isinstance(budget, int) or isinstance(budget, bool):
        return
    halved = budget // 2
    cap = settings.get("max_tokens")
    if isinstance(cap, int) and not isinstance(cap, bool):
        halved = min(halved, cap - 1)
    if halved < _MIN_THINKING_BUDGET:
        del settings[_THINKING_KEY]
        return
    settings[_THINKING_KEY] = {**thinking, "budget_tokens": halved}
