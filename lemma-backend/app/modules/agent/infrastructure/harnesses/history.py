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


def _starts_with_tool_returns(message: object) -> bool:
    return any(
        type(part).__name__ == "ToolReturnPart"
        for part in getattr(message, "parts", ()) or ()
    )


def is_pinned_message(message: object) -> bool:
    """Whether this message may never be dropped to make room.

    Two kinds qualify: something the person actually said, and the summary that
    stands in for everything dropped around it. Both are irreplaceable. A later
    turn can re-read a file or re-run a command, but it cannot recover the
    request -- and an agent that has lost the request does not stop and ask, it
    substitutes a plausible one and reports that as the thing it was asked for.

    A tool's own content arrives as a `UserPromptPart` as well, in the same
    message as its `ToolReturnPart`. That is a tool result wearing a user's
    clothes, not something anybody said, so it does not qualify.
    """
    names = {type(part).__name__ for part in getattr(message, "parts", ()) or ()}
    if "UserPromptPart" not in names:
        return False
    if names & {"ToolReturnPart", "RetryPromptPart"}:
        return False
    return not _is_synthetic(message)


def _is_synthetic(message: object) -> bool:
    """Scaffolding wearing a user turn's clothes, which pins nothing.

    Two kinds reach here. A bare `<notes>` block, rebuilt every request. And the
    elision notices `runtime_history` writes, which are user-role so Anthropic
    does not hoist them out of the history -- but are ours, not the person's,
    and pinning them would keep every one of them forever.

    A note prepended to real user text is a different thing: that message is the
    user's, and it stays.
    """
    from app.modules.agent.services.runtime_history import SYNTHETIC_NOTICE_PREFIX

    for part in getattr(message, "parts", ()) or ():
        content = getattr(part, "content", None)
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith(SYNTHETIC_NOTICE_PREFIX):
                continue
            if not (stripped.startswith("<notes>") and stripped.endswith("</notes>")):
                return False
    return True


def _with_pinned(messages: Sequence[object], start: int) -> list[object]:
    """The tail from ``start``, with the pinned messages before it kept in front.

    Safe to reorder this way because a pinned message is self-contained -- a user
    turn or a summary -- so moving one ahead of the cut cannot orphan a tool
    result from its call.
    """
    return [
        message for message in messages[:start] if is_pinned_message(message)
    ] + list(messages[start:])


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


