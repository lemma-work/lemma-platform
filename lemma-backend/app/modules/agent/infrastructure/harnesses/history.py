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
        if not isinstance(content, str):
            # A list content is a person who attached something. Skipping it
            # here meant the loop fell through to "synthetic", so a multimodal
            # user turn was not pinned -- the one kind of message this module
            # exists to keep.
            return False
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
    """Bring a history under the ceiling, and say so when it could not.

    The backstop, not the strategy: it runs when compaction was skipped or
    failed. Losing old context is bad; having the provider reject the request is
    worse, and that is the only alternative.

    `known_size` lets a caller that has already measured `messages` skip the
    first re-tokenisation, which is the most expensive thing on the request path.
    """
    working = list(messages)
    size = known_size if known_size is not None else count_model_message_tokens(working)
    if ceiling <= 0 or size <= ceiling:
        return working

    trimmed = _fit_within(working, ceiling)
    dropped = len(working) - len(trimmed)
    return _with_drop_notice(trimmed, dropped=dropped) if dropped > 0 else trimmed


def _fit_within(working: list[object], ceiling: int) -> list[object]:
    """The largest suffix that fits, giving up the least valuable thing first.

    Three stages, in order of what they cost. Cut the unpinned middle; then give
    up pinned turns from the *second* oldest, so the request itself is the last
    thing standing; then shrink the recent tail, which only happens when a
    handful of messages exceed the window between them.

    The stages matter because stopping early is what the previous version did:
    it cut the middle, found the result still over, and returned it anyway --
    twelve of seventeen messages destroyed *and* a prompt 43% over the window,
    which is the provider rejection this exists to prevent, paid for twice.
    """
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

    # The pinned turns do not fit together. Give them up oldest-first, but never
    # the first one: an agent that has lost the request does not stop and ask.
    pins = [message for message in working[:tail_start] if is_pinned_message(message)]
    tail = list(working[tail_start:])
    while len(pins) > 1:
        pins = [pins[0], *pins[2:]]
        candidate = [*pins, *tail]
        if count_model_message_tokens(candidate) <= ceiling:
            return candidate

    # Even the request plus the newest few is too much. Shrink the tail. A single
    # message larger than the whole window cannot be fixed here -- `_trim_to_ceiling`
    # reports that rather than pretending otherwise.
    candidate = [*pins, *tail]
    while len(tail) > 1 and count_model_message_tokens(candidate) > ceiling:
        tail = tail[find_safe_cutoff(tail, 1) :]
        candidate = [*pins, *tail]
    return candidate


def _with_drop_notice(messages: list[object], *, dropped: int) -> list[object]:
    """Tell the model what this cost it.

    Every other cap on this branch announces itself; this was the largest one
    and the only silent one. Without it the model reads a history where two
    unrelated turns sit adjacent and a tool result answers a question it has no
    memory of asking -- and reasons confidently from that.

    Placed after the pinned prefix, which `_with_pinned` guarantees is
    self-contained, so it cannot land between a tool call and its result.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from app.modules.agent.services.runtime_history import SYNTHETIC_NOTICE_PREFIX

    leading_pins = 0
    for message in messages:
        if not is_pinned_message(message):
            break
        leading_pins += 1
    # Never past the last message: a model answers the newest turn, and a notice
    # sitting after it competes with the thing being answered. Reachable when
    # every surviving message is pinned.
    leading_pins = min(leading_pins, max(0, len(messages) - 1))
    notice = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"{SYNTHETIC_NOTICE_PREFIX} {dropped} earlier message(s) did "
                    "not fit the model's context window and are not shown. Work "
                    "from what remains; re-read a file or re-run a command if you "
                    "need something from the part that is missing."
                )
            )
        ]
    )
    return [*messages[:leading_pins], notice, *messages[leading_pins:]]


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

        from app.modules.agent.services.runtime_history import (
            SYNTHETIC_NOTICE_PREFIX,
        )

        # Marked synthetic, which is load-bearing rather than cosmetic. Unmarked,
        # this placeholder is indistinguishable from a user turn, so it gets
        # pinned -- and because the graph writes the processed history back, it
        # becomes the *first* pinned message on every later request.
        # `_bounded_pins` keeps the first and folds the rest, so a long
        # conversation would keep this placeholder and fold the user's actual
        # request: exactly the loss this module exists to prevent, reintroduced
        # by the fix for a different one.
        return [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=(
                            f"{SYNTHETIC_NOTICE_PREFIX} earlier turns of this "
                            "conversation are not shown"
                        )
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
            if after > ceiling:
                # Say so. The previous version logged enforcement it had not
                # achieved, so a request 43% over the window looked in the logs
                # exactly like one brought safely under it.
                logger.error(
                    "agent.history.token_ceiling_unenforceable.failed",
                    size_before=before,
                    size_after=after,
                    ceiling=ceiling,
                    dropped_count=len(messages) - len(trimmed),
                )
            else:
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
