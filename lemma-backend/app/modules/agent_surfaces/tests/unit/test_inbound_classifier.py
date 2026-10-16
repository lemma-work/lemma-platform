"""Request-time classification: what is dropped, what is answered now, what is stored.

A wrong drop loses a message somebody sent, so the drops are pinned one by
one; everything else must reach ingest.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.services.inbound_classifier import classify, thread_key
from app.modules.agent_surfaces.tests.slack_webhook_mix import (
    EXPECTED,
    slack_webhook_mix,
)

pytestmark = pytest.mark.unit


def _slack(event: dict) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": event,
    }


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (
            {"type": "message", "channel": "C1", "bot_id": "B1", "text": "hi"},
            "bot_echo",
        ),
        (
            {"type": "message", "channel": "C1", "subtype": "message_changed"},
            "slack_message_changed",
        ),
        ({"type": "reaction_added", "user": "U1"}, "slack_event_reaction_added"),
        ({"type": "user_change", "user": "U1"}, "slack_event_user_change"),
    ],
)
def test_what_nobody_acts_on_is_dropped_in_the_request(event, reason):
    assert classify("SLACK", _slack(event)).drop_reason == reason


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "message",
            "channel": "D1",
            "channel_type": "im",
            "user": "U1",
            "text": "hi",
            "ts": "1",
        },
        {
            "type": "app_mention",
            "channel": "C1",
            "user": "U1",
            "text": "<@B> hi",
            "ts": "1",
        },
        {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "text": "more",
            "ts": "2",
            "thread_ts": "1",
        },
        {
            "type": "message",
            "channel": "C1",
            "subtype": "thread_broadcast",
            "user": "U1",
            "text": "x",
            "ts": "2",
            "thread_ts": "1",
        },
        {
            "type": "message",
            "channel": "C1",
            "subtype": "file_share",
            "user": "U1",
            "files": [{}],
            "ts": "2",
        },
    ],
)
def test_a_person_writing_to_the_bot_is_a_message(event):
    verdict = classify("SLACK", _slack(event))
    assert verdict.message and verdict.unless_adopted is None


def test_channel_chatter_is_kept_only_where_a_group_adopted_the_channel():
    verdict = classify(
        "SLACK",
        _slack(
            {
                "type": "message",
                "channel": "C9",
                "user": "U1",
                "text": "lunch?",
                "ts": "1",
            }
        ),
    )
    assert verdict.message and verdict.unless_adopted == "C9"


def test_taps_and_lifecycle_are_not_messages_to_ingest():
    assert classify("SLACK", {"type": "block_actions"}).interaction
    assert classify("SLACK", _slack({"type": "app_home_opened"})).platform_update
    assert classify("TELEGRAM", {"update_id": 1, "callback_query": {}}).interaction
    assert classify("TELEGRAM", {"update_id": 1, "my_chat_member": {}}).platform_update
    assert (
        classify("TELEGRAM", {"update_id": 1, "edited_message": {}}).drop_reason
        == "edit"
    )


def test_whatsapp_receipts_drop_and_a_failed_send_is_a_platform_update():
    def body(value: dict) -> dict:
        return {"entry": [{"changes": [{"value": value}]}]}

    assert classify("WHATSAPP", body({"statuses": [{"status": "read"}]})).dropped
    failed = classify("WHATSAPP", body({"statuses": [{"status": "failed"}]}))
    assert failed.platform_update and not failed.message
    both = classify(
        "WHATSAPP",
        body(
            {
                "statuses": [{"status": "failed"}],
                "messages": [{"type": "text", "from": "1"}],
            }
        ),
    )
    assert both.platform_update and both.message


def test_an_unknown_platform_is_stored_for_ingest_to_decide():
    assert classify("RESEND", {"to": "pod@mail"}).message


def test_the_synthetic_day_is_classified_as_labelled():
    for delivery in slack_webhook_mix(2_000, seed=3):
        verdict = classify("SLACK", delivery.payload)
        expected = EXPECTED[delivery.kind]
        if expected == "drop":
            # Chatter is dropped by the one database question the request
            # asks: whether a group adopted the channel.
            assert verdict.dropped or verdict.unless_adopted, delivery
        elif expected == "platform_update":
            assert verdict.platform_update, delivery
        else:
            assert verdict.message and verdict.unless_adopted is None, delivery


def test_two_messages_in_one_thread_share_an_ordering_key():
    first = _slack({"type": "message", "channel": "C1", "ts": "2", "thread_ts": "1"})
    second = _slack({"type": "message", "channel": "C1", "ts": "3", "thread_ts": "1"})
    other = _slack({"type": "message", "channel": "C1", "ts": "4", "thread_ts": "9"})
    assert thread_key("SLACK", first) == thread_key("SLACK", second)
    assert thread_key("SLACK", first) != thread_key("SLACK", other)
    dm_a = _slack({"type": "message", "channel": "D1", "ts": "5"})
    dm_b = _slack({"type": "message", "channel": "D1", "ts": "6"})
    assert thread_key("SLACK", dm_a) == thread_key("SLACK", dm_b)
