"""Streaming helpers for pod-scoped conversations."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
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
    with_keepalive,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.conversations.read import ConversationReads
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.value_objects import AgentRunStartResult
from app.modules.agent.services.realtime import run_ending_payload

logger = get_logger(__name__)


async def start_and_stream_run(
    *,
    channel_service: ChannelServiceDep,
    unit_of_work: Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]],
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    start_run: Callable[[SqlAlchemyUnitOfWork], Awaitable[AgentRunStartResult]],
) -> StreamingResponse:
    """Send, then stream the run the send started or joined.

    ``unit_of_work`` opens a short-lived one: the auth context is built there,
    not in a request-scoped dependency, because a StreamingResponse keeps those
    (and their pooled connection) alive for the whole stream.
    """

    async def send() -> AgentRunStartResult:
        async with unit_of_work() as uow:
            return await start_run(uow)

    async def load_run(agent_run_id: UUID) -> AgentRun:
        async with unit_of_work() as uow:
            return await load_authorized_agent_run(
                ConversationReads.build(uow),
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                user_id=user_id,
                pod_id=pod_id,
            )

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
        result = await send()
    except (AgentNotFoundError, ConversationNotFoundError) as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except BaseException as exc:
        await close_subscription(type(exc), exc, exc.__traceback__)
        raise

    async def event_generator() -> AsyncGenerator[str, None]:
        # First frame, before anything the run publishes: a message sent while a
        # run was already working joins that run, and the only route that told
        # anyone so was `agent.conversation.message.append`. On the chat path a
        # person watched an apparently unanswered message instead
        # (`PS-AGENT-015`). `AdmitMessage.admit` already decided this; it was
        # only ever thrown away here.
        yield encode_stream_chunk(
            event_type="status",
            data={
                "started_new_run": result.started_new_run,
                "deduplicated": result.deduplicated,
            },
            agent_run_id=result.agent_run_id,
        )
        if result.deduplicated:
            # A re-send joins the run its first copy started, which may have
            # ended -- and published its ending -- before this subscription
            # existed. Waiting for that ending again never ends: the stream sat
            # on keepalives forever. Read once the subscription is open, so a
            # run that ends after this read is still heard below.
            ending = terminal_run_chunk(await load_run(result.agent_run_id))
            if ending is not None:
                yield ending
                await close_subscription()
                return
        try:

            async def ended() -> str | None:
                return terminal_run_chunk(await load_run(result.agent_run_id))

            async for chunk in with_keepalive(
                iter_subscription(iterator, result.agent_run_id), on_idle=ended
            ):
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
    reads: ConversationReads,
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
    user_id: UUID,
    pod_id: UUID,
) -> AgentRun:
    agent_run = await reads.get_agent_run(
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
        user_id=user_id,
        pod_id=pod_id,
    )
    if agent_run is None:
        raise ConversationNotFoundError("Agent run not found")
    return agent_run


def terminal_run_chunk(agent_run: AgentRun) -> str | None:
    ending = run_ending_payload(agent_run)
    if ending is None:
        return None
    return encode_stream_chunk(
        event_type=str(ending["type"]),
        data=ending["data"],
        agent_run_id=agent_run.id,
    )
