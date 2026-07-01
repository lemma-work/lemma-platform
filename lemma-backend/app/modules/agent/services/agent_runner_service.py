"""Background runner for agent harness execution."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Awaitable, Callable, Protocol
from uuid import UUID

import anyio
from pydantic_ai import UsageLimits

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.publisher import EventPublisher
from app.core.log.log import get_logger
from app.core.observability.telemetry import agent_run_telemetry_context
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRuntimeConfig,
    AgentRunUsage,
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    JsonValue,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
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
from app.modules.agent.services.conversation_service import (
    _POD_ASSISTANT_AGENT_ID,
)
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.realtime import (
    completed_payload,
    error_payload,
    message_payload,
    publish_conversation_event,
    status_payload,
    token_payload,
)
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.agent.services.workspace_location import (
    resolve_pod_cwd,
    resolve_workspace_location,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.callable_tool_factory import AgentCallableToolFactory
from app.modules.agent.tools.final_answer import get_final_answer_tool
from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS
from app.modules.agent.tools.workspace_cli.pydantic_adapter import view_image_toolset
from app.modules.agent.tools.tool_assembler import RunToolAssembler
from app.core.crypto import get_secret_cipher
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.usage.domain.entities import UsageReservation
from app.modules.usage.services.usage_context import (
    usage_context_from_agent_context,
    usage_execution_context,
)

logger = get_logger(__name__)

FULL_HISTORY_AGENT_RUN_COUNT = 5


async def _finalize_safely(coro: Awaitable[None], *, agent_run_id: UUID) -> None:
    """Await a finalization coroutine, swallowing all errors.

    Used inside ``asyncio.shield()`` so the coroutine completes even when the
    caller is cancelled. Any exception (DB error, cancellation) is logged and
    suppressed — finalization must never crash the worker.
    """
    try:
        await coro
    except asyncio.CancelledError:
        logger.warning(
            "Agent run finalization cancelled run=%s", agent_run_id
        )
    except Exception as exc:
        logger.error(
            "Agent run finalization failed run=%s: %s",
            agent_run_id,
            exc,
            exc_info=True,
        )


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
    return model_settings if isinstance(model_settings, dict) and model_settings else None


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
        if agent_run.status != AgentRunStatus.RUNNING:
            await self._finish_agent_run(
                conversation_id=conversation.id,
                agent_run_id=agent_run_id,
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
            output_data: JsonValue | None = None
            final_status: ConversationStatus | None = None
            final_error: str | None = None
            usage_data: AgentRunUsage | None = None
            surface_context = self._surface_context_from_conversation(conversation)
            runtime_profile_snapshot = resolved_runtime.public_snapshot()
            runtime_credentials = resolved_runtime.credentials or {}
            workspace_location = resolve_workspace_location(conversation)
            pod_cwd = resolve_pod_cwd(conversation)
            ctx = ConversationContext(
                user_id=user_id,
                org_id=conversation.organization_id,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                agent_name=agent.name,
                agent_run_id=agent_run_id,
                workload_type="agent",
                workload_id=agent.id,
                configured_accounts=await self._resolve_configured_accounts(
                    agent=agent,
                    user_id=user_id,
                ),
                runtime_profile=runtime_profile_snapshot,
                runtime_credentials=runtime_credentials,
                workspace_id=workspace_location.workspace_id,
                workspace_cwd=workspace_location.cwd,
                pod_cwd=pod_cwd,
                # Only the in-process pydantic (LEMMA) harness catches the
                # ask_user/request_approval pause signal; daemon harnesses run the
                # tools over MCP and own their own session, so they can't be paused
                # mid tool-call and use the WAITING output contract instead.
                supports_pause_signal=(
                    resolved_runtime.harness_kind == HarnessKind.LEMMA
                ),
                is_pod_default_agent=(agent.id == _POD_ASSISTANT_AGENT_ID),
                **surface_context,
            )
            try:
                ctx.context_brief = await AgentContextBriefBuilder(
                    self.uow_factory
                ).build(
                    agent=agent,
                    conversation=conversation,
                    user_id=user_id,
                    pod_id=conversation.pod_id,
                )
            except Exception as exc:
                logger.warning("Failed to build agent context brief: %s", exc)
            full_toolsets = await self.tool_assembler.assemble(
                agent=agent,
                conversation=conversation,
            )
            # `view_image` is a standalone toolset, appended for any agent/harness
            # whose resolved model declares VISION support — independent of the
            # agent's configured toolsets (see AgentToolset.VIEW_IMAGE).
            supports_vision = (
                resolved_runtime.model is not None
                and RuntimeModelCapability.VISION in resolved_runtime.model.capabilities
            )
            if supports_vision and view_image_toolset not in full_toolsets:
                full_toolsets = [*full_toolsets, view_image_toolset]
            # Daemon harnesses (Codex/Claude-Code) reach every tool through the MCP
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
                    uow_factory=self.uow_factory,
                    agent=agent,
                    ctx=ctx,
                    full_toolsets=full_toolsets,
                    agent_run_id=agent_run_id,
                    model_name=resolved_runtime.model_name_for_harness,
                    enable_prompt_caching=(
                        resolved_runtime.profile.protocol
                        == RuntimeProfileProtocol.OPENAI_COMPATIBLE
                        and settings.lemma_llm_caching_enabled
                    ),
                )
                harness_toolsets = []
            options = HarnessOptions(
                model_name=resolved_runtime.model_name_for_harness,
                toolsets=harness_toolsets,
                capabilities=harness_capabilities,
                model_settings=harness_model_settings,
                usage_limits=self.fixed_usage_limits,
                output_type=self._resolve_output_type(agent, conversation),
                should_stop=self._make_stop_checker(agent_run_id),
                extra={
                    "runtime_profile": runtime_profile_snapshot,
                    "runtime_credentials": runtime_credentials,
                },
            )
            usage_reservation = await self.usage_recorder.reserve(
                organization_id=conversation.organization_id,
                user_id=user_id,
                runtime_profile=runtime_profile_snapshot,
            )

            terminal_event_seen = False
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
                    if observer is not None:
                        try:
                            await observer.on_run_started(conversation, ctx)
                            observer_started = True
                        except Exception as exc:
                            logger.warning(
                                "Agent run observer start failed run=%s: %s",
                                agent_run_id,
                                exc,
                            )
                    try:
                        run_usage_context = usage_context_from_agent_context(
                            ctx,
                            source_type="agent_run",
                            source_id=str(agent_run_id),
                        )
                        with usage_execution_context(run_usage_context):
                            async for event in harness.run(
                                agent=harness_agent,
                                conversation=conversation,
                                messages=messages,
                                ctx=ctx,
                                options=options,
                                agent_run_id=agent_run_id,
                            ):
                                if terminal_event_seen:
                                    continue
                                if observer is not None:
                                    try:
                                        await observer.on_event(event, conversation, ctx)
                                    except Exception as exc:
                                        logger.warning(
                                            "Agent run observer failed run=%s event=%s: %s",
                                            agent_run_id,
                                            event.type,
                                            exc,
                                        )
                                if event.type == AgentEventType.USAGE:
                                    if isinstance(event.data, AgentRunUsage):
                                        usage_data = event.data
                                    elif isinstance(event.data, dict):
                                        usage_data = AgentRunUsage.model_validate(
                                            event.data
                                        )
                                    continue
                                should_stop = await self._handle_harness_event(
                                    event=event,
                                    conversation_id=conversation.id,
                                    agent_run_id=agent_run_id,
                                    output_data=output_data,
                                    final_status=final_status,
                                    final_error=final_error,
                                    usage_data=usage_data,
                                    organization_id=conversation.organization_id,
                                    pod_id=conversation.pod_id,
                                    user_id=user_id,
                                    agent_id=conversation.agent_id,
                                    started_at=agent_run.started_at,
                                    runtime_profile=runtime_profile_snapshot,
                                    usage_reservation=usage_reservation,
                                )
                                if event.type == AgentEventType.MESSAGE:
                                    saved_output = (
                                        self.message_writer.output_data_from_event(event)
                                    )
                                    if saved_output is not None:
                                        output_data = saved_output
                                    event_final_status, event_final_error = (
                                        self.message_writer.final_status_from_event(event)
                                    )
                                    if event_final_status is not None:
                                        final_status = event_final_status
                                    if event_final_error:
                                        final_error = event_final_error
                                if should_stop:
                                    terminal_event_seen = True
                    finally:
                        if observer is not None and observer_started:
                            try:
                                await observer.on_run_finished(conversation, ctx)
                            except Exception as exc:
                                logger.warning(
                                    "Agent run observer finish failed run=%s: %s",
                                    agent_run_id,
                                    exc,
                                )
        except BaseException as exc:
            if isinstance(exc, Exception):
                logger.error("Agent run failed: %s", exc, exc_info=True)
            else:
                logger.warning(
                    "Agent run cancelled (timeout or shutdown): run=%s", agent_run_id
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
            with anyio.CancelScope(shield=True):
                await _finalize_safely(
                    self._finish_agent_run(
                        conversation_id=conversation.id,
                        agent_run_id=agent_run_id,
                        status=AgentRunStatus.FAILED,
                        error=(
                            "Agent run failed. Please check the agent runtime configuration."
                            if isinstance(exc, Exception)
                            else "Agent run was interrupted (timeout or shutdown)"
                        ),
                        organization_id=conversation.organization_id,
                        pod_id=conversation.pod_id,
                        user_id=user_id,
                        agent_id=conversation.agent_id,
                        started_at=agent_run.started_at,
                        runtime_profile=runtime_profile_snapshot,
                        usage_reservation=usage_reservation,
                    ),
                    agent_run_id=agent_run_id,
                )

    async def _resolve_agent_runtime(
        self,
        agent_runtime: AgentRuntimeConfig,
        *,
        user_id: UUID,
        organization_id: UUID | None,
    ) -> ResolvedAgentRuntime:
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
        return (
            agent_run is not None
            and agent_run.status
            in {AgentRunStatus.STOP_REQUESTED, AgentRunStatus.STOPPED}
        )

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
        interval = settings.agent_run_stop_poll_interval_seconds
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
        async with self.uow_factory() as uow:
            repo = ConversationRepository(uow)
            runs = await repo.list_agent_runs_with_messages_by_run_id(agent_run_id)
            agent_run = self._find_agent_run(runs, agent_run_id)
            conversation = await repo.get_conversation(agent_run.conversation_id)
            self._validate_conversation_access(
                conversation,
                user_id=user_id,
                pod_id=pod_id,
            )
            agent = await self._resolve_agent(
                uow=uow,
                conversation=conversation,
                user_id=user_id,
                agent_name=agent_name,
            )
            messages = self._select_runtime_history(runs)
            return conversation, agent, agent_run, messages

    async def _handle_harness_event(
        self,
        *,
        event: AgentEvent,
        conversation_id: UUID,
        agent_run_id: UUID,
        output_data: JsonValue | None = None,
        final_status: ConversationStatus | None = None,
        final_error: str | None = None,
        usage_data: AgentRunUsage | None = None,
        organization_id: UUID | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        started_at: datetime | None = None,
        runtime_profile: dict[str, object | None] | None = None,
        usage_reservation: UsageReservation | None = None,
    ) -> bool:
        if event.type == AgentEventType.TOKEN:
            token_kind = "text"
            token_data = event.data
            if isinstance(event.data, dict):
                raw_kind = event.data.get("kind")
                if raw_kind is not None:
                    token_kind = str(raw_kind)
                token_data = event.data.get("data", "")
            await publish_conversation_event(
                conversation_id,
                token_payload(agent_run_id, str(token_data), kind=token_kind),
            )
            return False

        if event.type == AgentEventType.MESSAGE:
            saved_message = await self.message_writer.persist(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                data=event.data,
            )
            await publish_conversation_event(
                conversation_id,
                message_payload(agent_run_id, message_to_payload(saved_message)),
            )
            return False

        if event.type == AgentEventType.STATUS:
            await self._persist_status_event_metadata(
                conversation_id=conversation_id,
                data=event.data,
            )
            if (
                isinstance(event.data, dict)
                and event.data.get("status")
                in {"daemon.session.started", "daemon.session.invalid"}
            ):
                return False
            await publish_conversation_event(
                conversation_id,
                status_payload(
                    agent_run_id,
                    event.data
                    if isinstance(event.data, dict)
                    else {"status": str(event.data)},
                ),
            )
            return False

        if event.type == AgentEventType.ERROR:
            await self._finish_agent_run(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                status=AgentRunStatus.FAILED,
                error=str(event.data),
                usage_data=usage_data,
                organization_id=organization_id,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                started_at=started_at,
                runtime_profile=runtime_profile,
                usage_reservation=usage_reservation,
            )
            return True

        if event.type == AgentEventType.WAITING:
            # The agent paused for the user (ask_user / request_approval). Finish
            # this run as COMPLETED but flip the conversation to WAITING — the
            # proven final_answer pause shape. The pending tool call is already
            # persisted; resolving it starts a fresh run that resumes from history.
            await self._finish_agent_run(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                status=AgentRunStatus.COMPLETED,
                conversation_status=ConversationStatus.WAITING,
                output_data=output_data,
                usage_data=usage_data,
                organization_id=organization_id,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                started_at=started_at,
                runtime_profile=runtime_profile,
                usage_reservation=usage_reservation,
            )
            return True

        if event.type in {AgentEventType.COMPLETED, AgentEventType.STOPPED}:
            await self._finish_agent_run(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                status=(
                    AgentRunStatus.STOPPED
                    if event.type == AgentEventType.STOPPED
                    else AgentRunStatus.FAILED
                    if final_status == ConversationStatus.FAILED
                    else AgentRunStatus.COMPLETED
                ),
                conversation_status=final_status,
                error=final_error,
                output_data=output_data,
                usage_data=usage_data,
                organization_id=organization_id,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                started_at=started_at,
                runtime_profile=runtime_profile,
                usage_reservation=usage_reservation,
            )
            return True

        return False

    async def _persist_status_event_metadata(
        self,
        *,
        conversation_id: UUID,
        data: object,
    ) -> None:
        if not isinstance(data, dict):
            return
        status = data.get("status")
        if status not in {"daemon.session.started", "daemon.session.invalid"}:
            return
        local_session = data.get("local_session")
        if not isinstance(local_session, dict):
            return
        harness_kind = str(local_session.get("harness_kind") or "")
        session_id = str(local_session.get("session_id") or "")
        if not harness_kind or not session_id:
            return
        async with self.uow_factory() as uow:
            repo = ConversationRepository(uow)
            conversation = await repo.get_conversation(conversation_id)
            if conversation is None:
                return
            metadata: JsonObject = (
                dict(conversation.metadata)
                if isinstance(conversation.metadata, dict)
                else {}
            )
            if status == "daemon.session.invalid":
                existing = metadata.get("daemon_session")
                if (
                    isinstance(existing, dict)
                    and str(existing.get("harness_kind") or "") == harness_kind
                    and str(existing.get("session_id") or "") == session_id
                ):
                    metadata.pop("daemon_session", None)
                sessions = metadata.get("daemon_sessions")
                if isinstance(sessions, dict):
                    legacy = sessions.get(harness_kind)
                    if (
                        isinstance(legacy, dict)
                        and str(legacy.get("session_id") or "") == session_id
                    ):
                        sessions.pop(harness_kind, None)
                    if sessions:
                        metadata["daemon_sessions"] = sessions
                    else:
                        metadata.pop("daemon_sessions", None)
            else:
                metadata["daemon_session"] = {
                    "session_id": session_id,
                    "harness_kind": harness_kind,
                }
                metadata.pop("daemon_sessions", None)
            conversation.metadata = metadata
            await repo.update_conversation(conversation)

    async def _finish_agent_run(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        output_data: JsonValue | None = None,
        usage_data: AgentRunUsage | None = None,
        organization_id: UUID | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        started_at: datetime | None = None,
        runtime_profile: dict[str, object | None] | None = None,
        usage_reservation: UsageReservation | None = None,
    ) -> None:
        try:
            async with self.uow_factory() as uow:
                finish_result = await ConversationRepository(uow).finish_agent_run(
                    agent_run_id=agent_run_id,
                    status=status,
                    conversation_status=conversation_status,
                    error=error,
                    output_data=output_data,
                )
            if finish_result is None or not finish_result.updated:
                await self.usage_recorder.release(usage_reservation)
                return
            status = finish_result.status
            conversation_status = finish_result.conversation_status
            if status == AgentRunStatus.STOPPED:
                error = None
            event_data: JsonObject = {}
            if error:
                event_data["error"] = error
            if output_data is not None:
                event_data["output_data"] = output_data
            event_data["conversation_status"] = conversation_status.value
            event = AgentRunCompletedEvent(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                status=status,
                data=event_data or None,
            )
            if status == AgentRunStatus.FAILED:
                await publish_conversation_event(
                    conversation_id,
                    error_payload(agent_run_id, error or "Agent run failed"),
                )
            await publish_conversation_event(
                conversation_id,
                completed_payload(
                    conversation_id=conversation_id,
                    agent_run_id=agent_run_id,
                    status=status.value,
                    data=event_data or None,
                ),
            )
            await self._publish_lifecycle_event(event)
            await self._publish_usage_event(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                status=status,
                usage_data=usage_data,
                organization_id=organization_id,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                started_at=started_at,
                runtime_profile=runtime_profile,
                usage_reservation=usage_reservation,
            )
        except Exception as exc:
            logger.error(
                "Failed to finalize agent run run=%s: %s", agent_run_id, exc, exc_info=True
            )
            try:
                await self.usage_recorder.release(usage_reservation)
            except Exception as release_exc:
                logger.warning(
                    "Failed to release usage reservation run=%s: %s",
                    agent_run_id,
                    release_exc,
                )
            return

    async def _publish_lifecycle_event(self, event: object) -> None:
        await EventPublisher.publish(AGENT_EVENTS_STREAM, event)

    async def _publish_usage_event(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        status: AgentRunStatus,
        usage_data: AgentRunUsage | None,
        organization_id: UUID | None,
        pod_id: UUID | None,
        user_id: UUID | None,
        agent_id: UUID | None,
        started_at: datetime | None,
        runtime_profile: dict[str, object | None] | None,
        usage_reservation: UsageReservation | None,
    ) -> None:
        if usage_data is None or pod_id is None or user_id is None:
            await self.usage_recorder.release(usage_reservation)
            return
        if (
            usage_data.input_tokens <= 0
            and usage_data.output_tokens <= 0
            and usage_data.units <= 0
        ):
            await self.usage_recorder.release(usage_reservation)
            return
        context = usage_context_from_agent_context(
            ConversationContext(
                user_id=user_id,
                org_id=organization_id,
                pod_id=pod_id,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                workload_type="agent",
                workload_id=agent_id,
            ),
            source_type="agent_run",
            source_id=str(agent_run_id),
        )
        await self.usage_recorder.record(
            ctx=context,
            runtime_profile=runtime_profile,
            usage_data=usage_data,
            status=status.value,
            reservation=usage_reservation,
        )

    async def _resolve_agent(
        self,
        *,
        uow,
        conversation: Conversation,
        user_id: UUID,
        agent_name: str | None,
    ) -> Agent:
        if conversation.agent_id is None:
            return Agent(
                id=_POD_ASSISTANT_AGENT_ID,
                pod_id=conversation.pod_id,
                user_id=user_id,
                name=DEFAULT_POD_AGENT_NAME,
                instruction="",
                agent_runtime=conversation.agent_runtime,
                toolsets=list(POD_DEFAULT_AGENT_TOOLSETS),
            )
        agent = await AgentRepository(uow).get(conversation.agent_id)
        if agent is None:
            raise AgentNotFoundError(str(conversation.agent_id))
        if agent_name is not None and agent.name != agent_name:
            raise AgentNotFoundError(agent_name)
        return agent

    def _validate_conversation_access(
        self,
        conversation: Conversation | None,
        *,
        user_id: UUID,
        pod_id: UUID,
    ) -> None:
        if conversation is None:
            raise ConversationNotFoundError()
        if conversation.user_id != user_id:
            raise ConversationNotFoundError()
        if conversation.pod_id != pod_id:
            raise ConversationNotFoundError()

    def _find_agent_run(self, runs: list[AgentRun], agent_run_id: UUID) -> AgentRun:
        for run in runs:
            if run.id == agent_run_id:
                return run
        raise ConversationNotFoundError()

    def _select_runtime_history(self, runs: list[AgentRun]) -> list[Message]:
        if len(runs) <= FULL_HISTORY_AGENT_RUN_COUNT:
            return [message for run in runs for message in run.ordered_messages()]

        recent_run_ids = {run.id for run in runs[-FULL_HISTORY_AGENT_RUN_COUNT:]}
        selected: list[Message] = []
        for run in runs:
            messages = run.ordered_messages()
            if not messages:
                continue
            if run.id in recent_run_ids or len(messages) <= 2:
                selected.extend(messages)
                continue

            skipped_count = max(0, len(messages) - 2)
            selected.append(messages[0])
            selected.append(
                Message(
                    conversation_id=run.conversation_id,
                    sequence=messages[0].sequence,
                    agent_run_id=run.id,
                    role=MessageRole.SYSTEM.value,
                    kind=MessageKind.NOTIFICATION,
                    text=(
                        "Earlier agent run summarized: "
                        f"worked through {skipped_count} intermediate messages."
                    ),
                    metadata={
                        "synthetic": True,
                        "summary_kind": "agent_run_middle_elision",
                        "elided_message_count": skipped_count,
                    },
                )
            )
            selected.append(messages[-1])
        return selected

    def _surface_context_from_conversation(
        self,
        conversation: Conversation,
    ) -> JsonObject:
        metadata = conversation.metadata or {}
        surface_id = metadata.get("surface_id")
        surface_metadata_payload = metadata.get("surface_event_metadata")
        surface_metadata = None
        if isinstance(surface_metadata_payload, dict):
            try:
                from app.modules.agent_surfaces.domain.surface_event_metadata import (
                    SurfaceEventMetadata,
                )
                from pydantic import TypeAdapter

                surface_metadata = TypeAdapter(SurfaceEventMetadata).validate_python(
                    surface_metadata_payload
                )
            except Exception:
                surface_metadata = surface_metadata_payload
        return {
            "surface_id": UUID(str(surface_id)) if surface_id else None,
            "surface_platform": metadata.get("surface_platform"),
            "surface_metadata": surface_metadata,
            "external_channel_id": metadata.get("external_channel_id"),
            "external_thread_id": metadata.get("external_thread_id"),
            "external_user_id": metadata.get("external_user_id"),
            "external_message_id": metadata.get("external_message_id"),
            "agent_display_name": metadata.get("agent_display_name"),
        }

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
        return await AgentCallableToolFactory(
            self.uow_factory
        ).resolve_configured_accounts(agent=agent, user_id=user_id)
