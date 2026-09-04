"""Shared helpers for agent API controllers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status

from app.modules.usage.contracts.execution import build_usage_service
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import BadRequestError
from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.realtime import (
    conversation_channel as conversation_channel,
)

ChannelServiceDep = Annotated[RealtimeChannel, Depends(get_channel_service)]
_TERMINAL_STREAM_EVENTS = {"completed", "stopped", "error"}

#: An SSE comment: ignored by every client, visible as traffic to everything in
#: between. Emitted below during silence.
KEEPALIVE_FRAME = ": keepalive\n\n"

#: How long a stream may say nothing before the comment above is sent. Under the
#: 60s idle timeout intermediaries commonly apply, and it has to be: the module
#: publishes nothing while a tool runs (arguments stream as tokens *before* the
#: call, and the next frame is the return), so a shell command or a sub-agent
#: await bounded at 300s is minutes of silence on a healthy connection. A client
#: whose proxy closed it sees a dead socket rather than the `stream_error` frame
#: the code takes such care to distinguish from a failed run.
STREAM_KEEPALIVE_SECONDS = 15.0


def _build_conversation_service(uow) -> ConversationService:
    return ConversationService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_data_service(uow),
        usage_service=build_usage_service(uow),
    )


def _build_conversation_retry_service(uow) -> ConversationRetryService:
    return ConversationRetryService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_data_service(uow),
        usage_service=build_usage_service(uow),
    )


def _parse_metadata_filters(
    *,
    query_params: Iterable[tuple[str, str]],
) -> JsonObject | None:
    filters: JsonObject = {}
    for raw_key, value in query_params:
        if not raw_key.startswith("metadata."):
            continue
        key = raw_key.removeprefix("metadata.").strip()
        if not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Metadata filters must use metadata.<key>=value format.",
            )
        filters[key] = value
    return filters or None


def _parse_message_page_token(page_token: str | None) -> int | None:
    if page_token is None:
        return None
    try:
        value = int(page_token)
    except ValueError as exc:
        raise BadRequestError("Invalid page_token") from exc
    if value < 0:
        raise BadRequestError("Invalid page_token")
    return value


def encode_stream_chunk(
    *,
    event_type: str,
    data: object,
    agent_run_id: UUID | str | None = None,
    kind: str | None = None,
) -> str:
    payload = {
        "type": event_type,
        "data": data,
    }
    if event_type == "token" and kind:
        payload["kind"] = kind
    if event_type != "token":
        payload["agent_run_id"] = str(agent_run_id) if agent_run_id else None
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def iter_subscription(
    iterator,
    agent_run_id: UUID | None,
) -> AsyncGenerator[str, None]:
    async for message in iterator:
        try:
            payload = json.loads(message) if isinstance(message, str) else message
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        # Filter to this run, but only among events that belong to a run. A
        # payload with no `agent_run_id` is conversation-scoped (the generated
        # title is the one in production today) and was being dropped by the
        # same comparison — `None != "<uuid>"` — so it could never reach a
        # client that was streaming, which is every client that would want it.
        payload_run_id = payload.get("agent_run_id")
        if (
            agent_run_id is not None
            and payload_run_id is not None
            and payload_run_id != str(agent_run_id)
        ):
            continue

        event_type = str(payload.get("type", ""))
        yield encode_stream_chunk(
            event_type=event_type,
            data=payload.get("data"),
            agent_run_id=payload_run_id,
            kind=str(payload.get("kind")) if payload.get("kind") else None,
        )
        if event_type in _TERMINAL_STREAM_EVENTS:
            break


async def with_keepalive(
    chunks: AsyncGenerator[str, None],
    *,
    interval_seconds: float = STREAM_KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Re-yield ``chunks``, sending a comment frame through a long silence.

    The pending pull is held across a timeout rather than cancelled. Cancelling
    an async generator's ``__anext__`` closes the generator, so the naive
    ``wait_for`` version would end the stream on the first quiet interval —
    which is the failure it was added to prevent.
    """
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(chunks))
            done, _ = await asyncio.wait({pending}, timeout=interval_seconds)
            if not done:
                yield KEEPALIVE_FRAME
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.wait({pending})
            if not pending.cancelled():
                # The pull finished as the stream was torn down. Reading the
                # outcome is what stops the loop reporting an exception nobody
                # retrieved; there is nothing left to do with it.
                pending.exception()
        # Closed here rather than left to the loop's async-generator finalizer,
        # which runs at an unrelated moment and, for a subscription iterator,
        # after the client has already gone.
        await chunks.aclose()
