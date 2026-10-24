"""``workspace.end_conversation_tokens``: a conversation that ends takes its
sandbox tokens with it.

Subscribed to the agent module's ``AgentRunFinished``. A workspace sandbox
session's token carries its conversation as its delegation session and a
lifetime of its own (``WorkspaceSandboxService.get_session``); ending that
session refuses every such token minted before the end, whichever process still
holds one -- a background command, or another agent's session in the same
sandbox that lifted it.

A run that ends on a pause (``WAITING``) is not the conversation ending: an
approval or an answer resumes it, and its commands are still running. The run
that ends the pause emits its own fact. A conversation that resumes later mints
new tokens, which an earlier end does not reach.

JOB, because the effect is in Redis rather than the main database.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import BaseModel

from app.core.authorization.delegation_revocation import end_delegation_session
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished

WORKSPACE_END_CONVERSATION_TOKENS = "workspace.end_conversation_tokens"

#: A conversation in one of these has ended, as far as its tokens are told.
_ENDED = frozenset({"COMPLETED", "FAILED", "STOPPED"})


class ConversationEndedPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    run_id: UUID
    conversation_id: UUID
    conversation_status: str


def conversation_ended(fact: AgentRunFinished) -> bool:
    return fact.conversation_status in _ENDED


def conversation_tokens_handler(
    end: Callable[..., Awaitable[None]] = end_delegation_session,
) -> Callable[[JobContext, ConversationEndedPayload], Awaitable[None]]:
    """The handler, over the store that records a session's end."""

    async def end_conversation_tokens(
        ctx: JobContext, payload: ConversationEndedPayload
    ) -> None:
        # When the finishing transaction wrote this item, not when it runs: a
        # retry that recorded "now" would also refuse a run started since.
        await end(
            session_id=str(payload.conversation_id),
            ended_at=ctx.item.created_at.timestamp(),
        )

    return end_conversation_tokens


def register_conversation_tokens_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=WORKSPACE_END_CONVERSATION_TOKENS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=ConversationEndedPayload,
    )(conversation_tokens_handler())


register_conversation_tokens_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=WORKSPACE_END_CONVERSATION_TOKENS,
    cls=WorkClass.EVENTS,
    when=conversation_ended,
    dedup=lambda fact: f"workspace-conversation-tokens:{fact.run_id}",
)


__all__ = [
    "WORKSPACE_END_CONVERSATION_TOKENS",
    "conversation_ended",
    "conversation_tokens_handler",
]
