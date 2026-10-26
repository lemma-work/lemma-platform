"""A finished turn, written out for the memory pass to read.

Pure: the run's messages in, one prompt out. The pass reads the turn and not
the conversation, because every earlier turn had a pass of its own and reading
them again would only re-judge what was already kept. The agent's reply before
the turn comes along: "yes, always do it that way" means nothing without what
it answers.

Bounded, because it is a prompt. What the person said and what the agent
answered are where most of what is worth keeping is said, so they are kept
whole up to a generous cap. The work in between -- tool calls and what they
returned -- is cut short step by step, and past a total the remaining steps
are counted rather than shown.
"""

from __future__ import annotations

import re

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.domain.pauses import CALL_NAMES
from app.modules.agent.domain.state_kinds import PauseKind

#: One message the person sent or the agent answered.
WORDS_MAX_CHARS = 6000
#: The agent's reply before the turn: context for it, not part of it.
REPLY_BEFORE_MAX_CHARS = 1500
#: One tool call's arguments, or one tool's result.
STEP_MAX_CHARS = 400
#: All the steps of the turn together.
WORK_MAX_CHARS = 6000
#: Messages of one run read at all. Past it the turn is cut at the end, which
#: keeps what the person said at its start.
TURN_MESSAGES_MAX = 300

_CUT = " […]"


@dataclass(frozen=True, slots=True)
class TurnMessage:
    """One message of a finished run, as much of it as the pass reads.

    ``body`` is the text of a TEXT message, the arguments of a TOOL_CALL or
    the result of a TOOL_RETURN, as JSON -- already cut by the query to one
    character past its cap, so a cut is visible here without reading the rest.
    """

    role: MessageRole
    kind: MessageKind
    body: str | None
    tool_name: str | None = None


def render_turn(
    messages: Sequence[TurnMessage], *, reply_before: str | None, today: date
) -> str:
    """The turn as the pass reads it, in the order it happened."""
    parts = [f"Today is {today.isoformat()}."]
    if reply_before and reply_before.strip():
        parts.append(
            "Before this turn, the agent had said:\n"
            + _quoted(reply_before, REPLY_BEFORE_MAX_CHARS)
        )
    work_chars = 0
    steps_left_out = 0
    for message in messages:
        words = _words(message)
        if words is not None:
            parts.append(words)
            continue
        step = _step(message)
        if step is None:
            continue
        if work_chars + len(step) > WORK_MAX_CHARS:
            steps_left_out += 1
            continue
        work_chars += len(step)
        parts.append(step)
    if steps_left_out:
        parts.append(f"({steps_left_out} more steps of the agent's work not shown.)")
    return "\n\n".join(parts)


def said_by_the_person(messages: Sequence[TurnMessage]) -> bool:
    """Whether the person said anything in this turn, or answered the agent.

    A turn that only resumed work -- an approval clicked, a wait come due --
    carries nothing of theirs, and most of what is worth keeping is theirs. Nor
    does one whose every message is an acknowledgement ("thanks", "ok") or no
    words at all: the pass would read the whole turn and the memory brief to
    conclude "nothing", at a multiple of the turn's own cost. An answer to the
    agent's own question always counts -- a bare "yes" there can be a fact.
    """
    return any(
        _is_the_persons(message) and not _only_acknowledges(message)
        for message in messages
    )


#: Words that acknowledge and say nothing about anyone. Matched whole, so a
#: message counts only when every word in it is one of these.
_ACKNOWLEDGEMENTS = frozenset(
    {
        "hi", "hello", "hey", "thanks", "thank", "you", "thx", "ty", "ok",
        "okay", "k", "cool", "great", "nice", "good", "perfect", "awesome",
        "yes", "yeah", "yep", "no", "nope", "sure", "got", "it", "alright",
        "done", "bye", "cheers", "np", "lol", "haha", "much", "so",
    }
)  # fmt: skip


def _only_acknowledges(message: TurnMessage) -> bool:
    if message.kind is MessageKind.TOOL_RETURN:
        return False
    # `\w` is Unicode: words in any script are words, and only these count.
    words = re.findall(r"\w+", (message.body or "").lower())
    return all(word in _ACKNOWLEDGEMENTS for word in words)


def the_persons_words(messages: Sequence[TurnMessage]) -> list[TurnMessage]:
    """Only what the person said, of a run whose own work is not this turn's."""
    return [message for message in messages if _is_the_persons(message)]


def _is_the_persons(message: TurnMessage) -> bool:
    if not (message.body or "").strip():
        return False
    if message.role is MessageRole.USER and message.kind is MessageKind.TEXT:
        return True
    return (
        # The ask_user return is the person's own answer to the agent's question.
        message.kind is MessageKind.TOOL_RETURN
        and message.tool_name == CALL_NAMES[PauseKind.QUESTION]
    )


def _words(message: TurnMessage) -> str | None:
    if message.kind is not MessageKind.TEXT or not (message.body or "").strip():
        return None
    if message.role is MessageRole.USER:
        return "The person said:\n" + _quoted(message.body or "", WORDS_MAX_CHARS)
    if message.role is MessageRole.ASSISTANT:
        return "The agent said:\n" + _quoted(message.body or "", WORDS_MAX_CHARS)
    return None


def _step(message: TurnMessage) -> str | None:
    name = message.tool_name or "a tool"
    body = _cut(message.body or "null", STEP_MAX_CHARS)
    if message.kind is MessageKind.TOOL_CALL:
        return f"The agent called `{name}` with {body}"
    if message.kind is MessageKind.TOOL_RETURN:
        return f"`{name}` returned {body}"
    return None


def _quoted(text: str, limit: int) -> str:
    return "\n".join(f"> {line}" for line in _cut(text.strip(), limit).splitlines())


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_CUT)].rstrip() + _CUT


__all__ = [
    "REPLY_BEFORE_MAX_CHARS",
    "STEP_MAX_CHARS",
    "TURN_MESSAGES_MAX",
    "TurnMessage",
    "WORDS_MAX_CHARS",
    "WORK_MAX_CHARS",
    "render_turn",
    "said_by_the_person",
    "the_persons_words",
]
