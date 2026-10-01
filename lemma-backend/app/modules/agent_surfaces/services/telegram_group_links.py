"""A link that adds a pod's Telegram bot to a group, and says who asked.

Telegram lets a person add a bot to a group through a deep link,
``t.me/<bot>?startgroup=<code>``: the app asks which group, adds the bot, and
the bot then hears ``/start@<bot> <code>`` in that group, from that person.
The code ties the two ends together. It is minted here for a member signed in
to Lemma and redeemed when the bot hears it, so the group is adopted for the
right pod, with the right member answering for it, without anybody typing a
username first. It says nothing about which Telegram account used it -- a link
is easily passed on -- so it never links one to the member
(``services/telegram_group_join``).

One use and an hour long, like any other bearer credential. A code that has
expired or been spent does nothing; the group can be added again from Lemma.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

_TTL_SECONDS = 3600
_PREFIX = "agent_surfaces:telegram_group_link:"

#: What Telegram accepts as a start parameter: 1-64 of these characters.
_START = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?\s+([A-Za-z0-9_-]{16,64})\s*$")

#: A Telegram username. What a surface knows it by can also be a display name
#: ("Sales Bot") when Telegram could not be asked, and that is no link at all.
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class GroupLinkUnavailable(RuntimeError):
    """The link could not be minted: no bot name, or nowhere to keep the code."""


@dataclass(frozen=True, slots=True)
class GroupLinkClaim:
    surface_id: UUID
    user_id: UUID


def start_code(text: str | None) -> str | None:
    """The code in a group's ``/start@bot <code>`` message, if that is what it is."""
    match = _START.match((text or "").strip())
    return match.group(1) if match else None


async def mint_group_link(
    *, surface_id: UUID, user_id: UUID, bot_username: str | None, redis=None
) -> tuple[str, datetime]:
    """The link to hand the member, and when it stops working."""
    username = (bot_username or "").strip().lstrip("@")
    if not _USERNAME.match(username):
        raise GroupLinkUnavailable("Telegram has not said what this bot is called")
    code = secrets.token_urlsafe(24)
    client = redis or get_redis()
    try:
        await client.set(_PREFIX + code, f"{surface_id}:{user_id}", ex=_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        raise GroupLinkUnavailable("Could not keep the link's code") from exc
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_TTL_SECONDS)
    return f"https://t.me/{username}?startgroup={code}", expires_at


async def redeem_group_link(code: str, *, redis=None) -> GroupLinkClaim | None:
    """Who a code was minted for, spending it; None when it is unknown or spent."""
    client = redis or get_redis()
    try:
        raw = await client.getdel(_PREFIX + code)
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.telegram_group_links.redeem_unavailable.degraded",
            exc_info=True,
        )
        return None
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    surface_id, _, user_id = text.partition(":")
    try:
        return GroupLinkClaim(surface_id=UUID(surface_id), user_id=UUID(user_id))
    except ValueError:
        return None
