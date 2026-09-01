"""Who may touch a conversation, and which agent answers on it.

Both entry points into a run need these two questions settled the same way: the
request side (`ConversationService`) before it accepts a message, and the worker
side (`AgentRunnerService`) before it executes one. They each carried a copy,
and the copies had already drifted — the runner's checked the agent *name* and
the service's checked the agent *id*, so the same conversation could be reachable
through one and not the other.

Both checks are supersets now, with the extra condition optional. A caller that
does not pass it gets exactly what it had.

The permission checks below joined them for the same reason: they were private
methods on `ConversationService`, and every collaborator split out of that class
needed them, which is the definition of something that belongs one level down
rather than inside.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.current import get_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.ports import AgentRepository, ConversationRepository

POD_ASSISTANT_AGENT_ID = DEFAULT_POD_AGENT_ID


def validate_conversation_access(
    conversation: Conversation | None,
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None = None,
) -> None:
    """Raise `ConversationNotFoundError` unless this caller may reach it.

    Not-found rather than forbidden on purpose: a conversation in someone else's
    pod should not be distinguishable from one that does not exist.

    Reachable by the person who opened it, or by anyone since added to it. The
    two clauses are not redundant. Membership is only populated on an entity
    somebody asked for it on, so an owner check that depended on the list would
    lock people out of their own conversations wherever it was not loaded; and
    the owner is not removable, so "opened it" is a standing claim rather than a
    transitional allowance. What changes in a later step is which of the two is
    load-bearing: once a run acts as its sender rather than as the conversation,
    `user_id` is provenance and the membership row backfilled for it is the
    access record.
    """
    if conversation is None:
        raise ConversationNotFoundError()
    if conversation.user_id != user_id and not conversation.has_participant(user_id):
        raise ConversationNotFoundError()
    if conversation.pod_id != pod_id:
        raise ConversationNotFoundError()
    if (
        agent_id is not None
        and conversation.agent_id != agent_id
        and not conversation.has_agent_participant(agent_id)
    ):
        # A conversation's own `agent_id` is the one that answers by default.
        # An agent added to the conversation may also be addressed in it, which
        # is what an `@mention` does -- so being present is as good a claim as
        # being the default, and nothing else is.
        raise ConversationNotFoundError()


async def resolve_agent(
    conversation: Conversation,
    *,
    user_id: UUID,
    agent_repository: object,
    agent_name: str | None = None,
) -> Agent:
    """The agent answering on this conversation, real or the pod assistant.

    A conversation with no `agent_id` is answered by the pod's default
    assistant, which has no row of its own — it is synthesised here so every
    caller downstream can treat it like any other agent.
    """
    if conversation.agent_id is None:
        # Lazy import: the registry imports the subagents toolset, which imports
        # the conversation service — importing it at module load would cycle.
        from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

        return Agent(
            id=POD_ASSISTANT_AGENT_ID,
            pod_id=conversation.pod_id,
            user_id=user_id,
            name=DEFAULT_POD_AGENT_NAME,
            instruction="",
            agent_runtime=conversation.agent_runtime,
            toolsets=list(POD_DEFAULT_AGENT_TOOLSETS),
        )

    agent = await agent_repository.get(conversation.agent_id)  # type: ignore[attr-defined]
    if agent is None:
        raise AgentNotFoundError(str(conversation.agent_id))
    if agent_name is not None and agent.name != agent_name:
        raise AgentNotFoundError(agent_name)
    return agent


async def resolve_run_agent(
    agent_run: object,
    conversation: Conversation,
    *,
    user_id: UUID,
    agent_repository: object,
) -> Agent:
    """The agent that answers one run.

    The conversation names a default. A run may name a different one, because
    an `@mention` addresses an agent for a single turn -- and the run is where
    that decision was recorded, so the run is what has to be read back.

    `resolve_agent` cannot answer this: given a name it *asserts* the
    conversation's own agent matches, which is the right check for "am I
    talking to who I think" and the wrong one for "who is answering this turn".
    """
    run_agent_id = getattr(agent_run, "agent_id", None)
    if run_agent_id is not None and run_agent_id != conversation.agent_id:
        agent = await agent_repository.get(run_agent_id)  # type: ignore[attr-defined]
        if agent is None:
            raise AgentNotFoundError(str(run_agent_id))
        return agent
    return await resolve_agent(
        conversation,
        user_id=user_id,
        agent_repository=agent_repository,
    )


async def resolve_agent_for_path(
    agent_repository: AgentRepository,
    *,
    pod_id: UUID,
    agent_name: str,
) -> Agent:
    agent = await agent_repository.get_by_pod_and_name(
        pod_id=pod_id,
        name=agent_name,
    )
    if agent is None:
        raise AgentNotFoundError(agent_name)
    return agent


async def resolve_expected_agent_id(
    agent_repository: AgentRepository,
    *,
    pod_id: UUID,
    agent_name: str | None,
) -> UUID | None:
    if agent_name is None:
        return None
    agent = await resolve_agent_for_path(
        agent_repository,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    return agent.id


async def authorized_conversation(
    conversation_repository: ConversationRepository,
    agent_repository: AgentRepository,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
    action: str,
) -> Conversation:
    agent_id = await resolve_expected_agent_id(
        agent_repository,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    conversation = await conversation_repository.get_conversation(conversation_id)
    validate_conversation_access(
        conversation,
        user_id=user_id,
        pod_id=pod_id,
        agent_id=agent_id,
    )
    await require_agent_action(
        user_id=user_id,
        pod_id=pod_id,
        agent_id=conversation.agent_id,
        action=action,
    )
    return conversation


async def require_agent_action(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> None:
    await require_agent_actions(
        user_id=user_id,
        pod_id=pod_id,
        agent_id=agent_id,
        actions=(action,),
    )


async def require_agent_actions(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    actions: Sequence[str],
) -> None:
    _ = user_id
    ctx = get_current_context()
    if ctx is None:
        raise RuntimeError("Context is required for conversation authorization")
    resource = ResourceRef(
        resource_type=ResourceType.AGENT if agent_id is not None else ResourceType.POD,
        resource_id=agent_id or pod_id,
        pod_id=pod_id,
    )
    await ctx.require_all([(action, resource) for action in actions])
