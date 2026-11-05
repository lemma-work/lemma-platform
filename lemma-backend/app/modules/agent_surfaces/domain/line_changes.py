"""A platform saying a message the bot heard was edited or deleted.

Slack (``message_changed`` / ``message_deleted``), Teams (``messageUpdate`` /
``messageDelete``) and Telegram (``edited_message``; Telegram tells a bot of no
deletion) say so. The message is named by the platform's own id for it, in the
chat it was said in -- or, said privately, by who said it -- so it is applied
only to the line heard at that place, never to one heard anywhere else.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent


class LineChangeKind(StrEnum):
    EDITED = "EDITED"
    DELETED = "DELETED"


class ParsedLineChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: str
    kind: LineChangeKind
    external_message_id: str
    #: The chat it was said in.
    external_channel_id: str | None = None
    #: Said privately -- to the bot alone: the place is the person's.
    is_dm: bool = False
    #: Who said it, as the platform names them; their private place's key.
    sender_external_ids: tuple[str, ...] = ()
    #: The workspace or tenant, where the platform has one.
    tenant_id: str | None = None
    #: The message as it reads now, parsed as a message is (``EDITED``).
    edited: ParsedInboundSurfaceEvent | None = None


__all__ = ["LineChangeKind", "ParsedLineChange"]
