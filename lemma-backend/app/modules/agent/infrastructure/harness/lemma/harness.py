"""PydanticAI harness implementation."""

from __future__ import annotations
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.run import AgentRun
from contextlib import AbstractAsyncContextManager

import asyncio
from collections.abc import Mapping
from typing import AsyncIterator, Sequence
from uuid import UUID


from app.core.infrastructure.db.transient_errors import is_transient_database_error
from app.core.infrastructure.work import LeaseLost
from app.modules.agent.infrastructure.harness.lemma.tool_returns import (
    OutstandingToolCalls,
    paused_call_ids,
)
from app.modules.agent.infrastructure.harness.lemma.deferred_projection import (
    DeferredCallProjection,
)
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.tools import ToolSpec
from app.modules.agent.infrastructure.harness.lemma.mock_model import (
    build_mock_model,
    is_mock_llm_enabled,
)
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai import DeferredToolRequests
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelMessage,
)
from app.modules.agent.infrastructure.harness.lemma.driver import (
    STOP_WHILE_BUSY,
    next_event_or_stop,
    teardown_driver,
)
from app.modules.agent.infrastructure.harness.lemma.node_loop import NodeLoop
from app.modules.agent.infrastructure.unavailable_models import (
    remember_provider_refusal,
)
from app.modules.agent.infrastructure.harness.lemma.retry import (
    drive_with_retry,
    reraise_driver_failure,
)
from app.modules.agent.infrastructure.harness.lemma.model_streaming import (
    ModelRequestStreamer,
    StopChecker,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.infrastructure.harness.lemma.prompt_layout import (
    run_instructions,
)
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    HarnessKind,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    LeaseGate,
    harness_capabilities,
)
from app.modules.agent.infrastructure.harness.lemma.pending_user_messages import (
    ClaimSteered,
)
from app.modules.agent.infrastructure.harness.lemma.run_log import (
    starting_history,
)
from app.modules.agent.infrastructure.harness.lemma.cache_telemetry import (
    CacheTelemetry,
)
from app.modules.agent.infrastructure.harness.lemma.history_parts import (
    RunNoticeCapability,
)
from app.modules.agent.infrastructure.harness.lemma.final_answer_output import (
    final_output_message,
)
from app.modules.agent.infrastructure.harness.lemma.run_options import (
    final_answer_output,
    harness_usage_limits,
    prompt_caching_enabled,
)
from app.modules.agent.services.tool_runtime import ToolRuntime
from app.modules.agent.infrastructure.harness.lemma.history import history_capabilities
from app.modules.agent.infrastructure.harness.lemma.history_replay import (
    history_and_prompt,
)
from app.modules.agent.infrastructure.harness.lemma.provider_error_log import (
    failure_event,
    log_model_http_error,
)
from app.modules.agent.config import agent_settings
from app.modules.usage.contracts.metering import (
    RUN_INDEX_METADATA_KEY,
    with_external_stream_retries,
)
from app.modules.agent.infrastructure.providers.model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
    provider_model_settings,
    with_affinity,
)
from app.modules.agent.infrastructure.providers.summarization import (
    lazy_compaction_target,
)
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)

#: Events buffered between the model-streaming driver and its consumer. Large
#: enough that an ordinary consumer never waits, small enough that a stalled one
#: stops the producer rather than growing without limit.
_STREAM_QUEUE_ITEMS = 256

DEFAULT_TOOL_RETRIES = 5


