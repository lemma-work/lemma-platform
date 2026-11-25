"""A Slack trigger's stored routing key and a heard message's key are one key.

They are built in two modules -- `_slack_binding` when the schedule is created,
from the trigger and the author's config; `slack_trigger_events` when the pod's
bot hears a message, from what Slack sent. Matching is containment of the
second in the first, so if they disagree by a key name or a type nothing ever
fires and nothing anywhere says so. The catalog advertised these three triggers
for years with no routing at all; this is what keeps them routed.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.slack.triggers import (
    slack_trigger_events,
)
from app.modules.connectors.contracts.triggers import TriggerBinding
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.infrastructure.adapters.external_schedule_writer import (
    _slack_binding,
)

pytestmark = pytest.mark.unit

TEAM = "T0INCIDENT"
CHANNEL = "C0INC42"
THREAD = "1700000000.000100"
POD = "0199a1f6-0000-7000-8000-0000000000aa"


def _heard(**event: object) -> dict[str, object]:
    """The match keys of every trigger event one Slack delivery is."""
    payload = {
        "type": "event_callback",
        "team_id": TEAM,
        "event_id": "Ev0001",
        "token": "legacy-verification-token",
        "event": {
            "type": "message",
            "user": "U0RESPONDER",
            "text": "Database failover started",
            "ts": "1700000100.000200",
            "channel": CHANNEL,
            "channel_type": "channel",
            **event,
        },
    }
    parsed = SlackMessageParser().parse(payload)
    assert parsed is not None
    return {
        str(normalized.match["event"]): normalized
        for normalized in slack_trigger_events(parsed, pod_ids=frozenset({POD}))
        if normalized.match is not None
    }


def _stored(event_type: str, config: dict[str, object]) -> dict[str, object]:
    return {
        **config,
        **_slack_binding(
            TriggerBinding(connector_id="slack", event_type=event_type), config
        ),
    }


def _contains(config: dict[str, object], key: dict[str, object]) -> bool:
    """`schedules.config @> key`, for flat keys."""
    return all(config.get(name) == value for name, value in key.items())


def test_a_channel_message_schedule_matches_a_message_in_its_channel():
    stored = _stored("message", {"team_id": TEAM, "channel_id": CHANNEL})

    heard = _heard()

    assert set(heard) == {"message"}
    assert _contains(stored, heard["message"].match or {})


def test_a_thread_reply_fires_its_threads_schedule_and_the_channels():
    thread = _stored(
        "message.thread",
        {"team_id": TEAM, "channel_id": CHANNEL, "thread_ts": THREAD},
    )
    channel = _stored("message", {"team_id": TEAM, "channel_id": CHANNEL})

    heard = _heard(thread_ts=THREAD)

    assert set(heard) == {"message", "message.thread"}
    assert _contains(thread, heard["message.thread"].match or {})
    assert _contains(channel, heard["message"].match or {})


def test_a_message_that_starts_no_thread_is_no_thread_reply():
    thread = _stored(
        "message.thread",
        {"team_id": TEAM, "channel_id": CHANNEL, "thread_ts": THREAD},
    )

    heard = _heard()

    assert "message.thread" not in heard
    assert not _contains(thread, heard["message"].match or {})


def test_a_reply_in_another_thread_does_not_match():
    stored = _stored(
        "message.thread",
        {"team_id": TEAM, "channel_id": CHANNEL, "thread_ts": THREAD},
    )

    heard = _heard(thread_ts="1700000000.999999")

    assert not _contains(stored, heard["message.thread"].match or {})


def test_an_app_mention_matches_only_mention_schedules():
    mention = _stored("app_mention", {"team_id": TEAM, "channel_id": CHANNEL})
    channel = _stored("message", {"team_id": TEAM, "channel_id": CHANNEL})

    heard = _heard(type="app_mention", text="<@U0BOT> page the on-call")

    assert set(heard) == {"app_mention"}
    assert _contains(mention, heard["app_mention"].match or {})
    assert not _contains(channel, heard["app_mention"].match or {})


def test_a_message_in_another_channel_does_not_match():
    stored = _stored("message", {"team_id": TEAM, "channel_id": "C0ELSEWHERE"})

    assert not _contains(stored, _heard()["message"].match or {})


def test_a_retry_and_a_second_bot_name_the_same_event():
    """Slack retries with the same event id; a second app in the channel is
    sent its own event id for the same message. Both are one fire."""
    first = _heard()["message"]
    again = _heard()["message"]

    assert first.source_event_id == again.source_event_id
    assert (
        first.source_event_id
        != _heard(ts="1700000100.000300")["message"].source_event_id
    )


def test_the_run_is_not_handed_the_apps_verification_token():
    heard = _heard()["message"]

    assert "token" not in heard.payload
    assert heard.payload["event"]["text"] == "Database failover started"
    assert heard.pod_ids == frozenset({POD})


def test_the_ids_an_author_typed_are_stored_trimmed():
    stored = _stored("message", {"team_id": f" {TEAM}", "channel_id": f"{CHANNEL} "})

    assert stored["team_id"] == TEAM and stored["channel_id"] == CHANNEL


@pytest.mark.parametrize(
    ("event_type", "config"),
    [
        ("message", {"team_id": TEAM}),
        ("app_mention", {"channel_id": CHANNEL}),
        ("message.thread", {"team_id": TEAM, "channel_id": CHANNEL}),
    ],
)
def test_a_trigger_missing_what_it_fires_on_is_refused(event_type, config):
    """The catalog marks these required and nothing else reads its schema: a
    schedule stored without one would never fire."""
    with pytest.raises(ScheduleValidationError):
        _slack_binding(
            TriggerBinding(connector_id="slack", event_type=event_type), config
        )
