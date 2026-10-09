"""An export's cursor carries exactly where it stopped, and refuses anything else."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.agent.contracts.contact_conversations import ConversationCursor
from app.modules.contacts.services.export import BadExportCursor, ExportCursor
from app.modules.datastore.contracts.contact_rows import ContactRowsCursor

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "cursor",
    [
        ExportCursor(),
        ExportCursor(
            conversations=ConversationCursor(
                created_at=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
                conversation_id=uuid4(),
            )
        ),
        ExportCursor(in_rows=True),
        ExportCursor(rows=ContactRowsCursor(table="orders", key="42"), in_rows=True),
    ],
    ids=["start", "in-conversations", "rows-start", "in-rows"],
)
def test_a_cursor_comes_back_as_it_went(cursor):
    assert ExportCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("token", ["", "junk", "eyJwaGFzZSI6MX0", "bm90IGpzb24"])
def test_a_cursor_the_export_never_gave_is_refused(token):
    with pytest.raises(BadExportCursor):
        ExportCursor.decode(token)
