"""What WhatsApp says about groups themselves, rather than what is said in them.

A business cannot be added to a WhatsApp group; it creates one. Meta answers the
creation at once with only a ``request_id`` and confirms it later, in a
``group_lifecycle_update`` webhook that echoes that id alongside the group's own
-- and, usually, its invite link. A deletion is confirmed the same way.

People joining and leaving come in ``group_participants_update``: a
``group_participants_add`` names each person added in ``added_participants``
(seated in the group's channel before they speak), a
``group_participants_remove`` each one out in ``removed_participants`` -- by
``wa_id``, which is their number, else the ``input`` the business removed them
by. Participants a removal failed for are still there, and are not read.

Everything else Meta sends about groups (settings, suspension) changes nothing
Lemma keeps, so it is not read here; its payloads carry no ``messages`` and the
message parser answers None for them too.

Meta's own samples disagree on details (a timestamp as a string or a number,
trailing commas), so nothing here depends on more than the fields named below.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.groups import GroupUpdateKind, ParsedGroupUpdate
from app.modules.agent_surfaces.platforms.common import payload_section, payload_text

_LIFECYCLE_FIELD = "group_lifecycle_update"
_PARTICIPANTS_FIELD = "group_participants_update"
#: Who a participants event moves, by its type: the list naming them.
_MOVED = {
    "group_participants_add": (GroupUpdateKind.MEMBER_JOINED, "added_participants"),
    "group_participants_remove": (GroupUpdateKind.MEMBER_LEFT, "removed_participants"),
}


def whatsapp_group_updates(payload: dict[str, object]) -> list[ParsedGroupUpdate]:
    """Every group creation and deletion a webhook body confirms or refuses,
    and everybody it says joined a group or left it."""
    updates: list[ParsedGroupUpdate] = []
    for entry in _objects(payload.get("entry")):
        for change in _objects(entry.get("changes")):
            field = change.get("field")
            if field not in (_LIFECYCLE_FIELD, _PARTICIPANTS_FIELD):
                continue
            value = payload_section(change, "value")
            phone_number_id = (
                payload_text(payload_section(value, "metadata"), "phone_number_id")
                or None
            )
            for group in _objects(value.get("groups")):
                if field == _PARTICIPANTS_FIELD:
                    updates.extend(_participants_moved(group))
                    continue
                update = _group_update(group, phone_number_id=phone_number_id)
                if update is not None:
                    updates.append(update)
    return updates


def _participants_moved(group: dict[str, object]) -> list[ParsedGroupUpdate]:
    """One update per person a participants event says joined or left."""
    moved = _MOVED.get(payload_text(group, "type"))
    chat = payload_text(group, "group_id").strip()
    if moved is None or not chat:
        return []
    kind, key = moved
    updates: list[ParsedGroupUpdate] = []
    for person in _objects(group.get(key)):
        number = (
            (payload_text(person, "wa_id") or payload_text(person, "input"))
            .strip()
            .lstrip("+")
        )
        if number:
            updates.append(
                ParsedGroupUpdate(
                    platform="WHATSAPP",
                    kind=kind,
                    external_channel_id=chat,
                    external_user_id=number,
                    phone=number,
                )
            )
    return updates


def _group_update(
    group: dict[str, object], *, phone_number_id: str | None
) -> ParsedGroupUpdate | None:
    kind = _kind(payload_text(group, "type"), failed=bool(group.get("errors")))
    if kind is None:
        return None
    return ParsedGroupUpdate(
        platform="WHATSAPP",
        kind=kind,
        request_id=payload_text(group, "request_id").strip() or None,
        external_channel_id=payload_text(group, "group_id").strip() or None,
        title=payload_text(group, "subject").strip() or None,
        invite_link=payload_text(group, "invite_link").strip() or None,
        phone_number_id=phone_number_id,
    )


def _kind(event_type: str, *, failed: bool) -> GroupUpdateKind | None:
    """What the event means. A failed deletion changes nothing, so it is not one."""
    if event_type == "group_create":
        return GroupUpdateKind.CREATE_FAILED if failed else GroupUpdateKind.CREATED
    if event_type == "group_delete" and not failed:
        return GroupUpdateKind.DELETED
    return None


def _objects(items: object) -> list[dict[str, object]]:
    return (
        [item for item in items if isinstance(item, dict)]
        if isinstance(items, list)
        else []
    )
