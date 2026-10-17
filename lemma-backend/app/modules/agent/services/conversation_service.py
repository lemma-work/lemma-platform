"""Conversation service for unified agent chats."""

from __future__ import annotations

from functools import partial

from uuid import UUID


from app.modules.agent.services.conversation_approvals import (
    ApprovalCoordinator,
)
from app.modules.agent.services.conversation_queries import ConversationQueries
from app.modules.agent.services.conversation_pauses import ConversationPauses
from app.modules.agent.services.conversation_turns import TurnCoordinator
from app.modules.agent.services.conversation_retry_service import RetryCoordinator
from app.modules.agent.services.send_window import SendWindow, window_key
from app.modules.agent.domain.queued_messages import CLIENT_MESSAGE_ID
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)
from app.modules.agent.domain.server_metadata import with_server_keys_kept
from app.modules.agent.domain.sentinels import UNSET, UnsetType
from app.core.authorization.delegation import POD_DEFAULT_AGENT_SELECTOR
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.services.conversation_access import (
    resolve_expected_agent_id,
    require_agent_action,
    require_agent_actions,
    resolve_agent_for_path,
    validate_conversation_access,
)
from app.modules.agent.domain.conversation_titles import (
    normalize_conversation_title,
)
from app.modules.agent.domain.entities import (
    Conversation,
)
from app.modules.agent.domain.errors import (
    ConversationNotFoundError,
)
from app.modules.agent.domain.ports import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRuntimeConfig,
    ConversationType,
)
from app.modules.agent.domain.workspace_location import (
    apply_location_metadata,
)
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.agent.services.runtime_availability import (
    TurnRuntimeCheck,
    ensure_turn_runtime_available,
)
from app.modules.usage.contracts.execution import UsageService
from app.modules.agent.services.pause_resume import PauseResume


