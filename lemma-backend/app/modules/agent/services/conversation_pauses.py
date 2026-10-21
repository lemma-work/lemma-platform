"""The pause half of :class:`ConversationService`: answering and reading pauses.

``ask_user``, ``request_approval`` and ``browser_sign_in`` end their run when
called and leave the conversation WAITING. These are the entry points that read
what a conversation is waiting on and record a person's answer to it. They live
on the service because ``agent_surfaces`` reaches them off one import of it; the
work itself is :class:`ApprovalCoordinator`'s.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.approvals import ApprovalResolution
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pausing_tools import PausingKind, PendingPause
from app.modules.agent.domain.ports import AgentRepository, ConversationRepository
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_access import authorized_conversation
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator
from app.modules.agent.services.pause_resume import PAUSING_TOOL_NAMES


async def _retire_platform_cards(conversation_id: UUID, approval_id: str) -> None:
    # Deferred like `agent_service`'s reach into the surfaces: the contract
    # loads the platforms, and only a decision in the web app needs them.
    from app.modules.agent_surfaces.contracts.egress import (
        retire_cards_answered_in_lemma,
    )

    await retire_cards_answered_in_lemma(
        conversation_id=conversation_id, tool_call_id=approval_id
    )


class ConversationPauses:
    """Reads and answers a conversation's pauses; mixed into ConversationService."""

    uow: SqlAlchemyUnitOfWork
    conversation_repository: ConversationRepository
    agent_repository: AgentRepository
    approvals: ApprovalCoordinator

    async def resolve_user_approval(
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

        ``ask_user`` / ``request_approval`` end their run when called (conversation
        -> WAITING) instead of blocking. This records the decision durably, then
        synthesizes the tool's return (the answers, or the approved tool's result
        run as the user, or a denial) and starts a fresh run that replays it from
        history so the agent continues where it left off.

        This is the HTTP entry point: it authorizes the caller, then delegates to
        :meth:`resolve_user_approval_internal`. Surface ingress (which has already
        authorized the external user against the conversation owner) calls the
        internal method directly with the loaded conversation.
        """
        conversation = await authorized_conversation(
            self.conversation_repository,
            self.agent_repository,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            action=Permissions.AGENT_EXECUTE,
        )
        resolution = await self.resolve_user_approval_internal(
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
        # reply retires its own, which is why this is on the web entry point
        # and not on the internal one they share.
        self.uow.after_commit(
            lambda: _retire_platform_cards(conversation.id, approval_id)
        )
        return resolution

    async def resolve_user_approval_internal(
        self,
        *,
        conversation: Conversation,
        approval_id: str,
        user_id: UUID,
        pod_id: UUID,
        decision: AgentRunApprovalDecision,
        response: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> ApprovalResolution:
        """Resume a paused run for an already-authorized + loaded conversation.

        Kept on the service because `agent_surfaces` reaches this and
        `get_pending_user_interaction` off one import of this class; see
        `ApprovalCoordinator` for what it does.
        """
        return await self.approvals.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=approval_id,
            user_id=user_id,
            pod_id=pod_id,
            decision=decision,
            response=response,
            agent_name=agent_name,
        )

    async def get_pending_ask_user(
        self,
        *,
        conversation_id: UUID,
    ) -> PendingPause | None:
        """Oldest unresolved ``ask_user`` pause for a conversation, or ``None``.

        Used by surface ingress to render the questions on the surface (from a
        WAITING event) and to route a typed reply back into the run as the
        answer.
        """
        return await self.approvals.oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=(PausingKind.ASK_USER.value,),
        )

    async def get_pending_approval(
        self,
        *,
        conversation_id: UUID,
    ) -> PendingPause | None:
        """Oldest unresolved ``request_approval`` pause, or ``None``.

        The approval counterpart of :meth:`get_pending_ask_user`, and it has to
        be its own lookup rather than a filter applied to
        :meth:`get_pending_user_interaction`. That one answers "what is this
        conversation waiting on", of *any* pausing kind, which is right for
        routing a typed reply back — a person answering in words answers
        whatever is pending. It is wrong for rendering, because a conversation
        can hold more than one unresolved pause at a time: an `ask_user` nobody
        ever tapped stays unresolved forever, and being older it is the one
        returned. The approval card then had nothing to render and the run sat
        WAITING with nobody told.
        """
        return await self.approvals.oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=(PausingKind.REQUEST_APPROVAL.value,),
        )

    async def get_pending_sign_in(
        self,
        *,
        conversation_id: UUID,
    ) -> PendingPause | None:
        """Oldest unresolved ``browser_sign_in`` pause, or ``None``.

        Its own lookup for the same reason :meth:`get_pending_approval` is: a
        conversation can hold several unresolved pauses, and the oldest of *any*
        kind is the wrong one to render a sign-in link for.

        This is what replaced a table. A sign-in used to write a row carrying
        the origin, the reason and a status -- all three of which are already
        here: the first two in the paused call's arguments, and the third in
        whether this returns anything at all.
        """
        return await self.approvals.oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=(PausingKind.BROWSER_SIGN_IN.value,),
        )

    async def get_pending_user_interaction(
        self,
        *,
        conversation_id: UUID,
    ) -> PendingPause | None:
        """Oldest unresolved pausing tool call (``ask_user`` or
        ``request_approval``) for a conversation, or ``None``.

        Used by surface ingress to route a typed reply back into the paused run.
        """
        return await self.approvals.oldest_unresolved_pause(
            conversation_id=conversation_id,
            tool_names=PAUSING_TOOL_NAMES,
        )
