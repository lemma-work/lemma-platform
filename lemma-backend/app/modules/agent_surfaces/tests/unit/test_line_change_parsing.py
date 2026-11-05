"""What a platform's edit or deletion of a message names, as each says it.

The message is named by the platform's own id in the place it was said, and an
edit carries what it says now, parsed as the message itself would be. An app's
own messages -- the bot's answers among them -- are nobody's to change here.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.line_changes import LineChangeKind
from app.modules.agent_surfaces.platforms.slack.adapter import SlackSurfaceAdapter
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram.adapter import (
    TelegramSurfaceAdapter,
)

pytestmark = pytest.mark.unit


def _slack(event: dict) -> dict:
    return {"type": "event_callback", "team_id": "T1", "event": event}


def test_a_slack_edit_names_the_message_and_says_what_it_says_now():
    [change] = SlackSurfaceAdapter().parse_line_changes(
        _slack(
            {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C1",
                "channel_type": "channel",
                "message": {
                    "type": "message",
                    "user": "U1",
                    "text": "ship on Monday",
                    "ts": "1700000000.000100",
                    "edited": {"user": "U1", "ts": "1700000050.000000"},
                },
                "previous_message": {"user": "U1", "text": "ship on Friday"},
            }
        )
    )
    assert change.kind is LineChangeKind.EDITED
    assert change.external_message_id == "1700000000.000100"
    assert change.external_channel_id == "C1"
    assert change.tenant_id == "T1"
    assert change.is_dm is False
    assert change.edited is not None
    assert change.edited.message_text == "ship on Monday"


def test_a_slack_deletion_names_the_deleted_message():
    [change] = SlackSurfaceAdapter().parse_line_changes(
        _slack(
            {
                "type": "message",
                "subtype": "message_deleted",
                "channel": "D1",
                "deleted_ts": "1700000000.000100",
                "previous_message": {"user": "U1", "text": "ship on Friday"},
            }
        )
    )
    assert change.kind is LineChangeKind.DELETED
    assert change.external_message_id == "1700000000.000100"
    assert change.is_dm is True
    assert change.sender_external_ids == ("U1",)


def test_a_slack_thread_root_turned_tombstone_reads_as_deleted():
    [change] = SlackSurfaceAdapter().parse_line_changes(
        _slack(
            {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C1",
                "message": {"subtype": "tombstone", "text": "", "ts": "1.1"},
            }
        )
    )
    assert change.kind is LineChangeKind.DELETED


def test_an_apps_own_message_changing_is_nobodys_to_apply():
    adapter = SlackSurfaceAdapter()
    assert (
        adapter.parse_line_changes(
            _slack(
                {
                    "type": "message",
                    "subtype": "message_changed",
                    "channel": "C1",
                    "message": {"bot_id": "B1", "text": "Done.", "ts": "1.1"},
                }
            )
        )
        == []
    )
    # A plain message is not a change of one.
    assert (
        adapter.parse_line_changes(
            _slack(
                {
                    "type": "message",
                    "channel": "C1",
                    "user": "U1",
                    "text": "hi",
                    "ts": "1",
                }
            )
        )
        == []
    )


def _teams(kind: str, **extra: object) -> dict:
    return {
        "type": kind,
        "id": "1700000000000",
        "from": {"id": "29:1", "aadObjectId": "aad-1", "name": "Ada"},
        "conversation": {"id": "19:abc@thread.tacv2", "conversationType": "channel"},
        "channelData": {
            "tenant": {"id": "tenant-1"},
            "channel": {"id": "19:abc@thread.tacv2"},
            "team": {"id": "team-1"},
            **extra,
        },
        "serviceUrl": "https://smba.example",
    }


def test_a_teams_edit_and_deletion_name_the_message_in_its_channel():
    adapter = TeamsSurfaceAdapter()
    [edited] = adapter.parse_line_changes(
        {**_teams("messageUpdate", eventType="editMessage"), "text": "ship Monday"}
    )
    assert edited.kind is LineChangeKind.EDITED
    assert edited.external_message_id == "1700000000000"
    assert edited.external_channel_id == "19:abc@thread.tacv2"
    assert edited.tenant_id == "tenant-1"
    assert edited.edited is not None and edited.edited.message_text == "ship Monday"
    [deleted] = adapter.parse_line_changes(
        _teams("messageDelete", eventType="softDeleteMessage")
    )
    assert deleted.kind is LineChangeKind.DELETED
    # An undelete brings back nothing this kept.
    assert (
        adapter.parse_line_changes(_teams("messageUpdate", eventType="undeleteMessage"))
        == []
    )


def test_a_telegram_edit_names_the_message_in_its_chat():
    [change] = TelegramSurfaceAdapter().parse_line_changes(
        {
            "update_id": 2,
            "edited_message": {
                "message_id": 5,
                "date": 1700000000,
                "edit_date": 1700000050,
                "chat": {"id": -100, "type": "supergroup", "title": "Ops"},
                "from": {"id": 7, "is_bot": False, "first_name": "Ada"},
                "text": "ship on Monday",
            },
        }
    )
    assert change.kind is LineChangeKind.EDITED
    assert change.external_message_id == "5"
    assert change.external_channel_id == "-100"
    assert change.edited is not None
    assert change.edited.message_text == "ship on Monday"
