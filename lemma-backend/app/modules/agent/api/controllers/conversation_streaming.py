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

    async def event_generator() -> AsyncGenerator[str, None]:
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
            yield encode_stream_chunk(
                event_type="error",
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
    conversation = await service.get_conversation(
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
