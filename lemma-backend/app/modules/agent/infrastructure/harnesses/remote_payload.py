"""Dispatch payload construction shared by remote Agent Host harnesses."""

from __future__ import annotations

from collections.abc import Mapping

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    user_prompt_text,
)
from app.modules.workspace.contracts.tooling import WorkspaceSandboxService
from app.core.config import settings
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.services.runtime_model_factory import provider_model_settings
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.runtime_notes import prepend_runtime_notes
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    ConversationType,
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


from app.modules.agent.tools.skills.pydantic_adapter import (
    LOCAL_WORKSPACE_SKILL_OVERRIDE,
    LOCAL_WORKSPACE_SKILL_OVERRIDE_MARKER,
)

#: What a host agent is given of the user's Lemma identity. An allowlist rather
#: than "whatever `get_env_vars` returned", so a sandbox-only variable added
#: later does not silently start leaving the sandbox.
_HOST_AGENT_ENVIRONMENT = frozenset(
    {
        "LEMMA_TOKEN",
        "LEMMA_BASE_URL",
        "LEMMA_AUTH_URL",
        "LEMMA_HOST_ORIGIN",
        "LEMMA_USER_ID",
        "LEMMA_POD_ID",
        "LEMMA_ORG_ID",
    }
)


def host_agent_environment(workspace_env: Mapping[str, str]) -> dict[str, str]:
    """The identity a host agent is given, out of a sandbox's environment.

    The same delegated session the sandbox gets, for an agent that runs on the
    user's own machine instead. `LEMMA_WORKSPACE_URL` is deliberately not among
    them: it addresses the cloud sandbox, and a host agent that believed it
    would be pointed at a filesystem that is not the folder it was bound to.

    The addresses are replaced, not copied. A sandbox's are chosen for the
    sandbox's network -- on Desktop `host.lemma.internal`, which only the
    guest's containers can resolve -- and a host agent needs the ones this
    machine can reach, the same perspective the MCP URL is built from.
    """
    identity = {
        name: value
        for name, value in workspace_env.items()
        if name in _HOST_AGENT_ENVIRONMENT
    }
    return identity | _host_addresses()


def _host_addresses() -> dict[str, str]:
    """Where the backend is reachable from the machine the host agent runs on."""
    addresses = {
        "LEMMA_BASE_URL": settings.cli_api_url or settings.api_url,
        "LEMMA_AUTH_URL": settings.cli_auth_frontend_url or settings.auth_frontend_url,
        "LEMMA_HOST_ORIGIN": settings.frontend_url,
    }
    return {name: value for name, value in addresses.items() if value}


def run_start_payload(
    *,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    agent_run_id: UUID,
    runtime_instructions: str,
    carries_history: bool,
    resumed_tool_call_id: str | None = None,
) -> JsonObject:
    """Everything one dispatched run needs, and nothing it does not.

    ``carries_history`` is set when the run is not even going to try to resume a
    provider session, so the prompt has to bring the conversation with it, and
    ``resumed_tool_call_id`` names the pausing call this run exists to answer.
    See :func:`_turn_messages`.

    Deliberately does not carry ``runtime_credentials``. They were assembled
    here and then dropped by the only caller, on the one code path whose
    destination is a third-party machine — a leak waiting for someone to widen
    the spec it feeds.
    """
    return {
        "agent_run_id": str(agent_run_id),
        "conversation_id": str(conversation.id),
        "prompt": _prompt_payload(
            agent=agent,
            conversation=conversation,
            messages=_turn_messages(
                messages,
                carries_history=carries_history,
                resumed_tool_call_id=resumed_tool_call_id,
            ),
            ctx=ctx,
            runtime_instructions=runtime_instructions,
        ),
        "agent": agent.model_dump(mode="json"),
        "conversation": conversation.model_dump(
            mode="json", exclude={"messages", "agent_runs"}
        ),
        "context": ctx.model_dump(mode="json"),
    }


