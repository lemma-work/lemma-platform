"""A person deciding a pause in Lemma: the web's approval decision endpoint, and
a question answered on a channel's own question line.

``ResolvePause`` does the work; this is what authorizes the person against the
conversation first, and what retires the cards a chat platform still shows for
the same pause once the decision has committed -- answered here, not there.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.pauses.resolve import ResolvePause
from app.modules.agent.domain.approvals import ApprovalResolution
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.conversation_access import authorized_conversation


async def _retire_platform_cards(conversation_id: UUID, approval_id: str) -> None:
    # Deferred like `agent_service`'s reach into the surfaces: the contract
    # loads the platforms, and only a decision in the web app needs them.
    from app.modules.agent_surfaces.contracts.egress import (
        retire_cards_answered_in_lemma,
    )

    await retire_cards_answered_in_lemma(
        conversation_id=conversation_id, tool_call_id=approval_id
    )


class DecidePause:
    """Authorizes the person, then records their decision and resumes the run."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        resolve: ResolvePause,
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.agent_repository = agent_repository
        self.resolve = resolve

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> DecidePause:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            resolve=ResolvePause.build(uow),
        )

    async def decide(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
        user_id: UUID,
        pod_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> ApprovalResolution:
        """Record the user's decision and resume the paused agent run.

        ``ask_user`` / ``request_approval`` end their run when called
        (conversation -> WAITING) instead of blocking. This records the decision
        durably, then synthesizes the tool's return (the answers, or the
        approved tool's result run as the user, or a denial) and starts a fresh
        run that replays it from history so the agent continues where it left
        off.
        """
        conversation = await authorized_conversation(
            self.conversations,
            self.agent_repository,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_EXECUTE,
        )
        resolution = await self.resolve.resolve(
            conversation=conversation,
            approval_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            decision=decision,
            response=response,
            agent_name=agent_name,
        )
        # Answered here, not on the platform: the cards posted there for this
        # pause are retired once the decision has committed. A tap or a typed
        # reply retires its own, which is why this is on Lemma's entry point and
        # not on the resolution they share.
        self.uow.after_commit(
            lambda: _retire_platform_cards(conversation.id, approval_id)
        )
        return resolution
