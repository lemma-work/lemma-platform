"""Harness adapter that delegates execution to a connected user daemon."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from uuid import UUID

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    ConversationType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    MessageKind,
    MessageRole,
    to_json_value,
)
from app.modules.agent.infrastructure.daemon_hub import (
    agent_runtime_daemon_hub,
    daemon_mcp_url,
)
from app.modules.agent.infrastructure.mcp import (
    LEMMA_MCP_SERVER_NAME,
    exported_tool_name,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage


DEFAULT_DAEMON_EVENT_TIMEOUT_SECONDS = 7200.0
# Bounded and orthogonal to DEFAULT_DAEMON_EVENT_TIMEOUT_SECONDS above: that one
# tolerates a *live* connection's long silent gap between events (a slow tool
# call); this one bounds how long a disconnected run waits for its daemon to
# reconnect before giving up. Kept far shorter on purpose -- see run()'s
# RECONNECTING handling below.
DEFAULT_RECONNECT_GRACE_SECONDS = 120.0


class DaemonHarness:
    """Forwards a run to the user's host daemon over the daemon websocket."""

    def __init__(
        self,
        kind: HarnessKind,
        *,
        event_timeout_seconds: float = DEFAULT_DAEMON_EVENT_TIMEOUT_SECONDS,
        reconnect_grace_seconds: float = DEFAULT_RECONNECT_GRACE_SECONDS,
    ):
        self.kind = kind
        self.event_timeout_seconds = event_timeout_seconds
        self.reconnect_grace_seconds = reconnect_grace_seconds

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
        daemon_id = _daemon_id_from_options(options)
        daemon_user_id = _daemon_user_id_from_options(options)
        payload = _run_start_payload(
            agent=agent,
            conversation=conversation,
            messages=messages,
            ctx=ctx,
            options=options,
            agent_run_id=agent_run_id,
            harness_kind=self.kind,
        )
        try:
            payload["mcp"] = await _mcp_payload(
                agent_run_id=agent_run_id,
                conversation_id=conversation.id,
                ctx=ctx,
                options=options,
                prompt=_prompt_text(payload),
            )
            queue = await agent_runtime_daemon_hub.start_run(
                daemon_id=daemon_id,
                user_id=daemon_user_id,
                agent_run_id=agent_run_id,
                payload=payload,
            )
        except RuntimeError as exc:
            yield AgentEvent(
                type=AgentEventType.ERROR,
                data=str(exc),
                agent_run_id=agent_run_id,
            )
            return

        stop_sent = False
        # Set the moment a RECONNECTING sentinel is first seen; cleared once a
        # real event arrives again. Bounds how long this run waits out a dead
        # connection, independent of (and much shorter than) event_timeout_seconds.
        reconnecting_since: float | None = None
        try:
            while True:
                if (
                    not stop_sent
                    and options.should_stop is not None
                    and await options.should_stop()
                ):
                    await agent_runtime_daemon_hub.stop_run(
                        daemon_id=daemon_id,
                        user_id=daemon_user_id,
                        agent_run_id=agent_run_id,
                    )
                    stop_sent = True
                deadline = time.monotonic() + self.event_timeout_seconds
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if reconnecting_since is not None:
                            grace_remaining = (
                                reconnecting_since + self.reconnect_grace_seconds
                            ) - time.monotonic()
                            if grace_remaining <= 0:
                                yield AgentEvent(
                                    type=AgentEventType.ERROR,
                                    data="User daemon did not reconnect within the grace period",
                                    agent_run_id=agent_run_id,
                                )
                                return
                            remaining = min(remaining, grace_remaining)
                        if remaining <= 0:
                            raise TimeoutError
                        try:
                            event = await asyncio.wait_for(
                                queue.get(),
                                timeout=min(remaining, 1.0),
                            )
                            break
                        except TimeoutError:
                            if (
                                stop_sent
                                or options.should_stop is None
                                or not await options.should_stop()
                            ):
                                continue
                            await agent_runtime_daemon_hub.stop_run(
                                daemon_id=daemon_id,
                                user_id=daemon_user_id,
                                agent_run_id=agent_run_id,
                            )
                            stop_sent = True
                except TimeoutError:
                    yield AgentEvent(
                        type=AgentEventType.ERROR,
                        data="User daemon did not emit a run event before timeout",
                        agent_run_id=agent_run_id,
                    )
                    return

                if event.type == AgentEventType.RECONNECTING:
                    if reconnecting_since is None:
                        reconnecting_since = time.monotonic()
                        yield AgentEvent(
                            type=AgentEventType.STATUS,
                            data={
                                "status": "Connection to your machine dropped; waiting to reconnect…",
                                "phase": "reconnecting",
                            },
                            agent_run_id=agent_run_id,
                        )
                    continue

                if reconnecting_since is not None:
                    reconnecting_since = None
                    yield AgentEvent(
                        type=AgentEventType.STATUS,
                        data={"status": "Reconnected.", "phase": "reconnected"},
                        agent_run_id=agent_run_id,
                    )

                yield event
                if event.type in {
                    AgentEventType.COMPLETED,
                    AgentEventType.STOPPED,
                    AgentEventType.ERROR,
                    AgentEventType.REJECTED,
                }:
                    return
        finally:
            await agent_runtime_daemon_hub.finish_run(
                daemon_id=daemon_id,
                user_id=daemon_user_id,
                agent_run_id=agent_run_id,
            )


