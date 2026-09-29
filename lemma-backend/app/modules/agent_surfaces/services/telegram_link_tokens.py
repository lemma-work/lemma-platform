"""One-time links that connect a Telegram chat to the signed-in Lemma user.

The shared Telegram bot learns who a stranger is by asking: a contact share,
then an email address and a code sent to it. On an installation that cannot
send mail -- Lemma Desktop, by default -- that last step can never finish. But
the person who wants to talk to the bot is usually already signed in to Lemma
on the same computer, and the app can vouch for them directly: it mints a link
for the signed-in user, Telegram opens the bot with it as the `/start`
payload, and the bot binds whoever pressed Start to that user.

Kept in Redis, not a table: a grant lives for ten minutes and is read once, and
the store needs nothing a table would add. Keyed by a hash of the token, so the
token itself -- the only thing that grants anything -- is never stored, and
consumed with GETDEL, so two deliveries of the same `/start` cannot both link.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

#: How long a minted link works. Long enough to switch to Telegram and press
#: Start, short enough that a link left in a clipboard or a chat is soon inert.
TELEGRAM_LINK_TOKEN_TTL_SECONDS = 10 * 60

#: What marks a `/start` payload as a link, so it is told apart from the plain
#: `/start` Telegram sends when a chat is opened without one.
START_PAYLOAD_PREFIX = "link_"

#: Telegram's own rule for a start parameter: up to 64 characters from
#: `A-Za-z0-9_-`. A token outside it could not have come from a link this
#: module minted, so it is refused without asking Redis.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{16,59}")

_KEY_PREFIX = "agent_surfaces:telegram_link"


class TelegramLinkGrant(BaseModel):
    """Who a link is for, and where their chat should go."""

    user_id: UUID
    #: The pod whose agent answers. None leaves the choice to signup's own
    #: workspace rules, as if the person had verified by email.
    pod_id: UUID | None = None
    expires_at: datetime


class MintedTelegramLink(BaseModel):
    token: str
    grant: TelegramLinkGrant

    @property
    def start_payload(self) -> str:
        return START_PAYLOAD_PREFIX + self.token


def link_token_from_start(message_text: str) -> str | None:
    """The token in a `/start link_<token>` message, or None for anything else.

    Tolerates the `/start@botname` form Telegram uses in a chat with several
    bots; nothing else about the message is loosened.
    """
    parts = (message_text or "").strip().split()
    if len(parts) != 2:
        return None
    command, payload = parts
    if command.split("@", 1)[0].lower() != "/start":
        return None
    if not payload.startswith(START_PAYLOAD_PREFIX):
        return None
    return payload[len(START_PAYLOAD_PREFIX) :]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TelegramLinkTokenStore:
    def __init__(
        self,
        *,
        redis: Redis | None = None,
        ttl_seconds: int = TELEGRAM_LINK_TOKEN_TTL_SECONDS,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = get_redis(url=settings.redis_url)
        return self._redis

    @staticmethod
    def _key(token: str) -> str:
        return f"{_KEY_PREFIX}:{hashlib.sha256(token.encode()).hexdigest()}"

    async def mint(
        self, *, user_id: UUID, pod_id: UUID | None = None
    ) -> MintedTelegramLink:
        # 24 bytes is 32 URL-safe characters: `link_` plus the token stays well
        # inside Telegram's 64-character start parameter.
        token = secrets.token_urlsafe(24)
        grant = TelegramLinkGrant(
            user_id=user_id,
            pod_id=pod_id,
            expires_at=self._clock() + timedelta(seconds=self._ttl_seconds),
        )
        await self._client().set(
            self._key(token), grant.model_dump_json(), ex=self._ttl_seconds, nx=True
        )
        return MintedTelegramLink(token=token, grant=grant)

    async def consume(self, token: str) -> TelegramLinkGrant | None:
        """The grant, exactly once, while it is still in date.

        GETDEL rather than a read and a delete: two deliveries of one `/start`
        -- a redelivery, a double tap -- must not both find it. The expiry is
        checked again here because Redis's TTL is the store's clock, not the
        grant's, and the grant carries the time the person was shown.
        """
        if not _TOKEN_SHAPE.fullmatch(token):
            return None
        raw = await self._client().getdel(self._key(token))
        if raw is None:
            return None
        grant = TelegramLinkGrant.model_validate_json(raw)
        if grant.expires_at <= self._clock():
            return None
        return grant
