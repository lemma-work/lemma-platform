"""Shared helpers for agent API controllers."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status

from app.composition.agent_usage import build_usage_service
from app.composition.authorization import create_authorization_service
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


def _build_conversation_service(uow) -> ConversationService:
    return ConversationService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_service(uow),
        usage_service=build_usage_service(uow),
    )


def _build_conversation_retry_service(uow) -> ConversationRetryService:
    return ConversationRetryService(
        uow=uow,
        conversation_repository=ConversationRepository(uow),
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_service(uow),
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
