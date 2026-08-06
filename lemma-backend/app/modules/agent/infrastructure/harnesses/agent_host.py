"""Remote harness adapter for the Agent Host control plane.

The run worker consumes one ordered per-run Redis Stream with a blocking read.
The previous design interleaved two sources: a one-second PostgreSQL poll for
durable events and an eagerly drained queue fed by a pub/sub forwarder task for
cosmetic chunks. A single blocking read replaces the poll, the queue, the
forwarder task, and the interleaving, and delivers lower latency than the poll
it replaces.

PostgreSQL is still consulted, but only for lease state - acceptance, expiry,
and recovery - on a slow cadence, because none of that arrives as an event.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostEventType,
    AgentHostRunSpec,
    AgentHostRunState,
)
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
)
from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt
from app.modules.agent.infrastructure.agent_host_channels import poke_host
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_event_stream import (
    AgentHostEventStream,
    StreamedEvent,
    agent_host_event_stream,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure import agent_host_session_memory
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.agent_host_artifacts import (
    AgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventEnvelope,
    AgentHostEventNormalizer,
    error_event,
    event_text,
    is_terminal_event,
)
from app.modules.agent.infrastructure.harnesses.agent_host_run_window import (
    CREDENTIAL_DEADLINE_MESSAGE,
    DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
    DispatchedRun,
    LeaseOutcome,
    credential_bounded_timeout,
    credential_exhausted,
    credential_refresh_due,
    expiry_message,
    failure_events,
    terminal_checkpoint_state,
)
from app.modules.agent.infrastructure.harnesses.agent_host_stream_reader import (
    StreamReader,
    StreamUnavailable,
)
from app.modules.agent.infrastructure.agent_host_final_answer import (
    adopt_recorded_final_answer,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import (
    mcp_payload,
    run_start_payload,
    token_expires_at,
)
from app.modules.agent.infrastructure.mcp import exported_tool_name
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_TOOL_NAME,
    agent_output_schema,
    final_answer_expected,
)

logger = get_logger(__name__)

# How long a blocking stream read waits before the loop rechecks its deadlines.
DEFAULT_STREAM_BLOCK_MS = 1_000
# Lease state does not arrive as an event, so it is polled - but slowly, since
# it only matters for acceptance, expiry, and recovery.
DEFAULT_LEASE_CHECK_SECONDS = 5.0
DEFAULT_TERMINAL_EVENT_GRACE_SECONDS = 5.0



@dataclass(frozen=True, slots=True)
class _AgentHostRunConfig:
    harness_id: UUID
    runtime_profile_id: UUID
    config_selections: JsonObject
    wait_timeout_seconds: int
    model_name: str | None


def _agent_host_run_config(options: HarnessOptions) -> _AgentHostRunConfig:
    profile = _runtime_profile(options)
    harness_id = UUID(str(profile["harness_id"]))
    runtime_profile_id = UUID(str(profile["profile_id"]))
    config = _json_object(profile.get("config"))
    # Read the saved revision so malformed legacy profiles fail before
    # dispatch. Admission intentionally uses the latest live revision after
    # selections are revalidated by the repository.
    str(config["harness_snapshot_revision"])
    # The model comes from the profile snapshot rather than options.model_name,
    # which substitutes a "default" placeholder when nothing is pinned. Agent
    # Host rejects any model the harness does not advertise, so an unpinned
    # profile must send no model and let the harness use its own default.
    raw_model = profile.get("provider_model_name") or profile.get("model_name")
    model_name = str(raw_model).strip() if raw_model else None
    return _AgentHostRunConfig(
        harness_id=harness_id,
        runtime_profile_id=runtime_profile_id,
        config_selections=_json_object(config.get("config_selections")),
        wait_timeout_seconds=_integer(
            config.get("host_wait_timeout_seconds"),
            default=300,
        ),
        model_name=model_name or None,
    )


class RemoteHarness:
    """Dispatch one run through the Agent Host protocol."""

    kind = HarnessKind.HARNESS

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        artifact_writer: AgentHostArtifactWriter | None = None,
        event_stream: AgentHostEventStream | None = None,
        event_timeout_seconds: float = DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
        lease_check_seconds: float = DEFAULT_LEASE_CHECK_SECONDS,
        stream_block_ms: int = DEFAULT_STREAM_BLOCK_MS,
        terminal_event_grace_seconds: float = DEFAULT_TERMINAL_EVENT_GRACE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.artifact_writer = artifact_writer
        self.events = event_stream or agent_host_event_stream()
        self.event_timeout_seconds = event_timeout_seconds
        self.lease_check_seconds = lease_check_seconds
        self.stream_block_ms = stream_block_ms
        self.terminal_event_grace_seconds = terminal_event_grace_seconds
        # Only the credential window is measured against a wall clock, and it
        # is an hour wide. Injecting it is what lets a test prove a run
        # outliving its original token without waiting out the real hour.
        self._now = clock or (lambda: datetime.now(timezone.utc))

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
        try:
            run_config = _agent_host_run_config(options)
        except (KeyError, TypeError, ValueError) as exc:
            yield error_event(agent_run_id, f"Invalid Agent Host runtime profile: {exc}")
            return

        try:
            dispatch = await self._enqueue_run(
                agent=agent,
                conversation=conversation,
                messages=messages,
                ctx=ctx,
                options=options,
                agent_run_id=agent_run_id,
                run_config=run_config,
            )
        except (AgentHostRepositoryError, RuntimeError, ValueError) as exc:
            yield error_event(agent_run_id, str(exc))
            return

        # The stream is dropped only once the run has genuinely finished. This
        # generator is also closed on cancellation and on a consumer that stops
        # early, and deleting there would throw away events the host is still
        # appending - and would do it with an await inside a finally during
        # GeneratorExit, which is not reliably allowed to complete. The stream's
        # own TTL is the backstop for the abandoned case.
        finished = False
        try:
            async for event in self._consume(
                agent_run_id=agent_run_id,
                agent=agent,
                ctx=ctx,
                conversation=conversation,
                options=options,
                run_config=run_config,
                dispatch=dispatch,
            ):
                yield event
            finished = True
        finally:
            if finished:
                await self.events.delete(run_id=agent_run_id)

    async def _consume(
        self,
        *,
        agent_run_id: UUID,
        agent: Agent,
        ctx: AgentContext,
        conversation: Conversation,
        options: HarnessOptions,
        run_config: _AgentHostRunConfig,
        dispatch: DispatchedRun,
    ) -> AsyncIterator[AgentEvent]:
        """Drive one dispatched run to a terminal event.

        Returns only when the run is over, one way or another; every exit
        yields something terminal first.
        """
        normalizer = AgentHostEventNormalizer(
            agent_run_id=agent_run_id,
            model_name=options.model_name,
            harness_key=dispatch.harness_key,
            structured_expected=final_answer_expected(
                agent=agent, conversation=conversation
            ),
            output_schema=agent_output_schema(agent),
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + dispatch.event_timeout_seconds
        accept_deadline = loop.time() + run_config.wait_timeout_seconds
        lease_check_due_at = loop.time()
        terminal_checkpoint_seen_at: float | None = None
        stop_sent = False
        # Advances as refreshes land, so the run is bounded by the credential it
        # currently holds rather than by the one it was dispatched with.
        credential_expires_at = dispatch.credential_expires_at
        reader = StreamReader(
            stream=self.events,
            run_id=agent_run_id,
            block_ms=self.stream_block_ms,
        )

        while True:
            try:
                batch = await reader.next_batch()
            except StreamUnavailable as exc:
                # Our outage, not the host's: stop its run before giving up so
                # the provider is not left executing against the pod.
                await self._enqueue_host_cancel(agent_run_id)
                for event in failure_events(normalizer, agent_run_id, str(exc)):
                    yield event
                return

            for entry in batch:
                for event in await self._normalize(
                    normalizer,
                    entry,
                    ctx=ctx,
                    conversation=conversation,
                    agent_run_id=agent_run_id,
                ):
                    terminal = is_terminal_event(event)
                    yield event
                    if terminal:
                        return

            now = loop.time()
            if now >= deadline:
                # The deadline we advertised has passed, so the host is either
                # gone or still working on a run we can no longer report. Cancel
                # it: without this the ACP agent keeps burning tokens and running
                # tools for a turn Lemma has already failed.
                await self._enqueue_host_cancel(agent_run_id)
                for event in failure_events(
                    normalizer, agent_run_id, dispatch.deadline_message
                ):
                    yield event
                return

            if now < lease_check_due_at:
                continue
            lease_check_due_at = now + self.lease_check_seconds

            wall_clock = self._now()
            if credential_refresh_due(
                expires_at=credential_expires_at, now=wall_clock
            ):
                refreshed = await self._refresh_credential(
                    agent_run_id=agent_run_id,
                    ctx=ctx,
                    options=options,
                    agent=agent,
                    conversation=conversation,
                )
                if refreshed is not None:
                    credential_expires_at = refreshed
            if credential_exhausted(
                expires_at=credential_expires_at, now=wall_clock
            ):
                # Every refresh failed. Ending the run here is what keeps this
                # from becoming a run whose tools silently 401 for the rest of
                # its life without anything being reported to anyone.
                await self._enqueue_host_cancel(agent_run_id)
                for event in failure_events(
                    normalizer, agent_run_id, CREDENTIAL_DEADLINE_MESSAGE
                ):
                    yield event
                return

            stop_sent = await self._cancel_if_requested(
                agent_run_id=agent_run_id,
                options=options,
                stop_sent=stop_sent,
            )
            outcome = await self._check_lease(
                agent_run_id=agent_run_id,
                seen_at=terminal_checkpoint_seen_at,
                accept_deadline=accept_deadline,
                now=loop.time(),
            )
            terminal_checkpoint_seen_at = outcome.seen_at

            if outcome.terminal_state is not None:
                # The run reached a terminal state without a terminal event, so
                # this is the only chance to pick up a final answer the tool
                # recorded before the stream went quiet.
                await adopt_recorded_final_answer(
                self.uow_factory, normalizer, agent_run_id=agent_run_id
            )
                for event in normalizer.finish_without_terminal(
                    state=outcome.terminal_state
                ):
                    yield event
                return

            if outcome.expired_state is not None:
                yield error_event(agent_run_id, expiry_message(outcome.expired_state))
                return

    async def _check_lease(
        self,
        *,
        agent_run_id: UUID,
        seen_at: float | None,
        accept_deadline: float,
        now: float,
    ) -> LeaseOutcome:
        """Reconcile lease state, which never arrives as an event.

        Kept out of the consume loop so that loop stays about events.
        """
        async with self.uow_factory() as uow:
            repository = AgentHostDispatchRepository(uow)
            await repository.reconcile_expired_run(run_id=agent_run_id)
            lease = await repository.get_run_lease(run_id=agent_run_id)
            await uow.commit()

        seen_at, terminal_state = terminal_checkpoint_state(
            lease=lease,
            seen_at=seen_at,
            now=now,
            grace_seconds=self.terminal_event_grace_seconds,
        )
        if terminal_state is not None:
            return LeaseOutcome(seen_at=seen_at, terminal_state=terminal_state)

        expired_state = await self._expire_if_unaccepted(
            lease=lease,
            now=now,
            accept_deadline=accept_deadline,
            agent_run_id=agent_run_id,
        )
        return LeaseOutcome(seen_at=seen_at, expired_state=expired_state)

    async def _normalize(
        self,
        normalizer: AgentHostEventNormalizer,
        entry: StreamedEvent,
        *,
        ctx: AgentContext,
        conversation: Conversation,
        agent_run_id: UUID,
    ) -> list[AgentEvent]:
        envelope = AgentHostEventEnvelope(
            sequence=entry.sequence,
            type=entry.type,
            object_id=entry.object_id,
            payload=entry.payload,
        )
        events: list[AgentEvent] = []
        payload_override: JsonObject | None = None
        if self.artifact_writer is not None:
            materialized = await self.artifact_writer.materialize_event(
                payload=entry.payload,
                pod_id=conversation.pod_id,
                user_context=ctx,
                directory_path=f"{_resolve_pod_cwd(conversation)}/agent-output",
                agent_run_id=agent_run_id,
                event_sequence=entry.sequence,
                harness_key=normalizer.harness_key,
            )
            if materialized.markdown:
                payload_override = dict(entry.payload)
                existing = event_text(payload_override)
                payload_override["text"] = (
                    f"{existing}\n\n{materialized.markdown}"
                    if existing
                    else materialized.markdown
                )
            for warning in materialized.warnings:
                events.append(
                    AgentEvent(
                        type=AgentEventType.STATUS,
                        data={
                            "status": "agent_host.artifact_warning",
                            "detail": warning,
                            "agent_host_sequence": entry.sequence,
                        },
                        agent_run_id=agent_run_id,
                        sequence=entry.sequence,
                    )
                )
        if entry.type == AgentHostEventType.TERMINAL.value:
            await adopt_recorded_final_answer(
                self.uow_factory, normalizer, agent_run_id=agent_run_id
            )
        events.extend(normalizer.normalize(envelope, payload_override=payload_override))
        return events

    async def _refresh_credential(
        self,
        *,
        agent_run_id: UUID,
        ctx: AgentContext,
        options: HarnessOptions,
        agent: Agent,
        conversation: Conversation,
    ) -> datetime | None:
        """Mint a replacement Lemma credential and send it to the host.

        The token dispatched with the run lasts an hour and nothing renewed it,
        so a long turn either had to be cut short at that expiry or carry on
        with every ``lemma_*`` call returning 401 — which the agent experiences
        as its tools disappearing part-way through the task.

        Returns the new expiry, or ``None`` if the refresh did not land, in
        which case the caller keeps the old one and tries again next cycle.
        """
        try:
            mcp = await mcp_payload(
                agent_run_id=agent_run_id,
                conversation_id=conversation.id,
                ctx=ctx,
                options=options,
                extra_tool_names=(
                    [exported_tool_name(FINAL_ANSWER_TOOL_NAME)]
                    if final_answer_expected(agent=agent, conversation=conversation)
                    else []
                ),
            )
            encrypted = await get_secret_cipher().encrypt_json_async(mcp)
            if encrypted is None:
                raise RuntimeError("could not encrypt the refreshed MCP configuration")
            async with self.uow_factory() as uow:
                command = await AgentHostDispatchRepository(
                    uow
                ).enqueue_credential_refresh(
                    run_id=agent_run_id,
                    encrypted_mcp_payload=encrypted,
                )
                await uow.commit()
        except (AgentHostRepositoryError, RuntimeError, ValueError, KeyError) as exc:
            logger.warning(
                "agent.harnesses.agent_host.credential_refresh_failed.degraded",
                agent_run_id=str(agent_run_id),
                error_type=type(exc).__name__,
            )
            return None
        if command is None:
            # The run ended while we were minting; nothing left to refresh.
            return None
        await poke_host(command.host_id)
        return token_expires_at(mcp)

    async def _cancel_if_requested(
        self,
        *,
        agent_run_id: UUID,
        options: HarnessOptions,
        stop_sent: bool,
    ) -> bool:
        if stop_sent or options.should_stop is None:
            return stop_sent
        if not await options.should_stop():
            return False
        await self._enqueue_host_cancel(agent_run_id)
        return True

    async def _enqueue_host_cancel(self, agent_run_id: UUID) -> None:
        """Ask the host to stop this run, and wake it up to hear that.

        A no-op once the lease is terminal, so it is safe to call from every
        path that gives up on a run.
        """
        async with self.uow_factory() as uow:
            command = await AgentHostDispatchRepository(uow).enqueue_cancel(
                run_id=agent_run_id
            )
            await uow.commit()
        if command is not None:
            await poke_host(command.host_id)

    async def _expire_if_unaccepted(
        self,
        *,
        lease: object | None,
        now: float,
        accept_deadline: float,
        agent_run_id: UUID,
    ) -> AgentHostRunState | None:
        if (
            lease is None
            or getattr(lease, "accepted_at", None) is not None
            or now < accept_deadline
        ):
            return None
        async with self.uow_factory() as uow:
            state = await AgentHostDispatchRepository(uow).expire_unaccepted_run(
                run_id=agent_run_id
            )
            await uow.commit()
        return state

    async def _enqueue_run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
        run_config: _AgentHostRunConfig,
    ) -> DispatchedRun:
        # Resolved first, because it decides what the prompt has to contain: a
        # run that will not even try to resume a session is talking to an agent
        # with no history, so the prompt must carry it.
        async with self.uow_factory() as uow:
            harness = await AgentHostRepository(uow).get_harness(
                harness_id=run_config.harness_id
            )
            if harness is None:
                raise RuntimeError("Agent Host harness is unavailable")
            resume_session_id = await agent_host_session_memory.resume_session_id(
                uow,
                conversation_id=conversation.id,
                harness_id=run_config.harness_id,
                capabilities=harness.capabilities,
            )
            harness_id = harness.id
            host_id = harness.host_id
            harness_key = harness.harness_key
            config_revision = harness.config_revision

        payload = run_start_payload(
            agent=agent,
            conversation=conversation,
            messages=messages,
            ctx=ctx,
            agent_run_id=agent_run_id,
            runtime_instructions=load_agent_host_runtime_prompt(),
            carries_history=resume_session_id is None,
        )
        prompt = _json_object(payload.get("prompt"))
        mcp = await mcp_payload(
            agent_run_id=agent_run_id,
            conversation_id=conversation.id,
            ctx=ctx,
            options=options,
            prompt=_joined_prompt(prompt),
            # final_answer is served by the MCP route but is not in
            # options.toolsets (the in-process harness gets it via output_type),
            # so name it explicitly.
            extra_tool_names=(
                [exported_tool_name(FINAL_ANSWER_TOOL_NAME)]
                if final_answer_expected(agent=agent, conversation=conversation)
                else []
            ),
        )
        encrypted_mcp = await get_secret_cipher().encrypt_json_async(mcp)
        if encrypted_mcp is None:
            raise RuntimeError("could not encrypt MCP configuration")
        dispatched_at = datetime.now(timezone.utc)
        timeout_seconds, credential_bounded = credential_bounded_timeout(
            configured_seconds=self.event_timeout_seconds,
            credential_expires_at=token_expires_at(mcp),
            now=dispatched_at,
            agent_run_id=agent_run_id,
        )
        async with self.uow_factory() as uow:
            run_spec = AgentHostRunSpec(
                agent_run_id=agent_run_id,
                conversation_id=conversation.id,
                harness_id=harness_id,
                profile_revision=config_revision,
                model_name=run_config.model_name,
                config_selections=run_config.config_selections,
                system_prompt=str(
                    prompt.get("system_prompt")
                    or prompt.get("recovery_system_prompt")
                    or ""
                ),
                prompt=[{"type": "text", "text": str(prompt.get("user_prompt") or "")}],
                resume_session_id=resume_session_id,
                context={
                    "agent": payload.get("agent"),
                    "conversation": payload.get("conversation"),
                    "lemma": payload.get("context"),
                    "session_id": prompt.get("session_id"),
                },
                run_deadline=dispatched_at + timedelta(seconds=timeout_seconds),
            )
            await AgentHostDispatchRepository(uow).enqueue_run(
                host_id=host_id,
                harness_id=run_config.harness_id,
                runtime_profile_id=run_config.runtime_profile_id,
                run_spec=run_spec,
                encrypted_mcp_payload=encrypted_mcp,
                command_ttl_seconds=run_config.wait_timeout_seconds,
            )
            await uow.commit()
        await poke_host(host_id)
        return DispatchedRun(
            harness_key=harness_key,
            event_timeout_seconds=timeout_seconds,
            credential_bounded=credential_bounded,
            credential_expires_at=token_expires_at(mcp),
        )


def _resolve_pod_cwd(conversation: Conversation) -> str:
    from app.modules.agent.services.workspace_location import resolve_pod_cwd

    return resolve_pod_cwd(conversation)


def _runtime_profile(options: HarnessOptions) -> JsonObject:
    extra = getattr(options, "extra", None)
    profile = _json_object(extra).get("runtime_profile") if extra else None
    if not isinstance(profile, dict):
        raise ValueError("runtime profile is missing from harness options")
    return profile


def _json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: object, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _joined_prompt(prompt: JsonObject) -> str:
    parts = [
        str(prompt.get(key) or "")
        for key in ("system_prompt", "recovery_system_prompt", "user_prompt")
    ]
    return "\n\n".join(part for part in parts if part)
