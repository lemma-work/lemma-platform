"""Reading who comes and goes in a Telegram group.

``my_chat_member`` is the bot's own membership moving -- added, removed,
promoted, blocked in a private chat. Being added to a group is somebody
bringing the bot in, and the update names them in ``from``
(``joined_group_event``). Being removed, or put back, stops or resumes the
group's mirror (``group_membership_updates``). For anybody else a
``left_chat_member`` service message takes them out of the mirror, and a
``new_chat_members`` one seats them in it before they say a word. Telegram
sends service messages whatever the bot's privacy mode.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import GroupUpdateKind, ParsedGroupUpdate

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


def group_membership_updates(payload: dict[str, object]) -> list[ParsedGroupUpdate]:
    """The bot taken out of a group or put back, or somebody else leaving or
    joining it."""
    update = payload.get("my_chat_member")
    if isinstance(update, dict):
        return _bot_moved(update)
    message = payload.get("message")
    if not isinstance(message, dict):
        return []
    return _somebody_left(message) + _somebody_joined(message)


def _bot_moved(update: dict[str, object]) -> list[ParsedGroupUpdate]:
    chat = _section(update, "chat")
    chat_id = str(chat.get("id") or "").strip()
    if chat.get("type") not in _GROUP_TYPES or not chat_id:
        return []
    old = _status(update, "old_chat_member")
    new = _status(update, "new_chat_member")
    if old in _PRESENT and new in _ABSENT:
        return [_group_update(GroupUpdateKind.BOT_LEFT, chat_id)]
    if old in _ABSENT and new in _PRESENT:
        return [_group_update(GroupUpdateKind.BOT_RETURNED, chat_id)]
    return []


def _somebody_left(message: dict[str, object]) -> list[ParsedGroupUpdate]:
    """A ``left_chat_member`` service message; the bot's own is ``my_chat_member``."""
    left = _section(message, "left_chat_member")
    chat = _section(message, "chat")
    chat_id = str(chat.get("id") or "").strip()
    person = str(left.get("id") or "").strip()
    if left.get("is_bot") or chat.get("type") not in _GROUP_TYPES:
        return []
    if not chat_id or not person:
        return []
    return [_group_update(GroupUpdateKind.MEMBER_LEFT, chat_id, person=person)]


def _somebody_joined(message: dict[str, object]) -> list[ParsedGroupUpdate]:
    """A ``new_chat_members`` service message: one update per person, never a
    bot -- this one's own arrival is ``my_chat_member``, and other bots are
    not people in the group."""
    chat = _section(message, "chat")
    chat_id = str(chat.get("id") or "").strip()
    if chat.get("type") not in _GROUP_TYPES or not chat_id:
        return []
    joined = message.get("new_chat_members")
    updates: list[ParsedGroupUpdate] = []
    for person in joined if isinstance(joined, list) else []:
        if not isinstance(person, dict) or person.get("is_bot"):
            continue
        person_id = str(person.get("id") or "").strip()
        if person_id:
            updates.append(
                _group_update(
                    GroupUpdateKind.MEMBER_JOINED,
                    chat_id,
                    person=person_id,
                    name=_name(person),
                )
            )
    return updates


def _name(person: dict[str, object]) -> str | None:
    parts = (person.get("first_name"), person.get("last_name"))
    name = " ".join(str(part).strip() for part in parts if part)
    return name or None


def _group_update(
    kind: GroupUpdateKind,
    chat_id: str,
    *,
    person: str | None = None,
    name: str | None = None,
) -> ParsedGroupUpdate:
    return ParsedGroupUpdate(
        platform=SurfacePlatform.TELEGRAM.value,
        kind=kind,
        external_channel_id=chat_id,
        external_user_id=person,
        display_name=name,
    )


def _section(update: dict[str, object], key: str) -> dict[str, object]:
    value = update.get(key)
    return value if isinstance(value, dict) else {}


def _status(update: dict[str, object], key: str) -> str | None:
    status = _section(update, key).get("status")
    return str(status) if status else None
