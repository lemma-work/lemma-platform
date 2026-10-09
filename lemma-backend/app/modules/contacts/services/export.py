"""Exporting a contact: what the pod holds about them, a page at a time.

Their conversations first, then their rows in the pod's contact-owned tables.
One opaque cursor walks both, so a client follows ``next_cursor`` until it is
absent and has everything, however much there is.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.contact_conversations import (
    ConversationCursor,
    ExportedConversation,
    export_contact_conversations,
)
from app.modules.datastore.contracts.contact_rows import (
    ContactRow,
    ContactRowsCursor,
    export_contact_rows,
)


class BadExportCursor(ValueError):
    """A cursor this export did not hand out."""


@dataclass(frozen=True, slots=True)
class ExportCursor:
    """Where an export stopped: in the conversations, or in the rows after them."""

    conversations: ConversationCursor | None = None
    rows: ContactRowsCursor | None = None
    #: Past the conversations; the rows are next.
    in_rows: bool = False

    def encode(self) -> str:
        if self.in_rows:
            state: dict[str, str | None] = {
                "phase": "rows",
                "table": self.rows.table if self.rows else None,
                "key": self.rows.key if self.rows else None,
            }
        else:
            after = self.conversations
            state = {
                "phase": "conversations",
                "at": after.created_at.isoformat() if after else None,
                "id": str(after.conversation_id) if after else None,
            }
        raw = json.dumps(state, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> "ExportCursor":
        try:
            padded = token + "=" * (-len(token) % 4)
            state = json.loads(base64.urlsafe_b64decode(padded.encode()))
            if state["phase"] == "rows":
                table, key = state.get("table"), state.get("key")
                rows = (
                    ContactRowsCursor(table=str(table), key=str(key))
                    if table is not None and key is not None
                    else None
                )
                return cls(rows=rows, in_rows=True)
            if state["at"] is None:
                return cls()
            return cls(
                conversations=ConversationCursor(
                    created_at=datetime.fromisoformat(state["at"]),
                    conversation_id=UUID(state["id"]),
                )
            )
        except (
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise BadExportCursor(
                "That export cursor is not one this export gave"
            ) from exc


@dataclass(frozen=True, slots=True)
class ExportPage:
    conversations: tuple[ExportedConversation, ...]
    rows: tuple[ContactRow, ...]
    next_cursor: ExportCursor | None


async def export_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    contact_id: UUID,
    cursor: ExportCursor | None,
) -> ExportPage:
    """One page of the export, starting where ``cursor`` says."""
    cursor = cursor or ExportCursor()
    if cursor.in_rows:
        return await _rows_page(uow_factory, pod_id, contact_id, cursor.rows, ())
    async with uow_factory() as uow:
        page = await export_contact_conversations(
            uow, pod_id=pod_id, contact_id=contact_id, after=cursor.conversations
        )
    if page.next_after is not None:
        return ExportPage(
            conversations=page.conversations,
            rows=(),
            next_cursor=ExportCursor(conversations=page.next_after),
        )
    # The conversations end on this page, so the rows start on it: an export
    # with no rows, the common case, is then one request rather than two.
    return await _rows_page(uow_factory, pod_id, contact_id, None, page.conversations)


async def _rows_page(
    uow_factory: UnitOfWorkFactory,
    pod_id: UUID,
    contact_id: UUID,
    after: ContactRowsCursor | None,
    conversations: tuple[ExportedConversation, ...],
) -> ExportPage:
    rows = await export_contact_rows(
        uow_factory, pod_id=pod_id, contact_id=contact_id, after=after
    )
    return ExportPage(
        conversations=conversations,
        rows=rows.rows,
        next_cursor=(
            ExportCursor(rows=rows.next_after, in_rows=True)
            if rows.next_after is not None
            else None
        ),
    )
