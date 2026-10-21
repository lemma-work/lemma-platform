"""Whether a mirrored place is private, and who the platform says left it.

The payloads are the platforms' own documented shapes: Slack's Events API
envelopes (``app_mention`` carries no ``channel_type``; ``member_left_channel``,
``channel_left``, ``group_left``, ``channel_shared``, ``channel_unshared``), a
Teams ``conversationUpdate`` with ``membersRemoved``, and Telegram's
``left_chat_member`` service message and ``my_chat_member`` update.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.groups import GroupUpdateKind
from app.modules.agent_surfaces.domain.place_privacy import (
    PlacePrivacy,
    is_private_place,
    said_privacy,
)
from app.modules.agent_surfaces.platforms.slack.group_updates import (
    slack_group_updates,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.teams.group_updates import (
    teams_group_updates,
)
from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser
from app.modules.agent_surfaces.platforms.telegram.membership import (
    group_membership_updates,
)
from app.modules.agent_surfaces.services.inbound_classifier import classify

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"
TEAM = "T024BE7LD"
BOT = "U0LAN0Z89"


def _slack(event: dict[str, Any]) -> dict[str, Any]:
    """An Events API envelope, as Slack signs and sends it."""
    return {
        "token": "XXYYZZ",
        "team_id": TEAM,
        "api_app_id": "AXXXXXXXXX",
        "event": event,
        "type": "event_callback",
        "authorizations": [
            {
                "team_id": TEAM,
                "user_id": BOT,
                "is_bot": True,
                "is_enterprise_install": False,
            }
        ],
        "event_id": "Ev08MFMKH6",
        "event_time": 1234567890,
    }


def _app_mention_in(channel: str) -> dict[str, Any]:
    """Slack's documented ``app_mention``: no ``channel_type``."""
    return _slack(
        {
            "type": "app_mention",
            "user": "W021FGA1Z",
            "text": f"<@{BOT}> is it everything a river should be?",
            "ts": "1515449522.000016",
            "channel": channel,
            "event_ts": "1515449522000016",
        }
    )


# -- privacy -------------------------------------------------------------------


def test_an_app_mention_says_nothing_about_privacy_so_its_place_is_private():
    parsed = SlackMessageParser().parse(_app_mention_in("C0LAN2Q65"))

    assert parsed is not None
    assert said_privacy(SurfacePlatform.SLACK, parsed.metadata) is None
    assert is_private_place(SurfacePlatform.SLACK, parsed.metadata)


@pytest.mark.parametrize(
    ("channel_type", "said"),
    [
        ("channel", PlacePrivacy.PUBLIC),
        ("group", PlacePrivacy.PRIVATE),
        ("mpim", PlacePrivacy.PRIVATE),
        ("", None),
    ],
)
def test_a_slack_message_names_its_channel_type(channel_type, said):
    assert said_privacy(SurfacePlatform.SLACK, {"channel_type": channel_type}) is said


def _teams_channel_message() -> dict[str, Any]:
    raw = (FIXTURES / "teams_channel_mention_event.json").read_text()
    return json.loads(raw.replace("__FAKE_TEAMS_SERVICE_URL__", "https://smba.test"))


def test_a_teams_channel_message_names_no_membership_so_its_place_is_private():
    parsed = TeamsMessageParser().parse(_teams_channel_message())

    assert parsed is not None
    assert said_privacy(SurfacePlatform.TEAMS, parsed.metadata) is None
    assert is_private_place(SurfacePlatform.TEAMS, parsed.metadata)


@pytest.mark.parametrize(
    ("membership", "said"),
    [
        ("standard", PlacePrivacy.PUBLIC),
        ("private", PlacePrivacy.PRIVATE),
        ("shared", PlacePrivacy.PRIVATE),
    ],
)
def test_a_teams_channel_whose_membership_is_named_is_read_from_it(membership, said):
    activity = _teams_channel_message()
    activity["channelData"]["channel"]["membershipType"] = membership

    parsed = TeamsMessageParser().parse(activity)

    assert parsed is not None
    assert said_privacy(SurfacePlatform.TEAMS, parsed.metadata) is said


def test_whatever_another_platform_says_its_groups_are_private():
    assert is_private_place(SurfacePlatform.TELEGRAM, {"channel_type": "channel"})


# -- Slack: who left, and what the channel is now --------------------------------


def test_a_person_leaving_a_slack_channel_is_a_member_left():
    payload = _slack(
        {
            "type": "member_left_channel",
            "user": "W06GH7XHN",
            "channel": "C0698JE0H",
            "channel_type": "C",
            "team": TEAM,
        }
    )

    [update] = slack_group_updates(payload)

    assert update.kind is GroupUpdateKind.MEMBER_LEFT
    assert (update.external_channel_id, update.external_user_id) == (
        "C0698JE0H",
        "W06GH7XHN",
    )
    assert update.tenant_id == TEAM
    assert classify("SLACK", payload).platform_update


@pytest.mark.parametrize(
    "event",
    [
        {"type": "member_left_channel", "user": BOT, "channel": "C0698JE0H"},
        {"type": "channel_left", "channel": "C0698JE0H", "actor_id": "W123456789"},
        {"type": "group_left", "channel": "C0698JE0H", "actor_id": "W123456789"},
    ],
    ids=["member-left-naming-the-bot", "channel-left", "group-left"],
)
def test_the_bot_taken_out_of_a_slack_channel_is_a_bot_left(event):
    payload = _slack(event)

    [update] = slack_group_updates(payload)

    assert update.kind is GroupUpdateKind.BOT_LEFT
    assert update.external_user_id is None
    assert classify("SLACK", payload).platform_update


