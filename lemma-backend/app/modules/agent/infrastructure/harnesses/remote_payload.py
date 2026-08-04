"""Dispatch payload construction shared by remote Agent Host harnesses."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from app.composition.agent_workspace import WorkspaceSandboxService
from app.core.config import settings
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.runtime_notes import prepend_runtime_notes
from app.modules.agent.domain.value_objects import (
    ConversationType,
    HarnessKind,
    HarnessOptions,
    JsonObject,
    MessageKind,
    MessageRole,
    to_json_value,
)
from app.modules.agent.infrastructure.mcp import (
    LEMMA_MCP_SERVER_NAME,
    exported_tool_name,
)
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_TOOL_NAME,
)


def run_start_payload(
    *,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    options: HarnessOptions,
    agent_run_id: UUID,
    harness_kind: HarnessKind,
    runtime_instructions: str,
) -> JsonObject:
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
            messages=_current_turn_messages(messages),
            ctx=ctx,
            runtime_instructions=runtime_instructions,
        ),
        "agent": agent.model_dump(mode="json"),
        "conversation": conversation.model_dump(
            mode="json", exclude={"messages", "agent_runs"}
        ),
        "context": ctx.model_dump(mode="json"),
        "runtime_profile": options.extra.get("runtime_profile"),
        "runtime_credentials": options.extra.get("runtime_credentials"),
        "mcp": {},
    }


async def mcp_payload(
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    ctx: AgentContext,
    options: HarnessOptions,
    prompt: str | None = None,
    extra_tool_names: Sequence[str] = (),
) -> JsonObject:
    """Build the MCP endpoint the host's bridge will call back on.

    ``token_expires_at`` is part of the payload because the credential inside it
    is minted once, encrypted into START_RUN once, and then used verbatim by a
    remote process for the whole run. Nothing refreshes it. A run allowed to
    outlive it does not fail -- it keeps going with every Lemma tool call
    returning 401, which the agent experiences as its tools quietly vanishing.
    Publishing the real expiry lets the dispatcher bound the run by it instead.
    """
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
    return {
        "server_name": LEMMA_MCP_SERVER_NAME,
        "url": (
            f"{settings.api_url.rstrip('/')}/agent-runtime/conversations/"
            f"{conversation_id}/mcp"
        ),
        "authorization": f"Bearer {token}",
        "token": token,
        "token_expires_at": _token_expiry_iso(token),
        "run_id": str(agent_run_id),
        "conversation_id": str(conversation_id),
        "workspace": {
            "id": str(getattr(ctx, "workspace_id", None) or "default"),
            "cwd": _workspace_cwd(ctx),
        },
        "tool_names": await _exported_tool_names(
            agent_run_id=agent_run_id,
            ctx=ctx,
            options=options,
            prompt=prompt,
            extra_names=extra_tool_names,
        ),
    }


def token_expires_at(mcp: JsonObject) -> datetime | None:
    """Read back the expiry :func:`mcp_payload` published, if it has one."""
    raw = mcp.get("token_expires_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _token_expiry_iso(token: str) -> str | None:
    """Decode the JWT ``exp`` claim without verifying the signature.

    We minted this token moments ago, so there is nothing to authenticate here;
    we only need the issuer's own idea of when it dies. Returns None for a token
    that is not a JWT or carries no usable ``exp``, and the caller then falls
    back to its configured ceiling.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        return None
    return datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()


async def _exported_tool_names(
    *,
    agent_run_id: UUID,
    ctx: AgentContext,
    options: HarnessOptions,
    prompt: str | None,
    extra_names: Sequence[str] = (),
) -> list[str]:
    if not options.toolsets:
        return list(extra_names)
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
    # Tools the MCP route serves that are not in `options.toolsets` — the MCP
    # service re-assembles independently, so the two lists must be reconciled
    # here or this one under-reports what the host can actually call.
    names.extend(name for name in extra_names if name not in names)
    return names


def _prompt_payload(
    *,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    runtime_instructions: str,
) -> JsonObject:
    sections: list[str] = []
    instructions = build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=ctx,
    )
    if instructions:
        sections.append("# Instructions\n" + instructions)
    sections.append(runtime_instructions)
    output_contract = _output_contract(agent=agent, conversation=conversation)
    if output_contract:
        sections.append(output_contract)
    # No output_schema/structured keys: nothing downstream reads them. The run
    # spec carries only system_prompt + user_prompt, and the schema reaches the
    # agent as the `lemma_final_answer` tool's inputSchema over MCP.
    return {
        "user_prompt": prepend_runtime_notes(_render_history(messages)),
        "system_prompt": "\n\n".join(section for section in sections if section),
    }


def _current_turn_messages(messages: Sequence[Message]) -> list[Message]:
    ordered = sorted(messages, key=lambda item: item.sequence)
    for message in reversed(ordered):
        if message.role == MessageRole.USER:
            return [message]
    return ordered[-1:]


def _runtime_profile_value(options: HarnessOptions, key: str) -> object | None:
    profile = options.extra.get("runtime_profile")
    return profile.get(key) if isinstance(profile, dict) else None


def _workspace_cwd(ctx: AgentContext) -> str:
    get_workspace_cwd = getattr(ctx, "get_workspace_cwd", None)
    if callable(get_workspace_cwd):
        value = get_workspace_cwd()
        if value:
            return str(value)
    return f"/workspace/conversations/{ctx.conversation_id}"


def _output_contract(*, agent: Agent, conversation: Conversation) -> str:
    """Tell the agent to end the task by calling the final-answer tool.

    The schema also rides on the tool's own ``inputSchema`` over MCP, but ACP
    adapters vary in how much of that reaches the model, so echoing it here is
    cheap insurance. The plain-JSON fallback is what makes the normalizer's
    whole-message-is-JSON parse a legitimate signal rather than a guess.
    """
    if not agent.output_schema and conversation.type != ConversationType.TASK:
        return ""
    tool = exported_tool_name(FINAL_ANSWER_TOOL_NAME)
    schema_block = (
        "\n\nThe `output` value must match this JSON schema:\n```json\n"
        + json.dumps(to_json_value(agent.output_schema), indent=2, sort_keys=True)
        + "\n```"
        if agent.output_schema
        else ""
    )
    return (
        "# Final Answer\n"
        f"End this task by calling the `{tool}` tool with `status` "
        '("COMPLETED", "FAILED", or "WAITING"), `output`, and an optional '
        "`error`. Do not print that JSON as your reply — call the tool.\n\n"
        "Use WAITING when you need more from the user, and FAILED only when the "
        "task cannot be completed."
        + schema_block
        + f"\n\nIf `{tool}` is unavailable, then and only then reply with that "
        "JSON object as your entire message and nothing else."
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
    return body + ("\n\n" + "\n\n".join(extras) if extras else "")
