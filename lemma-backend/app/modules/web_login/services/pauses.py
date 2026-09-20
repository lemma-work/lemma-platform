"""How a sign-in reaches the run it paused.

Three questions, all asked of the `agent` module and none answerable here:
whose conversation this is, what it is waiting to be signed in to, and how an
answer closes that wait.

Protocols with adapters behind them, rather than calls the service reaches for
inside itself, for one reason: a double placed *inside* the subject certifies
the half that was not written. `SignInService` takes these as collaborators, so
a test stands in front of them and a rename of the real thing fails that test
instead of slipping past it.

All three adapters import `agent` inside the function. `agent` imports this module's
contracts, so naming it at the top would close a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class ReadPause(Protocol):
    """What a conversation is waiting to be signed in to, if anything.

    This is the whole of what a sign-in request used to be: a row holding an
    origin, a reason and a status, where the first two are the paused tool
    call's own arguments and the third is whether this returns anything at all.
    """

    async def __call__(
        self, uow: "SqlAlchemyUnitOfWork", conversation_id: UUID
    ) -> object | None: ...


class OwnerOfConversation(Protocol):
    """Whose conversation this is, or ``None`` when there is no such row.

    Asked before the pause is read rather than after, because the pause is the
    thing being protected: its origin and its reason are the person's, and
    resolving it speaks to their agent in their name.
    """

    async def __call__(
        self, uow: "SqlAlchemyUnitOfWork", conversation_id: UUID
    ) -> UUID | None: ...


class ResumePause(Protocol):
    """How an answer closes the wait and hands the outcome to the agent."""

    async def __call__(
        self,
        uow: "SqlAlchemyUnitOfWork",
        *,
        conversation_id: UUID,
        tool_call_id: str,
        user_id: UUID,
        approved: bool,
        response: dict[str, object] | None = None,
    ) -> bool: ...


async def owner_through_contracts(
    uow: "SqlAlchemyUnitOfWork", conversation_id: UUID
) -> UUID | None:
    """The conversation's owner, through `agent`'s own contract."""
    from app.modules.agent.contracts.conversations_for_surfaces import (
        surface_conversation,
    )

    found = await surface_conversation(uow, conversation_id)
    return None if found is None else found.user_id


async def pending_through_contracts(
    uow: "SqlAlchemyUnitOfWork", conversation_id: UUID
) -> object | None:
    """The conversation's unresolved sign-in, through `agent`'s own contract."""
    from app.modules.agent.contracts.conversations_for_surfaces import (
        pending_sign_in,
    )

    return await pending_sign_in(uow, conversation_id)


async def resume_through_approvals(
    uow: "SqlAlchemyUnitOfWork",
    *,
    conversation_id: UUID,
    tool_call_id: str,
    user_id: UUID,
    approved: bool,
    response: dict[str, object] | None = None,
) -> bool:
    """Close a sign-in's pause through the endpoint an approval button uses.

    That path is idempotent -- the decision row is the double-submit lock -- and
    self-healing, which is what makes it safe to call from a retry. It is also
    what makes a second answer harmless: the first writer wins, so a stale tab
    cannot overturn a decision the agent has already been given.

    `response` carries `saved` and `saved_detail`, the way `ask_user` carries
    its answers. They used to live in a table of this feature's own.
    """
    from app.modules.agent.contracts.conversations_for_surfaces import (
        AgentRunApprovalDecision,
        resolve_pending_interaction,
    )

    return await resolve_pending_interaction(
        uow,
        conversation_id=conversation_id,
        approval_id=tool_call_id,
        user_id=user_id,
        decision=(
            AgentRunApprovalDecision.APPROVE_ONCE
            if approved
            else AgentRunApprovalDecision.DENY
        ),
        response=response,
    )


__all__ = [
    "OwnerOfConversation",
    "ReadPause",
    "ResumePause",
    "owner_through_contracts",
    "pending_through_contracts",
    "resume_through_approvals",
]
