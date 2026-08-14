"""PydanticAI harness implementation."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from typing import AsyncIterator, Sequence
from uuid import UUID

import anyio

from app.modules.agent.infrastructure.harnesses.mock_model import (
    build_mock_model,
    is_mock_llm_enabled,
)
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai import BinaryContent, FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    FinalResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunUsage,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    MessageDraft,
    to_json_value,
)
from pydantic_ai.capabilities import ProcessHistory

from app.modules.agent.infrastructure.harnesses.history import build_history_processors
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
    parse_tool_call_args,
)
from app.modules.agent.infrastructure.harnesses.provider_error_log import (
    log_model_http_error,
)
from app.modules.agent.infrastructure.harnesses.streaming import CharStreamBuffer
from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
    retry_after_seconds,
)
from app.modules.agent.config import agent_settings
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
)
from app.modules.agent.services.summarization_model import (
    resolve_summarization_model,
)
from app.modules.agent.tools.final_answer.final_answer_text import final_answer_text
from app.modules.agent.tools.final_answer.final_answer_tool import FinalAgentResult
from app.modules.agent.tools.tool_errors import AgentInputRequired
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)
StopChecker = Callable[[], Awaitable[bool]]


class HarnessDriverCancelled(Exception):
    """The graph-driving task was cancelled while the run was still healthy.

    An ordinary `Exception` rather than a `CancelledError` subclass on purpose:
    nothing about *this* task is being cancelled, so every `except
    CancelledError` between here and the runner would draw the wrong conclusion
    and re-raise a cancellation that isn't happening. Raised as a failure
    because that is what it is — the graph stopped mid-node and whatever tool
    was executing was cancelled with it.
    """


def _user_facing_error_message(exc: Exception) -> str:
    """Return a sanitized, actionable message for the UI.

    Never forward raw provider exception text (which may contain API keys,
    request headers, or model-internal details) into user-visible payloads.
    """
    if isinstance(exc, ModelHTTPError):
        # These three are all "the provider said no", but they need different
        # things from the reader: wait, top up, or fix the config. A single
        # generic message sends people to the wrong place.
        if exc.status_code == 429:
            return (
                "The model provider is rate limiting this workspace (HTTP 429). "
                "The request was retried a few times without success — try "
                "again shortly."
            )
        if exc.status_code == 402:
            return (
                "The model provider rejected the request for billing reasons "
                "(HTTP 402). Please check the provider account's credit or quota."
            )
        if exc.status_code >= 500:
            return (
                f"The model provider is having trouble (HTTP {exc.status_code}) "
                "and the request was retried without success. Nothing you sent "
                "was lost — try again shortly."
            )
        return (
            f"The model provider returned an error (HTTP {exc.status_code}). "
            "Please check the agent runtime configuration."
        )
    if is_retryable_stream_error(exc):
        # A transport-level drop that survived every retry. Nothing was lost —
        # each completed message was persisted — so say so, because "check the
        # configuration" sends people hunting a bug that isn't theirs.
        return (
            "The connection to the model provider kept dropping. Nothing you "
            "sent was lost — send another message, or press Retry to pick up "
            "where it stopped."
        )
    if isinstance(exc, UnexpectedModelBehavior):
        return (
            "A tool failed repeatedly after several attempts and the run was "
            "stopped. Please check the agent configuration."
        )
    if isinstance(exc, UsageLimitExceeded):
        return (
            "The agent run hit a usage limit. "
            "Please check the agent runtime configuration."
        )
    if isinstance(exc, HarnessDriverCancelled):
        # Whatever the agent was doing stopped part-way, so "try again" is the
        # honest advice. Everything it finished before that point is persisted.
        return (
            "The agent stopped part-way through a step and could not finish. "
            "Nothing you sent was lost — send another message, or press Retry "
            "to pick up where it stopped."
        )
    return (
        "The model provider returned an error. "
        "Please check the agent runtime configuration."
    )


# Per-tool retry budget for the in-process agent. pydantic-ai defaults to 1, which
# turns a single bad/invalid tool call (e.g. arguments that fail schema validation)
# into a fatal run. 5 gives the model several chances to self-correct from the
# validation feedback before the run gives up. Execution errors are handled
# separately by GracefulToolset and never consume this budget.
DEFAULT_TOOL_RETRIES = 5

# Ceiling for the pause between stream-drop retries; see `_retry_backoff`.
_RETRY_BACKOFF_CAP_SECONDS = 6.0


def _reraise_driver_failure(
    pending_error: BaseException | None,
    *,
    cancelled_by_us: bool,
    agent_run_id: UUID,
) -> None:
    """Decide what a failure relayed out of the driver task means.

    A real error is re-raised so ``run()``'s handlers (ModelHTTPError,
    UsageLimitExceeded, AgentInputRequired, …) still fire.

    A relayed ``CancelledError`` is only ours to drop when we are the ones who
    cancelled — either our own cancellation is already propagating out of the
    consumer loop, or we tore the driver down on the way out. Otherwise the
    driver died under a healthy parent: the graph stopped mid-node, so any tool
    still executing was cancelled with it and its call will never get a result.

    Dropping that silently is how a truncated run came to report success. The
    generator returned normally, so ``run()`` emitted COMPLETED and the run was
    finalized with no error, no log line, and a trailing tool call that nothing
    ever answered — the agent simply stopped, and every layer above said it went
    fine.
    """

    if pending_error is None:
        return
    if not isinstance(pending_error, asyncio.CancelledError):
        raise pending_error
    if cancelled_by_us:
        return
    logger.error(
        "agent.pydantic_ai.driver_cancelled_mid_run.failed",
        agent_run_id=str(agent_run_id),
        exc_info=pending_error,
    )
    raise HarnessDriverCancelled(
        "The agent run was cancelled while a tool call was still running."
    ) from pending_error


async def _drive_with_retry(
    drive_once,
    *,
    queue,
    max_attempts: int,
    stream_reset,
    stopped,
    should_stop,
) -> None:
    """Run one agent-graph pass, re-entering it after a transient stream drop.

    Lives at module level rather than nested in `_execute` so the retry policy
    can be read — and reasoned about — without the several hundred lines of
    streaming machinery it sits inside.

    `drive_once` publishes the live run and a pre-request history snapshot into
    the `state` dict it is handed. Both are required to resume: the run carries
    the usage already spent, and the snapshot is the history as it stood
    *before* the failing request, which is what keeps a retry from replaying a
    half-written response or re-executing a completed tool.
    """
    carried_usage: dict[str, int] = {}
    resume_history: list[object] | None = None
    attempt = 0
    while True:
        state: dict[str, object] = {}
        try:
            await drive_once(resume_history, state, carried_usage)
            return
        # Deliberately `Exception`, not `BaseException`: a CancelledError must
        # reach the caller untouched so pydantic-graph's scopes unwind in this
        # task. It would have been re-raised here anyway —
        # `is_retryable_stream_error` never retries cancellation — so letting it
        # skip the handler entirely is the same behaviour with less to reason
        # about.
        except Exception as exc:  # noqa: BLE001 — re-raised below when fatal
            run = state.get("run")
            snapshot = state.get("resume_from")
            if (
                run is None
                or snapshot is None
                or attempt + 1 >= max_attempts
                or not is_retryable_stream_error(exc)
            ):
                raise
            # Resuming re-asks only the request that failed. Completed tool
            # results are replayed from the snapshot rather than re-executed,
            # so no tool ever runs twice.
            _accumulate_usage(carried_usage, getattr(run, "usage", None))
            resume_history = list(snapshot)  # type: ignore[arg-type]
            attempt += 1
            logger.warning(
                "agent.pydantic_ai.model_stream_retry.degraded",
                attempt=attempt,
                max_attempts=max_attempts,
                error_type=type(exc).__name__,
            )
            # Tell the client to drop the half-streamed bubble; the replacement
            # response streams from scratch.
            await queue.put(("event", stream_reset()))
            if await should_stop():
                await queue.put(("event", stopped()))
                return
            await asyncio.sleep(retry_after_seconds(exc) or _retry_backoff(attempt))


class PydanticAIHarness:
    """Execute an agent through PydanticAI and emit domain events."""

    kind = HarnessKind.LEMMA

    def __init__(self, *, emit_tokens: bool = True):
        self.emit_tokens = emit_tokens

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        malformed_tool_call_ids: set[str] = set()
        emitted_tool_response_ids: set[str] = set()
        terminal_event_seen = False

        try:
            async for event in self._execute(
                agent=agent,
                conversation=conversation,
                messages=messages,
                ctx=ctx,
                options=options,
                agent_run_id=agent_run_id,
                malformed_tool_call_ids=malformed_tool_call_ids,
                emitted_tool_response_ids=emitted_tool_response_ids,
                should_stop=options.should_stop,
            ):
                yield event
                if event.type in {AgentEventType.ERROR, AgentEventType.STOPPED}:
                    terminal_event_seen = True
        except ModelHTTPError as exc:
            log_model_http_error(
                exc,
                agent_run_id=agent_run_id,
                model_name=getattr(options, "model_name", None),
            )
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=_user_facing_error_message(exc),
                agent_run_id=agent_run_id,
            )
            return
        except UnexpectedModelBehavior as exc:
            # Reached only when a tool genuinely failed every retry (default 5) —
            # GracefulToolset turns ordinary execution errors into tool responses,
            # so this is the rare "model kept sending invalid arguments" case.
            logger.debug(
                'agent.pydantic_ai.agent_run_ended_after_repeated.diagnostic',
                exc_info=True,
            )
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=_user_facing_error_message(exc),
                agent_run_id=agent_run_id,
            )
            return
        except UsageLimitExceeded as exc:
            logger.warning(
                "agent.pydantic_ai.agent_run_hit_usage_limit.degraded", exc_info=True
            )
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=_user_facing_error_message(exc),
                agent_run_id=agent_run_id,
            )
            return
        except AgentInputRequired as exc:
            # The agent called ask_user / request_approval: pause the run cleanly
            # rather than failing. The tool call is already persisted; the runner
            # finishes this run and flips the conversation to WAITING. The user's
            # submission later starts a fresh run that resumes from history.
            logger.debug(
                "agent.pydantic_ai.agent_input_required_kind_call.observed",
                tool_call_id=exc.tool_call_id,
            )
            yield AgentEvent(
                type=AgentEventType.WAITING,
                data={
                    "tool_call_id": exc.tool_call_id,
                    "kind": exc.kind,
                    "conversation_id": str(conversation.id),
                },
                agent_run_id=agent_run_id,
            )
            return
        except Exception as exc:
            logger.error("agent.pydantic_ai.pydanticai_harness_type.failed", exc_info=True)
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=_user_facing_error_message(exc),
                agent_run_id=agent_run_id,
            )
            return

        if terminal_event_seen:
            return

        yield AgentEvent(
            type=AgentEventType.COMPLETED,
            data={"conversation_id": str(conversation.id)},
            agent_run_id=agent_run_id,
        )

    async def _execute(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
        malformed_tool_call_ids: set[str],
        emitted_tool_response_ids: set[str],
        should_stop: StopChecker | None,
    ) -> AsyncIterator[AgentEvent]:
        history, user_prompt = self._history_and_prompt(messages)
        # e2e mock mode swaps only the model — the rest of the harness (tool
        # execution, streaming, events, persistence) runs for real.
        if is_mock_llm_enabled():
            model = build_mock_model(conversation)
        else:
            model = _runtime_profile_model(options)
        agent_kwargs: dict[str, object] = {
            # Per-toolset prompt fragments (e.g. web search) are contributed by the
            # matching capabilities, so they're suppressed here to avoid duplication.
            "instructions": build_agent_instructions(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
                include_toolset_prompts=False,
            ),
            # A single invalid tool call must not kill the run: give the model room
            # to self-correct from validation feedback before giving up.
            "retries": DEFAULT_TOOL_RETRIES,
            # A TASK conversation returns through the final_answer output tool.
            # With pydantic-ai's v1 "early" strategy, a normal tool emitted in
            # the same model response (notably request_approval and ask_user) is
            # skipped once final_answer validates — so the pause never happens,
            # the approval is never persisted, and the run silently completes
            # instead of waiting for the user. Graceful executes that sibling
            # tool, which is what raises AgentInputRequired and pauses the run.
            "end_strategy": "graceful",
        }
        if options.toolsets:
            agent_kwargs["toolsets"] = options.toolsets
        # History processors ride as ProcessHistory capabilities (the
        # history_processors= kwarg is deprecated in pydantic-ai).
        history_processors = build_history_processors(
            options,
            # Falls back to this run's model when HISTORY_SUMMARIZATION_MODEL is
            # unset, so behaviour is unchanged unless a deployment opts in.
            summarization_model=await resolve_summarization_model(
                organization_id=conversation.organization_id,
                user_id=conversation.user_id,
                fallback=model,
            ),
        )
        capabilities = list(options.capabilities or [])
        capabilities.extend(
            ProcessHistory(processor) for processor in history_processors
        )
        if options.output_type is not None:
            agent_kwargs["output_type"] = options.output_type
        if options.model_settings is not None:
            agent_kwargs["model_settings"] = options.model_settings
        if capabilities:
            agent_kwargs["capabilities"] = capabilities

        pydantic_agent = PydanticAIAgent(model, **agent_kwargs)

        def _new_run_context(resume_history: list[object] | None):
            """Start the graph, either fresh or resuming from recorded messages.

            On a resume the user prompt is deliberately omitted: it is already the
            last message in ``resume_history``, and passing it again would append a
            duplicate turn. pydantic-ai supports resuming without a prompt — the
            same path the approval flow uses.
            """
            iter_kwargs: dict[str, object] = {
                "message_history": (
                    resume_history if resume_history is not None else (history or None)
                ),
                "deps": ctx,
                "usage_limits": options.usage_limits,
            }
            if resume_history is not None or user_prompt is None:
                return pydantic_agent.iter(**iter_kwargs)
            return pydantic_agent.iter(user_prompt, **iter_kwargs)

        # pydantic-ai's agent.iter() enters anyio cancel scopes that are bound to
        # the TASK they're created in. We drive the whole iteration in a CHILD
        # task so those scopes live entirely within it. When a streaq task
        # timeout / worker shutdown cancels us, we cancel the child and
        # pydantic-graph unwinds its scopes *inside the child task* — never
        # touching streaq's own cancel scope in the parent task. Doing the
        # cleanup in the parent (whether via anyio.CancelScope or asyncio.shield)
        # corrupts that shared scope stack and crashes the whole worker with
        # "Attempted to exit a cancel scope that isn't the current task's current
        # cancel scope". Events stream out through a queue.
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        max_stream_attempts = max(1, agent_settings.agent_model_stream_max_attempts)

        async def _drive_once(
            resume_history: list[object] | None,
            state: dict[str, object],
            carried_usage: dict[str, int],
        ) -> None:
            async with _new_run_context(resume_history) as run:
                # Published before the node loop so the retry handler can read
                # usage off the run that just failed.
                state["run"] = run
                async for node in run:
                        if PydanticAIAgent.is_model_request_node(node):
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
                            if pending is not None and (
                                not snapshot or snapshot[-1] is not pending
                            ):
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
                            async for event in self._stream_model_request(
                                node,
                                run,
                                agent_run_id=agent_run_id,
                                malformed_tool_call_ids=malformed_tool_call_ids,
                                should_stop=should_stop,
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
                                        await queue.put(("event", held))
                                    await queue.put(("event", event))
                                    return
                                await queue.put(("event", event))
                            for held in buffered:
                                await queue.put(("event", held))
                        elif PydanticAIAgent.is_call_tools_node(node):
                            async for event in self._stream_tool_calls(
                                node,
                                run,
                                conversation_id=ctx.conversation_id,
                                agent_run_id=agent_run_id,
                                malformed_tool_call_ids=malformed_tool_call_ids,
                                emitted_tool_response_ids=emitted_tool_response_ids,
                                should_stop=should_stop,
                            ):
                                await queue.put(("event", event))
                                if event.type in {
                                    AgentEventType.ERROR,
                                    AgentEventType.STOPPED,
                                }:
                                    return
                        elif PydanticAIAgent.is_end_node(node):
                            if node.data.tool_call_id:
                                if (
                                    node.data.tool_call_id
                                    not in emitted_tool_response_ids
                                ):
                                    await queue.put(
                                        (
                                            "event",
                                            AgentEvent(
                                                type=AgentEventType.MESSAGE,
                                                data=MessageDraft.of_tool_return(
                                                    tool_name=node.data.tool_name
                                                    or "unknown_tool",
                                                    tool_call_id=node.data.tool_call_id,
                                                    tool_result=to_json_value(
                                                        node.data.output
                                                    ),
                                                    metadata={
                                                        "tool_name": node.data.tool_name
                                                        or "unknown_tool"
                                                    },
                                                ),
                                                agent_run_id=agent_run_id,
                                            ),
                                        )
                                    )
                                    if await self._should_stop(should_stop):
                                        await queue.put(
                                            ("event", self._stopped_event(agent_run_id))
                                        )
                                        return
                                final_message = self._final_output_message(
                                    output=node.data.output,
                                    tool_name=node.data.tool_name,
                                    tool_call_id=node.data.tool_call_id,
                                )
                                if final_message is not None:
                                    await queue.put(
                                        (
                                            "event",
                                            AgentEvent(
                                                type=AgentEventType.MESSAGE,
                                                data=final_message,
                                                agent_run_id=agent_run_id,
                                            ),
                                        )
                                    )
                                    if await self._should_stop(should_stop):
                                        await queue.put(
                                            ("event", self._stopped_event(agent_run_id))
                                        )
                                        return

                            elif options.output_type is not None:
                                final_message = self._final_output_message(
                                    output=node.data.output,
                                    tool_name=None,
                                    tool_call_id=None,
                                )
                                if final_message is not None:
                                    await queue.put(
                                        (
                                            "event",
                                            AgentEvent(
                                                type=AgentEventType.MESSAGE,
                                                data=final_message,
                                                agent_run_id=agent_run_id,
                                            ),
                                        )
                                    )
                                    if await self._should_stop(should_stop):
                                        await queue.put(
                                            ("event", self._stopped_event(agent_run_id))
                                        )
                                        return

                # Tokens burned by abandoned attempts are still billed by the
                # provider, so they are carried forward rather than forgotten.
                totals = _usage_totals(run.usage, carried_usage)
                await queue.put(
                    (
                        "event",
                        AgentEvent(
                            type=AgentEventType.USAGE,
                            data=AgentRunUsage(
                                model_name=options.model_name,
                                usage_kind="llm",
                                input_tokens=totals["input_tokens"],
                                output_tokens=totals["output_tokens"],
                                request_count=totals["requests"],
                                tool_call_count=totals["tool_calls"],
                                metadata={
                                    "cache_write_tokens": totals["cache_write_tokens"],
                                    "cache_read_tokens": totals["cache_read_tokens"],
                                    "input_audio_tokens": totals["input_audio_tokens"],
                                    "output_audio_tokens": totals[
                                        "output_audio_tokens"
                                    ],
                                },
                            ),
                            agent_run_id=agent_run_id,
                        ),
                    )
                )

        async def _drive() -> None:
            try:
                await _drive_with_retry(
                    _drive_once,
                    queue=queue,
                    max_attempts=max_stream_attempts,
                    stream_reset=lambda: self._stream_reset_event(agent_run_id),
                    stopped=lambda: self._stopped_event(agent_run_id),
                    should_stop=lambda: self._should_stop(should_stop),
                )
            except BaseException as exc:  # noqa: BLE001 — relayed to parent below
                # Includes CancelledError from our own task.cancel(). pydantic's
                # scopes are unwound by the `async with` above, in THIS task.
                await queue.put(("error", exc))
            finally:
                await queue.put(("done", None))

        task = create_inherited_task(_drive(), name="agent-model-stream")
        pending_error: BaseException | None = None
        cancelled_by_us = False
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "done":
                    break
                if kind == "error":
                    pending_error = payload  # type: ignore[assignment]
                    continue
                yield payload  # type: ignore[misc]
        finally:
            if not task.done():
                cancelled_by_us = True
                task.cancel()
            # Let the child finish unwinding pydantic's scopes (in its own task),
            # shielded so our own cancellation doesn't abandon that cleanup.
            with anyio.CancelScope(shield=True):
                try:
                    await task
                except BaseException:
                    pass

        _reraise_driver_failure(
            pending_error,
            cancelled_by_us=cancelled_by_us,
            agent_run_id=agent_run_id,
        )

    async def _stream_model_request(
        self,
        node,
        run,
        *,
        agent_run_id: UUID,
        malformed_tool_call_ids: set[str],
        should_stop: StopChecker | None,
    ) -> AsyncIterator[AgentEvent]:
        token_buffers = {
            "text": CharStreamBuffer(max_chars=50),
            "thinking": CharStreamBuffer(max_chars=50),
            "tool": CharStreamBuffer(max_chars=50),
        }
        part_kinds: dict[int, str] = {}
        part_contents: dict[int, str] = {}
        part_objects: dict[int, object] = {}
        tool_names: dict[int, str] = {}
        tool_stream_started: set[int] = set()
        tool_stream_has_args: set[int] = set()

        def token_delta(kind: str, chunk: str) -> dict[str, str]:
            return {"kind": kind, "data": chunk}

        def append_token_text(kind: str, text: str) -> list[dict[str, str]]:
            if not self.emit_tokens:
                return []
            return [
                token_delta(kind, chunk) for chunk in token_buffers[kind].append(text)
            ]

        def drain_token_buffer(
            kind: str,
            *,
            force: bool = False,
        ) -> list[dict[str, str]]:
            if not self.emit_tokens:
                return []
            return [
                token_delta(kind, chunk)
                for chunk in token_buffers[kind].drain(force=force)
            ]

        def drain_all_token_buffers(*, force: bool = False) -> list[dict[str, str]]:
            chunks: list[dict[str, str]] = []
            for kind in ("text", "thinking", "tool"):
                chunks.extend(drain_token_buffer(kind, force=force))
            return chunks

        def start_tool_stream(index: int, tool_name: str) -> list[dict[str, str]]:
            if index in tool_stream_started:
                return []
            tool_stream_started.add(index)
            return append_token_text(
                "tool",
                f'{{"tool_name":{json.dumps(tool_name)},"args":',
            )

        def completed_part_message(
            *,
            part: object,
            part_kind: str | None,
            part_content: str | None,
        ) -> MessageDraft | None:
            if isinstance(part, TextPart) or part_kind == "text":
                final_text = part_content if part_content is not None else part.content
                if not final_text:
                    return None
                return MessageDraft.of_text(final_text)

            if isinstance(part, ThinkingPart) or part_kind == "thinking":
                final_thinking = (
                    part_content if part_content is not None else part.content
                )
                if not final_thinking:
                    return None
                return MessageDraft.of_thinking(final_thinking)

            if isinstance(part, ToolCallPart) or part_kind == "tool_call":
                tool_args = parse_tool_call_args(part.args)
                if tool_args is None:
                    malformed_tool_call_ids.add(part.tool_call_id)
                    logger.debug(
                        'agent.pydantic_ai.skipping_malformed_tool_call_persistence.diagnostic',
                        tool_call_id=part.tool_call_id,
                    )
                    return None
                return MessageDraft.of_tool_call(
                    tool_name=part.tool_name,
                    tool_call_id=part.tool_call_id,
                    tool_args=tool_args,
                    metadata={"tool_name": part.tool_name},
                )

            return None

        async with node.stream(run.ctx) as request_stream:
            async for event in request_stream:
                if isinstance(event, PartStartEvent):
                    part_objects[event.index] = event.part
                    if isinstance(event.part, TextPart):
                        part_kinds[event.index] = "text"
                        content = event.part.content or ""
                        part_contents[event.index] = content
                        for token_chunk in append_token_text("text", content):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                    elif isinstance(event.part, ThinkingPart):
                        part_kinds[event.index] = "thinking"
                        content = event.part.content or ""
                        part_contents[event.index] = content
                        for token_chunk in append_token_text("thinking", content):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                    elif isinstance(event.part, ToolCallPart):
                        part_kinds[event.index] = "tool_call"
                        tool_names[event.index] = event.part.tool_name
                        for token_chunk in start_tool_stream(
                            event.index,
                            event.part.tool_name,
                        ):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                        initial_args = _tool_call_args_text(event.part.args)
                        if initial_args:
                            tool_stream_has_args.add(event.index)
                            for token_chunk in append_token_text("tool", initial_args):
                                yield AgentEvent(
                                    type=AgentEventType.TOKEN,
                                    data=token_chunk,
                                    agent_run_id=agent_run_id,
                                )
                                if await self._should_stop(should_stop):
                                    yield self._stopped_event(agent_run_id)
                                    return

                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta):
                        part_kinds[event.index] = "text"
                        content_delta = event.delta.content_delta or ""
                        part_contents[event.index] = (
                            part_contents.get(event.index, "") + content_delta
                        )
                        for token_chunk in append_token_text("text", content_delta):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                    elif isinstance(event.delta, ThinkingPartDelta):
                        part_kinds.setdefault(event.index, "thinking")
                        content_delta = getattr(event.delta, "content_delta", "") or ""
                        if content_delta:
                            part_contents[event.index] = (
                                part_contents.get(event.index, "") + content_delta
                            )
                            for token_chunk in append_token_text(
                                "thinking",
                                content_delta,
                            ):
                                yield AgentEvent(
                                    type=AgentEventType.TOKEN,
                                    data=token_chunk,
                                    agent_run_id=agent_run_id,
                                )
                                if await self._should_stop(should_stop):
                                    yield self._stopped_event(agent_run_id)
                                    return
                    elif isinstance(event.delta, ToolCallPartDelta):
                        part_kinds.setdefault(event.index, "tool_call")
                        if event.delta.tool_name_delta:
                            tool_names[event.index] = (
                                tool_names.get(event.index, "")
                                + event.delta.tool_name_delta
                            )
                        tool_delta = _tool_call_delta_text(event.delta)
                        if tool_delta:
                            for token_chunk in start_tool_stream(
                                event.index,
                                tool_names.get(event.index, ""),
                            ):
                                yield AgentEvent(
                                    type=AgentEventType.TOKEN,
                                    data=token_chunk,
                                    agent_run_id=agent_run_id,
                                )
                                if await self._should_stop(should_stop):
                                    yield self._stopped_event(agent_run_id)
                                    return
                            tool_stream_has_args.add(event.index)
                            for token_chunk in append_token_text("tool", tool_delta):
                                yield AgentEvent(
                                    type=AgentEventType.TOKEN,
                                    data=token_chunk,
                                    agent_run_id=agent_run_id,
                                )
                                if await self._should_stop(should_stop):
                                    yield self._stopped_event(agent_run_id)
                                    return

                elif isinstance(event, PartEndEvent):
                    part_kind = part_kinds.pop(event.index, None)
                    part_content = part_contents.pop(event.index, None)
                    part_objects.pop(event.index, None)
                    if isinstance(event.part, ToolCallPart) or part_kind == "tool_call":
                        for token_chunk in start_tool_stream(
                            event.index,
                            getattr(event.part, "tool_name", None)
                            or tool_names.get(event.index, ""),
                        ):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                        if event.index not in tool_stream_has_args:
                            final_args = _tool_call_args_text(
                                getattr(event.part, "args", None)
                            )
                            for token_chunk in append_token_text(
                                "tool",
                                final_args or "{}",
                            ):
                                yield AgentEvent(
                                    type=AgentEventType.TOKEN,
                                    data=token_chunk,
                                    agent_run_id=agent_run_id,
                                )
                                if await self._should_stop(should_stop):
                                    yield self._stopped_event(agent_run_id)
                                    return
                        for token_chunk in append_token_text("tool", "}"):
                            yield AgentEvent(
                                type=AgentEventType.TOKEN,
                                data=token_chunk,
                                agent_run_id=agent_run_id,
                            )
                            if await self._should_stop(should_stop):
                                yield self._stopped_event(agent_run_id)
                                return
                        tool_names.pop(event.index, None)
                        tool_stream_started.discard(event.index)
                        tool_stream_has_args.discard(event.index)
                    for token_chunk in drain_all_token_buffers(force=True):
                        yield AgentEvent(
                            type=AgentEventType.TOKEN,
                            data=token_chunk,
                            agent_run_id=agent_run_id,
                        )
                        if await self._should_stop(should_stop):
                            yield self._stopped_event(agent_run_id)
                            return
                    message = completed_part_message(
                        part=event.part,
                        part_kind=part_kind,
                        part_content=part_content,
                    )
                    if message is not None:
                        yield AgentEvent(
                            type=AgentEventType.MESSAGE,
                            data=message,
                            agent_run_id=agent_run_id,
                        )
                        if await self._should_stop(should_stop):
                            yield self._stopped_event(agent_run_id)
                            return

                elif isinstance(event, FinalResultEvent):
                    for token_chunk in drain_all_token_buffers(force=True):
                        yield AgentEvent(
                            type=AgentEventType.TOKEN,
                            data=token_chunk,
                            agent_run_id=agent_run_id,
                        )
                        if await self._should_stop(should_stop):
                            yield self._stopped_event(agent_run_id)
                            return

        for token_chunk in drain_all_token_buffers(force=True):
            yield AgentEvent(
                type=AgentEventType.TOKEN,
                data=token_chunk,
                agent_run_id=agent_run_id,
            )
            if await self._should_stop(should_stop):
                yield self._stopped_event(agent_run_id)
                return

        for part_index in sorted(part_kinds):
            part = part_objects.get(part_index)
            if part is None:
                continue
            message = completed_part_message(
                part=part,
                part_kind=part_kinds[part_index],
                part_content=part_contents.get(part_index),
            )
            if message is not None:
                yield AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=message,
                    agent_run_id=agent_run_id,
                )
                if await self._should_stop(should_stop):
                    yield self._stopped_event(agent_run_id)
                    return

    async def _stream_tool_calls(
        self,
        node,
        run,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        malformed_tool_call_ids: set[str],
        emitted_tool_response_ids: set[str],
        should_stop: StopChecker | None,
    ) -> AsyncIterator[AgentEvent]:
        async with node.stream(run.ctx) as handle_stream:
            async for event in handle_stream:
                if isinstance(event, FunctionToolCallEvent):
                    continue
                if isinstance(event, FunctionToolResultEvent):
                    result_part = event.part
                    if result_part.tool_call_id in malformed_tool_call_ids:
                        logger.debug(
                            'agent.pydantic_ai.skipping_tool_result_malformed_call.diagnostic',
                            tool_call_id=result_part.tool_call_id,
                        )
                        continue
                    tool_output = result_part.content
                    if isinstance(tool_output, BinaryContent):
                        tool_output = {
                            "type": "binary_content",
                            "media_type": tool_output.media_type,
                            "size_bytes": len(tool_output.data)
                            if tool_output.data
                            else 0,
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
                        agent_run_id=agent_run_id,
                    )
                    if await self._should_stop(should_stop):
                        yield self._stopped_event(agent_run_id)
                        return

    async def _should_stop(self, should_stop: StopChecker | None) -> bool:
        if should_stop is None:
            return False
        try:
            return await should_stop()
        except Exception:
            return False

    def _stopped_event(self, agent_run_id: UUID) -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.STOPPED,
            data={"reason": "stop_requested"},
            agent_run_id=agent_run_id,
        )

    def _stream_reset_event(self, agent_run_id: UUID) -> AgentEvent:
        """Tell the client to discard the partially streamed response.

        Shaped as an empty TOKEN rather than a new event type so existing
        clients stay correct without changing: they append "" (a no-op) and
        then receive the replacement response. Clients that understand
        ``kind == "stream_reset"`` clear the in-flight bubble first, so the
        retried answer doesn't appear glued onto the abandoned one.
        """
        return AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": "stream_reset", "data": ""},
            agent_run_id=agent_run_id,
        )

    def _history_and_prompt(
        self,
        messages: Sequence[Message],
    ) -> tuple[list[ModelMessage], str | None]:
        return history_and_prompt(messages)

    def _final_output_message(
        self,
        *,
        output: object,
        tool_name: str | None,
        tool_call_id: str | None,
    ) -> MessageDraft | None:
        if output is None:
            return None

        structured_output = to_json_value(output)
        metadata: JsonObject = {
            "is_final_answer": True,
            "structured_output": structured_output,
        }
        if tool_name:
            metadata["tool_name"] = tool_name
            metadata["final_answer_tool_name"] = tool_name
        if tool_call_id:
            metadata["tool_call_id"] = tool_call_id

        if isinstance(output, FinalAgentResult):
            metadata["final_answer_status"] = output.status
            if output.error:
                metadata["final_answer_error"] = output.error
            if output.output is not None:
                metadata["structured_output"] = output.output
            content_text = self._final_answer_text(
                output.output,
                fallback=output.error,
            )
            if not content_text:
                content_text = output.error or output.status
        else:
            content_text = self._final_answer_text(structured_output)

        if not content_text:
            return None

        return MessageDraft.of_text(content_text, metadata=metadata)

    def _final_answer_text(
        self,
        data: object,
        *,
        fallback: str | None = None,
    ) -> str:
        # Shared with the Agent Host normalizer so identical structured output
        # reads identically on a surface, whichever harness produced it.
        return final_answer_text(data, fallback=fallback)


def _preview(raw: str, *, limit: int = 160) -> str:
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]}..."


def _runtime_profile_model(options: HarnessOptions):
    return require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=options.extra.get("runtime_profile"),
        runtime_credentials=options.extra.get("runtime_credentials"),
        fallback_model_name=options.model_name,
    )


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


def _usage_value(usage: object, field: str) -> int:
    value = getattr(usage, field, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


# Every usage field the harness reports, in one place so a retry can carry all of
# them forward without the accumulator and the emitter drifting apart.
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "requests",
    "tool_calls",
    "cache_write_tokens",
    "cache_read_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
)


def _accumulate_usage(carried: dict[str, int], usage: object) -> None:
    """Fold an abandoned attempt's usage into the running total."""
    if usage is None:
        return
    for field in _USAGE_FIELDS:
        carried[field] = carried.get(field, 0) + _usage_value(usage, field)


def _usage_totals(usage: object, carried: dict[str, int]) -> dict[str, int]:
    """This attempt's usage plus everything earlier attempts already spent."""
    return {
        field: _usage_value(usage, field) + carried.get(field, 0)
        for field in _USAGE_FIELDS
    }


def _retry_backoff(attempt: int) -> float:
    """Backoff before re-entering the graph, with jitter.

    Short by design: the user is watching a stalled response, and the failure we
    retry is a dropped connection rather than a loaded server. A provider that
    wants longer says so via ``Retry-After``, which takes precedence.
    """
    base = min(_RETRY_BACKOFF_CAP_SECONDS, 0.5 * (3 ** (attempt - 1)))
    return base + random.uniform(0, base / 2)