def _daemon_id_from_options(options: HarnessOptions) -> UUID:
    profile = options.extra.get("runtime_profile")
    if isinstance(profile, dict):
        daemon_id = profile.get("daemon_id")
        if daemon_id:
            return UUID(str(daemon_id))
        config: object = profile.get("config")
        if isinstance(config, dict) and config.get("daemon_id"):
            return UUID(str(config["daemon_id"]))
    raise RuntimeError("USER_DAEMON runtime profile is missing daemon_id")


def _daemon_user_id_from_options(options: HarnessOptions) -> UUID:
    profile = options.extra.get("runtime_profile")
    if isinstance(profile, dict) and profile.get("user_id"):
        return UUID(str(profile["user_id"]))
    raise RuntimeError("USER_DAEMON runtime profile is missing user_id")


def _run_start_payload(
    *,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    options: HarnessOptions,
    agent_run_id: UUID,
    harness_kind: HarnessKind,
) -> JsonObject:
    current_messages = _current_turn_messages(messages)
    session_id = _local_daemon_session_id(
        conversation=conversation,
        harness_kind=harness_kind,
    )
    return {
        "agent_run_id": str(agent_run_id),
        "conversation_id": str(conversation.id),
        "harness_kind": harness_kind.value,
        "model_name": options.model_name,
        "runtime": {
            "profile_id": _runtime_profile_value(options, "profile_id"),
            "harness_kind": harness_kind.value,
            "model_name": options.model_name,
        },
        "prompt": _prompt_payload(
            agent=agent,
            conversation=conversation,
            messages=current_messages,
            ctx=ctx,
            session_id=session_id,
        ),
        "agent": agent.model_dump(mode="json"),
        "conversation": conversation.model_dump(mode="json", exclude={"messages", "agent_runs"}),
        "context": ctx.model_dump(mode="json"),
        "runtime_profile": options.extra.get("runtime_profile"),
        "runtime_credentials": options.extra.get("runtime_credentials"),
        "mcp": {},
    }


async def _mcp_payload(
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    ctx: AgentContext,
    options: HarnessOptions,
    prompt: str | None = None,
) -> JsonObject:
    url = daemon_mcp_url(conversation_id)
    workspace_id = _context_workspace_id(ctx)
    workspace_cwd = _context_workspace_cwd(ctx)
    workspace_service = WorkspaceSandboxService()
    try:
        workspace_env = await workspace_service.get_env_vars(
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            organization_id=ctx.org_id,
            workload_type=getattr(ctx, "workload_type", None),
            workload_id=getattr(ctx, "workload_id", None),
            workload_name=ctx.agent_name,
            scope=getattr(ctx, "scope", None),
            session_id=str(agent_run_id),
        )
        token = workspace_env["LEMMA_TOKEN"]
    finally:
        await workspace_service.close()
    payload: JsonObject = {
        "server_name": LEMMA_MCP_SERVER_NAME,
        "url": url,
        "authorization": f"Bearer {token}",
        "token": token,
        "run_id": str(agent_run_id),
        "conversation_id": str(conversation_id),
        "workspace": {
            "id": workspace_id,
            "cwd": workspace_cwd,
        },
        "tool_names": await _exported_tool_names_from_options(
            agent_run_id=agent_run_id,
            ctx=ctx,
            options=options,
            prompt=prompt,
        ),
    }
    return payload


async def _exported_tool_names_from_options(
    *,
    agent_run_id: UUID,
    ctx: AgentContext,
    options: HarnessOptions,
    prompt: str | None,
) -> list[str]:
    if not options.toolsets:
        return []
    run_ctx = RunContext(
        deps=ctx,
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=prompt,
        retries={},
        run_id=str(agent_run_id),
        metadata={
            "agent_run_id": str(agent_run_id),
            "conversation_mcp": True,
            "model_name": options.model_name,
        },
        model_settings=options.model_settings,
    )
    names: list[str] = []
    for raw_toolset in options.toolsets:
        if not isinstance(raw_toolset, AbstractToolset):
            continue
        toolset = await raw_toolset.for_run(run_ctx)
        async with toolset:
            for original_name, tool in (await toolset.get_tools(run_ctx)).items():
                names.append(exported_tool_name(tool.tool_def.name or original_name))
    return names


def _prompt_text(payload: JsonObject) -> str:
    prompt = payload.get("prompt")
    if isinstance(prompt, dict):
        return _join_prompt_parts(
            str(prompt.get("system_prompt") or ""),
            str(prompt.get("user_prompt") or ""),
        )
    return ""


def _current_turn_messages(messages: Sequence[Message]) -> list[Message]:
    sorted_messages = sorted(messages, key=lambda item: item.sequence)
    for message in reversed(sorted_messages):
        if message.role == MessageRole.USER:
            return [message]
    return sorted_messages[-1:]