def enforce_token_ceiling(
    messages: Sequence[object], *, ceiling: int, known_size: int | None = None
) -> list[object]:
    """Drop the oldest messages until the history fits, keeping pairs intact.

    This is the backstop, not the strategy: it runs when summarization was
    skipped or failed. Losing old context is bad; having the provider reject the
    request is worse, and that is the only alternative.

    `known_size` lets a caller that has already measured `messages` skip the
    first re-tokenisation. Counting a long history is the most expensive thing
    on the request path, and doing it twice to make one decision is waste the
    worker pays for on every model request.
    """
    working = list(messages)
    size = known_size if known_size is not None else count_model_message_tokens(working)
    if ceiling <= 0 or size <= ceiling:
        return working

    # Advance the cut rather than walking one message at a time: each count is a
    # full re-tokenisation, and this runs on the request path. Pinned messages
    # are carried across every cut -- this guard used to drop the summary the
    # compactor had just paid an LLM call to produce, because that summary sits
    # at the front and the front is what got dropped.
    floor = max(1, len(working) - _MIN_TAIL_MESSAGES)
    cut = max(1, len(working) // 2)
    while cut < floor:
        candidate = _with_pinned(working, find_safe_cutoff(working, cut))
        if count_model_message_tokens(candidate) <= ceiling:
            return candidate
        cut += max(1, (floor - cut) // 2)

    tail_start = find_safe_cutoff(working, floor)
    candidate = _with_pinned(working, tail_start)
    if count_model_message_tokens(candidate) <= ceiling:
        return candidate

    # Even every user turn together is over the ceiling. Keep the first thing
    # they asked for -- the one message whose loss changes what the agent does --
    # and the most recent turns, and let the rest go.
    tail = list(working[tail_start:])
    first_pinned = next(
        (message for message in working if is_pinned_message(message)), None
    )
    if first_pinned is not None and not any(
        message is first_pinned for message in tail
    ):
        return [first_pinned, *tail]
    return tail or working[-1:]


def _trim_to_ceiling(
    messages: Sequence[object], ceiling: int
) -> tuple[int, list[object] | None, int]:
    """``(size_before, trimmed, size_after)``; ``trimmed`` is None when nothing
    was needed.

    Counting and trimming are one unit so the whole thing is a single hop onto a
    worker thread rather than several -- the after-count included, which the log
    line below used to take on the event loop itself.
    """
    before = count_model_message_tokens(messages)
    if before <= ceiling:
        return before, None, before
    trimmed = enforce_token_ceiling(messages, ceiling=ceiling, known_size=before)
    return before, trimmed, count_model_message_tokens(trimmed)


def build_history_processors(
    options: HarnessOptions,
    *,
    summarization_model: object,
) -> list[object]:
    """The history processors this run should apply, in order."""
    processors: list[object] = []
    ceiling = options.history_hard_token_ceiling

    # First: detach images the model has already had several turns to read.
    # pydantic-ai re-uploads every image on every model request for the life of
    # the run, so this runs before anything measures the history.
    from app.modules.agent.infrastructure.harnesses.history_compaction import (
        strip_stale_images,
    )

    async def _detach_stale_images(messages: Sequence[object]) -> list[object]:
        return strip_stale_images(list(messages))

    processors.append(_detach_stale_images)

    if (
        options.history_summarization_enabled
        and options.history_summarization_token_limit > 0
    ):
        # Imported here rather than at module scope: the compactor imports this
        # module back for the pinning predicate and the cutoff.
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            HistoryCompactor,
        )

        processors.append(
            HistoryCompactor(
                model=summarization_model,
                trigger_tokens=options.history_summarization_token_limit,
                keep_messages=options.history_summarization_keep_messages,
            )
        )

    async def _ensure_leading_user_message(
        messages: Sequence[object],
    ) -> list[object]:
        """Guarantee the history opens with something a provider will accept.

        Anthropic requires the first message to be a user turn. Trimming and
        compaction both cut at a safe point for tool pairing, which says nothing
        about role -- so the backstop that exists to prevent a provider
        rejection could produce one. Pinned user turns usually sit at the front
        already; this covers the case where none survived.
        """
        working = list(messages)
        if not working or type(working[0]).__name__ != "ModelResponse":
            return working
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        return [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=("[earlier turns of this conversation are not shown]")
                    )
                ]
            ),
            *working,
        ]

    if ceiling > 0:
        # Runs last, so it also catches the case the summarizer cannot: it
        # swallows its own failures and returns the ORIGINAL history with
        # `skip_reason="failed"`, which is safe for the data and fatal for the
        # request that follows.
        async def _ceiling_guard(messages: Sequence[object]) -> list[object]:
            # The whole check runs on a worker thread: the halving loop below
            # re-tokenizes on each pass, so this is the most CPU-hungry thing on
            # the request path and the last place it should hold the event loop.
            before, trimmed, after = await run_blocking(
                _trim_to_ceiling, messages, ceiling
            )
            if trimmed is None:
                return list(messages)
            # Field names avoid "token"/"message", which the logging contract
            # reserves for things that could carry secrets or user text.
            logger.warning(
                "agent.history.token_ceiling_enforced.degraded",
                size_before=before,
                size_after=after,
                dropped_count=len(messages) - len(trimmed),
            )
            return trimmed

        processors.append(_ceiling_guard)

    # Last, so it sees whatever the stages above produced.
    processors.append(_ensure_leading_user_message)
    return processors
