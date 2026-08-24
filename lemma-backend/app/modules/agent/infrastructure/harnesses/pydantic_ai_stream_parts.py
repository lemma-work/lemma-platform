"""What one model request accumulates while it streams.

A streamed response arrives as parts, and each part arrives in pieces: a start,
any number of deltas, an end. Turning that back into whole messages means
carrying state across events -- what kind each part is, what text it has
gathered, whether a tool call has begun streaming its arguments yet.

That state used to be seven containers and six closures inside
``_stream_model_request``, which is most of why that function was the most
complex in the repository. The behaviour is unchanged; it has a name now, and
the loop that drives it can be read without also holding the bookkeeping.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import MessageDraft
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    parse_tool_call_args,
)
from app.modules.agent.infrastructure.harnesses.streaming import CharStreamBuffer

logger = get_logger(__name__)

_TOKEN_KINDS = ("text", "thinking", "tool")


class StreamingParts:
    """The parts of one model request, as they arrive.

    ``emit_tokens`` is read once here rather than at each call site: when a run
    does not stream tokens every append and drain is a no-op, and saying that in
    one place is what lets the callers stay linear.
    """

    def __init__(self, *, emit_tokens: bool, malformed_tool_call_ids: set[str]) -> None:
        self._emit_tokens = emit_tokens
        self._malformed_tool_call_ids = malformed_tool_call_ids
        self._buffers = {kind: CharStreamBuffer(max_chars=50) for kind in _TOKEN_KINDS}
        self._tool_stream_started: set[int] = set()
        self.kinds: dict[int, str] = {}
        self.contents: dict[int, str] = {}
        self.objects: dict[int, object] = {}
        self.tool_names: dict[int, str] = {}
        self.tool_stream_has_args: set[int] = set()

    @staticmethod
    def _delta(kind: str, chunk: str) -> dict[str, str]:
        return {"kind": kind, "data": chunk}

    def append_token_text(self, kind: str, text: str) -> list[dict[str, str]]:
        if not self._emit_tokens:
            return []
        return [self._delta(kind, chunk) for chunk in self._buffers[kind].append(text)]

    def drain_token_buffer(
        self, kind: str, *, force: bool = False
    ) -> list[dict[str, str]]:
        if not self._emit_tokens:
            return []
        return [
            self._delta(kind, chunk) for chunk in self._buffers[kind].drain(force=force)
        ]

    def drain_all_token_buffers(self, *, force: bool = False) -> list[dict[str, str]]:
        chunks: list[dict[str, str]] = []
        for kind in _TOKEN_KINDS:
            chunks.extend(self.drain_token_buffer(kind, force=force))
        return chunks

    def start_tool_stream(self, index: int, tool_name: str) -> list[dict[str, str]]:
        """Open a tool call's argument stream, once per part."""
        if index in self._tool_stream_started:
            return []
        self._tool_stream_started.add(index)
        return self.append_token_text(
            "tool", f'{{"tool_name":{json.dumps(tool_name)},"args":'
        )

    def forget_tool_part(self, index: int) -> None:
        """Drop everything remembered about a tool part that ended.

        The three containers are forgotten together or not at all: a name left
        behind without its stream state is what makes the next part at the same
        index inherit the last one's tool.
        """
        self.tool_names.pop(index, None)
        self._tool_stream_started.discard(index)
        self.tool_stream_has_args.discard(index)

    def completed_part_message(
        self, *, part: Any, part_kind: str | None, part_content: str | None
    ) -> MessageDraft | None:
        """The message a finished part becomes, or None when it becomes nothing.

        A part that streamed has its text in ``part_content``; one that did not
        carries it on the part itself. Empty either way means there is nothing
        to persist -- and "empty" has to mean whitespace too. A model that goes
        straight from a response into a tool call emits its text part as a bare
        ``"\n\n"``, which is truthy, so each one was stored as a message and
        rendered as its own chat bubble: one run produced twelve.

        What is *not* decided here is whether text is the final answer. This
        runs per part, at the moment the part ends, and a text part ends before
        the tool call beside it has arrived -- so the one fact that would settle
        it, whether the response went on to call a tool, is not known yet. Text
        is therefore left unmarked and ``RunMessageWriter`` defaults it to
        ``is_final_answer``, which is right for the last part of a run and wrong
        for every preamble before a tool call. Deciding it needs the whole
        response, the way the agent-host harness's ``_flush_messages`` does.
        """
        if isinstance(part, TextPart) or part_kind == "text":
            text = part_content if part_content is not None else part.content
            return MessageDraft.of_text(text) if (text or "").strip() else None

        if isinstance(part, ThinkingPart) or part_kind == "thinking":
            thinking = part_content if part_content is not None else part.content
            return (
                MessageDraft.of_thinking(thinking) if (thinking or "").strip() else None
            )

        if isinstance(part, ToolCallPart) or part_kind == "tool_call":
            return self._tool_call_message(part)

        return None

    def _tool_call_message(self, part: Any) -> MessageDraft | None:
        """A tool call is only persistable once its arguments parse."""
        tool_args = parse_tool_call_args(part.args)
        if tool_args is None:
            self._malformed_tool_call_ids.add(part.tool_call_id)
            logger.debug(
                "agent.pydantic_ai.skipping_malformed_tool_call_persistence.diagnostic",
                tool_call_id=part.tool_call_id,
            )
            return None
        return MessageDraft.of_tool_call(
            tool_name=part.tool_name,
            tool_call_id=part.tool_call_id,
            tool_args=tool_args,
            metadata={"tool_name": part.tool_name},
        )
