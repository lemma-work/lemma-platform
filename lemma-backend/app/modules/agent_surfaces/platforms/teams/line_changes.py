"""Teams saying a message was edited or deleted.

``messageUpdate`` (``channelData.eventType`` ``editMessage``) carries the
message as it now reads, in the shape of the message itself, so it is parsed
as one; ``messageDelete`` (``softDeleteMessage``) names it by its activity id.
An undelete is not read: the line's words are gone by then.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.agent_surfaces.domain.line_changes import (
    LineChangeKind,
    ParsedLineChange,
)
from app.modules.agent_surfaces.platforms.common import (
    payload_first,
    payload_section,
    payload_text,
)
from app.modules.agent_surfaces.platforms.teams.parser import (
    TeamsMessageParser,
    _teams_routing,
)

#: The activity types read here.
TEAMS_LINE_CHANGE_ACTIVITIES = frozenset({"messageUpdate", "messageDelete"})


def teams_line_changes(
    payload: Mapping[str, Any], parser: TeamsMessageParser
) -> list[ParsedLineChange]:
    """The edit or deletion this activity reports, if it reports one."""
    kind = payload_text(payload, "type")
    if kind not in TEAMS_LINE_CHANGE_ACTIVITIES:
        return []
    sender = payload_section(payload, "from")
    if payload_text(sender, "role") == "bot":
        return []
    event = payload_text(payload_section(payload, "channelData"), "eventType")
    message_id = payload_text(payload, "id").strip()
    routing = _teams_routing(dict(payload))
    if not message_id or not routing.external_channel_id:
        return []
    tenant = payload_section(payload_section(payload, "channelData"), "tenant")
    change = ParsedLineChange(
        platform="TEAMS",
        kind=LineChangeKind.DELETED,
        external_message_id=message_id,
        external_channel_id=routing.external_channel_id,
        is_dm=routing.is_dm,
        sender_external_ids=tuple(
            value
            for value in (
                payload_text(sender, "aadObjectId"),
                payload_text(sender, "id"),
            )
            if value
        ),
        tenant_id=(
            payload_first(tenant, "id") or payload_text(payload, "tenantId") or None
        ),
    )
    if kind == "messageDelete":
        return [change] if event in ("", "softDeleteMessage") else []
    if event not in ("", "editMessage"):
        return []
    edited = parser.parse(dict(payload))
    if edited is None:
        return []
    return [change.model_copy(update={"kind": LineChangeKind.EDITED, "edited": edited})]


__all__ = ["TEAMS_LINE_CHANGE_ACTIVITIES", "teams_line_changes"]
