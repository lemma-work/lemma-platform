"""Telegram saying a message was edited.

``edited_message`` is the message as it now reads, with the original's
``message_id``, so it is parsed as the message itself would be. Telegram
tells a bot of no deletion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.agent_surfaces.domain.line_changes import (
    LineChangeKind,
    ParsedLineChange,
)
from app.modules.agent_surfaces.platforms.common import payload_section
from app.modules.agent_surfaces.platforms.telegram.parser import TelegramMessageParser


def telegram_line_changes(
    payload: Mapping[str, Any], parser: TelegramMessageParser
) -> list[ParsedLineChange]:
    """The edit this update reports, if it reports one."""
    edited_message = payload.get("edited_message")
    if not isinstance(edited_message, dict):
        return []
    if payload_section(edited_message, "from").get("is_bot"):
        return []
    edited = parser.parse(
        {"update_id": payload.get("update_id"), "message": edited_message}
    )
    if edited is None or not edited.external_message_id:
        return []
    sender = edited.sender_external_user_id
    return [
        ParsedLineChange(
            platform="TELEGRAM",
            kind=LineChangeKind.EDITED,
            external_message_id=edited.external_message_id,
            external_channel_id=edited.external_channel_id,
            is_dm=edited.is_dm,
            sender_external_ids=(sender,) if sender else (),
            edited=edited,
        )
    ]


__all__ = ["telegram_line_changes"]
