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
import time
from typing import Protocol
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pydantic_ai import CallToolsNode, DeferredToolRequests, ModelRequestNode
from pydantic_ai.messages import ModelMessage
from pydantic_ai.run import AgentRun
from pydantic_ai.result import FinalResult
from pydantic_graph import End
from uuid import UUID

from pydantic_ai import Agent as PydanticAIAgent


from app.core.log.log import get_logger
from app.modules.agent.domain.run_budget_pause import (
    budget_pause_events,
    budget_pause_tool_call_id,
)
from app.modules.agent.domain.pauses import CALL_NAMES
from app.modules.agent.infrastructure.harness.lemma.tool_returns import waiting_event
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.tools.tool_errors import result_is_failure

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
    MessageKind,
    to_json_value,
)
from app.modules.agent.infrastructure.harness.lemma.model_streaming import (
    ModelRequestStreamer,
)
from app.modules.agent.infrastructure.harness.lemma.node_events import (
    _canonical,
    _is_open_answer,
    _not_final,
    settle_response_answers,
    unique_stored_calls,
)
from app.modules.agent.infrastructure.harness.lemma.run_log import (
    RunLogRecorder,
    budget_pause_response,
)
from app.modules.agent.infrastructure.harness.lemma.usage import (
    warn_if_implausible_usage,
)
from app.modules.agent.services.run_input_settings import shorter_answer_settings

logger = get_logger(__name__)

__all__ = ["NodeLoop", "settle_response_answers", "unique_stored_calls"]


#: Fraction of the run's wall-clock budget past which an answer that ran out of
#: time is not tried again.
_LATE_IN_THE_RUN = 0.9


class FinalOutputWriter(Protocol):
    def __call__(
        self, *, output: object, tool_name: str | None, tool_call_id: str | None
    ) -> MessageDraft | None:
        """Persist final output when it produces a user-facing message."""