async def mcp_payload[DepsT: AgentContext](
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    ctx: DepsT,
    options: HarnessOptions[DepsT],
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
        agent_environment = host_agent_environment(workspace_env)
    finally:
        await workspace_service.close()
    return {
        "environment": agent_environment,
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
    except binascii.Error, ValueError, UnicodeDecodeError:
        return None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        return None
    return datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()


async def _exported_tool_names[DepsT: AgentContext](
    *,
    agent_run_id: UUID,
    ctx: DepsT,
    options: HarnessOptions[DepsT],
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
        model_settings=provider_model_settings(options.model_settings),
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
        # Agent Host resolves the native cwd. This prompt separately names the
        # sandbox cwd used by Lemma MCP execution tools.
        runs_as_remote_process=True,
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


def _turn_messages(
    messages: Sequence[Message],
    *,
    carries_history: bool,
    resumed_tool_call_id: str | None = None,
) -> list[Message]:
    """The messages this prompt has to carry.

    Normally just the latest user message. A Lemma conversation maps to one
    provider session, kept in one working directory, and the agent loads it back
    on every turn — so the rest is already on the other side and resending it
    would only duplicate the conversation in its context.

    A run that resumes a pause is the same rule with a different answer. Waking
    from a ``wait_for`` adds no user message, so "the latest user message" is the
    request that started the task — and re-sending that to an agent whose
    session already contains it does not read as "carry on", it reads as the
    person asking again, so the agent does the work twice. What the session has
    genuinely not seen is the return Lemma synthesized for the call it paused
    on, which is exactly what resuming has to deliver.

    The other exception is a run that is not going to try: a harness that never
    advertised ``loadSession`` has no session to resume, ever. Sending one lone
    message there leaves the agent answering a follow-up it has never seen the
    start of. This costs nothing in the usual case, because a resumable harness
    only lacks a stored session on a conversation's first turn, where there is
    no history to send.
    """
    ordered = sorted(messages, key=lambda item: item.sequence)
    if carries_history:
        return ordered
    if resumed_tool_call_id is not None:
        resumed = [
            message
            for message in ordered
            if message.kind == MessageKind.TOOL_RETURN
            and message.tool_call_id == resumed_tool_call_id
        ]
        if resumed:
            return resumed
    for message in reversed(ordered):
        if message.role == MessageRole.USER:
            return [message]
    return ordered[-1:]


def _runtime_profile_value[DepsT](
    options: HarnessOptions[DepsT], key: str
) -> object | None:
    profile = options.extra.get("runtime_profile")
    return profile.get(key) if isinstance(profile, dict) else None


def _workspace_cwd(ctx: AgentContext) -> str:
    get_workspace_cwd = getattr(ctx, "get_workspace_cwd", None)
    if callable(get_workspace_cwd):
        value = get_workspace_cwd()
        if value:
            return str(value)
    # The project root, not a directory named after the conversation id: that
    # shape is not what `resolve_workspace_location` produces, so a payload
    # carrying it would send a remote harness somewhere the conversation's own
    # metadata does not name.
    return WORKSPACE_ROOT


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


def _user_turn_text(message: Message) -> str:
    """Everything the surface knows about one user message.

    A user turn carries more than its text: who sent it, what it quotes, which
    channel it came from, the files attached, whether it was a voice note, and
    whether a reply has to go back as email. The in-process harness assembles all
    of that. This path sent the bare text, so the same Slack thread answered by a
    Codex or Claude Code host arrived with no sender, no referent for a quoted
    reply, and no sign of the files already in the datastore -- reading as though
    the agent had ignored the attachment. One builder now serves both paths.
    """
    body = user_prompt_text(message)
    attachments = (message.metadata or {}).get("attachments")
    if isinstance(attachments, list) and attachments:
        body += f"\n\nAttachments: {json.dumps(to_json_value(attachments))}"
    return body


def _history_tool_result(result: object) -> str:
    """One tool return, as it appears in a replayed transcript.

    Everything `_render_history` produces ends up concatenated into a single
    user turn -- the ACP layer merges system framing, history and the new
    message into one text block -- so a tool result is not on a tool channel by
    the time a model reads it. It reads as something the user typed.

    That is tolerable for data. It is not tolerable for Lemma's own
    instructions to the agent: `load_skill` appends a "Local Lemma Workspace
    Override" paragraph addressed to the reader, and replaying it inside a user
    turn on every non-resuming turn is why agents echoed it back into their
    replies. The agent already acted on it when the tool returned; it does not
    need it again, and it must not receive it as the user's words.
    """
    rendered = json.dumps(to_json_value(result), indent=2)
    if LOCAL_WORKSPACE_SKILL_OVERRIDE_MARKER not in rendered:
        return rendered
    # Every depth it can be stored at. A result `unwrap_mcp_content` could not
    # unwrap -- more than one content block, say -- keeps the skill as JSON text
    # inside a text block, so the paragraph is escaped once by the tool and
    # again by the `json.dumps` above. Matching only the single-escaped form
    # left it in, on exactly the path it most needed removing from.
    for encoded in _encodings_of(LOCAL_WORKSPACE_SKILL_OVERRIDE, depth=3):
        rendered = rendered.replace(encoded, "")
    return rendered


def _encodings_of(text: str, *, depth: int) -> list[str]:
    """`text` as it reads after 1..depth rounds of JSON string escaping."""
    forms = []
    for _ in range(depth):
        text = json.dumps(text)[1:-1]
        forms.append(text)
    # Deepest first, so a shallower form cannot match inside a deeper one.
    return forms[::-1]


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
            f"{_history_tool_result(message.tool_result)}"
        )
    elif message.role == MessageRole.USER:
        return _user_turn_text(message)
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
