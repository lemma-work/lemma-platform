"""PydanticAI harness implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import AsyncIterator, Sequence
from uuid import UUID

import anyio

from app.modules.agent.infrastructure.harnesses.mock_model import (
    build_mock_model,
    is_mock_llm_enabled,
)
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import NodeLoop
from app.modules.agent.infrastructure.harnesses.pydantic_ai_retry import (
    HarnessDriverCancelled,
    drive_with_retry,
    reraise_driver_failure,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_streaming import (
    ModelRequestStreamer,
)
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
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
)
from app.modules.agent.infrastructure.harnesses.provider_error_log import (
    log_model_http_error,
)
from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
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


def _instructions(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: AgentContext,
) -> str:
    """The run's system instructions, timed as its own phase.

    Prompt assembly reads and renders every prompt fragment the agent's
    configuration pulls in, so it is worth telling apart from the model call it
    sits immediately in front of.
    """
    with run_phase("instructions") as span:
        text = build_agent_instructions(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            include_toolset_prompts=False,
        )
        span.set_attribute("lemma.instructions.chars", len(text))
        return text


def _report_teardown_failure(exc: BaseException, *, agent_run_id: UUID) -> None:
    """Report a driver task that crashed unwinding — but not cancellation.

    Swallowed either way: ``reraise_driver_failure`` owns what the run reports.
    But a bare ``pass`` is how a teardown that has been failing goes unnoticed.
    """
    if isinstance(exc, asyncio.CancelledError):
        return
    logger.error(
        "agent.pydantic_ai.stream_teardown.failed",
        agent_run_id=str(agent_run_id),
        exc_info=exc,
    )


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
                "agent.pydantic_ai.agent_run_ended_after_repeated.diagnostic",
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
            logger.error(
                "agent.pydantic_ai.pydanticai_harness_type.failed", exc_info=True
            )
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
        with run_phase("history_convert") as history_span:
            history, user_prompt = self._history_and_prompt(
                messages, protocol=_runtime_profile_protocol(options)
            )
            history_span.set_attribute("lemma.history.model_messages", len(history))
        # e2e mock mode swaps only the model — the rest of the harness (tool
        # execution, streaming, events, persistence) runs for real.
        if is_mock_llm_enabled():
            model = build_mock_model(conversation)
        else:
            model = _runtime_profile_model(options)
        agent_kwargs: dict[str, object] = {
            # Per-toolset prompt fragments (e.g. web search) are contributed by the
            # matching capabilities, so they're suppressed here to avoid duplication.
            "instructions": _instructions(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
            ),
            # A single invalid tool call must not kill the run: give the model room
            # to self-correct from validation feedback before giving up.
            "retries": DEFAULT_TOOL_RETRIES,
            # A TASK conversation returns through the final_answer output tool.
            # Under the "early" strategy a normal tool emitted in the same model
            # response (notably request_approval and ask_user) is skipped once
            # final_answer validates — so the pause never happens, the approval
            # is never persisted, and the run silently completes instead of
            # waiting for the user. Graceful executes that sibling tool, which
            # is what raises AgentInputRequired and pauses the run.
            #
            # Pinned rather than load-bearing *today*: "graceful" became the
            # pydantic-ai default after this was written against 1.x, where the
            # default was "early" and this kwarg was the fix. It stays explicit
            # because a default is not a promise, and the bug it prevents is
            # silent — the run reports success and nobody is ever asked.
            #
            # It is also not the reason the pause survives. What outranks a
            # co-emitted answer is that the pause RAISES: an exception abandons
            # the run outright. pydantic-ai's native deferral (`CallDeferred`,
            # or a tool declared `requires_approval=True`) cannot do that —
            # `_tool_execution._finalize_deferred` resolves deferred calls only
            # `if not self.final_result`, and a validated `final_answer` sets
            # one, so the deferral is discarded under every end strategy.
            #
            # Precisely: an output *tool* in the same response wins. Text-based
            # output (NativeOutput/PromptedOutput) does not preempt a deferral,
            # because `_agent_graph` short-circuits on `if tool_calls:` before
            # consulting the text processor. Switching `final_answer` to that
            # shape would make native deferral viable — and would discard the
            # status lifecycle TASK conversations drive through it, and still
            # do nothing for the Agent Host harness, which is not pydantic-ai.
            # See test_pause_beats_final_answer.py.
            "end_strategy": "graceful",
        }
        if options.toolsets:
            agent_kwargs["toolsets"] = options.toolsets
        # History processors ride as ProcessHistory capabilities (the
        # history_processors= kwarg is deprecated in pydantic-ai).
        with run_phase("summarization_model"):
            # Falls back to this run's model when HISTORY_SUMMARIZATION_MODEL is
            # unset, so behaviour is unchanged unless a deployment opts in.
            summarization_model = await resolve_summarization_model(
                organization_id=conversation.organization_id,
                user_id=conversation.user_id,
                fallback=model,
            )
        history_processors = build_history_processors(
            options,
            summarization_model=summarization_model,
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
                # Our conversation id, not pydantic-ai's. Left unset, pydantic-ai
                # takes the most recent conversation id off `message_history` --
                # and we rebuild history from the database every run, so there is
                # never one to inherit and it mints a fresh UUID7 per run instead.
                # That id becomes `gen_ai.conversation.id`, which the
                # OpenInference instrumentation maps onto `session.id`, which is
                # the only thing Phoenix groups a session by. So every turn
                # became its own single-trace session: 648 traces, 648 sessions,
                # exactly one-to-one, on the deployment where this was found.
                "conversation_id": str(conversation.id),
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
        streamer = ModelRequestStreamer(
            emit_tokens=self.emit_tokens,
            agent_run_id=agent_run_id,
            should_stop=should_stop,
        )

        node_loop = NodeLoop(
            run_context=_new_run_context,
            queue=queue,
            streamer=streamer,
            options=options,
            agent_run_id=agent_run_id,
            conversation_id=ctx.conversation_id,
            final_output_message=self._final_output_message,
        )

        async def _drive() -> None:
            try:
                await drive_with_retry(
                    node_loop.drive_once,
                    queue=queue,
                    max_attempts=max_stream_attempts,
                    stream_reset=lambda: self._stream_reset_event(agent_run_id),
                    stopped=streamer.stopped_event,
                    should_stop=streamer.stop_requested,
                    emit_usage=node_loop.emit_usage,
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
                except BaseException as exc:
                    _report_teardown_failure(exc, agent_run_id=agent_run_id)

        reraise_driver_failure(
            pending_error,
            cancelled_by_us=cancelled_by_us,
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
        *,
        protocol: str | None = None,
    ) -> tuple[list[ModelMessage], str | None]:
        return history_and_prompt(messages, protocol=protocol)

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


def _runtime_profile_protocol(options: HarnessOptions) -> str | None:
    """Which provider family this run's model belongs to, if it says.

    Read for one reason: whether a stored thought can be replayed to it. See
    `pydantic_ai_thinking.thinking_part_from_message` -- Anthropic and the
    OpenAI-compatible providers want different credentials, so "can this be
    replayed" has no answer until the target is known.
    """
    runtime_profile = options.extra.get("runtime_profile")
    if not isinstance(runtime_profile, Mapping):
        return None
    protocol = runtime_profile.get("protocol")
    return protocol if isinstance(protocol, str) and protocol else None


def _runtime_profile_model(options: HarnessOptions):
    return require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=options.extra.get("runtime_profile"),
        runtime_credentials=options.extra.get("runtime_credentials"),
        fallback_model_name=options.model_name,
    )


# Every usage field the harness reports, in one place so a retry can carry all of
# them forward without the accumulator and the emitter drifting apart.
