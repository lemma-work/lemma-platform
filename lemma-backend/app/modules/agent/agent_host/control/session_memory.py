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
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.agent_host.domain.host import (
    AGENT_HOST_SESSION_METADATA_KEY,
    AgentHostHarnessCapabilities,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.db.conversations import Conversations
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
    it resends on every heartbeat, so writing unconditionally meant one
    ``jsonb_set`` per heartbeat per active run to store a value that had not
    moved since the run began.

    The row lock is taken only once a change is in sight. Reading under
    ``FOR UPDATE`` first meant every heartbeat locked the conversation's
    metadata to learn that nothing had changed, contending with message
    appends on exactly the conversations that are busiest. The decision is
    made again under the lock, because a newer run may have bound in between.
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
    conversations = Conversations(uow)
    opened = _OpenedSession(
        session_id=session_id,
        harness_id=str(harness_id),
        host_id=str(host_id),
        created_at=created_at,
    )
    unlocked = await conversations.get_conversation_metadata_key(
        conversation_id, AGENT_HOST_SESSION_METADATA_KEY
    )
    if _rebound(unlocked, checkpoint, opened) is None:
        return False
    stored = await conversations.get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        for_update=True,
    )
    binding_value = _rebound(stored, checkpoint, opened)
    if binding_value is None:
        return False
    await conversations.set_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        binding_value,
    )
    return True


@dataclass(frozen=True, slots=True)
class _OpenedSession:
    """The session a checkpoint reports, with the run's lease that opened it."""

    session_id: str
    harness_id: str
    host_id: str
    created_at: datetime


def _rebound(
    stored: object, checkpoint: AgentHostRunCheckpoint, opened: _OpenedSession
) -> JsonObject | None:
    """The binding this checkpoint calls for, or None when nothing would change.

    None too when a newer run already holds the binding: a replay must not
    take it back.
    """
    stored = stored if isinstance(stored, dict) else {}
    if _binding_is_newer(stored, checkpoint.run_id, opened.created_at):
        return None
    # Stored with the harness that opened it. A Codex rollout id means nothing
    # to Claude Code, so a conversation moved to another harness starts a fresh
    # session there instead of failing a load every turn.
    binding_value: JsonObject = {
        "harness_id": opened.harness_id,
        "host_id": opened.host_id,
        "session_id": opened.session_id,
        "run_id": str(checkpoint.run_id),
        "run_created_at": opened.created_at.isoformat(),
    }
    same_session = (
        stored.get("session_id") == opened.session_id
        and stored.get("harness_id") == opened.harness_id
        and stored.get("host_id", opened.host_id) == opened.host_id
    )
    binding_value.update(_host_directory_binding(stored, checkpoint, same_session))
    binding_value.update(_instruction_binding(stored, checkpoint, same_session))
    binding_value.update(_prompted_binding(stored, checkpoint, same_session))
    return None if stored == binding_value else binding_value


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


def _prompted_binding(
    stored: JsonObject, checkpoint: AgentHostRunCheckpoint, same_session: bool
) -> JsonObject:
    # The newest run whose prompt this session is known to hold, which is not
    # always the run the binding names: that one may have failed before its
    # prompt was sent. Gated as the instructions' promotion is, and never
    # carried into a different session. With it, how far into the conversation
    # that prompt's history ran (`seen_through`), promoted from the run's
    # pending prompt the same way.
    if checkpoint.state in _STATES_PROVING_THE_PROMPT_LANDED:
        return _landed_prompt(stored, str(checkpoint.run_id), same_session)
    kept = ("prompted_run_id", "prompted_through") if same_session else ()
    result: JsonObject = {key: stored[key] for key in kept if key in stored}
    if "pending_prompt" in stored:
        result["pending_prompt"] = stored["pending_prompt"]
    return result


def _landed_prompt(stored: JsonObject, run_id: str, same_session: bool) -> JsonObject:
    result: JsonObject = {"prompted_run_id": run_id}
    pending = stored.get("pending_prompt")
    if isinstance(pending, dict) and pending.get("run_id") == run_id:
        if isinstance(pending.get("seen_through"), int):
            result["prompted_through"] = pending["seen_through"]
        return result
    if pending is not None:
        # Another run's promise, still owed.
        result["pending_prompt"] = pending
    # Every heartbeat of a run that has landed lands again, with its promise
    # already kept: what it recorded stays.
    if same_session and stored.get("prompted_run_id") == run_id:
        through = stored.get("prompted_through")
        if isinstance(through, int):
            result["prompted_through"] = through
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
    seen_through: int | None = None,
) -> None:
    """Note which run is carrying which instructions, pending its delivery.

    ``seen_through`` is the newest message of the history this run's prompt
    was built from, kept pending the same way: what was written in no run
    after it, the session has not been sent.

    A promise, not a record: :func:`remember_provider_session` turns it into one
    when the host reports that it actually prompted.
    """
    conversations = Conversations(uow)
    stored = await conversations.get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
        for_update=True,
    )
    binding = dict(stored) if isinstance(stored, dict) else {}
    binding["pending_instructions"] = {"run_id": str(run_id), "digest": digest}
    if seen_through is not None:
        binding["pending_prompt"] = {
            "run_id": str(run_id),
            "seen_through": seen_through,
        }
    await conversations.set_conversation_metadata_key(
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
    stored = await Conversations(uow).get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
    )
    if not isinstance(stored, dict) or stored.get("harness_id") != str(harness_id):
        return False
    return stored.get("instructions_digest") == digest


@dataclass(frozen=True, slots=True)
class ResumedSession:
    """The provider session a turn continues, and how far into it Lemma knows."""

    session_id: str
    #: The newest run whose prompt the session is known to hold. What it holds
    #: of the conversation runs to that run's start: anything written in no run
    #: since, it has never been sent. None when no prompt is known to have
    #: landed in it, or it was bound before Lemma kept this.
    last_run_id: UUID | None
    #: The newest message of the history that run's prompt was built from:
    #: exact where ``last_run_id`` is only its start. None where unknown.
    seen_through: int | None = None


async def resumed_session(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    harness_id: UUID,
    capabilities: JsonObject,
) -> ResumedSession | None:
    """The provider session this conversation should continue in, if any.

    Absent on a conversation's first turn, when the conversation last spoke to
    a different harness, and for a harness that never advertised
    ``loadSession`` — asking such an agent to load would only cost a round trip
    before it fell back to a new session anyway.
    """
    if not AgentHostHarnessCapabilities.model_validate(capabilities).load_session:
        return None
    stored = await Conversations(uow).get_conversation_metadata_key(
        conversation_id,
        AGENT_HOST_SESSION_METADATA_KEY,
    )
    if not isinstance(stored, dict) or stored.get("harness_id") != str(harness_id):
        return None
    session_id = stored.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    seen_through = stored.get("prompted_through")
    return ResumedSession(
        session_id=session_id,
        last_run_id=_uuid(stored.get("prompted_run_id")),
        seen_through=seen_through if isinstance(seen_through, int) else None,
    )


async def resume_session_id(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    harness_id: UUID,
    capabilities: JsonObject,
) -> str | None:
    """Just the id :func:`resumed_session` names."""
    session = await resumed_session(
        uow,
        conversation_id=conversation_id,
        harness_id=harness_id,
        capabilities=capabilities,
    )
    return session.session_id if session is not None else None


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None
