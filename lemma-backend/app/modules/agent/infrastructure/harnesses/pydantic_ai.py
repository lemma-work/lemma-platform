"""PydanticAI harness implementation."""

from __future__ import annotations

import asyncio
import json
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
from app.modules.agent.infrastructure.harnesses.streaming import CharStreamBuffer
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
)
from app.modules.agent.tools.final_answer.final_answer_tool import FinalAgentResult
from app.modules.agent.tools.tool_errors import AgentInputRequired
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)
StopChecker = Callable[[], Awaitable[bool]]


def _user_facing_error_message(exc: Exception) -> str:
    """Return a sanitized, actionable message for the UI.

    Never forward raw provider exception text (which may contain API keys,
    request headers, or model-internal details) into user-visible payloads.
    """
    if isinstance(exc, ModelHTTPError):
        return (
            f"The model provider returned an error (HTTP {exc.status_code}). "
            "Please check the agent runtime configuration."
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
            logger.error(
                "agent.pydantic_ai.model_request_status_model.failed",
                status_code=exc.status_code,
            exc_info=True,
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
            logger.debug('agent.pydantic_ai.agent_run_ended_after_repeated.diagnostic')
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=_user_facing_error_message(exc),
                agent_run_id=agent_run_id,
            )
            return
        except UsageLimitExceeded as exc:
            logger.warning("agent.pydantic_ai.agent_run_hit_usage_limit.degraded")
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
            summarization_model=model,
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
        iter_kwargs: dict[str, object] = {
            "message_history": history or None,
            "deps": ctx,
            "usage_limits": options.usage_limits,
        }
        run_context = (
            pydantic_agent.iter(**iter_kwargs)
            if user_prompt is None
            else pydantic_agent.iter(user_prompt, **iter_kwargs)
        )

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

        async def _drive() -> None:
            try:
                async with run_context as run:
                    async for node in run:
                        if PydanticAIAgent.is_model_request_node(node):
                            async for event in self._stream_model_request(
                                node,
                                run,
                                agent_run_id=agent_run_id,
                                malformed_tool_call_ids=malformed_tool_call_ids,
                                should_stop=should_stop,
                            ):
                                await queue.put(("event", event))
                                if event.type in {
                                    AgentEventType.ERROR,
                                    AgentEventType.STOPPED,
                                }:
                                    return
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

                    run_usage = run.usage
                    await queue.put(
                        (
                            "event",
                            AgentEvent(
                                type=AgentEventType.USAGE,
                                data=AgentRunUsage(
                                    model_name=options.model_name,
                                    usage_kind="llm",
                                    input_tokens=_usage_value(
                                        run_usage, "input_tokens"
                                    ),
                                    output_tokens=_usage_value(
                                        run_usage, "output_tokens"
                                    ),
                                    request_count=_usage_value(run_usage, "requests"),
                                    tool_call_count=_usage_value(
                                        run_usage, "tool_calls"
                                    ),
                                    metadata={
                                        "cache_write_tokens": _usage_value(
                                            run_usage, "cache_write_tokens"
                                        ),
                                        "cache_read_tokens": _usage_value(
                                            run_usage, "cache_read_tokens"
                                        ),
                                        "input_audio_tokens": _usage_value(
                                            run_usage, "input_audio_tokens"
                                        ),
                                        "output_audio_tokens": _usage_value(
                                            run_usage, "output_audio_tokens"
                                        ),
                                    },
                                ),
                                agent_run_id=agent_run_id,
                            ),
                        )
                    )
            except BaseException as exc:  # noqa: BLE001 — relayed to parent below
                # Includes CancelledError from our own task.cancel(). pydantic's
                # scopes are unwound by the `async with` above, in THIS task.
                await queue.put(("error", exc))
            finally:
                await queue.put(("done", None))

        task = create_inherited_task(_drive(), name="agent-model-stream")
        pending_error: BaseException | None = None
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
                task.cancel()
            # Let the child finish unwinding pydantic's scopes (in its own task),
            # shielded so our own cancellation doesn't abandon that cleanup.
            with anyio.CancelScope(shield=True):
                try:
                    await task
                except BaseException:
                    pass

        # On normal completion, re-raise a real error from the driver so run()'s
        # handlers (ModelHTTPError, UsageLimitExceeded, AgentInputRequired, …)
        # still fire. A CancelledError relayed here is dropped: the parent's own
        # cancellation (if any) already propagated out of the loop above.
        if pending_error is not None and not isinstance(
            pending_error, asyncio.CancelledError
        ):
            raise pending_error

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
            logger.debug('agent.pydantic_ai.agent_stop_check_s.diagnostic')
            return False

    def _stopped_event(self, agent_run_id: UUID) -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.STOPPED,
            data={"reason": "stop_requested"},
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
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("answer", "content", "message", "summary"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            if data:
                return json.dumps(data, indent=2, default=str)
        if data is None:
            return fallback or ""
        return str(data)


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
