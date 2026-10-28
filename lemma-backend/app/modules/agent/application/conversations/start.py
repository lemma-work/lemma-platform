"""Opening a conversation in a pod.

Every opener comes through here -- the web's create, a first message, a
sub-agent, a pod ask, a channel, a call, a surface thread -- so the rules about
what a new conversation may claim are decided once: whose agent it runs (and
whether the person may run it), what kind it is, where it works, and which of
the server-only facts (a contact, an origin, a parent run, an ask, a call) it
carries. None of those last ones is reachable from a request a client sends.

Writes ride the caller's transaction, as opening always has: the request's unit
of work commits it, or the caller's own does.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.calls import CallMark
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_ask_record import PodAsk
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    ConversationOrigin,
    ConversationType,
)
from app.modules.agent.domain.workspace_location import apply_location
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.conversation_access import (
    named_agent,
    require_execute,
    resolve_agent_for_path,
    validate_conversation_access,
)
from app.modules.pod.contracts.agent_access import pod_organization_id


async def authorize_opening(
    agent_repository: AgentRepository,
    *,
    pod_id: UUID,
    agent_name: str | None,
    user_id: UUID,
) -> None:
    """Refuse, as ``StartConversation`` would, a person who may not run this agent.

    Checking releases the pooled connection -- it commits -- so a caller
    holding a transaction-scoped lock around opening checks here first,
    then opens with ``require_execute_grant=False``.
    """
    agent_name = named_agent(agent_name)
    agent = (
        await resolve_agent_for_path(
            agent_repository, pod_id=pod_id, agent_name=agent_name
        )
        if agent_name is not None
        else None
    )
    await require_execute(pod_id=pod_id, user_id=user_id, agent=agent)


class StartConversation:
    """Opens a new conversation, checked and placed."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, agent_repository: AgentRepository
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.agent_repository = agent_repository

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> StartConversation:
        return cls(uow, agent_repository=repositories.AgentRepository(uow))

    async def start(
        self,
        *,
        pod_id: UUID,
        agent_name: str | None,
        user_id: UUID,
        title: str | None = None,
        instructions: str | None = None,
        agent_runtime: AgentRuntimeConfig | None = None,
        parent_id: UUID | None = None,
        type: ConversationType = ConversationType.CHAT,
        metadata: dict[str, object] | None = None,
        require_execute_grant: bool = True,
        contact_id: UUID | None = None,
        channel_id: UUID | None = None,
        origin: ConversationOrigin | None = None,
        parent_run_id: UUID | None = None,
        spawn_key: str | None = None,
        reports_to_parent: bool = False,
        ask: PodAsk | None = None,
        call: CallMark | None = None,
        started_on: str | None = None,
    ) -> Conversation:
        """A new conversation in the pod.

        ``contact_id`` makes it a contact's (``domain/outsiders``): only the
        server's own openers pass it -- the surface that met the contact -- and
        no request a client sends reaches it. Neither does the typed state
        after it: ``origin`` (a person's chat, or a project, when no opener
        says otherwise), a sub-agent's link to its parent, the ask another pod
        opened it with, the call it belongs to. ``started_on`` is the platform
        (or ``web_widget``) a surface opened it on, for the started count.
        """
        organization_id = await pod_organization_id(self.uow, pod_id)
        # A parent is the caller's own conversation in this pod, or it does not
        # exist: the child inherits its working directory and repository, and
        # is listed under it, so a foreign id would read another person's
        # workspace and file the child where its owner cannot see it.
        parent = (
            validate_conversation_access(
                await self.conversations.get_conversation(parent_id),
                user_id=user_id,
                pod_id=pod_id,
            )
            if parent_id is not None
            else None
        )
        agent_name = named_agent(agent_name)
        agent = (
            await resolve_agent_for_path(
                self.agent_repository, pod_id=pod_id, agent_name=agent_name
            )
            if agent_name is not None
            else None
        )
        # require_execute_grant is False for self-spawn (an agent launching
        # another instance of itself) — see SubAgentService.spawn. An agent has no
        # agent.execute grant on itself, but running another copy of the agent the
        # user is already running is no privilege escalation. And for a caller
        # that checked first (`authorize_opening`) so that nothing in here
        # releases its connection.
        if require_execute_grant:
            await require_execute(pod_id=pod_id, user_id=user_id, agent=agent)
        # A PROJECT is an explicit user choice (a pinned group); never coerce it.
        # Otherwise a structured-output agent implies a TASK conversation.
        if type == ConversationType.PROJECT:
            conversation_type = ConversationType.PROJECT
        elif agent is not None and agent.output_schema:
            conversation_type = ConversationType.TASK
        else:
            conversation_type = type

        conversation = Conversation(
            user_id=user_id,
            pod_id=pod_id,
            contact_id=contact_id,
            channel_id=channel_id,
            organization_id=organization_id,
            agent_id=agent.id if agent else None,
            title=title,
            instructions=instructions,
            agent_runtime=agent_runtime,
            parent_id=parent_id,
            type=conversation_type,
            metadata=dict(metadata) if metadata else {},
            origin=origin
            or (
                ConversationOrigin.PROJECT
                if conversation_type == ConversationType.PROJECT
                else ConversationOrigin.CHAT
            ),
            parent_run_id=parent_run_id,
            spawn_key=spawn_key,
            reports_to_parent=reports_to_parent,
            ask=ask,
            call=call,
        )
        await self._apply_inherited_cwd(conversation, parent=parent)
        return await self.conversations.create_conversation(
            conversation, started_on=started_on
        )

    async def _apply_inherited_cwd(
        self,
        conversation: Conversation,
        *,
        parent_id: UUID | None = None,
        parent: Conversation | None = None,
    ) -> None:
        """Record where this conversation works, once, at creation.

        The rules — inheritance, an explicit cwd, a project repo — all live in
        ``workspace_location.apply_location``, which is also what reads
        the result back. Creation hands over the parent it already read to
        check the caller may reach it; otherwise it is fetched lazily, because
        most conversations settle their location without ever needing one.
        """

        async def _parent() -> Conversation | None:
            if parent is not None or parent_id is None:
                return parent
            return await self.conversations.get_conversation(parent_id)

        await apply_location(conversation, fetch_parent=_parent)
