"""Reasoning that a model wrote into its *text* channel, and how to find it.

Some OpenAI-compatible models (Fireworks MiniMax M3 is the one that bit us) do
not return reasoning as a separate part. They inline it in the content as
``<think>...</think>``. Nothing downstream expects that: the text channel is
where the *answer* lives, so inlined reasoning is read as the answer -- shown as
one on screen, and returned as one to whatever called the agent.

This module is the single place that knows the convention. It has three shapes
because there are three jobs:

``split_thinking_segments``
    A whole string, split into its reasoning and non-reasoning runs, in order.
    Used where the complete text is in hand -- persisting a message, repairing a
    stored row.

``strip_thinking_tokens``
    The same split, keeping only the text. What a surface wants: reasoning must
    never reach Slack or Telegram at all.

``ThinkingStreamSplitter``
    The same split over a *stream*, where a tag can straddle two deltas. A naive
    per-delta split lets ``<thi`` + ``nk>`` through and the user reads the
    model's reasoning as it is typed. This holds back trailing text that could
    still turn out to be the start of a tag and releases it once the next delta
    proves otherwise.

It lives in ``app/core`` rather than beside its first caller because the agent
module and the surfaces module both need it, and a module may not import
another's infrastructure.
"""

from __future__ import annotations

import re
from typing import Literal

#: The tags the convention uses. ``<thinking>`` is accepted as well because
#: models emit both, and the closing tag may be either spelling.
THINKING_TAGS: tuple[str, str] = ("<think>", "</think>")

Segment = tuple[Literal["thinking", "text"], str]

# Ordered alternation, and the order is load-bearing:
#   - self-closing first, so ``<think/>`` is not read as an unclosed open tag
#   - then a closed block, non-greedy, so two blocks stay two
#   - then an unclosed open tag, greedy to end of string -- a model that writes
#     ``<think>`` and never closes it has reasoned for the rest of the message,
#     and treating the remainder as an answer is the worst of the options.
_THINK_RE = re.compile(
    r"<think(?:ing)?[^>]*/>"
    r"|<think(?:ing)?[^>]*>.*?</think(?:ing)?>"
    r"|<think(?:ing)?[^>]*>.*",
    re.DOTALL | re.IGNORECASE,
)

_OPEN_RE = re.compile(r"<think(?:ing)?[^>]*>", re.IGNORECASE)
_CLOSE_RE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)
_SELF_CLOSING_RE = re.compile(r"<think(?:ing)?[^>]*/>", re.IGNORECASE)

# Longest tag we must never emit half of: ``</thinking>``.
_MAX_TAG = len("</thinking>")

# Fenced code spans, so a tag *inside* one is prose about the convention rather
# than an instance of it. An answer explaining `<think>` tags in a code block is
# rare but it is a real answer, and eating it would be a worse bug than the one
# this module fixes.
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _FENCE_RE.finditer(text)]


def _inside_any(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def _tag_body(match: str) -> str:
    """The reasoning inside a matched block, without its tags."""
    if _SELF_CLOSING_RE.fullmatch(match):
        return ""
    open_match = _OPEN_RE.search(match)
    body = match[open_match.end() :] if open_match else match
    close_match = _CLOSE_RE.search(body)
    return body[: close_match.start()] if close_match else body


def split_thinking_segments(text: str) -> list[Segment]:
    """``[(kind, text)]`` in source order, where kind is thinking or text.

    Empty and whitespace-only runs are dropped: they are not content, and
    keeping them would turn one message into two -- a bubble plus a blank one.
    """
    if not text:
        return []

    fenced = _fenced_spans(text)
    segments: list[Segment] = []
    cursor = 0

    for match in _THINK_RE.finditer(text):
        if _inside_any(match.start(), fenced):
            continue
        before = text[cursor : match.start()]
        if before.strip():
            segments.append(("text", before))
        body = _tag_body(match.group())
        if body.strip():
            segments.append(("thinking", body))
        cursor = match.end()

    remainder = text[cursor:]
    if remainder.strip():
        segments.append(("text", remainder))
    return segments


def has_thinking_tokens(text: str | None) -> bool:
    """Whether ``text`` carries reasoning. Cheap guard before the split."""
    if not text or "<think" not in text.lower():
        return False
    return any(kind == "thinking" for kind, _ in split_thinking_segments(text))


def strip_thinking_tokens(text: str) -> str:
    """``text`` with every reasoning block removed, whitespace collapsed.

    Text with no reasoning comes back unchanged apart from the strip. Text that
    was *entirely* reasoning comes back empty, and callers are expected to treat
    that as "there was no answer" rather than as an empty answer.
    """
    if not text:
        return ""
    return "".join(
        chunk for kind, chunk in split_thinking_segments(text) if kind == "text"
    ).strip()


class ThinkingStreamSplitter:
    """Classify a token stream into reasoning and answer as it arrives.

    Stateful for the length of one stream, because a tag can arrive in pieces.
    ``feed`` returns the segments that are safe to emit *now*; anything that
    could still be the start of a tag is held until the next delta settles it,
    and ``flush`` releases whatever is left when the stream ends.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    @property
    def inside_thinking(self) -> bool:
        """Whether the stream is currently mid-reasoning."""
        return self._inside

    def feed(self, delta: str) -> list[Segment]:
        self._buffer += delta
        out: list[Segment] = []

        while True:
            if self._inside:
                match = _CLOSE_RE.search(self._buffer)
                if match is None:
                    # Release all but a possible partial closing tag.
                    safe_upto = self._safe_prefix_end()
                    if safe_upto:
                        out.append(("thinking", self._buffer[:safe_upto]))
                        self._buffer = self._buffer[safe_upto:]
                    break
                if match.start():
                    out.append(("thinking", self._buffer[: match.start()]))
                self._buffer = self._buffer[match.end() :]
                self._inside = False
                continue

            match = _OPEN_RE.search(self._buffer)
            if match is not None:
                if match.start():
                    out.append(("text", self._buffer[: match.start()]))
                self._buffer = self._buffer[match.end() :]
                # ``<think/>`` opens and closes in one go.
                self._inside = not _SELF_CLOSING_RE.fullmatch(match.group())
                continue

            safe_upto = self._safe_prefix_end()
            if safe_upto:
                out.append(("text", self._buffer[:safe_upto]))
                self._buffer = self._buffer[safe_upto:]
            break

        return [(kind, chunk) for kind, chunk in out if chunk]

    def flush(self) -> list[Segment]:
        """Whatever is left, once no further delta can change its meaning."""
        remainder = self._buffer
        self._buffer = ""
        if not remainder:
            return []
        # An unclosed block ends as reasoning, matching `_THINK_RE`'s greedy
        # branch: the model reasoned to the end and never wrote an answer.
        return [("thinking" if self._inside else "text", remainder)]

    def _safe_prefix_end(self) -> int:
        """How much of the buffer cannot be the opening of a tag."""
        last_open = self._buffer.rfind("<")
        if last_open != -1 and (len(self._buffer) - last_open) <= _MAX_TAG:
            return last_open
        return len(self._buffer)
