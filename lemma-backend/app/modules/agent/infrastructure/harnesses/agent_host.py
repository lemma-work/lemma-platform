"""Remote harness adapter for the durable Agent Host control plane."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
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
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.agent_host_events import (
    AgentHostEventNormalizer,
    error_event,
    event_text,
    is_terminal_event,
)
from app.modules.agent.infrastructure.harnesses.agent_host_artifacts import (
    AgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import (
    mcp_payload,
    run_start_payload,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostEventModel,
    AgentHostRunLeaseModel,
)

DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS = 7200.0
DEFAULT_AGENT_HOST_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_TERMINAL_EVENT_GRACE_SECONDS = 5.0
_AGENT_HOST_RUNTIME_INSTRUCTIONS = (
    "# Runtime\n"
    "You are running through Lemma Agent Host. Use the Lemma MCP tools "
    "(the lemma_* tools) for file and command execution; they run in the "
    "conversation workspace. The provider process directory is private host "
    "scratch space and must not be treated as the Lemma workspace.\n\n"
    "# Native image generation\n"
    "When running as Codex and the user asks to generate or edit an image, "
    "use Codex's built-in `$imagegen` capability. Do not substitute Pillow, "
    "SVG, canvas, Python, shell scripts, or an external image CLI unless the "
    "user explicitly requests that implementation. Copy each final generated "
    "image into the `.lemma-artifacts` directory in the provider scratch "
    "workspace. Agent Host publishes files from that directory into the "
    "conversation's pod files; do not call the Lemma CLI to upload a private "
    "host path."
)


@dataclass(frozen=True, slots=True)
class _AgentHostRunConfig:
    harness_id: UUID
    runtime_profile_id: UUID
    config_selections: JsonObject
    wait_timeout_seconds: int
    fallback_profile_id: str | None


def _agent_host_run_config(options: HarnessOptions) -> _AgentHostRunConfig:
    profile = _runtime_profile(options)
    harness_id = UUID(str(profile["harness_id"]))
    runtime_profile_id = UUID(str(profile["profile_id"]))
    config = _json_object(profile.get("config"))
    # Read the saved revision so malformed legacy profiles fail before
    # dispatch. Admission intentionally uses the latest live revision after
    # selections are revalidated by the repository.
    str(config["harness_snapshot_revision"])
    config_selections = _json_object(config.get("config_selections"))
    fallback_profile_id = config.get("fallback_profile_id")
    return _AgentHostRunConfig(
        harness_id=harness_id,
        runtime_profile_id=runtime_profile_id,
        config_selections=config_selections,
        wait_timeout_seconds=_integer(
            config.get("host_wait_timeout_seconds"),
            default=300,
        ),
        fallback_profile_id=(
            str(fallback_profile_id) if fallback_profile_id is not None else None
        ),
    )


class RemoteHarness:
    """Dispatch one run through the PostgreSQL-backed Agent Host protocol."""

    kind = HarnessKind.HARNESS

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        artifact_writer: AgentHostArtifactWriter | None = None,
        event_timeout_seconds: float = DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_AGENT_HOST_POLL_INTERVAL_SECONDS,
        terminal_event_grace_seconds: float = DEFAULT_TERMINAL_EVENT_GRACE_SECONDS,
    ) -> None:
        self.uow_factory = uow_factory
        self.artifact_writer = artifact_writer
        self.event_timeout_seconds = event_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.terminal_event_grace_seconds = terminal_event_grace_seconds

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
            yield error_event(
                agent_run_id,
                f"Invalid Agent Host runtime profile: {exc}",
            )
            return

        try:
            await self._enqueue_run(
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

        normalizer = AgentHostEventNormalizer(
            agent_run_id=agent_run_id,
            model_name=options.model_name,
        )
        sequence = 0
        stop_sent = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.event_timeout_seconds
        accept_deadline = loop.time() + run_config.wait_timeout_seconds
        terminal_checkpoint_seen_at: float | None = None
        while True:
            stop_sent = await self._cancel_if_requested(
                agent_run_id=agent_run_id,
                options=options,
                stop_sent=stop_sent,
            )

            async with self.uow_factory() as uow:
                repo = AgentHostDispatchRepository(uow)
                await repo.reconcile_expired_run(run_id=agent_run_id)
                rows = await repo.events_after(
                    run_id=agent_run_id,
                    sequence=sequence,
                )
                lease = await repo.get_run_lease(run_id=agent_run_id)

            sequence, events = await _normalize_rows(
                normalizer,
                rows,
                sequence,
                artifact_writer=self.artifact_writer,
                ctx=ctx,
                conversation=conversation,
                agent_run_id=agent_run_id,
            )
            for event in events:
                yield event
                if is_terminal_event(event):
                    return

            if (
                lease is not None
                and lease.checkpoint is None
                and lease.state == AgentHostRunState.FAILED.value
            ):
                if _can_fallback(AgentHostRunState.FAILED, run_config, options):
                    assert run_config.fallback_profile_id is not None
                    assert options.fallback_run is not None
                    async for event in options.fallback_run(
                        run_config.fallback_profile_id
                    ):
                        yield event
                    return
                yield error_event(
                    agent_run_id,
                    lease.error_detail
                    or "Agent Host rejected the run before provider dispatch",
                )
                return

            terminal_checkpoint_seen_at, terminal_state = _terminal_checkpoint_state(
                lease=lease,
                seen_at=terminal_checkpoint_seen_at,
                now=loop.time(),
                grace_seconds=self.terminal_event_grace_seconds,
            )
            if terminal_state is not None:
                for event in normalizer.finish_without_terminal(state=terminal_state):
                    yield event
                return

            expired_state = await self._expire_if_unaccepted(
                lease=lease,
                now=loop.time(),
                accept_deadline=accept_deadline,
                agent_run_id=agent_run_id,
            )
            if expired_state is not None:
                if _can_fallback(expired_state, run_config, options):
                    assert run_config.fallback_profile_id is not None
                    assert options.fallback_run is not None
                    async for event in options.fallback_run(
                        run_config.fallback_profile_id
                    ):
                        yield event
                    return
                message = (
                    "No Agent Host received the run before its wait deadline"
                    if expired_state is AgentHostRunState.FAILED
                    else (
                        "Agent Host delivery could not be confirmed; "
                        "the run was not repeated through a fallback"
                    )
                )
                yield error_event(agent_run_id, message)
                return

            remaining = deadline - loop.time()
            if remaining <= 0:
                terminal = error_event(
                    agent_run_id,
                    "Agent Host did not emit a terminal event before the run deadline",
                )
                for event in normalizer.close_outstanding(terminal):
                    yield event
                yield terminal
                return
            await asyncio.sleep(min(self.poll_interval_seconds, remaining))

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
        async with self.uow_factory() as uow:
            await AgentHostDispatchRepository(uow).enqueue_cancel(run_id=agent_run_id)
        return True

    async def _expire_if_unaccepted(
        self,
        *,
        lease: AgentHostRunLeaseModel | None,
        now: float,
        accept_deadline: float,
        agent_run_id: UUID,
    ) -> AgentHostRunState | None:
        if lease is None or lease.checkpoint is not None or now < accept_deadline:
            return None
        async with self.uow_factory() as uow:
            return await AgentHostDispatchRepository(uow).expire_unaccepted_run(
                run_id=agent_run_id
            )

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
    ) -> None:
        payload = run_start_payload(
            agent=agent,
            conversation=conversation,
            messages=messages,
            ctx=ctx,
            options=options,
            agent_run_id=agent_run_id,
            harness_kind=self.kind,
            runtime_instructions=_AGENT_HOST_RUNTIME_INSTRUCTIONS,
        )
        prompt = _json_object(payload.get("prompt"))
        mcp = await mcp_payload(
            agent_run_id=agent_run_id,
            conversation_id=conversation.id,
            ctx=ctx,
            options=options,
            prompt=_joined_prompt(prompt),
        )
        encrypted_mcp = await get_secret_cipher().encrypt_json_async(mcp)
        if encrypted_mcp is None:
            raise RuntimeError("could not create MCP route")
        async with self.uow_factory() as uow:
            harness = await AgentHostRepository(uow).get_harness(
                harness_id=run_config.harness_id
            )
            if harness is None:
                raise RuntimeError("Agent Host harness is unavailable")
            run_spec = AgentHostRunSpec(
                agent_run_id=agent_run_id,
                conversation_id=conversation.id,
                harness_id=run_config.harness_id,
                profile_revision=harness.config_revision,
                model_name=options.model_name,
                config_selections=run_config.config_selections,
                system_prompt=str(
                    prompt.get("system_prompt")
                    or prompt.get("recovery_system_prompt")
                    or ""
                ),
                prompt=[
                    {
                        "type": "text",
                        "text": str(prompt.get("user_prompt") or ""),
                    }
                ],
                context={
                    "agent": payload.get("agent"),
                    "conversation": payload.get("conversation"),
                    "lemma": payload.get("context"),
                    "session_id": prompt.get("session_id"),
                },
                mcp_route_id=str(uuid4()),
                run_deadline=datetime.now(timezone.utc)
                + timedelta(seconds=self.event_timeout_seconds),
            )
            await AgentHostDispatchRepository(uow).enqueue_run(
                host_id=harness.host_id,
                harness_id=run_config.harness_id,
                runtime_profile_id=run_config.runtime_profile_id,
                run_spec=run_spec,
                encrypted_mcp_payload=encrypted_mcp,
                command_ttl_seconds=run_config.wait_timeout_seconds,
            )


async def _normalize_rows(
    normalizer: AgentHostEventNormalizer,
    rows: list[AgentHostEventModel],
    sequence: int,
    *,
    artifact_writer: AgentHostArtifactWriter | None,
    ctx: AgentContext,
    conversation: Conversation,
    agent_run_id: UUID,
) -> tuple[int, list[AgentEvent]]:
    events: list[AgentEvent] = []
    for row in rows:
        sequence = row.sequence
        payload_override: JsonObject | None = None
        if artifact_writer is not None:
            from app.modules.agent.services.workspace_location import resolve_pod_cwd

            materialized = await artifact_writer.materialize_event(
                payload=_json_object(row.payload),
                pod_id=conversation.pod_id,
                user_context=ctx,
                directory_path=f"{resolve_pod_cwd(conversation)}/agent-output",
                agent_run_id=agent_run_id,
                event_sequence=row.sequence,
                harness_key=row.harness_key,
            )
            if materialized.markdown:
                payload_override = _json_object(row.payload)
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
                            "agent_host_sequence": row.sequence,
                        },
                        agent_run_id=agent_run_id,
                        sequence=row.sequence,
                    )
                )
        events.extend(normalizer.normalize(row, payload_override=payload_override))
    return sequence, events
def _terminal_checkpoint_state(
    *,
    lease: AgentHostRunLeaseModel | None,
    seen_at: float | None,
    now: float,
    grace_seconds: float,
) -> tuple[float | None, AgentHostRunState | None]:
    if lease is None:
        return None, None
    state = AgentHostRunState(lease.state)
    if state not in TERMINAL_AGENT_HOST_RUN_STATES:
        return None, None
    if seen_at is None:
        return now, None
    if now - seen_at < grace_seconds:
        return seen_at, None
    return seen_at, state


def _runtime_profile(options: HarnessOptions) -> JsonObject:
    profile = options.extra.get("runtime_profile")
    if not isinstance(profile, dict):
        raise ValueError("Agent Host runtime profile is missing")
    return profile


def _can_fallback(
    state: AgentHostRunState,
    run_config: _AgentHostRunConfig,
    options: HarnessOptions,
) -> bool:
    return (
        state is AgentHostRunState.FAILED
        and run_config.fallback_profile_id is not None
        and options.fallback_run is not None
    )


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
    return "\n\n".join(
        part
        for part in (
            str(
                prompt.get("system_prompt")
                or prompt.get("recovery_system_prompt")
                or ""
            ),
            str(prompt.get("user_prompt") or ""),
        )
        if part
    )
