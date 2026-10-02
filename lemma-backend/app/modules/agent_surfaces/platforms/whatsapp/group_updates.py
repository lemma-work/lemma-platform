"""What WhatsApp says about groups themselves, rather than what is said in them.

A business cannot be added to a WhatsApp group; it creates one. Meta answers the
creation at once with only a ``request_id`` and confirms it later, in a
``group_lifecycle_update`` webhook that echoes that id alongside the group's own
-- and, usually, its invite link. A deletion is confirmed the same way.

Everything else Meta sends about groups (people joining and leaving, settings,
suspension) changes nothing Lemma keeps, so it is not read here; its payloads
carry no ``messages`` and the message parser answers None for them too.

Meta's own samples disagree on details (a timestamp as a string or a number,
trailing commas), so nothing here depends on more than the fields named below.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.groups import GroupUpdateKind, ParsedGroupUpdate
from app.modules.agent_surfaces.platforms.common import payload_section, payload_text

_LIFECYCLE_FIELD = "group_lifecycle_update"


def whatsapp_group_updates(payload: dict[str, object]) -> list[ParsedGroupUpdate]:
    """Every group creation and deletion a webhook body confirms or refuses."""
    updates: list[ParsedGroupUpdate] = []
    for entry in _objects(payload.get("entry")):
        for change in _objects(entry.get("changes")):
            if change.get("field") != _LIFECYCLE_FIELD:
                continue
            value = payload_section(change, "value")
            phone_number_id = (
                payload_text(payload_section(value, "metadata"), "phone_number_id")
                or None
            )
            for group in _objects(value.get("groups")):
                update = _group_update(group, phone_number_id=phone_number_id)
                if update is not None:
                    updates.append(update)
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
