"""The history list's page token: opaque on the wire, both sort keys inside."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status

from app.modules.agent.domain.value_objects import ConversationListCursor


def encode_conversation_page_token(cursor: ConversationListCursor) -> str:
    raw = f"{cursor.last_activity_at.isoformat()}|{cursor.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _bare_uuid(page_token: str) -> UUID | None:
    try:
        return UUID(page_token)
    except ValueError:
        return None


def parse_conversation_page_token(
    page_token: str | None,
) -> ConversationListCursor | UUID | None:
    """Opaque on the wire; `activity|id` inside, the list's two sort keys.

    A bare conversation id -- what this endpoint handed out while it was ordered
    by id alone -- comes back as that id. It names a row, not a position in the
    new order, so the service looks the row up and continues after it; a
    client paging across the deploy that changed the order carries on rather
    than starting again.
    """
    # `?page_token=` is a client serialising "no token", not a bad one.
    if not page_token:
        return None
    legacy_id = _bare_uuid(page_token)
    if legacy_id is not None:
        return legacy_id
    try:
        decoded = base64.urlsafe_b64decode(page_token.encode("ascii")).decode()
        raw_activity, raw_id = decoded.split("|", 1)
        last_activity_at = datetime.fromisoformat(raw_activity)
        conversation_id = UUID(raw_id)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid page_token",
        ) from exc
    if last_activity_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid page_token",
        )
    return ConversationListCursor(last_activity_at=last_activity_at, id=conversation_id)
