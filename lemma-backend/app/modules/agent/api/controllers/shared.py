"""Shared helpers for agent API controllers."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import (
    get_channel_service,
)
from app.modules.agent.services.realtime import (
    conversation_channel as conversation_channel,
)

ChannelServiceDep = Annotated[RealtimeChannel, Depends(get_channel_service)]
_TERMINAL_STREAM_EVENTS = {"completed", "stopped", "error"}


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

        payload_run_id = payload.get("agent_run_id")
        if agent_run_id is not None and payload_run_id != str(agent_run_id):
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