def _context_workspace_id(ctx: AgentContext) -> str:
    value = getattr(ctx, "workspace_id", None)
    return str(value or "default")


def _context_workspace_cwd(ctx: AgentContext) -> str:
    get_workspace_cwd = getattr(ctx, "get_workspace_cwd", None)
    if callable(get_workspace_cwd):
        value = get_workspace_cwd()
        if value:
            return str(value)
    return f"/workspace/conversations/{ctx.conversation_id}"


def _runtime_profile_value(options: HarnessOptions, key: str) -> object | None:
    profile = options.extra.get("runtime_profile")
    return profile.get(key) if isinstance(profile, dict) else None


def _prompt_payload(
    *,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    session_id: str | None,
) -> JsonObject:
    instructions = build_agent_instructions(agent=agent, conversation=conversation, ctx=ctx)
    user_prompt = _render_history(messages)
    session_sections: list[str] = []
    if instructions:
        session_sections.append("# Instructions\n" + instructions)
    # The workspace cwd + working conventions are stated by build_agent_instructions
    # (the Working Directory section); here we only add the daemon-specific note that
    # tools run there and the provider's own process cwd is not the workspace.
    session_sections.append(
        "# Runtime\n"
        "You are running through a Lemma user daemon. Use the Lemma MCP tools "
        "(the lemma_* tools) for file and command execution; they run in your "
        "workspace working directory (see the Working Directory section). Any "
        "provider process cwd is daemon scratch space, not the workspace."
    )
    output_contract = _output_contract(agent=agent, conversation=conversation)
    if output_contract:
        session_sections.append(output_contract)
    payload: JsonObject = {
        "user_prompt": user_prompt,
        "output_schema": agent.output_schema or {},
        "structured": bool(agent.output_schema) or conversation.type == ConversationType.TASK,
    }
    system_prompt = "\n\n".join(section for section in session_sections if section.strip())
    if session_id is None:
        payload["system_prompt"] = system_prompt
    else:
        payload["session_id"] = session_id
        payload["recovery_system_prompt"] = system_prompt
    return payload


def _join_prompt_parts(system_prompt: str, user_prompt: str) -> str:
    return "\n\n".join(
        section
        for section in (system_prompt, "# Conversation\n" + user_prompt if user_prompt else "")
        if section.strip()
    )


def _local_daemon_session_id(
    *,
    conversation: Conversation,
    harness_kind: HarnessKind,
) -> str | None:
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    session = metadata.get("daemon_session")
    if isinstance(session, dict) and session.get("harness_kind") == harness_kind.value:
        value = session.get("session_id")
        return str(value) if value else None

    # Compatibility with conversations written before the single-session shape.
    sessions = metadata.get("daemon_sessions")
    if not isinstance(sessions, dict):
        return None
    session = sessions.get(harness_kind.value)
    if not isinstance(session, dict):
        return None
    value = session.get("session_id")
    return str(value) if value else None


def _output_contract(*, agent: Agent, conversation: Conversation) -> str:
    if not agent.output_schema and conversation.type != ConversationType.TASK:
        return ""
    output_schema = json.dumps(
        to_json_value(agent.output_schema or {}),
        indent=2,
        sort_keys=True,
    )
    return (
        "# Output Contract\n"
        "Your final response must be a single JSON object with this shape:\n"
        "{\n"
        '  "status": "COMPLETED" | "FAILED" | "WAITING",\n'
        '  "output": <data matching the agent output schema>,\n'
        '  "error": null | "short error message"\n'
        "}\n\n"
        "Use WAITING when more user input is required. Use FAILED only when "
        "the task cannot be completed. The output value must match this JSON "
        f"schema:\n```json\n{output_schema}\n```"
    )


def _render_history(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in sorted(messages, key=lambda item: item.sequence):
        text = _message_text(message)
        if text:
            lines.append(f"{message.role.upper()}:\n{text}")
    return "\n\n".join(lines)


def _message_text(message: Message) -> str:
    if message.kind == MessageKind.TOOL_CALL:
        body = (
            f"Tool call {message.tool_name}({message.tool_call_id}):\n"
            f"{json.dumps(to_json_value(message.tool_args), indent=2)}"
        )
    elif message.kind == MessageKind.TOOL_RETURN:
        body = (
            f"Tool result {message.tool_name or 'unknown_tool'}"
            f"({message.tool_call_id}):\n"
            f"{json.dumps(to_json_value(message.tool_result), indent=2)}"
        )
    else:
        body = message.text or ""
    metadata = message.metadata or {}
    extras: list[str] = []
    state = metadata.get("state") if isinstance(metadata, dict) else None
    if state is not None:
        extras.append(
            "UI state:\n```json\n"
            + json.dumps(to_json_value(state), indent=2)
            + "\n```"
        )
    attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
    if isinstance(attachments, list) and attachments:
        extras.append(f"Attachments: {json.dumps(to_json_value(attachments))}")
    if extras:
        return body + "\n\n" + "\n\n".join(extras)
    return body
