"""Reading Telegram's ``my_chat_member`` update: the bot's own membership changing.

Telegram sends it whenever the bot's status in a chat moves -- added, removed,
promoted, blocked in a private chat. One of those moves means somebody brought
the bot into a group, and the update names them in ``from``. That is the only
one this reads.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)

#: Statuses in which the bot is in the chat, and in which it is not.
_PRESENT = frozenset({"member", "administrator", "creator", "restricted"})
_ABSENT = frozenset({"left", "kicked"})
_GROUP_TYPES = frozenset({"group", "supergroup"})


def joined_group_event(
    payload: dict[str, object],
) -> ParsedSurfaceLifecycleEvent | None:
    """A JOINED_CHANNEL event when this update is the bot being added to a group."""
    update = payload.get("my_chat_member")
    if not isinstance(update, dict):
        return None
    chat = _section(update, "chat")
    if chat.get("type") not in _GROUP_TYPES:
        return None
    old = _status(update, "old_chat_member")
    new = _status(update, "new_chat_member")
    if old not in _ABSENT or new not in _PRESENT:
        return None
    chat_id = str(chat.get("id") or "").strip()
    actor = _section(update, "from")
    actor_id = str(actor.get("id") or "").strip()
    if not chat_id or not actor_id or actor.get("is_bot"):
        return None
    title = str(chat.get("title") or "").strip()
    return ParsedSurfaceLifecycleEvent(
        platform=SurfacePlatform.TELEGRAM,
        kind=SurfaceLifecycleKind.JOINED_CHANNEL,
        external_channel_id=chat_id,
        actor_external_user_id=actor_id,
        channel_title=title or None,
        raw_payload=payload,
    )


def _section(update: dict[str, object], key: str) -> dict[str, object]:
    value = update.get(key)
    return value if isinstance(value, dict) else {}


def _status(update: dict[str, object], key: str) -> str | None:
    status = _section(update, key).get("status")
    return str(status) if status else None
