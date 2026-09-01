"""Streaming helpers for pod-scoped conversations."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from uuid import UUID

import anyio
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from app.core.log.log import get_logger
from app.modules.agent.api.controllers.shared import (
    ChannelServiceDep,
    conversation_channel,
    encode_stream_chunk,
    iter_subscription,
)
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.value_objects import AgentRunStartResult, AgentRunStatus
from app.modules.agent.services.conversation_service import ConversationService

logger = get_logger(__name__)


async def start_and_stream_run(
    *,
    channel_service: ChannelServiceDep,
    conversation_id: UUID,
    start_run: Callable[[], Awaitable[AgentRunStartResult]],
) -> StreamingResponse:
    async def close_subscription(
        exc_type=None,
        exc=None,
        traceback=None,
    ) -> None:
        try:
            with anyio.CancelScope(shield=True):
                await subscription.__aexit__(exc_type, exc, traceback)
        except Exception:
            logger.warning(
                "agent.streaming.subscription_close_failed.degraded", exc_info=True
            )
            return

    subscription = channel_service.subscribe([conversation_channel(conversation_id)])
    iterator = await subscription.__aenter__()
    try:
        result = await start_run()
    except (AgentNotFoundError, ConversationNotFoundError) as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except BaseException as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise

    if result.agent_run_id is None:
        # Nobody is answering. The message is stored and everyone watching has
        # already been sent it; there is no run to follow, so the stream says
        # so once and ends rather than holding a connection open against a
        # subscription that will never see a frame.
        await close_subscription()

        async def unanswered() -> AsyncGenerator[str, None]:
            yield encode_stream_chunk(
                event_type="unanswered",
                data={"conversation_id": str(conversation_id)},
            )

        return StreamingResponse(unanswered(), media_type="text/event-stream")

    async def event_generator() -> AsyncGenerator[str, None]:
        # Who is answering, before a single token. Everything the client shows
        # while a turn is in flight -- the name on the bubble, "batman is
        # typing" -- needs this, and the first frame that would otherwise carry
        # it is the finished message. Without it a live turn is anonymous for
        # its whole duration, which is the entire time anyone is looking at it.
        yield encode_stream_chunk(
            event_type="run_started",
            data={"agent_id": str(result.agent_id) if result.agent_id else None},
            agent_run_id=result.agent_run_id,
        )
        try:
            async for chunk in iter_subscription(iterator, result.agent_run_id):
                yield chunk
        except Exception:
            logger.error(
                "agent.conversation_controller.agent_realtime_subscription.failed",
                conversation_id=str(conversation_id),
                agent_run_id=str(result.agent_run_id),
                exc_info=True,
            )
            # `stream_error`, not `error`. The two say opposite things about
            # the run: `error` is the run failing, and a client that sees one
            # is right to stop and offer a retry. This is the *transport*
            # giving up — the subscription was evicted or Redis dropped it —
            # while the run carries on writing messages nobody is listening
            # for. Sent under the same name, the sentence "Reconnect to
            # continue" reached a client that had just decided the run was
            # over, so the one frame that asks for a reconnect was the one
            # frame that guaranteed there would not be one.
            yield encode_stream_chunk(
                event_type="stream_error",
                data="Realtime stream interrupted. Reconnect to continue.",
                agent_run_id=result.agent_run_id,
            )
        finally:
            await close_subscription()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def load_authorized_agent_run(
    service: ConversationService,
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
    user_id: UUID,
    pod_id: UUID,
) -> AgentRun:
    conversation = await service.queries.get_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
        pod_id=pod_id,
    )
    agent_run = await service.conversation_repository.get_agent_run(agent_run_id)
    if agent_run is None or agent_run.conversation_id != conversation.id:
        raise ConversationNotFoundError("Agent run not found")
    return agent_run


def terminal_run_chunk(agent_run: AgentRun) -> str | None:
    if agent_run.status == AgentRunStatus.FAILED:
        return encode_stream_chunk(
            event_type="error",
            data=agent_run.error or "Agent run failed",
            agent_run_id=agent_run.id,
        )
    if agent_run.status == AgentRunStatus.STOPPED:
        return encode_stream_chunk(
            event_type="stopped",
            data={
                "conversation_id": str(agent_run.conversation_id),
                "status": agent_run.status.value,
            },
            agent_run_id=agent_run.id,
        )
    if agent_run.status == AgentRunStatus.COMPLETED:
        data: dict[str, object] = {
            "conversation_id": str(agent_run.conversation_id),
            "status": agent_run.status.value,
        }
        if agent_run.output_data is not None:
            data["output_data"] = agent_run.output_data
        return encode_stream_chunk(
            event_type="completed",
            data=data,
            agent_run_id=agent_run.id,
        )
    return None
