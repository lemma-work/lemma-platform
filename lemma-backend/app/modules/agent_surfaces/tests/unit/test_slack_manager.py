"""Lemma's own Slack app as the workspace's manager: what it reads and shows."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack.manager_parser import (
    ManagerInputKind,
    parse_manager_input,
)
from app.modules.agent_surfaces.services.slack_manager import is_manager_delivery
from app.modules.agent_surfaces.services.slack_manager_context import (
    person_link_url,
    teammate_name,
    verify_person_link,
)

pytestmark = pytest.mark.asyncio

LEMMA_APP = "A_LEMMA"


@pytest.fixture(autouse=True)
def deployment_app(monkeypatch):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.config.surface_settings.slack_app_id", LEMMA_APP
    )


def _event(event: dict, *, app: str = LEMMA_APP) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": app,
        "authorizations": [{"user_id": "U_MANAGER", "is_bot": True}],
        "event": event,
    }


# -- whose delivery is it -----------------------------------------------------


async def test_lemmas_app_with_no_teammate_receiving_is_the_manager():
    assert is_manager_delivery(_event({"type": "app_home_opened"}), [])


async def test_a_teammate_surface_receiving_is_not_the_manager():
    """Where Lemma's app is still a pod's bot, that pod keeps it."""
    assert not is_manager_delivery(_event({"type": "app_home_opened"}), [uuid4()])


async def test_an_unverified_fan_in_is_never_the_manager():
    assert not is_manager_delivery(_event({"type": "app_home_opened"}), None)


async def test_a_teammates_own_app_is_not_the_manager():
    assert not is_manager_delivery(
        _event({"type": "app_home_opened"}, app="A_OTHER"), []
    )


async def test_a_button_tap_names_its_app_too():
    payload = {"type": "block_actions", "api_app_id": LEMMA_APP, "team": {"id": "T1"}}
    assert is_manager_delivery(payload, [])


# -- what it receives ---------------------------------------------------------


async def test_home_opened_names_the_viewer():
    parsed = parse_manager_input(_event({"type": "app_home_opened", "user": "U1"}))
    assert (parsed.kind, parsed.team_id, parsed.user_id) == (
        ManagerInputKind.HOME_OPENED,
        "T1",
        "U1",
    )


async def test_its_own_invite_names_the_inviter_and_channel():
    parsed = parse_manager_input(
        _event(
            {
                "type": "member_joined_channel",
                "user": "U_MANAGER",
                "inviter": "U1",
                "channel": "C1",
            }
        )
    )
    assert parsed.kind is ManagerInputKind.JOINED_CHANNEL
    assert (parsed.user_id, parsed.channel_id) == ("U1", "C1")


async def test_a_colleague_joining_is_not_its_business():
    parsed = parse_manager_input(
        _event({"type": "member_joined_channel", "user": "U2", "channel": "C1"})
    )
    assert parsed.kind is ManagerInputKind.OTHER


async def test_a_dm_and_a_mention_are_told_who_answers():
    dm = parse_manager_input(
        _event({"type": "message", "channel_type": "im", "user": "U1", "channel": "D1"})
    )
    mention = parse_manager_input(
        _event({"type": "app_mention", "user": "U1", "channel": "C1"})
    )
    assert dm.kind is ManagerInputKind.DIRECT_MESSAGE
    assert mention.kind is ManagerInputKind.MENTIONED


async def test_a_bots_own_message_is_ignored():
    parsed = parse_manager_input(
        _event(
            {"type": "message", "channel_type": "im", "bot_id": "B1", "channel": "D1"}
        )
    )
    assert parsed.kind is ManagerInputKind.OTHER


async def test_removal_is_an_uninstall():
    assert (
        parse_manager_input(_event({"type": "app_uninstalled"})).kind
        is ManagerInputKind.UNINSTALLED
    )


async def test_a_member_revoking_their_own_sign_in_is_not_an_uninstall():
    """tokens_revoked names user tokens under ``oauth``; the manager keeps its bot."""
    parsed = parse_manager_input(
        _event({"type": "tokens_revoked", "tokens": {"oauth": ["U_MEMBER"]}})
    )
    assert parsed.kind is ManagerInputKind.OTHER


async def test_a_revoked_bot_token_names_the_bots_that_went():
    parsed = parse_manager_input(
        _event({"type": "tokens_revoked", "tokens": {"bot": ["U_MANAGER"]}})
    )
    assert parsed.kind is ManagerInputKind.UNINSTALLED
    assert parsed.revoked_bots == ("U_MANAGER",)
    assert parse_manager_input(_event({"type": "app_uninstalled"})).revoked_bots is None


async def test_add_to_slack_names_the_teammate_and_the_trigger():
    payload = {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "trigger_id": "trig",
        "actions": [{"action_id": views.ADD_ACTION_ID, "value": "pod-1"}],
    }
    parsed = parse_manager_input(payload)
    assert (parsed.kind, parsed.pod_id, parsed.trigger_id) == (
        ManagerInputKind.ADD_CLICKED,
        "pod-1",
        "trig",
    )


async def test_picking_a_teammate_carries_channel_and_surface():
    payload = {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "response_url": "https://hooks.slack.test/r",
        "actions": [
            {
                "action_id": f"{views.JOIN_ACTION_ID}:0",
                "value": json.dumps({"channel": "C1", "surface": "s-1"}),
            }
        ],
    }
    parsed = parse_manager_input(payload)
    assert parsed.kind is ManagerInputKind.JOIN_CLICKED
    assert (parsed.channel_id, parsed.surface_id, parsed.response_url) == (
        "C1",
        "s-1",
        "https://hooks.slack.test/r",
    )


def _submission(values: dict) -> dict:
    return {
        "type": "view_submission",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {
            "id": "V1",
            "callback_id": views.ADD_VIEW_CALLBACK_ID,
            "private_metadata": json.dumps({"pod": "pod-1"}),
            "state": {
                "values": {
                    block: {views.INPUT_ACTION_ID: {"value": value}}
                    for block, value in values.items()
                }
            },
        },
    }


async def test_the_add_form_reads_whichever_way_was_chosen():
    parsed = parse_manager_input(
        _submission(
            {
                views.CLIENT_ID_BLOCK_ID: "cid",
                views.CLIENT_SECRET_BLOCK_ID: "csec",
                views.SIGNING_SECRET_BLOCK_ID: "ssec",
            }
        )
    )
    assert parsed.kind is ManagerInputKind.ADD_SUBMITTED
    assert (parsed.view_id, parsed.pod_id) == ("V1", "pod-1")
    assert parsed.form.has_own_app and not parsed.form.refresh_token


async def test_half_an_own_app_is_noticed():
    parsed = parse_manager_input(_submission({views.CLIENT_ID_BLOCK_ID: "cid"}))
    assert parsed.form.has_part_of_own_app and not parsed.form.has_own_app


# -- proof of who someone is --------------------------------------------------


def _link_parts(url: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlsplit

    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def test_a_person_link_verifies_for_its_own_slack_user_only():
    parts = _link_parts(person_link_url("T1", "U1", now=1_000))
    good = {
        "team_id": "T1",
        "slack_user_id": "U1",
        "expires": int(parts["exp"]),
        "signature": parts["sig"],
    }
    assert verify_person_link(**good, now=1_000)
    assert not verify_person_link(**{**good, "slack_user_id": "U2"}, now=1_000)
    assert not verify_person_link(**{**good, "team_id": "T2"}, now=1_000)


async def test_a_person_link_expires():
    parts = _link_parts(person_link_url("T1", "U1", now=1_000))
    assert not verify_person_link(
        team_id="T1",
        slack_user_id="U1",
        expires=int(parts["exp"]),
        signature=parts["sig"],
        now=1_000 + 60 * 60,
    )


# -- what it shows ------------------------------------------------------------


async def test_teammate_names_read_as_the_web_app_shows_them():
    assert teammate_name("support-desk") == "Support desk"
    assert teammate_name("Ada") == "Ada"


async def test_the_roster_offers_add_only_to_who_is_not_in_slack():
    home = views.roster_home(
        team_name="Acme",
        teammates=[
            views.RosterEntry(
                pod_id="p1", name="Ada", bot_user_id="UADA", open_url="https://x"
            ),
            views.RosterEntry(pod_id="p2", name="Grace"),
        ],
        lemma_url="https://lemma.test",
    )
    adds = [
        block["accessory"]["value"]
        for block in home["blocks"]
        if block.get("accessory", {}).get("action_id") == views.ADD_ACTION_ID
    ]
    assert adds == ["p2"]
    assert any("<@UADA>" in str(block) for block in home["blocks"])


async def test_the_channel_picker_names_each_teammate_on_its_button():
    _, blocks = views.channel_picker(
        channel_id="C1", teammates=[("s1", "Ada"), ("s2", "Grace")]
    )
    buttons = blocks[-1]["elements"]
    assert [button["text"]["text"] for button in buttons] == ["Ada", "Grace"]
    assert len({button["action_id"] for button in buttons}) == 2


async def test_a_roster_that_leaves_teammates_off_says_how_many():
    home = views.roster_home(
        team_name="Acme",
        teammates=[views.RosterEntry(pod_id="p1", name="Ada")],
        lemma_url="https://lemma.test",
        more=3,
    )
    assert "3 more teammates are in Lemma." in str(home["blocks"])


async def test_a_long_picker_fits_slacks_rows_and_says_who_it_left_off():
    teammates = [(f"s{i}", f"Mate {i}") for i in range(103)]
    _, blocks = views.channel_picker(channel_id="C1", teammates=teammates)
    rows = [block for block in blocks if block["type"] == "actions"]
    assert all(len(row["elements"]) <= 25 for row in rows)
    action_ids = [button["action_id"] for row in rows for button in row["elements"]]
    assert len(action_ids) == len(set(action_ids)) == 100
    assert "3 more aren't listed" in str(blocks[-1])


async def test_with_nobody_in_slack_the_picker_says_where_to_add_one():
    text, blocks = views.channel_picker(channel_id="C1", teammates=[])
    assert "Home tab" in text and len(blocks) == 1


async def test_the_allow_view_falls_back_to_the_app_list_without_an_app_id():
    view = views.allow_modal(
        name="Ada",
        metadata={"pod": "p1"},
        authorize_url="https://slack.test/o",
        app_id="",
    )
    assert views.SLACK_APPS_URL + "|" in str(view)
