"""Which provider session a conversation is talking to.

One Lemma conversation is one Codex/Claude Code/OpenCode session. Without that,
the agent meets the user again on every message: it cannot see what it just
said, so it re-asks answered questions and contradicts itself.

The host opens a session and reports its id on the checkpoint it writes just
before dispatching the prompt; that id is stored against the conversation, and
every later turn is dispatched with it so the agent loads its own history back.
Both halves live here so the read and the write cannot drift apart.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_SESSION_METADATA_KEY,
    AgentHostHarnessCapabilities,
    AgentHostRunCheckpoint,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


async def remember_provider_session(
    uow: SqlAlchemyUnitOfWork,
    checkpoint: AgentHostRunCheckpoint,
) -> bool:
    """Bind the conversation to the provider session the host opened.

    Deliberately outside ``apply_checkpoint``'s state machine. That machine
    guards *dispatch* safety and drops anything stale or regressive, which is
    right for a lease and wrong here — a re-sent or late checkpoint still names
    the same session, and the cost of dropping it is the conversation silently
    losing its memory.

    Returns whether the binding actually changed. The host puts the session id
    on *every* checkpoint, and a non-terminal checkpoint is the lease heartbeat
    it resends on every poll, so writing unconditionally meant one ``jsonb_set``
    per poll per active run to store a value that had not moved since the run
    began.
    """
    session_id = checkpoint.detail.get("provider_session_id")
    if not isinstance(session_id, str) or not session_id:
        return False
    binding = (
        await uow.session.execute(
            select(AgentRunModel.conversation_id, AgentHostRunLeaseModel.harness_id)
            .join(
                AgentHostRunLeaseModel,
                AgentHostRunLeaseModel.run_id == AgentRunModel.id,
            )
            .where(AgentRunModel.id == checkpoint.run_id)
        )
    ).one_or_none()
    if binding is None:
        return False
    conversation_id, harness_id = binding
    # Stored with the harness that opened it. A Codex rollout id means nothing
    # to Claude Code, so a conversation moved to another harness starts a fresh
    # session there instead of failing a load every turn.
    binding_value = {"harness_id": str(harness_id), "session_id": session_id}
    repository = ConversationRepository(uow)
    stored = await repository.get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
    )
    if stored == binding_value:
        return False
    await repository.set_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        binding_value,
    )
    return True


async def resume_session_id(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    harness_id: UUID,
    capabilities: JsonObject,
) -> str | None:
    """The provider session this conversation should continue in, if any.

    Absent on a conversation's first turn, when the conversation last spoke to
    a different harness, and for a harness that never advertised
    ``loadSession`` — asking such an agent to load would only cost a round trip
    before it fell back to a new session anyway.
    """
    if not AgentHostHarnessCapabilities.model_validate(capabilities).load_session:
        return None
    stored = await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
    )
    if not isinstance(stored, dict) or stored.get("harness_id") != str(harness_id):
        return None
    session_id = stored.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None
