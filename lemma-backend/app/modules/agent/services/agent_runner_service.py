"""Background runner for agent harness execution."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import time
from app.modules.agent.contracts.run_observer import RunObserver
from uuid import UUID
from pydantic_ai.output import OutputSpec, ToolOutput
from pydantic_ai.capabilities import AgentCapability
from pydantic_ai.toolsets import AbstractToolset

import anyio
from pydantic_ai import UsageLimits

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.observability.telemetry import (
    agent_run_telemetry_context,
    record_span_input,
    record_span_output,
)
from app.modules.agent.config import agent_settings
from app.modules.agent.services.context_budget import (
    context_budget_for,
)
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentRunStatus,
    ConversationType,
    HarnessKind,
    JsonObject,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.capabilities import build_lemma_harness_tooling
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    ResolvedAgentRuntime,
    runtime_profile_service_for,
)
from app.modules.agent.services.outsider_runtime import (
    default_runtimes,
    in_process_runtime,
)
from app.modules.agent.services.outside_text import note_what_the_turn_reads
from app.modules.agent.services.run_limits import (
    budget_for_run,
    harness_usage_limits,
    make_stop_checker,
)
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.services.run_context_loader import (
    RunContextLoader,
    load_run_context,
)
from app.modules.agent.services.run_metrics import (
    AgentRunMetrics,
    RunOutcomeLabel,
    agent_run_metrics,
    ended_as,
    ended_by,
)
from app.modules.agent.services.run_phase_spans import (
    run_phase,
)
from app.modules.agent.services.run_context_builder import build_run_context
from app.modules.agent.services.run_event_pump import (
    RunEventPump,
    RunOutcome,
    drive_harness,
)
from app.modules.agent.services.turn_resume import TurnResume
from app.modules.agent.services.run_interruptions import run_is_not_over
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_finalizer import (
    is_usage_limit_error,
    RunFinalizer,
    finalize_safely,
    run_failure_message,
    run_failure_code,
    run_failure_reason,
)
from app.modules.agent.services.run_observer_delivery import (
    notify_run_failed,
    notify_run_finished,
    notify_run_started,
)
from app.modules.agent.services.run_usage_recorder import (
    RunUsageRecorder,
    usage_source_for,
)
from app.modules.usage.contracts import UsageReservation
from app.modules.usage.contracts.execution import (
    usage_context_from_agent_context,
)
from app.modules.usage.contracts.metering import metering_execution
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.final_answer import get_final_answer_tool
from app.modules.agent.tools.tool_assembler import RunToolAssembler
from app.modules.agent.services.run_input_settings import (
    run_input_text,
    with_reply_budget,
)
from app.modules.agent.services.generation_settings import (
    record_generation_settings,
    run_generation_settings,
)

logger = get_logger(__name__)

# Ceiling on the shielded finalization write. Comfortably longer than the write
# takes and comfortably inside the worker's shutdown grace period, so a healthy
# run always finalizes and a wedged one still lets the process exit.
_FINALIZATION_TIMEOUT_SECONDS = 8.0


#: The contract the surface layer implements; kept under the old name here.
AgentRunObserver = RunObserver


#: Builds the runtime-profile resolver over one unit of work.
ProfileServices = Callable[[SqlAlchemyUnitOfWork], AgentRuntimeProfileService]


class AgentRunnerService:
    """Executes one persisted agent run and persists harness messages."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        harness_registry: HarnessRegistry,
        fallback_model_name: str | None = None,
        fixed_usage_limits: UsageLimits | None = None,
        profile_services: ProfileServices = runtime_profile_service_for,
        terminal_units: UnitOfWorkFactory | None = None,
        step_units: UnitOfWorkFactory | None = None,
        tool_units: UnitOfWorkFactory | None = None,
        lease_alive: Callable[[], bool] | None = None,
        run_metrics: AgentRunMetrics | None = None,
        load_context: RunContextLoader = load_run_context,
    ) -> None:
        self.load_context = load_context
        self.uow_factory = uow_factory
        self.run_metrics = run_metrics or agent_run_metrics
        self.harness_registry = harness_registry
        # The shared factory by default, so a run resolves exactly as the MCP
        # bridge and the routes do -- harness included.
        self.profile_services = profile_services
        self.fallback_model_name = fallback_model_name
        self.fixed_usage_limits = fixed_usage_limits or harness_usage_limits()
        self.lease_alive = lease_alive  # see `events/turn_work.TurnFences`
        self.tool_assembler = RunToolAssembler(tool_units or uow_factory)
        self.usage_recorder = RunUsageRecorder(uow_factory)
        # `step_units`: where each step row commits (a turn's lease-fenced
        # transaction), so a worker that lost its turn cannot add to it.
        self.message_writer = RunMessageWriter(step_units or uow_factory)
        # `terminal_units`: where a run's end commits (a turn's fenced completion).
        self.finalizer = RunFinalizer(
            uow_factory, self.usage_recorder, terminal_units=terminal_units
        )
        self.event_pump = RunEventPump(self.message_writer, self.finalizer)
        self.turn_resume = TurnResume(tool_units or uow_factory, self.message_writer)

    async def execute(
        self,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        observer: AgentRunObserver | None = None,
        yield_requested: Callable[[], bool] | None = None,
    ) -> None:
        """Run ``agent_run_id`` to its end -- or, once ``yield_requested``
        (the worker going away), to the next step boundary: ``TurnYielded``."""
        conversation, agent, agent_run, messages = await self._load_run_context(
            agent_run_id=agent_run_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        run = RunIdentity.of_turn(
            conversation,
            agent_run,
            agent_run_id=agent_run_id,
            user_id=user_id,
            usage_source=usage_source_for(Audience.of(conversation)),
        )
        if agent_run.status != AgentRunStatus.RUNNING:
            await self.finalizer.finish(
                run=run,
                status=(
                    AgentRunStatus.STOPPED
                    if agent_run.status == AgentRunStatus.STOP_REQUESTED
                    else agent_run.status
                ),
                error=agent_run.error,
            )
            return
        answered = await self.turn_resume.committed_answer(agent_run_id)
        if answered is not None:
            await self.turn_resume.finish_on(
                answered, run=run, finalizer=self.finalizer
            )
            return
        await note_what_the_turn_reads(conversation.id, messages)  # before it acts
        usage_reservation: UsageReservation | None = None
        runtime_profile_snapshot: dict[str, object | None] | None = None
        run_kind = run_origin(
            metadata=conversation.metadata,
            parent_id=conversation.parent_id,
            conversation_id=conversation.id,
        ).origin
        # Known once the runtime resolves; a run that fails before that is
        # counted against no model rather than not counted.
        run_model: str | None = None
        run_started = time.monotonic()
        self.run_metrics.run_started(kind=run_kind)
        ended: RunOutcomeLabel = "failed"
        try:
            resolved_runtime = await self._runtime_for(
                conversation, agent_run, user_id=user_id
            )
            harness = self.harness_registry.get(resolved_runtime.harness_kind)
            run_model = resolved_runtime.model_name_for_harness
            outcome = RunOutcome()
            snapshot = resolved_runtime.runtime_snapshot()
            runtime_profile_snapshot = snapshot.as_payload()
            runtime_credentials = resolved_runtime.credentials or {}
            ctx = await build_run_context(
                uow_factory=self.uow_factory,
                conversation=conversation,
                agent=agent,
                agent_run=agent_run,
                user_id=user_id,
                resolved_runtime=resolved_runtime,
                runtime_profile_snapshot=runtime_profile_snapshot,
                resolve_configured_accounts=self._resolve_configured_accounts,
            )
            run = run.with_memory_after_turn(ctx.memory_after_turn)
            full_toolsets = await self.tool_assembler.assemble(
                agent=agent,
                conversation=conversation,
                vision_mode=ctx.vision_mode,
                # Already read while building the context, not loaded twice.
                grants=getattr(ctx, "grant_summary", None),
                host_execution=ctx.host_execution_mode,
                harness_kind=resolved_runtime.harness_kind,
                surface_platform=ctx.surface_platform,
            )
            # Remote harnesses reach every tool through the MCP server and keep the
            # full list; the in-process LEMMA harness shows core tools directly,
            # defers the rest over MCP and layers current-time/caching/todo.
            harness_toolsets: list[AbstractToolset[ConversationContext]] = full_toolsets
            harness_capabilities: list[AgentCapability[ConversationContext]] = []
            harness_model_settings: JsonObject | None = None
            compaction_model_settings: JsonObject | None = None
            lighter_reasoning_settings: JsonObject | None = None
            # Agent Host harnesses run their own model loop, so none of this
            # reaches them; the settings stay on the run as a record only.
            generation = run_generation_settings(
                resolved_runtime, agent_run.agent_runtime
            )
            if resolved_runtime.harness_kind == HarnessKind.LEMMA:
                harness_model_settings = generation.model_settings
                compaction_model_settings = generation.compaction_model_settings
                lighter_reasoning_settings = generation.lighter_reasoning_settings
                # The in-process harness realizes every tool surface as a
                # capability, so its toolset list is empty.
                harness_capabilities = await build_lemma_harness_tooling(
                    ctx=ctx,
                    full_toolsets=full_toolsets,
                    # Both protocols cache, by different mechanisms — see
                    # PromptCachingCapability.
                    enable_prompt_caching=(
                        resolved_runtime.profile.protocol
                        in (
                            RuntimeProfileProtocol.OPENAI_COMPATIBLE,
                            RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
                        )
                        and agent_settings.lemma_llm_caching_enabled
                    ),
                    protocol=resolved_runtime.profile.protocol,
                )
                harness_toolsets = []
            messages, notices = await self.turn_resume.settle_for_turn(
                agent_run_id,
                conversation=conversation,
                agent=agent,
                ctx=ctx,
                messages=messages,
                in_process=resolved_runtime.harness_kind == HarnessKind.LEMMA,
                reload=partial(
                    self._load_run_context,
                    agent_run_id=agent_run_id,
                    user_id=user_id,
                    pod_id=pod_id,
                    agent_name=agent_name,
                ),
            )
            usage_reservation = await self.usage_recorder.reserve(
                organization_id=conversation.organization_id,
                user_id=user_id,
                runtime_profile=runtime_profile_snapshot,
                outside_audience=ctx.audience.answers_outsiders,
            )
            run_with_usage = run.with_runtime_profile(
                runtime_profile_snapshot
            ).with_reservation(usage_reservation)
            enforced_usage_limits = self.fixed_usage_limits
            # Compaction thresholds belong to the model, not to a global
            # constant: a 70k trigger on a million-token model compacts a run
            # that had 900k to spare, and a 110k "ceiling" on a 128k model is
            # not a ceiling at all.
            context_budget = context_budget_for(resolved_runtime.model)
            options = HarnessOptions(
                model_name=resolved_runtime.model_name_for_harness,
                toolsets=harness_toolsets,
                capabilities=harness_capabilities,
                model_settings=with_reply_budget(
                    harness_model_settings, context_budget
                ),
                compaction_model_settings=compaction_model_settings,
                lighter_reasoning_settings=lighter_reasoning_settings,
                usage_limits=enforced_usage_limits,
                output_type=self._resolve_output_type(agent, conversation),
                should_stop=make_stop_checker(
                    agent_run_id, uow_factory=self.uow_factory
                ),
                spend=budget_for_run(run_with_usage),
                history_summarization_token_limit=(
                    context_budget.summarization_token_limit
                ),
                history_hard_token_ceiling=context_budget.hard_token_ceiling,
                context_window=context_budget.window,
                runtime_profile=snapshot,
                lease_alive=self.lease_alive,
                extra={
                    "runtime_credentials": runtime_credentials,
                },
            )
            options.notices.pending.extend(notices)
            tracer = trace.get_tracer(__name__)
            with agent_run_telemetry_context(
                conversation_id=conversation.id,
                agent_run_id=agent_run_id,
                agent_id=conversation.agent_id,
                pod_id=conversation.pod_id,
                organization_id=conversation.organization_id,
                user_id=user_id,
                agent_name=agent.name,
                harness_kind=resolved_runtime.harness_kind.value,
                model_name=resolved_runtime.model_name_for_harness,
            ) as telemetry_attributes:
                # `agent.run` and not `agent.run {name}`: names are chosen per
                # pod, so as a span name they would grow with the customer count
                # and break every "group by span name" view. The name is on the
                # span as `lemma.agent_name`, beside the ids.
                with tracer.start_as_current_span("agent.run") as span:
                    for key, value in telemetry_attributes.items():
                        span.set_attribute(key, value)
                    span.set_attribute(
                        SpanAttributes.OPENINFERENCE_SPAN_KIND,
                        OpenInferenceSpanKindValues.AGENT.value,
                    )
                    span.set_attribute("gen_ai.agent.name", agent.name)
                    span.set_attribute("lemma.run_kind", run_kind)
                    span.set_attribute(
                        "gen_ai.request.model",
                        resolved_runtime.model_name_for_harness,
                    )
                    record_generation_settings(
                        span, generation.effective, options.model_settings
                    )
                    # Trace summaries let operators identify a turn without opening it.
                    record_span_input(span, run_input_text(messages))
                    await notify_run_started(observer, conversation, ctx, agent_run_id)
                    raised = False
                    try:
                        run_usage_context = usage_context_from_agent_context(
                            ctx,
                            source_type=run.usage_source,
                            source_id=str(agent_run_id),
                        )
                        async with metering_execution(
                            run_usage_context, factory=self.uow_factory
                        ):
                            await drive_harness(
                                self.event_pump,
                                harness.run(
                                    agent=agent,
                                    conversation=conversation,
                                    messages=messages,
                                    ctx=ctx,
                                    options=options,
                                    agent_run_id=agent_run_id,
                                ),
                                run=run_with_usage,
                                outcome=outcome,
                                observer=observer,
                                conversation=conversation,
                                ctx=ctx,
                                on_first_token=partial(
                                    self.run_metrics.first_token, model=run_model
                                ),
                                yield_requested=(
                                    yield_requested
                                    if resolved_runtime.harness_kind
                                    == HarnessKind.LEMMA
                                    else None
                                ),
                            )
                    except BaseException:
                        # A cancellation or a shutdown yield too: nothing ended,
                        # so nothing is announced as finished.
                        raised = True
                        raise
                    finally:
                        # In `finally`, because a run that failed or was
                        # cancelled part-way is the one worth reading, and it
                        # still has whatever the model produced before it went.
                        record_span_output(span, outcome.output_data)
                        # Not announced as finished when it threw: the failure
                        # path below announces that, and "finished" would first
                        # send whatever narration the model buffered as though
                        # it were the answer. Announced even when the start
                        # failed: the finish is what sends the answer.
                        if not raised:
                            ended = ended_as(outcome.final_status)
                            await notify_run_finished(
                                observer, conversation, ctx, agent_run_id
                            )
        except BaseException as exc:
            ended = ended_by(exc)
            if is_usage_limit_error(exc):
                # Exhaustion is an expected policy outcome, not a runtime crash.
                logger.warning(
                    "agent.agent_runner_service.agent_run_quota_exhausted.degraded",
                    agent_run_id=agent_run_id,
                    exc_info=True,
                )
            elif isinstance(exc, Exception):
                logger.error(
                    "agent.agent_runner_service.agent_run_s.failed", exc_info=True
                )
            else:
                logger.warning(
                    "agent.agent_runner_service.agent_run_cancelled_timeout_or.timeout",
                    agent_run_id=agent_run_id,
                )
            # Shielded, so the write completes inside an already-cancelled
            # scope (task timeout, worker shutdown), and bounded, because an
            # uninterruptible write is how a SIGTERM'd worker hangs forever --
            # streaq's consumer never finishes, so the grace period enforcing
            # that cancellation is never reached. Seen on one mid-run SIGTERM in
            # four. `anyio.CancelScope(shield=True)` and not `asyncio.shield`:
            # the latter runs the coroutine in a new task, and the SQLAlchemy and
            # anyio scopes it touches are task-bound.
            identity = run.with_runtime_profile(
                runtime_profile_snapshot
            ).with_reservation(usage_reservation)
            # A cancellation is the worker going away, not the run being
            # wrong. A real exception still fails terminally, because retrying
            # a run that threw just throws again.
            with anyio.move_on_after(_FINALIZATION_TIMEOUT_SECONDS, shield=True):
                if run_is_not_over(exc):
                    # The worker is going away and the run is not over. Leave the
                    # status alone -- it stays RUNNING, which is what the worker
                    # that reclaims this job expects to find -- and announce
                    # nothing, because nothing has ended. Only the usage
                    # reservation goes back: the run that picks this work up
                    # takes its own, and holding both charges one conversation
                    # twice for a restart.
                    await finalize_safely(
                        self.usage_recorder.release(usage_reservation),
                        agent_run_id=agent_run_id,
                    )
                else:
                    await finalize_safely(
                        self.finalizer.finish(
                            run=identity,
                            status=AgentRunStatus.FAILED,
                            error=run_failure_message(exc),
                            error_code=run_failure_code(exc),
                            error_reason=run_failure_reason(exc),
                        ),
                        agent_run_id=agent_run_id,
                    )
                    await notify_run_failed(observer, conversation, exc, agent_run_id)
            # Re-raised on purpose (a cancellation, a lost lease, a shutdown
            # yield): the turn's work item goes back to the queue and the next
            # worker resumes it. Swallowing it made every interrupted run look
            # like a success, so a deploy ended every conversation in flight.
            if run_is_not_over(exc):
                raise
        finally:
            self.run_metrics.run_ended(
                kind=run_kind,
                outcome=ended,
                model=run_model,
                seconds=time.monotonic() - run_started,
            )

    async def _runtime_for(
        self, conversation: Conversation, agent_run: AgentRun, *, user_id: UUID
    ) -> ResolvedAgentRuntime:
        """The run's runtime -- one that runs in this process, for a stranger's turn.

        A stranger's turn never reaches a coding agent's shell, which would hold
        a token minted for the member who answers for the group; see
        ``outsider_runtime``.
        """
        resolved = await self._resolve_agent_runtime(
            agent_run.agent_runtime,
            user_id=user_id,
            organization_id=conversation.organization_id,
        )
        if not Audience.of(conversation).answers_outsiders:
            return resolved
        return await in_process_runtime(
            resolved,
            fallbacks=await default_runtimes(
                self.uow_factory, conversation.organization_id
            ),
            resolve=partial(
                self._resolve_agent_runtime,
                user_id=user_id,
                organization_id=conversation.organization_id,
            ),
        )

    async def _resolve_agent_runtime(
        self,
        agent_runtime: AgentRuntimeConfig,
        *,
        user_id: UUID,
        organization_id: UUID | None,
    ) -> ResolvedAgentRuntime:
        with run_phase("resolve_runtime"):
            async with self.uow_factory() as uow:
                return await self.profile_services(uow).resolve(
                    runtime=agent_runtime,
                    organization_id=organization_id,
                    user_id=user_id,
                )

    async def _load_run_context(
        self,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> tuple[Conversation, Agent, AgentRun, list[Message]]:
        return await self.load_context(
            self.uow_factory,
            agent_run_id=agent_run_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )

    def _resolve_output_type(
        self, agent: Agent, conversation: Conversation
    ) -> OutputSpec[object] | None:
        # TASK conversations always get the final_answer tool: it drives the task
        # lifecycle (status WAITING/COMPLETED/FAILED), not just structured output.
        # The output *schema* is only applied when the agent configures one — see
        # get_final_answer_tool, which uses `output: str` otherwise (no schema is
        # pushed to the model when output_schema is absent).
        #
        # A tool, named rather than left to the model's default: a model that
        # thinks by default would otherwise be asked for native JSON-schema
        # output, which some Anthropic-compatible gateways refuse.
        if conversation.type == ConversationType.TASK:
            return ToolOutput(get_final_answer_tool(agent))
        return None

    async def _resolve_configured_accounts(
        self,
        *,
        agent: Agent,
        user_id: UUID,
    ) -> dict[str, UUID]:
        with run_phase("configured_accounts"):
            return await AgentCallableToolFactory(
                self.uow_factory
            ).resolve_configured_accounts(agent=agent, user_id=user_id)
