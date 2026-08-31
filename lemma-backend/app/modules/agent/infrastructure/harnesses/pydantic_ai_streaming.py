"""Turning one model request into the tokens a user watches appear.

pydantic-ai hands back a stream of part-start / part-delta / part-end events
across text, thinking and tool-call parts. This translates them into LEMMA
`AgentEvent`s, keeps the accumulated parts in step, and -- at every single yield
point -- checks whether the user has pressed Stop.

That check is why this is a class rather than a set of functions. It appeared
eleven times as `if await self._should_stop(should_stop): yield
self._stopped_event(agent_run_id)`, with both values threaded through six
signatures to reach it. They are fields now, so the check reads as what it is
and the signatures say what actually varies.

Stopping mid-stream is a real requirement, not a nicety: the model may be
minutes into an answer and the tokens already shown have to stay shown, so the
stop has to be a yielded event rather than a raised exception.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    to_json_value,
)
from pydantic_ai import (
    BinaryContent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
)
from pydantic_ai.messages import (
    FinalResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)

from app.modules.agent.infrastructure.harnesses.pydantic_ai_stream_parts import (
    StreamingParts,
)

logger = get_logger(__name__)

StopChecker = Any


def _tool_call_delta_text(delta: ToolCallPartDelta) -> str:
    if not delta.args_delta:
        return ""
    return _tool_call_args_text(delta.args_delta)


def _tool_call_args_text(args: object) -> str:
    if args is None or args == "":
        return ""
    if isinstance(args, str):
        return args
    return json.dumps(to_json_value(args), default=str)


class ModelRequestStreamer:
    """Streams one model request's parts as agent events."""

    def __init__(
        self,
        *,
        emit_tokens: bool,
        agent_run_id: UUID,
        should_stop: StopChecker | None,
    ) -> None:
        self.emit_tokens = emit_tokens
        self.agent_run_id = agent_run_id
        self.should_stop = should_stop

    async def _stream_model_request(
        self,
        node,
        run,
        *,
        malformed_tool_call_ids: set[str],
    ) -> AsyncIterator[AgentEvent]:
        parts = StreamingParts(
            emit_tokens=self.emit_tokens,
            malformed_tool_call_ids=malformed_tool_call_ids,
        )

        # A stop is held back rather than returned on, so a part still in
        # progress -- one that never reached its PartEndEvent -- is flushed by
        # the tail below before the run ends.
        stopped: AgentEvent | None = None

        async with node.stream(run.ctx) as request_stream:
            async for event in request_stream:
                if isinstance(event, PartStartEvent):
                    handler = self._stream_part_start(
                        event,
                        parts,
                    )
                elif isinstance(event, PartDeltaEvent):
                    handler = self._stream_part_delta(
                        event,
                        parts,
                    )
                elif isinstance(event, PartEndEvent):
                    handler = self._stream_part_end(
                        event,
                        parts,
                    )
                elif isinstance(event, FinalResultEvent):
                    handler = self._stream_final_result(
                        parts,
                    )
                else:
                    continue
                async for agent_event in handler:
                    # Every early exit inside a handler is a stop check, and
                    # it announces itself by emitting STOPPED. Reading that
                    # here is what lets the handlers stay ordinary
                    # generators instead of needing a second channel to end
                    # the stream.
                    if agent_event.type is AgentEventType.STOPPED:
                        stopped = agent_event
                        break
                    yield agent_event
                if stopped is not None:
                    break

        async for event in self._flush_unfinished_parts(parts, stopped):
            yield event

    async def _flush_unfinished_parts(
        self,
        parts: StreamingParts,
        stopped: AgentEvent | None,
    ) -> AsyncIterator[AgentEvent]:
        """Persist parts the stream never finished, then end with the stop.

        `_stream_part_end` pops a part as it ends, so anything still in `kinds`
        here is a part abandoned mid-flight -- which is what a stop between two
        deltas produces. Without this the tokens had been shown and nothing was
        written, so the client cleared them.
        """
        async for token_event in self._emit_token_chunks(
            parts.drain_all_token_buffers(force=True),
        ):
            if token_event.type is AgentEventType.STOPPED:
                stopped = stopped or token_event
                break
            yield token_event

        for part_index in sorted(parts.kinds):
            part = parts.objects.get(part_index)
            if part is None:
                continue
            message = parts.completed_part_message(
                part=part,
                part_kind=parts.kinds[part_index],
                part_content=parts.contents.get(part_index),
            )
            if message is None:
                continue
            yield AgentEvent(
                type=AgentEventType.MESSAGE,
                data=message,
                agent_run_id=self.agent_run_id,
            )
            if stopped is None and await self.stop_requested():
                # Noted, not acted on: the remaining parts are flushed too.
                stopped = self.stopped_event()

        if stopped is not None:
            yield stopped

    async def _emit_token_chunks(
        self,
        chunks: list[dict[str, str]],
    ) -> AsyncIterator[AgentEvent]:
        """Emit token events, giving a stop request a chance between each.

        This shape appeared five times: a run cancelled mid-response should not
        keep streaming to the end of a buffered chunk list, so every chunk is a
        place the stream can end. Emitting STOPPED is how it ends -- the driver
        in `_stream_model_request` returns on seeing one, which closes whichever
        handler is suspended here.
        """
        for chunk in chunks:
            yield AgentEvent(
                type=AgentEventType.TOKEN,
                data=chunk,
                agent_run_id=self.agent_run_id,
            )
            if await self.stop_requested():
                yield self.stopped_event()
                return

    async def _stream_part_start(
        self,
        event,
        parts: StreamingParts,
    ) -> AsyncIterator[AgentEvent]:
        """A part has begun: record its kind and open its token stream.

        Stops the stream by emitting STOPPED; the driver returns on seeing one.
        """
        parts.objects[event.index] = event.part
        if isinstance(event.part, TextPart):
            parts.kinds[event.index] = "text"
            content = event.part.content or ""
            parts.contents[event.index] = content
            async for token_event in self._emit_token_chunks(
                parts.append_token_text("text", content),
            ):
                yield token_event
        elif isinstance(event.part, ThinkingPart):
            parts.kinds[event.index] = "thinking"
            content = event.part.content or ""
            parts.contents[event.index] = content
            async for token_event in self._emit_token_chunks(
                parts.append_token_text("thinking", content),
            ):
                yield token_event
        elif isinstance(event.part, ToolCallPart):
            parts.kinds[event.index] = "tool_call"
            parts.tool_names[event.index] = event.part.tool_name
            for token_chunk in parts.start_tool_stream(
                event.index,
                event.part.tool_name,
            ):
                yield AgentEvent(
                    type=AgentEventType.TOKEN,
                    data=token_chunk,
                    agent_run_id=self.agent_run_id,
                )
                if await self.stop_requested():
                    yield self.stopped_event()
                    return
            initial_args = _tool_call_args_text(event.part.args)
            if initial_args:
                parts.tool_stream_has_args.add(event.index)
                async for token_event in self._emit_token_chunks(
                    parts.append_token_text("tool", initial_args),
                ):
                    yield token_event

    async def _stream_part_delta(
        self,
        event,
        parts: StreamingParts,
    ) -> AsyncIterator[AgentEvent]:
        """A part grew: append the delta to what it is accumulating.

        Stops the stream by emitting STOPPED; the driver returns on seeing one.
        """
        if isinstance(event.delta, TextPartDelta):
            parts.kinds[event.index] = "text"
            content_delta = event.delta.content_delta or ""
            parts.contents[event.index] = (
                parts.contents.get(event.index, "") + content_delta
            )
            async for token_event in self._emit_token_chunks(
                parts.append_token_text("text", content_delta),
            ):
                yield token_event
        elif isinstance(event.delta, ThinkingPartDelta):
            parts.kinds.setdefault(event.index, "thinking")
            content_delta = getattr(event.delta, "content_delta", "") or ""
            if content_delta:
                parts.contents[event.index] = (
                    parts.contents.get(event.index, "") + content_delta
                )
                for token_chunk in parts.append_token_text(
                    "thinking",
                    content_delta,
                ):
                    yield AgentEvent(
                        type=AgentEventType.TOKEN,
                        data=token_chunk,
                        agent_run_id=self.agent_run_id,
                    )
                    if await self.stop_requested():
                        yield self.stopped_event()
                        return
        elif isinstance(event.delta, ToolCallPartDelta):
            parts.kinds.setdefault(event.index, "tool_call")
            if event.delta.tool_name_delta:
                parts.tool_names[event.index] = (
                    parts.tool_names.get(event.index, "") + event.delta.tool_name_delta
                )
            tool_delta = _tool_call_delta_text(event.delta)
            if tool_delta:
                for token_chunk in parts.start_tool_stream(
                    event.index,
                    parts.tool_names.get(event.index, ""),
                ):
                    yield AgentEvent(
                        type=AgentEventType.TOKEN,
                        data=token_chunk,
                        agent_run_id=self.agent_run_id,
                    )
                    if await self.stop_requested():
                        yield self.stopped_event()
                        return
                parts.tool_stream_has_args.add(event.index)
                async for token_event in self._emit_token_chunks(
                    parts.append_token_text("tool", tool_delta),
                ):
                    yield token_event

    async def _stream_part_end(
        self,
        event,
        parts: StreamingParts,
    ) -> AsyncIterator[AgentEvent]:
        """A part finished: drain its buffer and persist what it became.

        Stops the stream by emitting STOPPED; the driver returns on seeing one.
        """
        part_kind = parts.kinds.pop(event.index, None)
        part_content = parts.contents.pop(event.index, None)
        parts.objects.pop(event.index, None)
        if isinstance(event.part, ToolCallPart) or part_kind == "tool_call":
            async for tool_event in self._emit_tool_call_tokens(event, parts):
                yield tool_event
                if tool_event.type is AgentEventType.STOPPED:
                    return
        # A stop arriving during the drain is held until the part has been
        # persisted. `_emit_token_chunks` ends its stream by emitting STOPPED,
        # and returning on that landed the stop between "these tokens were
        # shown to the user" and "this part became a message" -- the one gap
        # where honouring it destroys work. Pressing Stop mid-answer therefore
        # discarded the answer being read, and the client cleared it from the
        # transcript. The part is the atomic unit; the stop waits for it.
        pending_stop: AgentEvent | None = None
        async for token_event in self._emit_token_chunks(
            parts.drain_all_token_buffers(force=True),
        ):
            if token_event.type is AgentEventType.STOPPED:
                pending_stop = token_event
                break
            yield token_event
        message = parts.completed_part_message(
            part=event.part,
            part_kind=part_kind,
            part_content=part_content,
        )
        if message is not None:
            yield AgentEvent(
                type=AgentEventType.MESSAGE,
                data=message,
                agent_run_id=self.agent_run_id,
            )
        if pending_stop is not None:
            yield pending_stop
        elif await self.stop_requested():
            yield self.stopped_event()
        return

    async def _emit_tool_call_tokens(
        self,
        event,
        parts: StreamingParts,
    ) -> AsyncIterator[AgentEvent]:
        """Stream a completed tool call's name and arguments as tokens."""
        for token_chunk in parts.start_tool_stream(
            event.index,
            getattr(event.part, "tool_name", None)
            or parts.tool_names.get(event.index, ""),
        ):
            yield AgentEvent(
                type=AgentEventType.TOKEN,
                data=token_chunk,
                agent_run_id=self.agent_run_id,
            )
            if await self.stop_requested():
                yield self.stopped_event()
                return
        if event.index not in parts.tool_stream_has_args:
            final_args = _tool_call_args_text(getattr(event.part, "args", None))
            for token_chunk in parts.append_token_text("tool", final_args or "{}"):
                yield AgentEvent(
                    type=AgentEventType.TOKEN,
                    data=token_chunk,
                    agent_run_id=self.agent_run_id,
                )
                if await self.stop_requested():
                    yield self.stopped_event()
                    return
        async for token_event in self._emit_token_chunks(
            parts.append_token_text("tool", "}"),
        ):
            yield token_event
        parts.forget_tool_part(event.index)

    async def _stream_final_result(
        self,
        parts: StreamingParts,
    ) -> AsyncIterator[AgentEvent]:
        """The model reached its final result.

        Stops the stream by emitting STOPPED; the driver returns on seeing one.
        """
        async for token_event in self._emit_token_chunks(
            parts.drain_all_token_buffers(force=True),
        ):
            yield token_event

    async def _stream_tool_calls(
        self,
        node,
        run,
        *,
        conversation_id: UUID,
        malformed_tool_call_ids: set[str],
        emitted_tool_response_ids: set[str],
    ) -> AsyncIterator[AgentEvent]:
        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, FunctionToolCallEvent):
                    continue
                if isinstance(event, FunctionToolResultEvent):
                    result_part = event.part
                    if result_part.tool_call_id in malformed_tool_call_ids:
                        logger.debug(
                            "agent.pydantic_ai.skipping_tool_result_malformed_call.diagnostic",
                            tool_call_id=result_part.tool_call_id,
                        )
                        continue
                    tool_output = result_part.content
                    if isinstance(tool_output, BinaryContent):
                        # Images are not carried into later runs -- only this
                        # stub is persisted. Say so in words the model reads,
                        # rather than leaving it to infer from a shape: a bare
                        # `{"type": "binary_content"}` beside a successful
                        # result reads as "you have seen this", and the model
                        # then answers questions about a picture it can no
                        # longer see.
                        tool_output = {
                            "type": "binary_content",
                            "media_type": tool_output.media_type,
                            "size_bytes": len(tool_output.data)
                            if tool_output.data
                            else 0,
                            "note": (
                                "An image was shown to you here when this tool "
                                "ran. It is not carried into later turns, so it "
                                "is no longer in your context -- open it again "
                                "if you need to look at it."
                            ),
                        }
                    elif hasattr(tool_output, "model_dump"):
                        tool_output = to_json_value(tool_output)
                    else:
                        tool_output = to_json_value(tool_output)

                    emitted_tool_response_ids.add(result_part.tool_call_id)
                    yield AgentEvent(
                        type=AgentEventType.MESSAGE,
                        data=MessageDraft.of_tool_return(
                            tool_name=result_part.tool_name or "unknown_tool",
                            tool_call_id=result_part.tool_call_id,
                            tool_result=tool_output,
                            metadata={
                                "tool_name": result_part.tool_name or "unknown_tool"
                            },
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                    if await self.stop_requested():
                        yield self.stopped_event()
                        return

    async def stop_requested(self) -> bool:
        """Whether the user has asked this run to stop.

        Keeps going when the checker fails, on purpose: the checker is a
        database read, and a run that cannot be asked whether to stop should
        keep going rather than stop for a reason nobody chose.

        What is *not* on purpose is doing that silently. A checker that fails
        every time means the stop button does nothing, and the failure it is
        answering "no" from is exactly the thing an operator needs to see.
        """
        if self.should_stop is None:
            return False
        try:
            return await self.should_stop()
        except Exception:
            logger.error(
                "agent.pydantic_ai_streaming.stop_check.failed",
                agent_run_id=str(self.agent_run_id),
                exc_info=True,
            )
            return False

    def stopped_event(self) -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.STOPPED,
            data={"reason": "stop_requested"},
            agent_run_id=self.agent_run_id,
        )