def test_the_bot_invited_back_is_a_bot_returned_and_a_colleague_joining_is_nothing():
    back = _slack({"type": "member_joined_channel", "user": BOT, "channel": "C1"})
    colleague = _slack(
        {"type": "member_joined_channel", "user": "W06GH7XHN", "channel": "C1"}
    )

    assert [u.kind for u in slack_group_updates(back)] == [GroupUpdateKind.BOT_RETURNED]
    assert slack_group_updates(colleague) == []


def test_a_slack_channel_shared_and_unshared():
    shared = _slack(
        {
            "type": "channel_shared",
            "connected_team_id": "E163Q94DX",
            "channel": "C123456789",
            "event_ts": "1561064063.001100",
        }
    )
    unshared = _slack(
        {
            "type": "channel_unshared",
            "previously_connected_team_id": "E163Q94DX",
            "channel": "C123456789",
            "is_ext_shared": False,
            "event_ts": "1561064063.001100",
        }
    )
    still_shared = copy.deepcopy(unshared)
    still_shared["event"]["is_ext_shared"] = True

    assert [u.kind for u in slack_group_updates(shared)] == [GroupUpdateKind.SHARED]
    assert [u.kind for u in slack_group_updates(unshared)] == [GroupUpdateKind.UNSHARED]
    assert slack_group_updates(still_shared) == []
    assert classify("SLACK", shared).platform_update


# -- Teams -----------------------------------------------------------------------


def _members_removed(*removed: dict[str, str]) -> dict[str, Any]:
    """A group chat's ``conversationUpdate``, as Bot Framework sends it."""
    return {
        "membersRemoved": list(removed),
        "type": "conversationUpdate",
        "timestamp": "2026-04-15T07:03:58.1046271Z",
        "id": "f:5e2f1b1e-0000-0000-0000-000000000000",
        "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "from": {
            "id": "29:1abc",
            "aadObjectId": "11111111-0000-0000-0000-000000000000",
        },
        "conversation": {
            "isGroup": True,
            "conversationType": "groupChat",
            "tenantId": "1b5c589f-1718-42c8-8244-166fbe5dd8fc",
            "id": "19:abc0group@thread.v2",
        },
        "recipient": {"id": "28:c3e201f2-a8ef-4026-bef4-d3da0153b191", "name": "bot"},
        "channelData": {"tenant": {"id": "1b5c589f-1718-42c8-8244-166fbe5dd8fc"}},
    }


def test_a_person_removed_from_a_teams_chat_is_named_by_their_aad_id():
    activity = _members_removed(
        {"id": "29:1xyz", "aadObjectId": "b20e77ef-bd6b-4636-9f5b-20dd28beba24"}
    )

    [update] = teams_group_updates(activity)

    assert update.kind is GroupUpdateKind.MEMBER_LEFT
    assert update.external_channel_id == "19:abc0group@thread.v2"
    assert update.external_user_id == "b20e77ef-bd6b-4636-9f5b-20dd28beba24"
    assert update.tenant_id == "1b5c589f-1718-42c8-8244-166fbe5dd8fc"
    assert classify("TEAMS", activity).platform_update


def test_the_bot_removed_from_a_teams_chat_is_a_bot_left():
    activity = _members_removed({"id": "28:c3e201f2-a8ef-4026-bef4-d3da0153b191"})

    assert [u.kind for u in teams_group_updates(activity)] == [GroupUpdateKind.BOT_LEFT]


# -- Telegram --------------------------------------------------------------------


def _telegram_group() -> dict[str, Any]:
    return {"id": -1009876543210, "title": "Launch crew", "type": "supergroup"}


def test_somebody_leaving_a_telegram_group_is_a_member_left():
    update = {
        "update_id": 10000,
        "message": {
            "message_id": 1365,
            "from": {"id": 1111111, "is_bot": False, "first_name": "Dana"},
            "chat": _telegram_group(),
            "date": 1441645532,
            "left_chat_member": {"id": 1111111, "is_bot": False, "first_name": "Dana"},
        },
    }

    [left] = group_membership_updates(update)

    assert left.kind is GroupUpdateKind.MEMBER_LEFT
    assert (left.external_channel_id, left.external_user_id) == (
        "-1009876543210",
        "1111111",
    )
    verdict = classify("TELEGRAM", update)
    assert verdict.platform_update and not verdict.message


def test_the_bot_kicked_from_a_telegram_group_is_a_bot_left():
    update = {
        "update_id": 10001,
        "my_chat_member": {
            "chat": _telegram_group(),
            "from": {"id": 1111111, "is_bot": False, "first_name": "Dana"},
            "date": 1441645532,
            "old_chat_member": {"user": {"id": 42, "is_bot": True}, "status": "member"},
            "new_chat_member": {"user": {"id": 42, "is_bot": True}, "status": "kicked"},
        },
    }

    assert [u.kind for u in group_membership_updates(update)] == [
        GroupUpdateKind.BOT_LEFT
    ]
