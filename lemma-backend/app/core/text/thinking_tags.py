"""Reasoning that a model wrote into its *text* channel, and how to find it.

Some OpenAI-compatible models (Fireworks MiniMax M3 is the one that bit us) do
not return reasoning as a separate part. They inline it in the content as
``<think>...</think>``. Nothing downstream expects that: the text channel is
where the *answer* lives, so inlined reasoning is read as the answer -- shown as
one on screen, and returned as one to whatever called the agent.

Others write only half of it. A model whose chat template opens the block in
the *prompt* starts its content already reasoning, with no ``<think>`` at all,
and the first thing that says so is the ``</think>`` that ends it. Everything
before an unopened close is reasoning -- and in a stream, it has already been
shown as the answer by the time the close arrives.

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
    proves otherwise. It is also how the other two read a whole string, so a
    streamed answer and the message saved from it hide exactly the same text.

    When the stream learns late what it already released -- an unopened close
    -- it says ``reset`` and releases the stream again, classified as it now
    knows it to be. A reader that cannot take back what it showed keeps what it
    can; one that can (the conversation's own stream) clears the bubble.

It lives in ``app/core`` rather than beside its first caller because the agent
module and the surfaces module both need it, and a module may not import
another's infrastructure.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

#: The tags the convention uses. ``<thinking>`` is accepted as well because
#: models emit both, and the closing tag may be either spelling.
THINKING_TAGS: tuple[str, str] = ("<think>", "</think>")

#: ``reset`` carries no text: what came before it in this stream is withdrawn,
#: and the segments after it say again what the stream held.
Segment = tuple[Literal["thinking", "text", "reset"], str]
RESET = "reset"

# One reader decides, for the whole string and for the stream alike: the
# whole-string functions feed the text through `ThinkingStreamSplitter` in one
# go. Two readers had two answers -- the stream hid what a saved message showed,
# or the reverse -- and a reasoning block one of them missed reached a person.
#
# The reader walks forward once, in one of three states, and never looks back:
#   - text: an open tag starts reasoning, a fence marker starts a code fence,
#     and -- until the stream has opened a block of its own -- a close tag
#     says everything before it was reasoning;
#   - a code fence: everything up to the same marker is the answer's code, tags
#     included -- an answer explaining the convention in a code block is still
#     an answer. A fence never closed runs to the end, as it does on screen;
#   - reasoning: everything up to the first close is reasoning, fences included
#     -- a thought that drafts code is still a thought. A block never closed
#     runs to the end: a model that writes ``<think>`` and never closes it has
#     reasoned for the rest of the message, and treating the remainder as an
#     answer is the worst of the options.
#
# A tag's name ends where the name ends: ``<thinker>`` is not ``<think>``. Its
# attributes are ``[^<>]*``, never ``[^>]*``: a tag never contains another ``<``,
# and without that stop every ``<think`` in a run of them scans to the end of the
# string, which is quadratic in what a model was made to repeat.
_TAG_NAME = r"think(?:ing)?(?=[\s/>])"
_OPEN_TAG = rf"<{_TAG_NAME}[^<>]*>"
_CLOSE_TAG = r"</think(?:ing)?>"
_TEXT_TOKEN_RE = re.compile(rf"```|~~~|{_OPEN_TAG}", re.IGNORECASE)
_TEXT_OR_CLOSE_TOKEN_RE = re.compile(
    rf"```|~~~|{_OPEN_TAG}|{_CLOSE_TAG}", re.IGNORECASE
)
_CLOSE_RE = re.compile(_CLOSE_TAG, re.IGNORECASE)
_FENCES = ("```", "~~~")

# What the end of the stream may be holding of a tag that is not complete yet,
# from its ``<``: part of the name, or the name and attributes of any length
# still waiting for their ``>``.
_PARTIAL_OPEN_RE = re.compile(
    r"<(?:t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?)?|<think(?:ing)?[\s/][^<>]*",
    re.IGNORECASE,
)
_PARTIAL_CLOSE_RE = re.compile(
    r"<(?:/(?:t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?)?)?", re.IGNORECASE
)


class _State(Enum):
    TEXT = "text"
    FENCE = "fence"
    THINKING = "thinking"


def split_thinking_segments(text: str) -> list[Segment]:
    """``[(kind, text)]`` in source order, where kind is thinking or text.

    Empty and whitespace-only runs are dropped: they are not content, and
    keeping them would turn one message into two -- a bubble plus a blank one.
    """
    if not text:
        return []
    splitter = ThinkingStreamSplitter()
    merged: list[Segment] = []
    for kind, chunk in (*splitter.feed(text), *splitter.flush()):
        if kind == RESET:
            merged = []
        elif merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + chunk)
        else:
            merged.append((kind, chunk))
    return [(kind, chunk) for kind, chunk in merged if chunk.strip()]


def has_thinking_tokens(text: str | None) -> bool:
    """Whether ``text`` carries reasoning. Cheap guard before the split."""
    if not text:
        return False
    lowered = text.lower()
    if "<think" not in lowered and "</think" not in lowered:
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
    could still be the start of a tag or a fence marker is held until the next
    delta settles it, and ``flush`` releases whatever is left when the stream
    ends and readies the splitter for the next one.

    Each character is read once: the buffer holds only what is undecided, and
    a held tag that is only waiting for its ``>`` is not read again until a
    delta could finish it.

    One splitter can read several streams in turn -- every model response of a
    run. A model that once began a response already reasoning will do it again,
    so after an unopened close the next stream starts *presumed* to be
    reasoning: shown as reasoning until its close confirms it, and released
    again as the answer (``reset``) if it ends without one.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._state = _State.TEXT
        self._fence = ""
        # The buffer is a tag's name and attributes, waiting for ``>``.
        self._awaiting_tag_end = False
        # No block has been opened in this stream, so a close would end one
        # the prompt opened.
        self._unopened_close_possible = True
        # This model opens its reasoning in the prompt; see the class docstring.
        self._reasons_unopened = False
        # The current reasoning is presumed, not yet confirmed by its close.
        self._presumed = False
        # What this stream has released, for a ``reset`` to say again.
        self._released: list[Segment] = []

    @property
    def inside_thinking(self) -> bool:
        """Whether the stream is currently mid-reasoning."""
        return self._state is _State.THINKING

    def feed(self, delta: str) -> list[Segment]:
        if self._awaiting_tag_end and "<" not in delta and ">" not in delta:
            # Still waiting: nothing in this delta can complete or end it.
            self._buffer += delta
            return []
        self._buffer += delta
        self._awaiting_tag_end = False
        out: list[Segment] = []
        position = 0
        while position < len(self._buffer):
            step = {
                _State.TEXT: self._read_text,
                _State.FENCE: self._read_fence,
                _State.THINKING: self._read_thinking,
            }[self._state]
            advanced = step(position, out)
            if advanced is None:
                break
            position = advanced
        else:
            self._buffer = ""
        return self._release(out)

    def flush(self) -> list[Segment]:
        """Whatever is left, once no further delta can change its meaning.

        Ends the stream: the next ``feed`` starts a new one.
        """
        remainder = self._buffer
        self._buffer = ""
        self._awaiting_tag_end = False
        out: list[Segment] = []
        if self._presumed:
            # Presumed reasoning that never closed was the answer after all. A
            # stream that wrote nothing has nothing to take back, and says so
            # with no reset: the pump ends a stream at every event it sees.
            if self._released or remainder.strip():
                self._reasons_unopened = False
                out = [(RESET, ""), *self._as_text(self._released)]
                if remainder:
                    out.append(("text", remainder))
        elif remainder:
            # An unclosed block ends as reasoning: the model reasoned to the end
            # and never wrote an answer.
            out = [("thinking" if self.inside_thinking else "text", remainder)]
        released = [(kind, chunk) for kind, chunk in out if chunk or kind == RESET]
        self._start_stream()
        return released

    def _start_stream(self) -> None:
        self._released = []
        self._fence = ""
        self._unopened_close_possible = not self._reasons_unopened
        self._presumed = self._reasons_unopened
        self._state = _State.THINKING if self._presumed else _State.TEXT

    def _release(self, out: list[Segment]) -> list[Segment]:
        released = [(kind, chunk) for kind, chunk in out if chunk or kind == RESET]
        for kind, chunk in released:
            if kind == RESET:
                self._released = []
            else:
                self._released.append((kind, chunk))
        return released

    @staticmethod
    def _as_text(segments: list[Segment]) -> list[Segment]:
        return [("text", chunk) for _kind, chunk in segments]

    # Each reader consumes from ``position``, appends what it decided, and
    # returns where the next one starts -- or ``None`` once the rest of the
    # buffer is undecided, keeping only that undecided rest.

    def _read_text(self, position: int, out: list[Segment]) -> int | None:
        buffer = self._buffer
        tokens = (
            _TEXT_OR_CLOSE_TOKEN_RE if self._unopened_close_possible else _TEXT_TOKEN_RE
        )
        match = tokens.search(buffer, position)
        if match is None:
            held = self._held_in_text(position)
            out.append(("text", buffer[position:held]))
            self._buffer = buffer[held:]
            return None
        pending = buffer.rfind("<", position, match.start())
        if pending != -1 and _PARTIAL_OPEN_RE.fullmatch(buffer, pending):
            # A tag still being written began before this token and may yet
            # end after it, which would make the token one of its attributes.
            out.append(("text", buffer[position:pending]))
            self._buffer = buffer[pending:]
            self._awaiting_tag_end = len(self._buffer) > len("<thinking")
            return None
        token = match.group()
        if token in _FENCES:
            out.append(("text", buffer[position : match.end()]))
            self._state, self._fence = _State.FENCE, token
        elif token.startswith("</"):
            return self._read_unopened_close(position, match, out)
        else:
            out.append(("text", buffer[position : match.start()]))
            self._unopened_close_possible = False
            # ``<think/>`` opens and closes in one go.
            if not token.endswith("/>"):
                self._state = _State.THINKING
        return match.end()

    def _read_unopened_close(
        self, position: int, match: re.Match[str], out: list[Segment]
    ) -> int:
        """A close with no open: what this stream said so far was reasoning.

        Quoted inline -- `` `</think>` `` -- it is prose about the convention.
        """
        buffer = self._buffer
        if match.start() > 0 and buffer[match.start() - 1] == "`":
            out.append(("text", buffer[position : match.end()]))
            return match.end()
        said = [chunk for kind, chunk in self._released if kind != RESET]
        said.extend(chunk for kind, chunk in out if kind != RESET)
        out.clear()
        out.append((RESET, ""))
        out.append(("thinking", "".join(said) + buffer[position : match.start()]))
        self._unopened_close_possible = False
        self._reasons_unopened = True
        return match.end()

    def _read_fence(self, position: int, out: list[Segment]) -> int | None:
        buffer = self._buffer
        end = buffer.find(self._fence, position)
        if end == -1:
            held = _held_marker(buffer, position, self._fence)
            out.append(("text", buffer[position:held]))
            self._buffer = buffer[held:]
            return None
        closed = end + len(self._fence)
        out.append(("text", buffer[position:closed]))
        self._state, self._fence = _State.TEXT, ""
        return closed

    def _read_thinking(self, position: int, out: list[Segment]) -> int | None:
        buffer = self._buffer
        match = _CLOSE_RE.search(buffer, position)
        if match is None:
            held = len(buffer)
            last_open = buffer.rfind("<", position)
            if last_open != -1 and _PARTIAL_CLOSE_RE.fullmatch(buffer, last_open):
                held = last_open
            out.append(("thinking", buffer[position:held]))
            self._buffer = buffer[held:]
            return None
        out.append(("thinking", buffer[position : match.start()]))
        self._state = _State.TEXT
        self._presumed = False
        self._unopened_close_possible = False
        return match.end()

    def _held_in_text(self, position: int) -> int:
        """Where the undecided tail of the text starts: a partial open or close
        tag, or a partial fence marker."""
        buffer = self._buffer
        last_open = buffer.rfind("<", position)
        if last_open != -1 and _PARTIAL_OPEN_RE.fullmatch(buffer, last_open):
            # Only the name-and-attributes shape can grow without bound; a
            # partial name is a handful of characters and is simply re-read.
            self._awaiting_tag_end = len(buffer) - last_open > len("<thinking")
            return last_open
        if (
            self._unopened_close_possible
            and last_open != -1
            and _PARTIAL_CLOSE_RE.fullmatch(buffer, last_open)
        ):
            return last_open
        held = len(buffer)
        for fence in _FENCES:
            held = min(held, _held_marker(buffer, position, fence))
        return held


def _held_marker(buffer: str, position: int, marker: str) -> int:
    """Where a trailing, still-incomplete ``marker`` starts, else the end."""
    for length in range(len(marker) - 1, 0, -1):
        if len(buffer) - position >= length and buffer.endswith(marker[:length]):
            return len(buffer) - length
    return len(buffer)


#: Lemma's own context blocks -- the memory brief's ``<memory …>`` sections
#: and the ``<index path=…>`` entries inside them -- which a model sometimes
#: repeats back. They are the platform's markup, never an answer.
_CONTEXT_BLOCKS = re.compile(
    r"<memory\b[^>]*>.*?</memory>|<index\b[^>]*>.*?</index>",
    re.DOTALL | re.IGNORECASE,
)


def strip_context_echo(text: str) -> str:
    """``text`` without the platform's own context blocks a model echoed."""
    if not text or "<" not in text:
        return text
    return _CONTEXT_BLOCKS.sub("", text).strip()
