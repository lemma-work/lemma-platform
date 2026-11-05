"""``agent.pause_expired``: a paused question's time ran out; the run is told.

Subscribed to channels' ``ChannelQuestionClosed`` for an agent's paused tool
call (``tool:``) that closed EXPIRED. The pause is resolved as nobody
answering it -- a DENY, never an approval -- and the run resumes on that
(``services.pause_expiry``). A JOB, not FAST: resolving a pause starts the
resume run, which builds usage services and publishes live frames. Safe to run
twice: the recorded decision is the lock, and a second resolve adopts it.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    Exponential,
    JobContext,
    PermanentFailure,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.events.work_kinds import AGENT_PAUSE_EXPIRED, pause_expired_key
from app.modules.agent.services.pause_expiry import expire_pause
from app.modules.channels.contracts import QuestionAsker, QuestionState
from app.modules.channels.contracts.facts import ChannelQuestionClosed

PAUSE_EXPIRED_RETRY = RetryPolicy(
    max_attempts=5,
    max_lease_losses=3,
    backoff=Exponential(
        base=timedelta(seconds=2), cap=timedelta(minutes=2), jitter=0.2
    ),
)


class PauseExpiredPayload(BaseModel):
    """The fields of ``ChannelQuestionClosed`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    pod_id: UUID
    asker_conversation_id: UUID | None = None
    asker_key: str | None = None


def expires_a_pause(fact: ChannelQuestionClosed) -> bool:
    return fact.state is QuestionState.EXPIRED and fact.asker is QuestionAsker.TOOL


async def pause_expired(ctx: JobContext, payload: PauseExpiredPayload) -> None:
    ctx.raise_if_stopping()
    try:
        if payload.asker_conversation_id is None or payload.asker_key is None:
            # The conversation is gone: nothing paused is left to resolve.
            return
        await expire_pause(
            SessionUnitOfWorkFactory(async_session_maker),
            pod_id=payload.pod_id,
            conversation_id=payload.asker_conversation_id,
            tool_call_id=payload.asker_key,
        )
    except DomainError as refused:
        # Not something a retry changes: the conversation refused the
        # resolution outright. The question stays expired on its line.
        raise PermanentFailure(refused.message) from refused


def register_pause_expiry_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_PAUSE_EXPIRED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=PauseExpiredPayload,
        retry=PAUSE_EXPIRED_RETRY,
    )(pause_expired)


register_pause_expiry_work(default_registry)
facts.subscribe(
    ChannelQuestionClosed,
    kind=AGENT_PAUSE_EXPIRED,
    cls=WorkClass.EVENTS,
    when=expires_a_pause,
    dedup=lambda fact: pause_expired_key(fact.message_id),
)


__all__ = [
    "PauseExpiredPayload",
    "expires_a_pause",
    "pause_expired",
    "register_pause_expiry_work",
]