class ConversationService(ConversationPauses):
    """Application service for conversation storage and run coordination."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: ConversationRepository,
        agent_repository: AgentRepository,
        usage_service: UsageService | None = None,
        turn_runtime_check: TurnRuntimeCheck = ensure_turn_runtime_available,
    ):
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.usage_service = usage_service
        self.send_window = SendWindow(getattr(uow, "session", None))
        self.resume_returns = ResumeToolReturnBuilder(uow, agent_repository)
        self.pauses = PauseResume(
            uow, conversation_repository, agent_repository, usage_service
        )
        self.approvals = ApprovalCoordinator(
            uow, conversation_repository, self.resume_returns, self.pauses
        )
        self.queries = ConversationQueries(
            uow, conversation_repository, agent_repository
        )
        self.turns = TurnCoordinator(
            uow,
            conversation_repository,
            agent_repository,
            self.approvals,
            self.pauses,
            usage_service,
            runtime_check=turn_runtime_check,
        )
        self.retries = RetryCoordinator(
            uow, conversation_repository, agent_repository, self.turns
        )

    async def create_conversation(
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
    ) -> Conversation:
        """A new conversation in the pod.

        ``contact_id`` makes it a contact's (``domain/outsiders``): only the
        server's own openers pass it -- the surface that met the contact -- and
        no request a client sends reaches it.
        """
        organization_id = await self._get_pod_organization_id(pod_id)
        # A parent is the caller's own conversation in this pod, or it does not
        # exist: the child inherits its working directory and repository, and
        # is listed under it, so a foreign id would read another person's
        # workspace and file the child where its owner cannot see it.
        parent = (
            validate_conversation_access(
                await self.conversation_repository.get_conversation(parent_id),
                user_id=user_id,
                pod_id=pod_id,
            )
            if parent_id is not None
            else None
        )
        agent_name = _named_agent(agent_name)
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
            await self._require_execute(pod_id=pod_id, user_id=user_id, agent=agent)
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
        )
        await self._apply_inherited_cwd(conversation, parent=parent)
        return await self.conversation_repository.create_conversation(conversation)

    async def authorize_opening(
        self, *, pod_id: UUID, agent_name: str | None, user_id: UUID
    ) -> None:
        """Refuse, as ``create_conversation`` would, a person who may not run
        this agent.

        Checking releases the pooled connection -- it commits -- so a caller
        holding a transaction-scoped lock around opening checks here first,
        then opens with ``require_execute_grant=False``.
        """
        agent_name = _named_agent(agent_name)
        agent = (
            await resolve_agent_for_path(
                self.agent_repository, pod_id=pod_id, agent_name=agent_name
            )
            if agent_name is not None
            else None
        )
        await self._require_execute(pod_id=pod_id, user_id=user_id, agent=agent)

    async def _require_execute(self, *, pod_id: UUID, user_id: UUID, agent) -> None:
        # Dispatching a *named* agent checks agent.execute and agent.read
        # together. agent.execute implies agent.read (IMPLIED_PERMISSIONS), so
        # an execute-only grant satisfies both; checking read here anyway warms
        # the decision cache for the run-load that follows and keeps the error
        # complete for callers missing everything. The default pod agent
        # (agent is None) needs only execute.
        actions = [Permissions.AGENT_EXECUTE]
        if agent is not None:
            actions.append(Permissions.AGENT_READ)
        await require_agent_actions(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=agent.id if agent else None,
            actions=actions,
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
        ``workspace_location.apply_location_metadata``, which is also what reads
        the result back. Creation hands over the parent it already read to
        check the caller may reach it; otherwise it is fetched lazily, because
        most conversations settle their location without ever needing one.
        """

        async def _parent() -> Conversation | None:
            if parent is not None or parent_id is None:
                return parent
            return await self.conversation_repository.get_conversation(parent_id)

        await apply_location_metadata(conversation, fetch_parent=_parent)

    async def update_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        title: str | None | UnsetType = UNSET,
        instructions: str | None | UnsetType = UNSET,
        agent_runtime: AgentRuntimeConfig | None | UnsetType = UNSET,
        metadata: dict[str, object] | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
    ) -> Conversation:
        agent_name = _named_agent(agent_name)
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        if conversation is None:
            raise ConversationNotFoundError()
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )

        if not isinstance(title, UnsetType):
            # A blank title normalizes to None, which is not "no change" -- it
            # is the conversation asking to be auto-titled again, since
            # `generate_title_if_absent` fires on a null title.
            conversation.title = normalize_conversation_title(title)
        if not isinstance(instructions, UnsetType):
            conversation.instructions = instructions
        if not isinstance(agent_runtime, UnsetType):
            conversation.agent_runtime = agent_runtime
        if not isinstance(metadata, UnsetType):
            conversation.metadata = with_server_keys_kept(
                conversation.metadata, metadata
            )
        if not isinstance(is_archived, UnsetType):
            conversation.is_archived = is_archived

        return await self.conversation_repository.update_conversation(conversation)

    async def add_user_message_and_start_run(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        agent_name: str | None = None,
        message_metadata: dict[str, object] | None = None,
        require_execute_grant: bool = True,
        typed_in_lemma: bool = False,
        client_message_id: str | None = None,
    ) -> AgentRunStartResult:
        """Store a message and start, or join, the run that answers it.

        A person's own send and a new conversation's first message go through
        `send_window`; a chat platform's next line in a thread does not (people
        say "yes" twice, and its delivery id already catches redeliveries).
        """
        # Once, here, so creating the conversation and checking it against the
        # expected agent read the same answer.
        agent_name = _named_agent(agent_name)
        start = partial(
            self._add_user_message_and_start_run,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=message_metadata,
            require_execute_grant=require_execute_grant,
            typed_in_lemma=typed_in_lemma,
            client_message_id=client_message_id,
        )
        if not typed_in_lemma and conversation_id is not None:
            return await start()
        key = window_key(
            user_id=user_id,
            content=content,
            conversation_id=conversation_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return await self.send_window.start_send_once(key, start)

    async def _add_user_message_and_start_run(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        agent_name: str | None,
        message_metadata: dict[str, object] | None,
        require_execute_grant: bool,
        typed_in_lemma: bool,
        client_message_id: str | None,
    ) -> AgentRunStartResult:
        conversation = await self._get_or_create_conversation_for_message(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            require_grant=require_execute_grant,
        )

        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # _get_or_create_conversation_for_message already returned a fully loaded,
        # access-checked conversation — no need to re-fetch it.
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_execute_grant is False only for self-spawn (SubAgentService).
        if require_execute_grant:
            await require_agent_action(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                action=Permissions.AGENT_EXECUTE,
            )
        return await self.turns.start(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            content=content,
            agent_name=agent_name,
            message_metadata=message_metadata,
            typed_in_lemma=typed_in_lemma,
            client_message_id=client_message_id,
        )

    async def send_from_lemma(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        message_metadata: dict[str, object] | None = None,
        client_message_id: str | None = None,
    ) -> AgentRunStartResult:
        """A message a person typed in Lemma's own composer.

        The only caller that may say so. Typed into a group's conversation it
        is marked as written in Lemma (``domain/private_notes``), which changes
        who the model takes the writer to be -- so a chat platform's line, a
        sub-agent's or a reply delivery's must never arrive this way. A client
        id sent only in the metadata, as the web app first did, is used too.
        """
        carried = (message_metadata or {}).get(CLIENT_MESSAGE_ID)
        return await self.add_user_message_and_start_run(
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            pod_id=pod_id,
            message_metadata=message_metadata,
            typed_in_lemma=True,
            client_message_id=client_message_id
            or (carried if isinstance(carried, str) and carried else None),
        )

    async def _get_or_create_conversation_for_message(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        require_grant: bool = True,
    ) -> Conversation:
        if conversation_id is not None:
            return await self.queries.get_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                pod_id=pod_id,
                agent_name=agent_name,
                require_read_grant=require_grant,
            )
        return await self.create_conversation(
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
        )

    async def _get_pod_organization_id(self, pod_id: UUID) -> UUID | None:
        return await pod_organization_id(self.uow, pod_id)

    async def stop_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        """Stop the run in flight, closing whatever pause it was sitting on."""
        return await self.turns.stop_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )

    async def withdraw_queued_message(
        self, *, conversation_id: UUID, message_id: UUID, user_id: UUID, pod_id: UUID
    ) -> None:
        """Take back a message the agent has not seen yet; see `TurnCoordinator`."""
        await self.turns.withdraw_queued_message(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            pod_id=pod_id,
        )

    async def retry_failed_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> AgentRunStartResult:
        """Run the failed latest turn again; see `RetryCoordinator`."""
        return await self.retries.retry_failed_run(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )


def _named_agent(agent_name: str | None) -> str | None:
    """The agent a conversation names, or None for the pod's own assistant.

    Blank means "no agent named", and so does the `POD_DEFAULT` selector the
    list endpoint already accepts, which is not any agent's name. Treating `""`
    as a name looked up an agent called nothing and answered 404 -- which every
    CLI chat without `--agent` sent. (`pod_default`, the assistant's stored
    name, already resolves to it by name.)
    """
    if agent_name is None:
        return None
    name = agent_name.strip()
    if not name or name == POD_DEFAULT_AGENT_SELECTOR:
        return None
    return name