class NodeLoop[DepsT]:
    """Drives one attempt at a pydantic-ai run into an event queue."""

    def __init__(
        self,
        *,
        run_context: Callable[
            [list[ModelMessage] | None, JsonObject | None],
            AbstractAsyncContextManager[AgentRun[DepsT, object]],
        ],
        queue: asyncio.Queue[tuple[str, object]],
        streamer: ModelRequestStreamer,
        options: HarnessOptions,
        agent_run_id: UUID,
        conversation_id: UUID,
        final_output_message: FinalOutputWriter,
        structured_output: bool = False,
        recorder: RunLogRecorder | None = None,
    ) -> None:
        self.run_context = run_context
        #: Writes each finished step to the run log; None for a run that keeps
        #: none (a private note's, or one handed no log).
        self.recorder = recorder
        #: The run owes a final answer through an output tool.
        self.structured_output = structured_output
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
        #: Each call's arguments as canonical JSON, by call id, until its return
        #: arrives: what tells a repeated call from a different one.
        self._call_args: dict[str, str] = {}
        # The run's own clock, not the attempt's: a retry that re-enters the
        # graph is the same run to the person waiting for it.
        self._started_at = time.monotonic()
        # The model settings the current attempt runs under, when a retry
        # changed them; None means the run's own.
        self._attempt_settings: JsonObject | None = None
        # A text-only response's closing text, held until the next node says
        # whether the run ends on it (`settle_response_answers`).
        self._undecided: list[AgentEvent] = []
        #: What a budget pause adds to the log after the run's own messages.
        self._paused_with: list[ModelMessage] = []

    def shorter_answer_settings(self) -> JsonObject | None:
        """Settings for one more try at an answer that ran out of time, or None.

        None once the run is nearly out of wall clock: another exchange as long
        as the one that just expired would carry it past its budget, and the
        pause that budget promises cannot interrupt a request in flight.
        """
        spend = self.options.spend
        wall_clock = spend.budget.wall_clock_seconds if spend is not None else 0
        elapsed = time.monotonic() - self._started_at
        if wall_clock > 0 and elapsed >= _LATE_IN_THE_RUN * wall_clock:
            return None
        return shorter_answer_settings(
            self._attempt_settings
            if self._attempt_settings is not None
            else self.options.model_settings,
            lighter_reasoning=self.options.lighter_reasoning_settings,
        )

    async def drive_once(
        self,
        resume_history: list[ModelMessage] | None,
        state: dict[str, object],
        *,
        model_settings: JsonObject | None = None,
    ) -> None:
        if model_settings is not None:
            self._attempt_settings = model_settings
        async with self.run_context(resume_history, self._attempt_settings) as run:
            # Published before the node loop: the retry handler only re-enters
            # an attempt that actually got as far as starting a run.
            state["run"] = run
            # A resumed attempt opens by re-asking the request that dropped.
            # That request was counted when it was first made; counting it
            # again would let a flaky connection spend the run's allowance.
            already_counted = resume_history is not None
            # The run's messages are logged only from a clean boundary: a
            # dropped stream leaves a truncated response behind, which the
            # retry replaces, and a stop leaves an unfinished one.
            clean = False
            try:
                async for node in run:
                    # The node before this one has finished: log what it added.
                    await self._log(run)
                    if PydanticAIAgent.is_model_request_node(node):
                        # Another request: the run went on past that answer.
                        await self._release_undecided(final=False)
                        # Counted before the request, not after: the ceiling is
                        # on what the run is about to spend, and a check that
                        # only ran afterwards would always allow one call past
                        # it.
                        if already_counted:
                            already_counted = False
                        elif await self._spend_a_model_request(node):
                            # Out of allowance: stopped before the request,
                            # waiting on a person to say whether to go on.
                            clean = True
                            return
                        if await self._pump_model_request(node, run, state):
                            return
                    elif PydanticAIAgent.is_call_tools_node(node):
                        if await self._pump_tool_calls(node, run):
                            return
                    elif PydanticAIAgent.is_end_node(node):
                        await self._release_undecided(final=True)
                        if await self._pump_end_node(node):
                            return
                clean = True
            finally:
                # However the attempt ended, what the model said is kept, and
                # nothing came after it.
                await self._release_undecided(final=True)
                if clean:
                    await self._log(run, then=self._paused_with, final=True)
                # Billing is the metered model's, per request; this only says
                # so when a provider reports a count that cannot be true.
                warn_if_implausible_usage(getattr(run, "usage", None))

    async def _log(
        self,
        run: AgentRun[DepsT, object],
        *,
        then: Sequence[ModelMessage] = (),
        final: bool = False,
    ) -> None:
        """Queue the run-log steps that bring the log up to what ``run`` holds."""
        if self.recorder is None:
            return
        for draft in self.recorder.take([*run.all_messages(), *then], final=final):
            await self.queue.put(
                (
                    "event",
                    AgentEvent(
                        type=AgentEventType.RUN_STEP,
                        data=draft,
                        agent_run_id=self.agent_run_id,
                    ),
                )
            )

    async def _release_undecided(self, *, final: bool) -> None:
        """Hand on the held closing text, final or not as the run turned out."""
        held, self._undecided = self._undecided, []
        for event in held:
            settled = (
                event if final or not _is_open_answer(event) else _not_final(event)
            )
            await self.queue.put(("event", settled))

    async def _spend_a_model_request(
        self, node: ModelRequestNode[DepsT, object] | None = None
    ) -> bool:
        """Count this step; True when it has spent the run's allowance.

        Then the run stops before the request, with no exception: it queues
        the approval it asks instead and the WAITING terminal, and the harness
        leaves that call open for its real return, as it does every pause's.
        """
        spend = self.options.spend
        if spend is None:
            return False
        spend.record_model_request()
        elapsed_seconds = time.monotonic() - self._started_at
        exhausted = spend.exhausted(elapsed_seconds=elapsed_seconds)
        if exhausted is None:
            # Not there yet, but possibly close enough to say so. Posted to
            # the run's mailbox and delivered into the very request this step
            # is about to make, so the run hears it while it still has room to
            # act on it.
            warning = spend.approaching(
                elapsed_seconds=elapsed_seconds,
                at=agent_settings.agent_run_warn_at,
            )
            if warning is not None:
                self.options.notices.post(warning.notice)
                logger.info(
                    "agent.run_budget.approaching.observed",
                    agent_run_id=str(self.agent_run_id),
                    dimension=warning.dimension.value,
                )
            return False

        tool_call_id = budget_pause_tool_call_id()
        events = budget_pause_events(
            agent_run_id=self.agent_run_id,
            tool_call_id=tool_call_id,
            exhausted=exhausted,
            sequence=0,
        )
        for event in events:
            await self.queue.put(("event", event))
        # The log ends where the run stopped: the request it was about to send,
        # and the approval it asks instead -- answered when the person decides.
        pending = [node.request] if node is not None else []
        self._paused_with = [*pending, *budget_pause_response(events)]
        logger.info(
            "agent.run_budget.exhausted.observed",
            agent_run_id=str(self.agent_run_id),
            dimension=exhausted.dimension.value,
        )
        await self.queue.put(
            (
                "event",
                waiting_event(
                    [(tool_call_id, CALL_NAMES[PauseKind.BUDGET])],
                    conversation_id=str(self.conversation_id),
                    agent_run_id=self.agent_run_id,
                ),
            )
        )
        return True

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
                await self.queue.put(("event", event))
                return True
            await self.queue.put(("event", event))
        buffered = unique_stored_calls(buffered)
        settled, self._undecided = settle_response_answers(buffered)
        for held in settled:
            self._remember_tool_call(held)
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
            if event.type is AgentEventType.MESSAGE:
                # Tools ran after a response with none to call: a retry, so
                # the run goes on and that response's text was not its answer.
                await self._release_undecided(final=False)
            self._record_tool_outcome(event)
            terminal = event.type in {
                AgentEventType.ERROR,
                AgentEventType.STOPPED,
            }
            await self.queue.put(("event", event))
            if terminal:
                return True
        return False

    def _waiting(self, deferred: DeferredToolRequests) -> AgentEvent:
        calls = [*deferred.calls, *deferred.approvals]
        return waiting_event(
            [
                (
                    call.tool_call_id,
                    str(deferred.metadata.get(call.tool_call_id, {}).get("kind", "")),
                )
                for call in calls
            ],
            conversation_id=str(self.conversation_id),
            agent_run_id=self.agent_run_id,
        )

    def _remember_tool_call(self, event: AgentEvent) -> None:
        draft = event.data
        if event.type is not AgentEventType.MESSAGE:
            return
        if getattr(draft, "kind", None) is not MessageKind.TOOL_CALL:
            return
        call_id = getattr(draft, "tool_call_id", None)
        if call_id:
            self._call_args[call_id] = _canonical(getattr(draft, "tool_args", None))

    def _record_tool_outcome(self, event: AgentEvent) -> None:
        """Track the failing streak, from the returns the run is already emitting.

        Read here rather than counted inside `GracefulToolset`, which is where
        the verdict is computed: that object is built per toolset by the
        capability assembler, nowhere near the run, and threading a per-run
        counter down to it would mean every toolset construction learning about
        budgets. The returns pass through here anyway.
        """
        spend = self.options.spend
        if spend is None:
            return
        draft = event.data
        if event.type is not AgentEventType.MESSAGE:
            return
        if getattr(draft, "kind", None) is not MessageKind.TOOL_RETURN:
            return
        result = getattr(draft, "tool_result", None)
        spend.record_tool_outcome(failed=result_is_failure(result))
        spend.record_tool_call(
            tool=str(getattr(draft, "tool_name", None) or ""),
            args=self._call_args.pop(str(getattr(draft, "tool_call_id", "")), ""),
            result=_canonical(result),
        )

    async def _pump_end_node(self, node: End[FinalResult[object]]) -> bool:
        """Emit whatever the run ended with."""
        deferred = node.data.output
        if isinstance(deferred, DeferredToolRequests):
            # Paused: every pausing call of the step is written, and every
            # other call of it has returned. The run waits on all of them.
            await self.queue.put(("event", self._waiting(deferred)))
            return True
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
                    await self.queue.put(("event", self.streamer.stopped_event()))
                    return True

        elif self.structured_output:
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
                    await self.queue.put(("event", self.streamer.stopped_event()))
                    return True
        return False
