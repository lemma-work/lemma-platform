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

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AGENT_HOST_SESSION_METADATA_KEY,
    AgentHostHarnessCapabilities,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel


# RUNNING is emitted on the host's first provider *event*, WAITING_INPUT and
# SUCCEEDED only after a turn actually ran. Everything else — the pre-dispatch
# states, RECOVERING, and every failure — is compatible with the prompt never
# having been sent.
_STATES_PROVING_THE_PROMPT_LANDED = frozenset(
    {
        AgentHostRunState.RUNNING,
        AgentHostRunState.WAITING_INPUT,
        AgentHostRunState.SUCCEEDED,
    }
)


async def remember_provider_session(
    uow: SqlAlchemyUnitOfWork,
    checkpoint: AgentHostRunCheckpoint,
) -> bool:
    """Bind the conversation to the provider session the host opened.

    A replay can arrive after a newer turn has opened a replacement session.
    Serialize metadata updates and retain the newest run's binding independently
    of the lease state machine, which guards dispatch rather than continuity.

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
            select(
                AgentRunModel.conversation_id,
                AgentHostRunLeaseModel.harness_id,
                AgentRunModel.created_at,
                AgentHostRunLeaseModel.host_id,
            )
            .join(
                AgentHostRunLeaseModel,
                AgentHostRunLeaseModel.run_id == AgentRunModel.id,
            )
            .where(AgentRunModel.id == checkpoint.run_id)
        )
    ).one_or_none()
    if binding is None:
        return False
    conversation_id, harness_id, created_at, host_id = binding
    repository = ConversationRepository(uow)
    stored = await repository.get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        for_update=True,
    )
    stored = stored if isinstance(stored, dict) else {}
    if _binding_is_newer(stored, checkpoint.run_id, created_at):
        return False
    # Stored with the harness that opened it. A Codex rollout id means nothing
    # to Claude Code, so a conversation moved to another harness starts a fresh
    # session there instead of failing a load every turn.
    binding_value: JsonObject = {
        "harness_id": str(harness_id),
        "host_id": str(host_id),
        "session_id": session_id,
        "run_id": str(checkpoint.run_id),
        "run_created_at": created_at.isoformat(),
    }
    same_session = (
        stored.get("session_id") == session_id
        and stored.get("harness_id") == str(harness_id)
        and stored.get("host_id", str(host_id)) == str(host_id)
    )
    binding_value.update(_host_directory_binding(stored, checkpoint, same_session))
    binding_value.update(_instruction_binding(stored, checkpoint, same_session))
    if stored == binding_value:
        return False
    await repository.set_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        binding_value,
    )
    return True


def _host_directory_binding(
    stored: JsonObject, checkpoint: AgentHostRunCheckpoint, same_session: bool
) -> JsonObject:
    # An observation from the leased host, never a filesystem grant or the cwd
    # for sandbox tools. Heartbeats from older hosts omit the field.
    cwd = checkpoint.detail.get("host_cwd")
    if cwd is None and same_session:
        cwd = stored.get("host_cwd")
    if isinstance(cwd, str) and cwd and "\0" not in cwd:
        return {"host_cwd": cwd}
    return {}


def _instruction_binding(
    stored: JsonObject, checkpoint: AgentHostRunCheckpoint, same_session: bool
) -> JsonObject:
    # Opening a session does not prove a prompt landed. Promote only this run's
    # pending digest after a provider event, and never carry delivery from a
    # different session. Other runs' pending instructions remain owed.
    result: JsonObject = {}
    pending = stored.get("pending_instructions")
    delivered = stored.get("instructions_digest") if same_session else None
    promoted = (
        checkpoint.state in _STATES_PROVING_THE_PROMPT_LANDED
        and isinstance(pending, dict)
        and pending.get("run_id") == str(checkpoint.run_id)
        and isinstance(pending.get("digest"), str)
    )
    if promoted:
        delivered = pending["digest"]
    elif pending is not None:
        result["pending_instructions"] = pending
    if isinstance(delivered, str) and delivered:
        result["instructions_digest"] = delivered
    return result


def _binding_is_newer(stored: JsonObject, run_id: UUID, created_at: datetime) -> bool:
    previous_id = stored.get("run_id")
    previous_created_at = stored.get("run_created_at")
    if not isinstance(previous_id, str) or not isinstance(previous_created_at, str):
        return False
    try:
        previous = (datetime.fromisoformat(previous_created_at), UUID(previous_id).int)
        return previous > (created_at, run_id.int)
    except ValueError, TypeError:
        return False


def instructions_digest(system_prompt: str) -> str:
    """A stable fingerprint of the instructions a run is dispatching.

    Taken over the exact string that lands on the run spec, not over the pieces
    it was assembled from, so any change a user can make — agent instructions,
    conversation instructions, the granted-resource brief — moves it.
    """
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


async def record_pending_instructions(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    run_id: UUID,
    digest: str,
) -> None:
    """Note which run is carrying which instructions, pending its delivery.

    A promise, not a record: :func:`remember_provider_session` turns it into one
    when the host reports that it actually prompted.
    """
    repository = ConversationRepository(uow)
    stored = await repository.get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        for_update=True,
    )
    binding = dict(stored) if isinstance(stored, dict) else {}
    binding["pending_instructions"] = {"run_id": str(run_id), "digest": digest}
    await repository.set_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        binding,
    )


async def instructions_already_delivered(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    harness_id: UUID,
    digest: str,
) -> bool:
    """Whether this session has already been told exactly these instructions.

    False for anything uncertain — no binding, another harness, no digest
    recorded, or a digest that has moved. The failure this guards against is
    an agent quietly running without its instructions, so every ambiguous case
    resolves toward sending them again.
    """
    stored = await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
    )
    if not isinstance(stored, dict) or stored.get("harness_id") != str(harness_id):
        return False
    return stored.get("instructions_digest") == digest


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
