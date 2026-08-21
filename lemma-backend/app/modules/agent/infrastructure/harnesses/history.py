"""History compaction for the in-process harness.

Production runs on Fireworks (OpenAI-compatible), where there is no
provider-native compaction to lean on — `CompactionPart` is Anthropic and
OpenAI-Responses only. So compaction is ours to get right, and it has two jobs
that pull in opposite directions:

1. Keep the prompt under the model's context window.
2. Keep the *prefix* byte-stable, because OpenAI-compatible prefix caching is
   what makes a long conversation affordable. Anything that rewrites the front
   of the history on every turn silently turns every request into a cache miss.

The compromise is: compact rarely, and when compacting, replace a prefix once
and keep the result stable afterwards. The summarizer already works this way —
it splits at a cutoff and keeps the tail — so what this module adds is an honest
token count, usage accounting, and a guarantee that a *failed* compaction can
never hand the provider an oversized prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import HarnessOptions
from app.modules.agent.services.history_tokens import count_model_message_tokens

logger = get_logger(__name__)

# Never drop below this many trailing messages, whatever the ceiling says. A
# request stripped to nothing is not a smaller request, it is a broken one.
_MIN_TAIL_MESSAGES = 4


async def _count_tokens_off_loop(messages: Sequence[object]) -> int:
    """Tokenize on a worker thread, never on the event loop.

    Counting a 100k-token history is ~30ms of pure CPU, and it runs on every
    model request. The worker is a single core running up to
    ``worker_concurrency`` agent runs, and its event loop already logs lag
    breaches — twenty runs each blocking 30ms serialize into a visible stall in
    every other run's token streaming and stop-checks. `run_blocking` is the
    house pattern for exactly this (see `app/core/concurrency/offload`).
    """
    return await run_blocking(count_model_message_tokens, messages)


def _is_response_with_tool_calls(message: object) -> bool:
    return any(
        type(part).__name__ == "ToolCallPart"
        for part in getattr(message, "parts", ()) or ()
    )


def _starts_with_tool_returns(message: object) -> bool:
    return any(
        type(part).__name__ == "ToolReturnPart"
        for part in getattr(message, "parts", ()) or ()
    )


def find_safe_cutoff(messages: Sequence[object], start: int) -> int:
    """Move a cutoff forward until it does not split a tool call from its result.

    Providers reject a history whose tool results have no matching call — so a
    truncation that lands mid-exchange trades a context-length error for a
    validation error. Scanning forward (never backward) also guarantees the
    trimmed history is strictly smaller, so the caller cannot loop.
    """
    index = max(0, min(start, len(messages)))
    while index < len(messages) and _starts_with_tool_returns(messages[index]):
        index += 1
    return index


def enforce_token_ceiling(messages: Sequence[object], *, ceiling: int) -> list[object]:
    """Drop the oldest messages until the history fits, keeping pairs intact.

    This is the backstop, not the strategy: it runs when summarization was
    skipped or failed. Losing old context is bad; having the provider reject the
    request is worse, and that is the only alternative.
    """
    working = list(messages)
    if ceiling <= 0 or count_model_message_tokens(working) <= ceiling:
        return working

    # Halve the head repeatedly rather than walk one message at a time: each
    # count is a full re-tokenisation, and this runs on the request path.
    while len(working) > _MIN_TAIL_MESSAGES:
        drop_to = find_safe_cutoff(working, max(1, len(working) // 2))
        if drop_to >= len(working) - _MIN_TAIL_MESSAGES:
            break
        working = working[drop_to:]
        if count_model_message_tokens(working) <= ceiling:
            return working

    # Still over: keep the smallest well-formed tail we are willing to send.
    tail_start = find_safe_cutoff(working, max(0, len(working) - _MIN_TAIL_MESSAGES))
    return working[tail_start:] or working[-1:]


def _trim_to_ceiling(
    messages: Sequence[object], ceiling: int
) -> tuple[int, list[object] | None]:
    """``(size_before, trimmed)``; ``trimmed`` is None when nothing was needed.

    Counting and trimming are one unit so the whole thing is a single hop onto a
    worker thread rather than several.
    """
    before = count_model_message_tokens(messages)
    if before <= ceiling:
        return before, None
    return before, enforce_token_ceiling(messages, ceiling=ceiling)


def build_history_processors(
    options: HarnessOptions,
    *,
    summarization_model: object,
) -> list[object]:
    """The history processors this run should apply, in order."""
    processors: list[object] = []
    ceiling = options.history_hard_token_ceiling

    if (
        options.history_summarization_enabled
        and options.history_summarization_token_limit > 0
    ):
        from pydantic_ai_summarization import create_summarization_processor

        summarizer = create_summarization_processor(
            model=summarization_model,
            trigger=("tokens", options.history_summarization_token_limit),
            keep=("messages", options.history_summarization_keep_messages),
            # Without this the library estimates len(text)/4, which under-counts
            # code, logs and JSON by 25-65% — see services/history_tokens.
            token_counter=_count_tokens_off_loop,
        )
        processors.append(summarizer)

    if ceiling > 0:
        # Runs last, so it also catches the case the summarizer cannot: it
        # swallows its own failures and returns the ORIGINAL history with
        # `skip_reason="failed"`, which is safe for the data and fatal for the
        # request that follows.
        async def _ceiling_guard(messages: Sequence[object]) -> list[object]:
            # The whole check runs on a worker thread: the halving loop below
            # re-tokenizes on each pass, so this is the most CPU-hungry thing on
            # the request path and the last place it should hold the event loop.
            before, trimmed = await run_blocking(_trim_to_ceiling, messages, ceiling)
            if trimmed is None:
                return list(messages)
            # Field names avoid "token"/"message", which the logging contract
            # reserves for things that could carry secrets or user text.
            logger.warning(
                "agent.history.token_ceiling_enforced.degraded",
                size_before=before,
                size_after=count_model_message_tokens(trimmed),
                dropped_count=len(messages) - len(trimmed),
            )
            return trimmed

        processors.append(_ceiling_guard)

    return processors
