"""Walking one pydantic-ai run, node by node, into a queue of agent events.

A run is a graph: request the model, call the tools it asked for, request again,
end. This drives that graph once and turns each node into events. Once, because
a mid-stream drop is retried by re-driving from a snapshot rather than by
resuming a half-finished node -- so this has to be callable repeatedly against
the same conversation without doubling anything.

Two rules make that safe, and both are easy to break by accident:

* **A model response is persisted all-or-nothing.** Parts that finish before the
  stream dies are not yet in `run.all_messages()`, so writing them as they
  arrive would duplicate them when the retry replays the node. Tokens still
  stream live; only the durable write waits for the node to finish.
* **The snapshot is taken before streaming, and includes `node.request`.** That
  request does not enter `all_messages()` until it is actually sent, and without
  it the resume history ends on a `ModelResponse` whose tool calls have no
  results -- at which point pydantic-ai, correctly, runs those tools again. That
  is how a retry would charge a card twice.

Everything reaches the caller through an `asyncio.Queue` rather than by yielding,
because the whole loop runs in a child task: `agent.iter()` enters anyio cancel
scopes bound to the task that created them, and unwinding them anywhere else
crashes the worker.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pydantic_ai import ModelRequestNode, CallToolsNode
from pydantic_ai.messages import ModelMessage
from pydantic_ai.run import AgentRun
from pydantic_ai.result import FinalResult
from pydantic_graph import End
from uuid import UUID

from pydantic_ai import Agent as PydanticAIAgent

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunUsage,
    HarnessOptions,
    MessageDraft,
    to_json_value,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_streaming import (
    ModelRequestStreamer,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_usage import (
    usage_totals,
)

logger = get_logger(__name__)


class FinalOutputWriter(Protocol):
    def __call__(
        self, *, output: object, tool_name: str | None, tool_call_id: str | None
    ) -> MessageDraft | None: ...


class NodeLoop[DepsT]:
    """Drives one attempt at a pydantic-ai run into an event queue."""

    def __init__(
        self,
        *,
        run_context: Callable[
            [list[ModelMessage] | None],
            AbstractAsyncContextManager[AgentRun[DepsT, object]],
        ],
        queue: asyncio.Queue[tuple[str, object]],
        streamer: ModelRequestStreamer,
        options: HarnessOptions[DepsT],
        agent_run_id: UUID,
        conversation_id: UUID,
        final_output_message: FinalOutputWriter,
    ) -> None:
        self.run_context = run_context
        self.queue = queue
        self.streamer = streamer
        self.options = options
        self.agent_run_id = agent_run_id
        self.conversation_id = conversation_id
        self.final_output_message = final_output_message
        # Both sets live for the whole run rather than one attempt: a tool
        # already reported must not be reported again after a retry.
        self.malformed_tool_call_ids: set[str] = set()
        self.emitted_tool_response_ids: set[str] = set()
        # Set per attempt by `drive_once`, so a terminal branch can bill without
        # the run being threaded down into every pump method.
        self._run: AgentRun[DepsT, object] | None = None
        self._carried_usage: dict[str, int] = {}
        self._usage_emitted = False

    async def drive_once(
        self,
        resume_history: list[ModelMessage] | None,
        state: dict[str, object],
        carried_usage: dict[str, int],
    ) -> None:
        async with self.run_context(resume_history) as run:
            # Published before the node loop so the retry handler can read
            # usage off the run that just failed.
            state["run"] = run
            self._run, self._carried_usage = run, carried_usage
            self._usage_emitted = False
            async for node in run:
                if PydanticAIAgent.is_model_request_node(node):
                    if await self._pump_model_request(node, run, state):
                        return
                elif PydanticAIAgent.is_call_tools_node(node):
                    if await self._pump_tool_calls(node, run):
                        return
                elif PydanticAIAgent.is_end_node(node):
                    if await self._pump_end_node(node):
                        return

            # The run finished on its own. The other exits bill for themselves:
            # a terminal event bills before queueing it (the pump finalizes on
            # sight of one and drops whatever follows), and a fatal exception
            # bills in `drive_with_retry` before it re-raises.
            self.emit_usage()

    def emit_usage(self) -> None:
        """Report what this attempt spent, including attempts already abandoned.

        Must reach the queue BEFORE any terminal event. `RunEventPump` finalizes
        the run the moment it sees one and drops everything after it, so usage
        queued afterwards is read by nobody and the run bills zero.

        Idempotent per attempt: the terminal branches call it explicitly and the
        `finally` calls it again for the paths that raise instead of ending.

        `put_nowait` rather than `await put`: the `finally` may already be
        unwinding a cancellation, where awaiting can raise before the event
        lands. The queue is unbounded, so it cannot be full.
        """
        if self._usage_emitted or self._run is None:
            return
        self._usage_emitted = True
        totals = usage_totals(self._run.usage, self._carried_usage)
        self.queue.put_nowait(
            (
                "event",
                AgentEvent(
                    type=AgentEventType.USAGE,
                    data=AgentRunUsage(
                        model_name=self.options.model_name,
                        usage_kind="llm",
                        input_tokens=totals["input_tokens"],
                        output_tokens=totals["output_tokens"],
                        request_count=totals["requests"],
                        tool_call_count=totals["tool_calls"],
                        metadata={
                            "cache_write_tokens": totals["cache_write_tokens"],
                            "cache_read_tokens": totals["cache_read_tokens"],
                            "input_audio_tokens": totals["input_audio_tokens"],
                            "output_audio_tokens": totals["output_audio_tokens"],
                        },
                    ),
                    agent_run_id=self.agent_run_id,
                ),
            )
        )

    async def _pump_model_request(
        self,
        node: ModelRequestNode[DepsT, object],
        run: AgentRun[DepsT, object],
        state: dict[str, object],
    ) -> bool:
        """Stream one model request, holding its messages until the node completes.

        True when the caller should stop consuming nodes."""
        # Snapshot BEFORE streaming, because after a
        # mid-stream failure `all_messages()` has grown a
        # truncated ModelResponse plus an empty
        # ModelRequest: resuming from that makes the model
        # continue an answer whose first half we threw away.
        #
        # `node.request` must be appended explicitly. It is
        # the request about to be sent (for a post-tool node,
        # the one carrying the ToolReturnParts) and it does
        # not enter `all_messages()` until the request
        # actually happens. Without it the resume history
        # ends on a ModelResponse whose tool calls have no
        # results, and pydantic-ai — correctly — executes
        # those tools again. That is how a retry would
        # double-charge a card.
        snapshot = list(run.all_messages())
        pending = getattr(node, "request", None)
        if pending is not None and (not snapshot or snapshot[-1] is not pending):
            snapshot.append(pending)
        state["resume_from"] = snapshot
        # A model response is persisted all-or-nothing. Parts
        # that finish before the stream dies are NOT in
        # `run.all_messages()` (pydantic-ai appends the
        # ModelResponse only once the node completes), so
        # writing them as they arrive would duplicate them on
        # resume. Tokens still stream live; only the durable
        # write waits for the node to finish.
        buffered: list[AgentEvent] = []
        async for event in self.streamer._stream_model_request(
            node,
            run,
            malformed_tool_call_ids=self.malformed_tool_call_ids,
        ):
            if event.type is AgentEventType.MESSAGE:
                buffered.append(event)
                continue
            if event.type in {
                AgentEventType.ERROR,
                AgentEventType.STOPPED,
            }:
                # A stop is a real ending, not a lost response:
                # flush what the model already produced before
                # the terminal event so the user keeps it.
                for held in buffered:
                    await self.queue.put(("event", held))
                self.emit_usage()
                await self.queue.put(("event", event))
                return True
            await self.queue.put(("event", event))
        for held in buffered:
            await self.queue.put(("event", held))
        return False

    async def _pump_tool_calls(
        self, node: CallToolsNode[DepsT, object], run: AgentRun[DepsT, object]
    ) -> bool:
        """Relay the tool calls and returns this node produced."""
        async for event in self.streamer._stream_tool_calls(
            node,
            run,
            conversation_id=self.conversation_id,
            malformed_tool_call_ids=self.malformed_tool_call_ids,
            emitted_tool_response_ids=self.emitted_tool_response_ids,
        ):
            terminal = event.type in {
                AgentEventType.ERROR,
                AgentEventType.STOPPED,
            }
            if terminal:
                self.emit_usage()
            await self.queue.put(("event", event))
            if terminal:
                return True
        return False

    async def _pump_end_node(self, node: End[FinalResult[object]]) -> bool:
        """Emit whatever the run ended with."""
        if node.data.tool_call_id:
            if node.data.tool_call_id not in self.emitted_tool_response_ids:
                await self.queue.put(
                    (
                        "event",
                        AgentEvent(
                            type=AgentEventType.MESSAGE,
                            data=MessageDraft.of_tool_return(
                                tool_name=node.data.tool_name or "unknown_tool",
                                tool_call_id=node.data.tool_call_id,
                                tool_result=to_json_value(node.data.output),
                                metadata={
                                    "tool_name": node.data.tool_name or "unknown_tool"
                                },
                            ),
                            agent_run_id=self.agent_run_id,
                        ),
                    )
                )
                if await self.streamer.stop_requested():
                    self.emit_usage()
                    await self.queue.put(("event", self.streamer.stopped_event()))
                    return True
            final_message = self.final_output_message(
                output=node.data.output,
                tool_name=node.data.tool_name,
                tool_call_id=node.data.tool_call_id,
            )
            if final_message is not None:
                await self.queue.put(
                    (
                        "event",
                        AgentEvent(
                            type=AgentEventType.MESSAGE,
                            data=final_message,
                            agent_run_id=self.agent_run_id,
                        ),
                    )
                )
                if await self.streamer.stop_requested():
                    self.emit_usage()
                    await self.queue.put(("event", self.streamer.stopped_event()))
                    return True

        elif self.options.output_type is not None:
            final_message = self.final_output_message(
                output=node.data.output,
                tool_name=None,
                tool_call_id=None,
            )
            if final_message is not None:
                await self.queue.put(
                    (
                        "event",
                        AgentEvent(
                            type=AgentEventType.MESSAGE,
                            data=final_message,
                            agent_run_id=self.agent_run_id,
                        ),
                    )
                )
                if await self.streamer.stop_requested():
                    self.emit_usage()
                    await self.queue.put(("event", self.streamer.stopped_event()))
                    return True
        return False