class PydanticAIHarness:
    """Execute an agent through PydanticAI and emit domain events."""

    kind = HarnessKind.LEMMA

    def __init__(
        self,
        *,
        emit_tokens: bool = True,
        model: Model | None = None,
        claim_steered: ClaimSteered | None = None,
    ) -> None:
        self.emit_tokens = emit_tokens
        # Which model a run talks to. The runtime profile decides in production;
        # a test hands in a scripted model here rather than replacing a name
        # inside this module. A scripted model is not metered, and neither is
        # its compaction call.
        self._model = model
        # Where messages sent mid-run come from: the database unless a test
        # says otherwise.
        self._claim_steered = claim_steered

    async def run[DepsT: ToolContext](
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: DepsT,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        # Every tool this run announces, until its result is recorded. A run
        # that dies in between leaves an orphaned call, and the next run is told
        # the tool never ran and invited to repeat it -- a duplicate email, a
        # duplicate record write.
        outstanding = OutstandingToolCalls()
        projection = DeferredCallProjection(_pauses(options.tools))
        terminal: AgentEvent | None = None

        try:
            async for event in self._execute(
                agent=agent,
                conversation=conversation,
                messages=messages,
                ctx=ctx,
                options=options,
                agent_run_id=agent_run_id,
                should_stop=options.should_stop,
            ):
                event = projection.project(event)
                outstanding.observe(event)
                if event.type in {
                    AgentEventType.ERROR,
                    AgentEventType.STOPPED,
                    AgentEventType.WAITING,
                }:
                    # Held back, not yielded here. The closing returns for tool
                    # calls still open are emitted below, and the pump drops
                    # every event after the terminal one -- so emitting the
                    # terminal first meant a stopped run kept a tool call that
                    # nothing ever answered. The next run then saw an
                    # unanswered call and could reasonably repeat a tool that
                    # had already run: the duplicate-send hazard the closing
                    # events exist to prevent.
                    terminal = event
                    continue
                yield event
        except ModelHTTPError as exc:
            log_model_http_error(
                exc,
                agent_run_id=agent_run_id,
                model_name=getattr(options, "model_name", None),
            )
            # A 404 for the model is remembered, so the next turn on it is
            # refused at once rather than paying for the same answer.
            await remember_provider_refusal(
                options.runtime_profile, status_code=exc.status_code
            )
            terminal = failure_event(exc, agent_run_id=agent_run_id)
        except UnexpectedModelBehavior as exc:
            # Two causes, told apart by `classify_run_failure`: a tool that kept
            # getting invalid arguments past every retry (GracefulToolset turns
            # ordinary execution errors into tool responses), or a response the
            # output cap cut off before it held anything usable.
            logger.warning(
                "agent.pydantic_ai.agent_run_ended_on_unusable_response.degraded",
                exc_info=True,
            )
            terminal = failure_event(exc, agent_run_id=agent_run_id)
        except UsageLimitExceeded as exc:
            logger.warning(
                "agent.pydantic_ai.agent_run_hit_usage_limit.degraded", exc_info=True
            )
            terminal = failure_event(exc, agent_run_id=agent_run_id)
        except Exception as exc:
            if is_transient_database_error(exc) or isinstance(exc, LeaseLost):
                # Lemma's own database, not the model: a stop check, a step
                # read or a tool's session could not get a connection. The turn
                # is interrupted, not over -- its work item retries it and the
                # resumed turn settles what was in flight. Recorded as the run
                # failing, this read "check the model settings". A lost lease
                # likewise: another worker has the turn and finishes it.
                raise
            logger.error(
                "agent.pydantic_ai.pydanticai_harness_type.failed", exc_info=True
            )
            terminal = failure_event(exc, agent_run_id=agent_run_id)

        if terminal is None:
            terminal = AgentEvent(
                type=AgentEventType.COMPLETED,
                data={"conversation_id": str(conversation.id)},
                agent_run_id=agent_run_id,
            )
        # Close whatever is still open before the run ends, whichever way it
        # ended. The pausing calls are excluded: their real returns are
        # appended when they are resolved, so a synthetic one would duplicate.
        for closing in outstanding.closing_events(
            terminal, skip_tool_call_ids=paused_call_ids(terminal)
        ):
            yield closing
        # Always last. Every path now holds its terminal back to here, so the
        # run ends with its history closed rather than with the terminal racing
        # the events that close it.
        yield terminal

    async def _execute[DepsT: ToolContext](
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: DepsT,
        options: HarnessOptions,
        agent_run_id: UUID,
        should_stop: StopChecker | None,
    ) -> AsyncIterator[AgentEvent]:
        snapshot = options.runtime_profile
        with run_phase("history_convert") as history_span:
            history, user_prompt, recorder = starting_history(
                messages,
                options=options,
                conversation_id=conversation.id,
                agent_run_id=agent_run_id,
            )
            history_span.set_attribute("lemma.history.model_messages", len(history))
        # e2e mock mode swaps only the model — the rest of the harness (tool
        # execution, streaming, events, persistence) runs for real.
        if self._model is not None:
            model = self._model
        elif is_mock_llm_enabled():
            model = _meter_mock_model(conversation, options)
        else:
            model = _runtime_profile_model(options)
        # The e2e mock scripts call a found tool by its own name, as a model
        # with a tool-addition channel does.
        reveals_natively = (
            self._model is None and is_mock_llm_enabled()
        ) or reveals_tools_natively(model)
        # The graph driver owns stream resets, history recovery and retry limits.
        # Retrying inside the model would hide connection drops from that driver.
        model = with_external_stream_retries(model)
        # Falls back to this run's model when HISTORY_SUMMARIZATION_MODEL is
        # unset, so behaviour is unchanged unless a deployment opts in.
        summarization_model = lazy_compaction_target(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            run_model=model,
            run_compaction_settings=with_affinity(
                options.compaction_model_settings,
                f"{ctx.pod_id}:compaction" if ctx.pod_id else None,
            ),
            meter=lambda resolved: (
                resolved
                if self._model is not None
                else _meter_compaction_model(resolved, options)
            ),
        )
        capabilities = harness_capabilities(
            ctx=ctx,
            specs=options.tools,
            runtime=ToolRuntime(options.tools),
            enable_prompt_caching=prompt_caching_enabled(snapshot),
            protocol=(
                snapshot.protocol
                if snapshot is not None
                else RuntimeProfileProtocol.OPENAI_COMPATIBLE
            ),
            claim_steered=self._claim_steered,
            on_steered=recorder.claimed if recorder is not None else None,
            reveals_tools_natively=reveals_natively,
            steer_due=options.steer_due,
        )
        # Before the history processors, not after. Capabilities run in
        # registration order, and the processors include the hard-ceiling guard
        # -- so a notice added after them is a request the guard never measured,
        # and the ceiling it exists to hold stops holding.
        #
        # The order also decides which request each notice rides. The budget
        # posts before the request is built, so its notice goes out on the one
        # that step is about to make. The compactor posts from a processor,
        # which now runs after this, so its notice lands on the following
        # request -- soon enough, since it fires with a fifth of the history
        # budget still to fill.
        capabilities.append(RunNoticeCapability(options.notices))
        # History processors ride as ProcessHistory capabilities (the
        # history_processors= kwarg is deprecated in pydantic-ai), between the
        # measurement of what else the request carries and the fitting of its
        # reply budget to what is left.
        capabilities.extend(
            history_capabilities(options, summarization_model=summarization_model)
        )
        if options.lease_alive is not None:
            capabilities.append(LeaseGate(options.lease_alive))
        capabilities.append(CacheTelemetry(run_index=options.run_index))
        output_type = final_answer_output(
            agent, conversation, pausing=frozenset(_pauses(options.tools))
        )
        pydantic_agent: PydanticAIAgent[DepsT, object] = PydanticAIAgent(
            model,
            instructions=run_instructions(
                agent=agent, conversation=conversation, ctx=ctx
            ),
            deps_type=type(ctx),
            retries=DEFAULT_TOOL_RETRIES,
            # A final answer must not skip a sibling approval tool that pauses the run.
            end_strategy="graceful",
            # A run that pauses ends on its deferred calls, every one of them,
            # once the step's other calls have finished.
            output_type=[
                output_type if output_type is not None else str,
                DeferredToolRequests,
            ],
            model_settings=provider_model_settings(options.model_settings),
            capabilities=capabilities,
        )

        def _new_run_context(
            resume_history: list[ModelMessage] | None,
            attempt_settings: JsonObject | None,
        ) -> AbstractAsyncContextManager[AgentRun[DepsT, object]]:
            # Resuming already carries the user prompt in its message history.
            return pydantic_agent.iter(
                None if resume_history is not None else user_prompt,
                message_history=resume_history
                if resume_history is not None
                else (history or None),
                deps=ctx,
                # A retry may ask for a shorter answer than the run's own
                # settings do; merged over them, not in place of them.
                model_settings=provider_model_settings(attempt_settings),
                usage_limits=harness_usage_limits(),
                conversation_id=str(conversation.id),
                # Read back by the metered model for each request's receipt.
                metadata={RUN_INDEX_METADATA_KEY: options.run_index},
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
        # Bounded so a slow consumer applies backpressure to model streaming
        # instead of accumulating every token and tool result in memory.
        # `channel_service` bounds its client queues for the same reason; this
        # one carries larger items.
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue(
            maxsize=_STREAM_QUEUE_ITEMS
        )
        max_stream_attempts = max(1, agent_settings.agent_model_stream_max_attempts)
        streamer = ModelRequestStreamer(
            emit_tokens=self.emit_tokens,
            agent_run_id=agent_run_id,
            should_stop=should_stop,
            pauses=_pauses(options.tools),
            idle_seconds=agent_settings.agent_model_stream_idle_timeout_seconds,
        )

        node_loop = NodeLoop(
            run_context=_new_run_context,
            queue=queue,
            streamer=streamer,
            options=options,
            agent_run_id=agent_run_id,
            conversation_id=ctx.conversation_id,
            final_output_message=self._final_output_message,
            structured_output=output_type is not None,
            recorder=recorder,
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
                    shorter_answer_settings=node_loop.shorter_answer_settings,
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
                item = await next_event_or_stop(queue, streamer.stop_requested)
                if item is STOP_WHILE_BUSY:
                    # The driver is somewhere that never checks for a stop --
                    # in practice, inside a tool call. Ending the loop here
                    # cancels it below, and the closing returns for whatever it
                    # left open are emitted by `run`.
                    yield streamer.stopped_event()
                    break
                kind, payload = item  # type: ignore[misc]
                if kind == "done":
                    break
                if kind == "error":
                    pending_error = payload  # type: ignore[assignment]
                    continue
                yield payload  # type: ignore[misc]
        finally:
            cancelled_by_us = await teardown_driver(task, agent_run_id=agent_run_id)

        reraise_driver_failure(
            pending_error,
            cancelled_by_us=cancelled_by_us,
            agent_run_id=agent_run_id,
            driver_task=task,
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
        protocol: RuntimeProfileProtocol | None = None,
        current_run_id: UUID | None = None,
        replay_prior_run_reasoning: bool = False,
    ) -> tuple[list[ModelMessage], str | None]:
        return history_and_prompt(
            messages,
            protocol=protocol,
            current_run_id=current_run_id,
            replay_prior_run_reasoning=replay_prior_run_reasoning,
        )

    def _final_output_message(
        self,
        *,
        output: object,
        tool_name: str | None,
        tool_call_id: str | None,
    ) -> MessageDraft | None:
        return final_output_message(
            output=output, tool_name=tool_name, tool_call_id=tool_call_id
        )


def _pauses(specs: Sequence[ToolSpec]) -> dict[str, PauseKind]:
    """Each pausing tool of the run, and the pause it opens."""
    return {
        spec.name: spec.policy.pauses
        for spec in specs
        if spec.policy.pauses is not None
    }


def reveals_tools_natively(model: Model) -> bool:
    """Whether ``model`` can be shown a tool mid-run without rewriting ``tools``.

    Either it has a channel for tools added mid-run, or it declares a tool with
    its schema withheld from the first request on (Anthropic's standalone
    ``defer_loading``), so a reveal unhides what was always in the array.
    Read off the model under the wrappers (metering, stream retries), which
    declare neither of their own.
    """
    while isinstance(model, WrapperModel):
        model = model.wrapped
    return isinstance(model, Model) and (
        model.tool_addition_mode is not None or model.tool_deferral_mode == "standalone"
    )


def _runtime_profile_model[DepsT](options: HarnessOptions) -> Model:
    snapshot = options.runtime_profile
    credentials = options.extra.get("runtime_credentials")
    return require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=snapshot.as_payload() if snapshot else None,
        runtime_credentials=credentials if isinstance(credentials, Mapping) else None,
        fallback_model_name=options.model_name,
    )


def _meter_mock_model[DepsT](
    conversation: Conversation, options: HarnessOptions
) -> Model:
    from app.modules.usage.contracts.metering import meter_model

    model = build_mock_model(conversation)
    snapshot = options.runtime_profile
    # The usage module takes the snapshot's dict form across its boundary.
    return meter_model(model, snapshot.as_payload()) if snapshot else model


def _meter_compaction_model[DepsT](
    model: Model | str, options: HarnessOptions
) -> Model | str:
    from app.modules.usage.contracts.metering import meter_model

    if isinstance(model, Model):
        snapshot = options.runtime_profile
        return meter_model(
            model,
            snapshot.as_payload() if snapshot else {},
            source="history_compaction",
        )
    return model
