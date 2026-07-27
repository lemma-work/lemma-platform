"""Harness adapter for the durable Agent Host v2 control plane."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.agent_host import (
    FOLLOW_ADAPTER_DEFAULT,
    TERMINAL_AGENT_HOST_RUN_STATES,
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
    MessageDraft,
)
from app.modules.agent.infrastructure.agent_host_repository import (
    AgentHostDispatchRepository,
    AgentHostRepository,
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.daemon import (
    _mcp_payload,
    _missing_tool_return_events,
    _run_start_payload,
)
from app.modules.agent.infrastructure.models import AgentHostEventModel
from app.modules.usage.contracts import AgentRunUsage


DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS = 7200.0
DEFAULT_AGENT_HOST_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_TERMINAL_EVENT_GRACE_SECONDS = 5.0
_AGENT_HOST_RUNTIME_INSTRUCTIONS = (
    "# Runtime\n"
    "You are running through Lemma Agent Host. Use the Lemma MCP tools "
    "(the lemma_* tools) for file and command execution; they run in the "
    "conversation workspace. The provider process directory is private host "
    "scratch space and must not be treated as the Lemma workspace."
)


class AgentHostHarness:
    """Dispatch one run through the PostgreSQL-backed Agent Host protocol."""

    kind = HarnessKind.AGENT_HOST

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        event_timeout_seconds: float = DEFAULT_AGENT_HOST_EVENT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_AGENT_HOST_POLL_INTERVAL_SECONDS,
        terminal_event_grace_seconds: float = DEFAULT_TERMINAL_EVENT_GRACE_SECONDS,
    ) -> None:
        self.uow_factory = uow_factory
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
        profile = _runtime_profile(options)
        try:
            integration_id = UUID(str(profile["host_integration_id"]))
            runtime_profile_id = UUID(str(profile["profile_id"]))
            config = _json_object(profile.get("config"))
            # The stored revision is an optimistic-concurrency token from
            # profile creation. Run admission below uses the latest live
            # integration revision after revalidating selections, so adding a
            # provider model does not invalidate every existing profile.
            _saved_profile_revision = str(config["integration_snapshot_revision"])
            config_selections = _json_object(config.get("config_selections"))
            if (
                options.model_name
                and options.model_name != FOLLOW_ADAPTER_DEFAULT
            ):
                config_selections["model"] = options.model_name
            wait_timeout_seconds = int(
                config.get("host_wait_timeout_seconds") or 300
            )
            fallback_profile_id = config.get("fallback_profile_id")
            if fallback_profile_id is not None:
                fallback_profile_id = str(fallback_profile_id)
        except (KeyError, TypeError, ValueError) as exc:
            yield _error(
                agent_run_id,
                f"Invalid Agent Host runtime profile: {exc}",
            )
            return

        payload = _run_start_payload(
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
        route_id = uuid4()
        now = datetime.now(timezone.utc)
        run_deadline = now + timedelta(seconds=self.event_timeout_seconds)
        try:
            mcp = await _mcp_payload(
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
                host_repo = AgentHostRepository(uow)
                integration = await host_repo.get_integration(
                    integration_id=integration_id
                )
                if integration is None:
                    raise RuntimeError("Agent Host integration is unavailable")
                profile_revision = integration.config_revision
                run_spec = AgentHostRunSpec(
                    agent_run_id=agent_run_id,
                    conversation_id=conversation.id,
                    integration_id=integration_id,
                    profile_revision=profile_revision,
                    config_selections=config_selections,
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
                    mcp_route_id=str(route_id),
                    run_deadline=run_deadline,
                )
                await AgentHostDispatchRepository(uow).enqueue_run(
                    host_id=integration.host_id,
                    integration_id=integration_id,
                    runtime_profile_id=runtime_profile_id,
                    run_spec=run_spec,
                    encrypted_mcp_payload=encrypted_mcp,
                    command_ttl_seconds=wait_timeout_seconds,
                )
        except (AgentHostRepositoryError, RuntimeError, ValueError) as exc:
            yield _error(agent_run_id, str(exc))
            return

        normalizer = _AgentHostEventNormalizer(
            agent_run_id=agent_run_id,
            model_name=options.model_name,
        )
        sequence = 0
        stop_sent = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.event_timeout_seconds
        accept_deadline = loop.time() + wait_timeout_seconds
        terminal_checkpoint_seen_at: float | None = None
        while True:
            if (
                not stop_sent
                and options.should_stop is not None
                and await options.should_stop()
            ):
                async with self.uow_factory() as uow:
                    await AgentHostDispatchRepository(uow).enqueue_cancel(
                        run_id=agent_run_id
                    )
                stop_sent = True

            async with self.uow_factory() as uow:
                repo = AgentHostDispatchRepository(uow)
                await repo.reconcile_expired_run(run_id=agent_run_id)
                rows = await repo.events_after(
                    run_id=agent_run_id,
                    sequence=sequence,
                )
                lease = await repo.get_run_lease(run_id=agent_run_id)

            for row in rows:
                sequence = row.sequence
                for event in normalizer.normalize(row):
                    yield event
                    if _is_terminal(event):
                        return

            if (
                lease is not None
                and AgentHostRunState(lease.state)
                in TERMINAL_AGENT_HOST_RUN_STATES
            ):
                if terminal_checkpoint_seen_at is None:
                    terminal_checkpoint_seen_at = loop.time()
                elif (
                    loop.time() - terminal_checkpoint_seen_at
                    >= self.terminal_event_grace_seconds
                ):
                    for event in normalizer.finish_without_terminal(
                        state=AgentHostRunState(lease.state)
                    ):
                        yield event
                    return
            else:
                terminal_checkpoint_seen_at = None

            if (
                lease is not None
                and lease.checkpoint is None
                and loop.time() >= accept_deadline
            ):
                async with self.uow_factory() as uow:
                    expired = await AgentHostDispatchRepository(
                        uow
                    ).expire_unaccepted_run(run_id=agent_run_id)
                if expired:
                    if fallback_profile_id and options.fallback_run is not None:
                        async for event in options.fallback_run(
                            fallback_profile_id
                        ):
                            yield event
                        return
                    yield _error(
                        agent_run_id,
                        "No Agent Host accepted the run before its wait deadline",
                    )
                    return

            remaining = deadline - loop.time()
            if remaining <= 0:
                terminal = _error(
                    agent_run_id,
                    "Agent Host did not emit a terminal event before the run deadline",
                )
                for event in normalizer.close_outstanding(terminal):
                    yield event
                yield terminal
                return
            await asyncio.sleep(min(self.poll_interval_seconds, remaining))


class _AgentHostEventNormalizer:
    """Convert durable canonical host events to the existing runtime stream."""

    def __init__(self, *, agent_run_id: UUID, model_name: str) -> None:
        self.agent_run_id = agent_run_id
        self.model_name = model_name
        self.message_text: dict[str, str] = {}
        self.thought_text: dict[str, str] = {}
        self.tool_calls: dict[str, str] = {}
        self.closed_tool_calls: set[str] = set()

    def normalize(self, row: AgentHostEventModel) -> list[AgentEvent]:
        event_type = AgentHostEventType(row.type)
        payload = _json_object(row.payload)
        object_id = row.object_id or f"event-{row.sequence}"
        metadata = {
            "agent_host_object_id": object_id,
            "agent_host_sequence": row.sequence,
            "integration_key": row.integration_key,
            "adapter_version": row.adapter_version,
        }
        if event_type is AgentHostEventType.AGENT_MESSAGE_CHUNK:
            text = _event_text(payload)
            self.message_text[object_id] = self.message_text.get(object_id, "") + text
            return [self._token(text)] if text else []
        if event_type is AgentHostEventType.AGENT_MESSAGE_UPSERT:
            return self._upsert_text(
                object_id=object_id,
                payload=payload,
                storage=self.message_text,
                kind="text",
            )
        if event_type is AgentHostEventType.AGENT_THOUGHT_CHUNK:
            text = _event_text(payload)
            self.thought_text[object_id] = self.thought_text.get(object_id, "") + text
            return [self._token(text, kind="thinking")] if text else []
        if event_type is AgentHostEventType.AGENT_THOUGHT_UPSERT:
            return self._upsert_text(
                object_id=object_id,
                payload=payload,
                storage=self.thought_text,
                kind="thinking",
            )
        if event_type is AgentHostEventType.TOOL_CALL_UPSERT:
            tool_name = str(payload.get("name") or payload.get("tool_name") or "tool")
            if object_id in self.tool_calls:
                return []
            self.tool_calls[object_id] = tool_name
            return [
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_tool_call(
                        tool_name=tool_name,
                        tool_call_id=object_id,
                        tool_args=payload.get("arguments", payload.get("args")),
                        metadata=metadata,
                    ),
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            ]
        if event_type is AgentHostEventType.TOOL_CALL_UPDATE:
            status = str(payload.get("status") or "").upper()
            if status not in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "DENIED",
            } or object_id in self.closed_tool_calls:
                return []
            tool_name = self.tool_calls.get(
                object_id,
                str(payload.get("name") or payload.get("tool_name") or "tool"),
            )
            self.closed_tool_calls.add(object_id)
            result = payload.get("result")
            if status != "COMPLETED":
                result = {
                    "success": False,
                    "error": str(payload.get("error") or status.lower()),
                }
            return [
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_tool_return(
                        tool_name=tool_name,
                        tool_call_id=object_id,
                        tool_result=result,
                        metadata=metadata,
                    ),
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            ]
        if event_type is AgentHostEventType.USAGE_UPDATE:
            usage = _json_object(payload.get("usage")) or payload
            return [
                AgentEvent(
                    type=AgentEventType.USAGE,
                    data=AgentRunUsage(
                        model_name=str(usage.get("model_name") or self.model_name),
                        input_tokens=_integer(usage.get("input_tokens")),
                        output_tokens=_integer(usage.get("output_tokens")),
                        request_count=_integer(usage.get("request_count"), default=1),
                        tool_call_count=_integer(usage.get("tool_call_count")),
                        units=float(usage.get("units") or 0),
                        metadata=metadata,
                    ),
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            ]
        if event_type in {
            AgentHostEventType.RUN_STATE,
            AgentHostEventType.PLAN_UPSERT,
            AgentHostEventType.CONFIG_UPDATE,
            AgentHostEventType.WARNING,
        }:
            return [
                AgentEvent(
                    type=AgentEventType.STATUS,
                    data={
                        "status": event_type.value,
                        "detail": payload,
                        **metadata,
                    },
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            ]
        if event_type in {
            AgentHostEventType.PERMISSION_REQUEST,
            AgentHostEventType.INPUT_REQUEST,
        }:
            events = self._flush_messages()
            events.append(
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_notification(
                        str(
                            payload.get("prompt")
                            or payload.get("message")
                            or "The local agent is waiting for input."
                        ),
                        metadata={**metadata, "is_final_answer": False},
                    ),
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            )
            events.append(
                AgentEvent(
                    type=AgentEventType.WAITING,
                    data=payload,
                    agent_run_id=self.agent_run_id,
                    sequence=row.sequence,
                )
            )
            return events
        if event_type is AgentHostEventType.TERMINAL:
            events = self._flush_messages()
            terminal = _terminal_event(
                agent_run_id=self.agent_run_id,
                state=str(payload.get("state") or payload.get("status") or "FAILED"),
                payload=payload,
                sequence=row.sequence,
            )
            events.extend(self.close_outstanding(terminal))
            events.append(terminal)
            return events
        return []

    def _upsert_text(
        self,
        *,
        object_id: str,
        payload: JsonObject,
        storage: dict[str, str],
        kind: str,
    ) -> list[AgentEvent]:
        full_text = _event_text(payload)
        previous = storage.get(object_id, "")
        storage[object_id] = full_text
        if full_text.startswith(previous):
            delta = full_text[len(previous) :]
        elif full_text != previous:
            delta = full_text
        else:
            delta = ""
        return [self._token(delta, kind=kind)] if delta else []

    def _token(self, text: str, *, kind: str = "text") -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": kind, "data": text},
            agent_run_id=self.agent_run_id,
        )

    def _flush_messages(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for object_id, text in self.thought_text.items():
            if text:
                events.append(
                    AgentEvent(
                        type=AgentEventType.MESSAGE,
                        data=MessageDraft.of_thinking(
                            text,
                            metadata={
                                "agent_host_object_id": object_id,
                                "is_final_answer": False,
                            },
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                )
        for object_id, text in self.message_text.items():
            if text:
                events.append(
                    AgentEvent(
                        type=AgentEventType.MESSAGE,
                        data=MessageDraft.of_text(
                            text,
                            metadata={
                                "agent_host_object_id": object_id,
                                "is_final_answer": True,
                            },
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                )
        self.thought_text.clear()
        self.message_text.clear()
        return events

    def close_outstanding(self, terminal: AgentEvent) -> list[AgentEvent]:
        outstanding = {
            tool_call_id: tool_name
            for tool_call_id, tool_name in self.tool_calls.items()
            if tool_call_id not in self.closed_tool_calls
        }
        return _missing_tool_return_events(
            outstanding_tool_calls=outstanding,
            terminal_event=terminal,
        )

    def finish_without_terminal(
        self,
        *,
        state: AgentHostRunState,
    ) -> list[AgentEvent]:
        events = self._flush_messages()
        terminal = _error(
            self.agent_run_id,
            "Agent Host reached terminal checkpoint "
            f"{state.value} without its required terminal event",
        )
        events.extend(self.close_outstanding(terminal))
        events.append(terminal)
        return events


def _runtime_profile(options: HarnessOptions) -> JsonObject:
    profile = options.extra.get("runtime_profile")
    if not isinstance(profile, dict):
        raise ValueError("Agent Host runtime profile is missing")
    return profile


def _json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


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


def _event_text(payload: JsonObject) -> str:
    return str(
        payload.get("text")
        or payload.get("delta")
        or payload.get("content")
        or ""
    )


def _integer(value: object, *, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _error(agent_run_id: UUID, message: str) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.ERROR,
        data=message,
        agent_run_id=agent_run_id,
    )


def _terminal_event(
    *,
    agent_run_id: UUID,
    state: str,
    payload: JsonObject,
    sequence: int | None = None,
) -> AgentEvent:
    normalized = state.upper()
    if normalized == AgentHostRunState.SUCCEEDED.value:
        event_type = AgentEventType.COMPLETED
        data: object = payload
    elif normalized == AgentHostRunState.WAITING_INPUT.value:
        event_type = AgentEventType.WAITING
        data = payload
    elif normalized == AgentHostRunState.CANCELLED.value:
        event_type = AgentEventType.STOPPED
        data = payload
    else:
        event_type = AgentEventType.ERROR
        data = str(
            payload.get("error")
            or payload.get("message")
            or f"Agent Host run ended in {normalized}"
        )
    return AgentEvent(
        type=event_type,
        data=data,
        agent_run_id=agent_run_id,
        sequence=sequence,
    )


def _is_terminal(event: AgentEvent) -> bool:
    return event.type in {
        AgentEventType.COMPLETED,
        AgentEventType.STOPPED,
        AgentEventType.ERROR,
        AgentEventType.REJECTED,
        AgentEventType.WAITING,
    }
