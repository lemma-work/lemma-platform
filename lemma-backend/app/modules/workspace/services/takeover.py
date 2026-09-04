"""Handing the wheel of the agent's browser to the person, briefly.

The agent hits a login wall. Rather than being told a password, it asks the
person to drive — and this is the record of that ask: who it is for, which
conversation raised it, what site it is about, and whether it has been dealt
with.

**The link is not the credential.** Unlike ``desktop_auth_handoff``, which needs
a PKCE challenge because the webview and the system browser are different
clients, a takeover is opened by the person in their own signed-in session. So
the id in the URL is a *lookup*, and holding it grants nothing: every read
checks that the request belongs to the caller. That matters more than it sounds,
because this link travels through Slack and WhatsApp, whose unfurl bots fetch
every URL they are shown.

State lives in Redis with a TTL rather than in Postgres, for the same reason the
session-approval store does: a takeover nobody answers should expire on its own,
and there is nothing here worth keeping once it has.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

_KEY_PREFIX = "workspace:takeover:v1"

#: Long enough for somebody to reach a phone, find the password and get through
#: a second factor; short enough that an ignored ask does not sit open all day.
DEFAULT_TTL_SECONDS = 15 * 60


class TakeoverStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class TakeoverNotFound(Exception):
    """No such request, or it expired."""


@dataclass(frozen=True, slots=True)
class TakeoverRequest:
    request_id: str
    user_id: UUID
    conversation_id: UUID | None
    origin: str
    reason: str
    status: TakeoverStatus
    created_at: datetime

    @property
    def is_open(self) -> bool:
        return self.status is TakeoverStatus.PENDING


class TakeoverStore:
    def __init__(
        self, redis_url: str | None = None, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = get_redis(url=self._redis_url)
        return self._redis

    @staticmethod
    def _key(request_id: str) -> str:
        return f"{_KEY_PREFIX}:{request_id}"

    async def create(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID | None,
        origin: str,
        reason: str,
    ) -> TakeoverRequest:
        redis = await self._get_redis()
        request = TakeoverRequest(
            request_id=secrets.token_urlsafe(24),
            user_id=user_id,
            conversation_id=conversation_id,
            origin=origin,
            reason=reason,
            status=TakeoverStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await redis.set(
            self._key(request.request_id),
            json.dumps(_encode(request)),
            ex=self._ttl_seconds,
        )
        return request

    async def get_for_user(self, request_id: str, user_id: UUID) -> TakeoverRequest:
        """The request, if it is this person's.

        A request belonging to somebody else is reported as missing rather than
        forbidden: the id is unguessable, so "not yours" and "not there" are the
        same fact to anyone entitled to ask, and distinguishing them only
        confirms an id to somebody who should not have one.
        """
        redis = await self._get_redis()
        raw = await redis.get(self._key(request_id))
        if raw is None:
            raise TakeoverNotFound(request_id)
        request = _decode(raw)
        if request.user_id != user_id:
            raise TakeoverNotFound(request_id)
        return request

    async def resolve(
        self, request_id: str, user_id: UUID, *, status: TakeoverStatus
    ) -> TakeoverRequest:
        """Close a request, keeping the row so the asker sees how it ended."""
        request = await self.get_for_user(request_id, user_id)
        resolved = TakeoverRequest(
            request_id=request.request_id,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            origin=request.origin,
            reason=request.reason,
            status=status,
            created_at=request.created_at,
        )
        redis = await self._get_redis()
        # Keeps whatever TTL is left rather than extending it: resolving is the
        # end of the exchange, not a reason to hold the record open longer.
        await redis.set(
            self._key(request_id), json.dumps(_encode(resolved)), keepttl=True
        )
        return resolved


def _encode(request: TakeoverRequest) -> dict[str, str | None]:
    return {
        "request_id": request.request_id,
        "user_id": str(request.user_id),
        "conversation_id": (
            str(request.conversation_id) if request.conversation_id else None
        ),
        "origin": request.origin,
        "reason": request.reason,
        "status": request.status.value,
        "created_at": request.created_at.isoformat(),
    }


def _decode(raw: str | bytes) -> TakeoverRequest:
    payload = json.loads(raw)
    conversation_id = payload.get("conversation_id")
    return TakeoverRequest(
        request_id=payload["request_id"],
        user_id=UUID(payload["user_id"]),
        conversation_id=UUID(conversation_id) if conversation_id else None,
        origin=payload["origin"],
        reason=payload.get("reason") or "",
        status=TakeoverStatus(payload["status"]),
        created_at=datetime.fromisoformat(payload["created_at"]),
    )
