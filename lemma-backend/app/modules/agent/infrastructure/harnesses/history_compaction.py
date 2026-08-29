"""Compaction that cannot lose the request.

The library this replaces got three things wrong at once, and together they cost
a conversation its own task.

It summarized only the *last* 16,000 characters of the span it was discarding
(`trim_tokens_to_summarize`, not exposed by the factory we called), so the
summary described the most recent tool call and never the goal. It folded user
turns into that prose rather than keeping them, so once the summary was wrong
about the goal there was nothing left to correct it from. And it returned the
summary as the first message in the list, which is exactly where the ceiling
guard's halving pass cut -- so the run paid for a summarization call and threw
the result away in the same pass.

What replaces it holds one rule: **the user's messages are never dropped and
never paraphrased.** The request is the only thing a later step cannot
reconstruct. Everything else -- what the agent did, what it found, what failed
-- can be described in prose, so that is what gets summarized, over the whole
span rather than its tail.

The result is `[every user turn, verbatim] + [one summary of the work] + [the
recent messages in full]`, and both of the first two are pinned so nothing
downstream can drop them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext

from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harnesses.history import (
    find_safe_cutoff,
    is_pinned_message,
)
from app.modules.agent.services.history_tokens import count_model_message_tokens

logger = get_logger(__name__)

#: Opens the message this module writes. A later pass recognises its own work by
#: it and keeps it verbatim instead of summarizing a summary -- which is how a
#: compacted history degrades a little more on every model step.
SUMMARY_MARKER = "[earlier in this conversation, summarized]"

#: A notice used in place of a summary the model could not produce. Dropping the
#: middle silently is what the failure mode above looks like from the inside.
FAILED_MARKER = "[earlier in this conversation, omitted]"

#: Per-part cap when rendering the transcript to summarize. Bounds the cost of a
#: single enormous tool result without dropping the step it belongs to -- every
#: step stays represented, just less verbosely.
_MAX_PART_CHARS = 2_000

#: Whole-transcript cap. If it bites, the *middle* goes: the beginning holds
#: what the work is, the end holds where it got to, and the library's mistake
#: was keeping only the latter.
_MAX_TRANSCRIPT_CHARS = 120_000

_SUMMARY_INSTRUCTIONS = """\
You are compacting the middle of an agent's working transcript so that the agent
can carry on without it. The user's own messages are preserved separately and
verbatim, so you do not need to restate them -- you are summarizing the work.

Write a factual brief, addressed to the agent that will read it, covering:

1. The goal being pursued, and any constraint or decision that still binds it.
2. What has been established: findings, file paths, identifiers, values and
   commands that later steps depend on. Reproduce exact strings exactly.
3. Where the work got to: what is finished, what is in progress, what failed and
   why it failed.

Preserve specifics over description -- a path or an error message is worth more
than a sentence about it. Invent nothing that is not in the transcript. Do not
address the user, do not apologise, and do not describe the transcript itself.
Write only the brief.
"""


def _part_text(part: object) -> str:
    """One transcript line's worth of a part, capped but never head-truncated."""
    name = type(part).__name__
    if name == "ToolCallPart":
        return f"[tool call] {getattr(part, 'tool_name', '?')}: {_clip(getattr(part, 'args', ''))}"
    if name == "ToolReturnPart":
        return f"[tool result] {getattr(part, 'tool_name', '?')}: {_clip(getattr(part, 'content', ''))}"
    if name == "RetryPromptPart":
        return f"[tool error] {_clip(getattr(part, 'content', ''))}"
    if name == "ThinkingPart":
        return ""
    return _clip(getattr(part, "content", None) or getattr(part, "text", ""))


