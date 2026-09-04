"""Getting one run onto a host, and keeping its credential alive.

Separated from the harness because dispatch and consumption are different
jobs with different failure modes: this one assembles a spec, mints a
run-scoped credential and hands both to the control plane, while the
harness drives whatever comes back. They share only the run id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Sequence
from uuid import UUID

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import NEW_SESSION_ONLY, AgentHostRunSpec
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt
from app.modules.agent.domain.value_objects import HarnessOptions
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.agent_host import session_memory
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_config import (
    AgentHostRunConfig,
    joined_prompt,
    json_object,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    DispatchedRun,
    credential_bounded_timeout,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import (
    mcp_payload,
    run_start_payload,
    token_expires_at,
)
from app.modules.agent.infrastructure.mcp import exported_tool_name
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_TOOL_NAME,
    final_answer_expected,
)

logger = get_logger(__name__)


async def refresh_credential(
    *,
    uow_factory: UnitOfWorkFactory,
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
        async with uow_factory() as uow:
            command = await AgentHostDispatchRepository(uow).enqueue_credential_refresh(
                run_id=agent_run_id,
                encrypted_mcp_payload=encrypted,
            )
            await uow.commit()
    except (AgentHostRepositoryError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning(
            "agent.harnesses.agent_host.credential_refresh_failed.degraded",
            agent_run_id=str(agent_run_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None
    if command is None:
        # The run ended while we were minting; nothing left to refresh.
        return None
    await poke_host(command.host_id)
    return token_expires_at(mcp)


def _resumed_tool_call_id(run: AgentRun | None) -> str | None:
    """Which paused tool call this run was started to answer, if any."""
    metadata = (run.metadata if run is not None else None) or {}
    value = metadata.get("resumed_tool_call_id")
    return value if isinstance(value, str) and value else None


async def enqueue_run(
    *,
    uow_factory: UnitOfWorkFactory,
    event_timeout_seconds: float,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: AgentContext,
    options: HarnessOptions,
    agent_run_id: UUID,
    run_config: AgentHostRunConfig,
) -> DispatchedRun:
    # Resolved first, because it decides what the prompt has to contain: a
    # run that will not even try to resume a session is talking to an agent
    # with no history, so the prompt must carry it.
    async with uow_factory() as uow:
        harness = await AgentHostRepository(uow).get_harness(
            harness_id=run_config.harness_id
        )
        if harness is None:
            raise RuntimeError("Agent Host harness is unavailable")
        resume_session_id = await session_memory.resume_session_id(
            uow,
            conversation_id=conversation.id,
            harness_id=run_config.harness_id,
            capabilities=harness.capabilities,
        )
        harness_id = harness.id
        host_id = harness.host_id
        harness_key = harness.harness_key
        config_revision = harness.config_revision
        # Set only on a run started to answer a pausing tool call. Read from
        # the run rather than passed in, because the run row is where the
        # resume recorded it and a second copy could only ever disagree.
        run = await ConversationRepository(uow).get_agent_run(agent_run_id)

    payload = run_start_payload(
        agent=agent,
        conversation=conversation,
        messages=messages,
        ctx=ctx,
        agent_run_id=agent_run_id,
        runtime_instructions=load_agent_host_runtime_prompt(),
        carries_history=resume_session_id is None,
        resumed_tool_call_id=_resumed_tool_call_id(run),
    )
    prompt = json_object(payload.get("prompt"))
    mcp = await mcp_payload(
        agent_run_id=agent_run_id,
        conversation_id=conversation.id,
        ctx=ctx,
        options=options,
        prompt=joined_prompt(prompt),
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
    # A conversation is one provider session, and a session keeps its own
    # history — so instructions delivered when it opened are still there on
    # every later turn. Re-sending them each time put another copy of a
    # multi-kilobyte block into the provider's transcript per message, which it
    # then re-read in full on every turn after that. Skipped only when this
    # exact text is already known to have reached this session; the host still
    # sends it if it ends up opening a new one.
    system_prompt = str(
        prompt.get("system_prompt") or prompt.get("recovery_system_prompt") or ""
    )
    digest = session_memory.instructions_digest(system_prompt)
    system_prompt_delivery: str | None = None
    if resume_session_id is not None:
        async with uow_factory() as uow:
            if await session_memory.instructions_already_delivered(
                uow,
                conversation_id=conversation.id,
                harness_id=harness_id,
                digest=digest,
            ):
                system_prompt_delivery = NEW_SESSION_ONLY
    dispatched_at = datetime.now(timezone.utc)
    timeout_seconds, credential_bounded = credential_bounded_timeout(
        configured_seconds=event_timeout_seconds,
        credential_expires_at=token_expires_at(mcp),
        now=dispatched_at,
        agent_run_id=agent_run_id,
    )
    async with uow_factory() as uow:
        run_spec = AgentHostRunSpec(
            agent_run_id=agent_run_id,
            conversation_id=conversation.id,
            harness_id=harness_id,
            profile_revision=config_revision,
            model_name=run_config.model_name,
            config_selections=run_config.config_selections,
            system_prompt=system_prompt,
            system_prompt_delivery=system_prompt_delivery,
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
        # A promise, committed with the command it belongs to. It becomes a
        # record only when the host reports that it prompted, so a run that
        # dies on the way out does not leave these instructions marked
        # delivered and skipped for the rest of the conversation.
        await session_memory.record_pending_instructions(
            uow,
            conversation_id=conversation.id,
            run_id=agent_run_id,
            digest=digest,
        )
        await uow.commit()
    await poke_host(host_id)
    return DispatchedRun(
        harness_key=harness_key,
        event_timeout_seconds=timeout_seconds,
        credential_bounded=credential_bounded,
        credential_expires_at=token_expires_at(mcp),
    )
