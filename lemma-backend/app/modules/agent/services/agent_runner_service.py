"""Background runner for agent harness execution."""

from __future__ import annotations

from collections.abc import Sequence
import time
from typing import Awaitable, Callable, Protocol
from uuid import UUID

import anyio
from pydantic_ai import UsageLimits

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.observability.telemetry import (
    agent_run_telemetry_context,
    record_span_input,
    record_span_output,
)
from app.modules.agent.config import agent_settings
from app.modules.agent.services.conversation_access import (
    resolve_agent,
    validate_conversation_access,
)
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.errors import (
    ConversationNotFoundError,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentRuntimeConfig,
    AgentRunStatus,
    ConversationType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
)
from app.modules.agent.capabilities import build_lemma_harness_tooling
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    ResolvedAgentRuntime,
)
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.services.run_phase_spans import (
    observe_first_output,
    record_history_size,
    run_phase,
)
from app.modules.agent.services.runtime_history import (
    apply_surface_history_window,
    runtime_full_run_ids,
    select_runtime_history,
)
from app.modules.agent.services.run_context_builder import build_run_context
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_finalizer import (
    is_usage_limit_error,
    RunFinalizer,
    finalize_safely,
    run_failure_message,
)
from app.modules.agent.services.run_observer_delivery import (
    notify_run_failed,
    notify_run_finished,
    notify_run_started,
)
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.composition.agent_usage import (
    UsageReservation,
    usage_context_from_agent_context,
    usage_execution_context,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.final_answer import get_final_answer_tool
from app.modules.agent.tools.tool_assembler import RunToolAssembler
from app.core.crypto import get_secret_cipher

logger = get_logger(__name__)

# Ceiling on the shielded finalization write. Comfortably longer than the write
# takes and comfortably inside the worker's shutdown grace period, so a healthy
# run always finalizes and a wedged one still lets the process exit.
_FINALIZATION_TIMEOUT_SECONDS = 8.0


def _run_input_text(messages: Sequence[Message]) -> str | None:
    """The prompt this run is answering: the last thing the user said.

    The harness is handed the whole selected history, but a trace's input is the
    turn, not the transcript -- the earlier turns are already their own traces in
    the same session. Tool returns and thinking blocks are skipped for the same
    reason: they are rows in the run, not the thing that started it.
    """
    for message in reversed(messages):
        if message.role is not MessageRole.USER:
            continue
        if message.kind is not MessageKind.TEXT:
            continue
        text = (message.text or "").strip()
        if text:
            return text
    return None


def _profile_model_settings(
    runtime_profile_snapshot: dict[str, object | None] | None,
) -> JsonObject | None:
    """Pull the model_settings dict out of a resolved runtime profile snapshot."""
    if not isinstance(runtime_profile_snapshot, dict):
        return None
    config = runtime_profile_snapshot.get("config")
    if not isinstance(config, dict):
        return None
    model_settings = config.get("model_settings")
    return (
        model_settings if isinstance(model_settings, dict) and model_settings else None
    )


class AgentRunObserver(Protocol):
    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

    async def on_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

    async def on_run_finished(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

    async def on_run_failed(
        self,
        conversation: Conversation,
        error: Exception,
    ) -> None:
        raise NotImplementedError


class AgentRunnerService:
    """Executes one persisted agent run and persists harness messages."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        harness_registry: HarnessRegistry,
        fallback_model_name: str | None = None,
        fixed_usage_limits: UsageLimits | None = None,
    ):
        self.uow_factory = uow_factory
        self.harness_registry = harness_registry
        self.fallback_model_name = fallback_model_name
        self.fixed_usage_limits = fixed_usage_limits or UsageLimits(request_limit=200)
        self.tool_assembler = RunToolAssembler(uow_factory)
        self.usage_recorder = RunUsageRecorder(uow_factory)
        self.message_writer = RunMessageWriter(uow_factory)
        self.finalizer = RunFinalizer(uow_factory, self.usage_recorder)
        self.event_pump = RunEventPump(self.message_writer, self.finalizer)

    async def execute(
        self,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        observer: AgentRunObserver | None = None,
    ) -> None:
        conversation, agent, agent_run, messages = await self._load_run_context(
            agent_run_id=agent_run_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        run = RunIdentity(
            conversation_id=conversation.id,
            agent_run_id=agent_run_id,
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            user_id=user_id,
            agent_id=conversation.agent_id,
            started_at=agent_run.started_at,
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
        usage_reservation: UsageReservation | None = None
        runtime_profile_snapshot: dict[str, object | None] | None = None
        try:
            resolved_runtime = await self._resolve_agent_runtime(
                agent_run.agent_runtime,
                user_id=user_id,
                organization_id=conversation.organization_id,
            )
            harness = self.harness_registry.get(resolved_runtime.harness_kind)
            outcome = RunOutcome()
            runtime_profile_snapshot = resolved_runtime.public_snapshot()
            runtime_credentials = resolved_runtime.credentials or {}
            ctx = await build_run_context(
                uow_factory=self.uow_factory,
                conversation=conversation,
                agent=agent,
                agent_run_id=agent_run_id,
                user_id=user_id,
                resolved_runtime=resolved_runtime,
                runtime_profile_snapshot=runtime_profile_snapshot,
                runtime_credentials=runtime_credentials,
                resolve_configured_accounts=self._resolve_configured_accounts,
            )
            full_toolsets = await self.tool_assembler.assemble(
                agent=agent,
                conversation=conversation,
                vision_mode=ctx.vision_mode,
                # Already read while building the context; the assembler would
                # otherwise load the same grants again on every run.
                grants=getattr(ctx, "grant_summary", None),
            )
            # Remote harnesses (Codex/Claude-Code) reach every tool through the MCP
            # server, so they keep the full toolset list. The in-process LEMMA
            # harness instead shows core tools directly and defers the heavy "extra"
            # tools over MCP, layering current-time/caching/todo capabilities.
            harness_toolsets: list[object] = full_toolsets
            harness_capabilities: list[object] = []
            harness_model_settings: JsonObject | None = None
            if resolved_runtime.harness_kind == HarnessKind.LEMMA:
                harness_model_settings = _profile_model_settings(
                    runtime_profile_snapshot
                )
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
                        and settings.lemma_llm_caching_enabled
                    ),
                    protocol=resolved_runtime.profile.protocol,
                )
                harness_toolsets = []
            usage_reservation = await self.usage_recorder.reserve(
                organization_id=conversation.organization_id,
                user_id=user_id,
                runtime_profile=runtime_profile_snapshot,
            )
            run_with_usage = run.with_runtime_profile(
                runtime_profile_snapshot
            ).with_reservation(usage_reservation)
            enforced_usage_limits = self.fixed_usage_limits
            options = HarnessOptions(
                model_name=resolved_runtime.model_name_for_harness,
                toolsets=harness_toolsets,
                capabilities=harness_capabilities,
                model_settings=harness_model_settings,
                usage_limits=enforced_usage_limits,
                output_type=self._resolve_output_type(agent, conversation),
                should_stop=self._make_stop_checker(agent_run_id),
                extra={
                    "runtime_profile": runtime_profile_snapshot,
                    "runtime_credentials": runtime_credentials,
                },
            )
            observer_started = False
            harness_agent = self._agent_with_resolved_runtime_metadata(
                agent,
                resolved_runtime=resolved_runtime,
            )
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
                with tracer.start_as_current_span("agent.run") as span:
                    for key, value in telemetry_attributes.items():
                        span.set_attribute(key, value)
                    span.set_attribute(
                        SpanAttributes.OPENINFERENCE_SPAN_KIND,
                        OpenInferenceSpanKindValues.AGENT.value,
                    )
                    span.set_attribute("gen_ai.agent.name", agent.name)
                    span.set_attribute(
                        "gen_ai.request.model",
                        resolved_runtime.model_name_for_harness,
                    )
                    # What a trace UI shows as the run's input and output. Without
                    # them a session reads as a column of timestamps: the turns are
                    # grouped correctly and every row is blank, so finding the run
                    # you want means opening each one.
                    record_span_input(span, _run_input_text(messages))
                    observer_started = await notify_run_started(
                        observer, conversation, ctx, agent_run_id
                    )
                    try:
                        run_usage_context = usage_context_from_agent_context(
                            ctx,
                            source_type="agent_run",
                            source_id=str(agent_run_id),
                        )
                        with usage_execution_context(run_usage_context):
                            await self.event_pump.drive(
                                observe_first_output(
                                    harness.run(
                                        agent=harness_agent,
                                        conversation=conversation,
                                        messages=messages,
                                        ctx=ctx,
                                        options=options,
                                        agent_run_id=agent_run_id,
                                    )
                                ),
                                run=run_with_usage,
                                outcome=outcome,
                                observer=observer,
                                conversation=conversation,
                                ctx=ctx,
                            )
                    finally:
                        # In `finally`, because a run that failed or was
                        # cancelled part-way is the one worth reading, and it
                        # still has whatever the model produced before it went.
                        record_span_output(span, outcome.output_data)
                        if observer_started:
                            await notify_run_finished(
                                observer, conversation, ctx, agent_run_id
                            )
        except BaseException as exc:
            if is_usage_limit_error(exc):
                # Not a crash: the organisation is out of plan quota. This was
                # the single most common "error" in production (154 in a week),
                # logged at ERROR with a stack trace and shown to the user as
                # "check the agent runtime configuration" — which sent people
                # debugging a system that was working exactly as designed.
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
            # Finalize the run, shielding the DB write so it completes even when
            # we're inside a cancelled cancel scope (streaq task timeout / worker
            # shutdown). We use anyio.CancelScope(shield=True) — same task as the
            # surrounding anyio scope — so the write runs to completion in-task.
            # (asyncio.shield is wrong here: it runs the coroutine in a NEW task,
            # and the SQLAlchemy/anyio cancel scopes it touches are task-bound,
            # raising "exit cancel scope in a different task".) The worker
            # grace_period gives this write time before the engine is disposed;
            # reconcile_orphaned_agent_runs is the backstop if it still loses.
            #
            # We deliberately do NOT re-raise CancelledError. The run is
            # finalized; re-raising propagates into streaq's `with scope:` block,
            # triggering a scope-corruption RuntimeError that crashes the worker.
            # Side effect: streaq records the interrupted job as *succeeded*
            # (no retry). That is intentional — the app DB (this FAILED write)
            # is the source of truth; interrupted runs fail terminally and the
            # user re-asks rather than the job silently re-running on another pod.
            # Shielded *and* bounded. The shield is what lets the write finish
            # while the surrounding scope is already cancelled; without a
            # deadline it also makes the write uninterruptible, and an
            # uninterruptible write is how a SIGTERM'd worker hangs forever:
            # streaq's consumer cannot finish, so its task group never exits and
            # the lifespan teardown is never reached — no grace period applies,
            # because the grace period is enforced by the very cancellation this
            # scope is ignoring. Observed on roughly one mid-run SIGTERM in four.
            #
            # Losing the write is survivable; `reconcile_orphaned_agent_runs`
            # already exists to finish runs that were interrupted. Losing the
            # worker is not: the platform SIGKILLs it and every other in-flight
            # run on that process dies with it.
            with anyio.move_on_after(_FINALIZATION_TIMEOUT_SECONDS, shield=True):
                await finalize_safely(
                    self.finalizer.finish(
                        run=run.with_runtime_profile(
                            runtime_profile_snapshot
                        ).with_reservation(usage_reservation),
                        status=AgentRunStatus.FAILED,
                        error=run_failure_message(exc),
                    ),
                    agent_run_id=agent_run_id,
                )
                await notify_run_failed(observer, conversation, exc, agent_run_id)

    async def _resolve_agent_runtime(
        self,
        agent_runtime: AgentRuntimeConfig,
        *,
        user_id: UUID,
        organization_id: UUID | None,
    ) -> ResolvedAgentRuntime:
        with run_phase("resolve_runtime"):
            async with self.uow_factory() as uow:
                service = AgentRuntimeProfileService(
                    AgentRuntimeProfileRepository(
                        uow,
                        encryption=get_secret_cipher(),
                    )
                )
                return await service.resolve(
                    runtime=agent_runtime,
                    organization_id=organization_id,
                    user_id=user_id,
                )

    def _agent_with_resolved_runtime_metadata(
        self,
        agent: Agent,
        *,
        resolved_runtime: ResolvedAgentRuntime,
    ) -> Agent:
        del resolved_runtime
        return agent

    async def _should_stop_run(self, agent_run_id: UUID) -> bool:
        async with self.uow_factory() as uow:
            agent_run = await ConversationRepository(uow).get_agent_run(agent_run_id)
        return agent_run is not None and agent_run.status in {
            AgentRunStatus.STOP_REQUESTED,
            AgentRunStatus.STOPPED,
        }

    def _make_stop_checker(self, agent_run_id: UUID) -> Callable[[], Awaitable[bool]]:
        """Build a throttled, sticky stop checker for the harness.

        The harness polls ``should_stop`` at every streaming checkpoint (per
        token delta, part, and tool call). Querying the DB on every checkpoint
        issues one ``SELECT`` per token across every concurrent run, churning the
        connection pool — the dominant per-token DB load under streaming. Cache
        the answer and re-query at most once per
        ``agent_run_stop_poll_interval_seconds``; once a stop is observed it
        sticks (no further queries). A stop request is still honored within the
        poll interval. Interval ``0`` disables throttling (every call queries).
        """
        interval = agent_settings.agent_run_stop_poll_interval_seconds
        stopped = False
        last_checked: float | None = None

        async def _check() -> bool:
            nonlocal stopped, last_checked
            if stopped:
                return True
            now = time.monotonic()
            if last_checked is not None and (now - last_checked) < interval:
                return False
            last_checked = now
            if await self._should_stop_run(agent_run_id):
                stopped = True
                return True
            return False

        return _check

    async def _load_run_context(
        self,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> tuple[Conversation, Agent, AgentRun, list[Message]]:
        with run_phase("load_context") as span:
            async with self.uow_factory() as uow:
                repo = ConversationRepository(uow)
                runs = await repo.load_runtime_history_digests_by_run_id(agent_run_id)
                agent_run = self._find_agent_run(runs, agent_run_id)
                conversation = await repo.get_conversation(agent_run.conversation_id)
                validate_conversation_access(
                    conversation,
                    user_id=user_id,
                    pod_id=pod_id,
                )
                agent = await resolve_agent(
                    conversation,
                    user_id=user_id,
                    agent_repository=AgentRepository(uow),
                    agent_name=agent_name,
                )
                # Which runs survive the trim decides which need every message,
                # and the trim can keep an old-but-active run while dropping
                # newer ones -- so it has to run before the messages are asked
                # for, not after.
                await repo.attach_runtime_history_messages(
                    runs, full_run_ids=runtime_full_run_ids(runs, conversation)
                )
                agent_run = self._find_agent_run(runs, agent_run_id)
                messages = self._select_runtime_history(runs, conversation)
                record_history_size(span, runs=runs, sent=messages)
                return conversation, agent, agent_run, messages

    def _find_agent_run(self, runs: list[AgentRun], agent_run_id: UUID) -> AgentRun:
        for run in runs:
            if run.id == agent_run_id:
                return run
        raise ConversationNotFoundError()

    def _apply_surface_history_window(
        self, runs: list[AgentRun], conversation: Conversation | None
    ) -> list[AgentRun]:
        return apply_surface_history_window(runs, conversation)

    def _select_runtime_history(
        self, runs: list[AgentRun], conversation: Conversation | None = None
    ) -> list[Message]:
        return select_runtime_history(runs, conversation)

    def _resolve_output_type(
        self, agent: Agent, conversation: Conversation
    ) -> object | None:
        # TASK conversations always get the final_answer tool: it drives the task
        # lifecycle (status WAITING/COMPLETED/FAILED), not just structured output.
        # The output *schema* is only applied when the agent configures one — see
        # get_final_answer_tool, which uses `output: str` otherwise (no schema is
        # pushed to the model when output_schema is absent).
        if conversation.type == ConversationType.TASK:
            return get_final_answer_tool(agent)
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