def _clip(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= _MAX_PART_CHARS:
        return text
    keep = _MAX_PART_CHARS // 2
    return f"{text[:keep]}\n... [{len(text) - _MAX_PART_CHARS} characters omitted] ...\n{text[-keep:]}"


def _render(messages: list[Any]) -> str:
    lines: list[str] = []
    for message in messages:
        role = "assistant" if type(message).__name__ == "ModelResponse" else "system"
        for part in getattr(message, "parts", ()) or ():
            rendered = _part_text(part)
            if rendered:
                lines.append(f"{role}: {rendered}")
    transcript = "\n".join(lines)
    if len(transcript) <= _MAX_TRANSCRIPT_CHARS:
        return transcript
    # Keep both ends. The beginning says what the work is; the end says where it
    # got to. Keeping only the end is the bug this module exists to undo.
    head = int(_MAX_TRANSCRIPT_CHARS * 0.6)
    tail = _MAX_TRANSCRIPT_CHARS - head
    return f"{transcript[:head]}\n... [middle of the transcript omitted] ...\n{transcript[-tail:]}"


def _summary_message(text: str) -> Any:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    return ModelRequest(parts=[UserPromptPart(content=text)])


@dataclass(slots=True)
class HistoryCompactor:
    """Replaces the oldest work with a summary, keeping every user turn."""

    model: object
    trigger_tokens: int
    keep_messages: int

    async def __call__(self, ctx: RunContext, messages: list[Any]) -> list[Any]:
        working = list(messages)
        if self.trigger_tokens <= 0 or self.keep_messages <= 0:
            return working
        size = await run_blocking(count_model_message_tokens, working)
        if size <= self.trigger_tokens:
            return working

        cutoff = find_safe_cutoff(working, max(0, len(working) - self.keep_messages))
        if cutoff <= 0:
            return working
        head, tail = working[:cutoff], working[cutoff:]

        # Pinned messages survive as themselves, so summarizing them would spend
        # tokens describing text that is about to be reproduced anyway.
        pinned = [message for message in head if is_pinned_message(message)]
        to_summarize = [message for message in head if not is_pinned_message(message)]
        if not to_summarize:
            return [*pinned, *tail]

        summary = await self._summarize(ctx, _render(to_summarize))
        compacted = [*pinned, _summary_message(summary), *tail]
        logger.info(
            "agent.history.compacted.observed",
            size_before=size,
            size_after=count_model_message_tokens(compacted),
            pinned_count=len(pinned),
            summarized_count=len(to_summarize),
            kept_count=len(tail),
        )
        return compacted

    async def _summarize(self, ctx: RunContext, transcript: str) -> str:
        from pydantic_ai import Agent

        try:
            result = await Agent(self.model, instructions=_SUMMARY_INSTRUCTIONS).run(
                transcript
            )
        except Exception:
            # Loud, and not a silent pass-through. The library swallowed this and
            # returned the original oversized history, which handed the ceiling
            # guard a request it could only fix by amputation.
            logger.warning(
                "agent.history.summarization_failed.degraded",
                transcript_length=len(transcript),
                exc_info=True,
            )
            return (
                f"{FAILED_MARKER}\nThe earlier steps of this conversation could not"
                " be summarized and are not shown. The user's messages above are"
                " complete; treat the work between them as unknown rather than as"
                " not having happened."
            )

        # Metered against the run that caused it. The library built its own bare
        # Agent internally, so every compaction was an LLM call Lemma paid for
        # and never saw.
        usage = getattr(ctx, "usage", None)
        if usage is not None:
            try:
                # An attribute in some pydantic-ai versions and a method in
                # others. Getting it wrong bills nothing and, behind the guard
                # below, says nothing either.
                reported = result.usage
                usage.incr(reported() if callable(reported) else reported)
            # Narrow on purpose. A broader catch here already hid a real defect
            # once: `result.usage` moved from method to attribute, every
            # compaction went unbilled, and the warning read like a quirk of the
            # provider rather than our bug.
            except AttributeError, TypeError:
                logger.warning(
                    "agent.history.usage_not_metered.degraded", exc_info=True
                )
        return f"{SUMMARY_MARKER}\n{result.output}"
