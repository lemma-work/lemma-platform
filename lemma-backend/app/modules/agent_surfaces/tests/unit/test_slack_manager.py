"""Lemma's own Slack app as the workspace's manager: what it reads and shows."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.slack_manager import ManagerInputKind
from app.modules.agent_surfaces.platforms.slack import manager_blocks as views
from app.modules.agent_surfaces.platforms.slack import (
    manager_parser,
)
from app.modules.agent_surfaces.services.slack_manager_context import sign_in_url


def parse_manager_input(payload: dict):
    """The ids the manager reads; the typed form is checked on its own."""
    return manager_parser.parse_manager_input(payload).input


pytestmark = pytest.mark.asyncio

LEMMA_APP = "A_LEMMA"
POD = uuid4()
AGENT = uuid4()
SURFACE = uuid4()


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
        "actions": [
            {
                "action_id": views.ADD_ACTION_ID,
                "value": views.teammate_value(POD, AGENT),
            }
        ],
    }
    parsed = parse_manager_input(payload)
    assert (parsed.kind, parsed.pod_id, parsed.agent_id, parsed.trigger_id) == (
        ManagerInputKind.ADD_CLICKED,
        POD,
        AGENT,
        "trig",
    )


async def test_picking_a_teammate_carries_channel_and_surface():
    payload = {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "response_url": "https://hooks.slack.test/r",
        "container": {"type": "message", "channel_id": "C1"},
        "actions": [
            {
                "action_id": f"{views.JOIN_ACTION_ID}:0",
                "value": json.dumps({"channel": "C1", "surface": str(SURFACE)}),
            }
        ],
    }
    parsed = parse_manager_input(payload)
    assert parsed.kind is ManagerInputKind.JOIN_CLICKED
    assert (
        parsed.channel_id,
        parsed.container_channel_id,
        parsed.surface_id,
        parsed.response_url,
    ) == ("C1", "C1", SURFACE, "https://hooks.slack.test/r")


def _submission(values: dict) -> dict:
    return {
        "type": "view_submission",
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {
            "id": "V1",
            "callback_id": views.ADD_VIEW_CALLBACK_ID,
            "private_metadata": json.dumps(views.modal_metadata(POD, AGENT)),
            "state": {
                "values": {
                    block: {views.INPUT_ACTION_ID: {"value": value}}
                    for block, value in values.items()
                }
            },
        },
    }


async def test_the_add_form_reads_whichever_way_was_chosen():
    read = manager_parser.parse_manager_input(
        _submission(
            {
                views.CLIENT_ID_BLOCK_ID: "cid",
                views.CLIENT_SECRET_BLOCK_ID: "csec",
                views.SIGNING_SECRET_BLOCK_ID: "ssec",
            }
        )
    )
    assert read.input.kind is ManagerInputKind.ADD_SUBMITTED
    assert (read.input.view_id, read.input.pod_id, read.input.agent_id) == (
        "V1",
        POD,
        AGENT,
    )
    assert read.form is not None
    assert read.form.has_own_app and not read.form.has_refresh_token


async def test_what_was_typed_never_rides_on_the_input_that_is_queued():
    """The ids are the work item; the secrets stay in the form, as SecretStr."""
    read = manager_parser.parse_manager_input(
        _submission(
            {
                views.TOKEN_BLOCK_ID: "xoxe-1-secret",
                views.CLIENT_SECRET_BLOCK_ID: "csec-secret",
            }
        )
    )
    queued = read.input.model_dump_json()
    assert "xoxe-1-secret" not in queued and "csec-secret" not in queued
    assert read.form is not None
    assert "xoxe-1-secret" not in repr(read.form)
    assert read.form.refresh_token.get_secret_value() == "xoxe-1-secret"


async def test_half_an_own_app_is_noticed():
    read = manager_parser.parse_manager_input(
        _submission({views.CLIENT_ID_BLOCK_ID: "cid"})
    )
    assert read.form is not None
    assert read.form.has_part_of_own_app and not read.form.has_own_app


async def test_its_own_leaving_is_read_and_dropped_not_passed_on():
    parsed = parse_manager_input(_event({"type": "channel_left", "channel": "C1"}))
    assert parsed.kind is ManagerInputKind.LEFT_CHANNEL


# -- the link a Home shows ----------------------------------------------------


async def test_the_home_link_names_whom_it_is_for_and_carries_no_proof():
    """Sign in with Slack is the proof; the link only says which Slack user."""
    url = sign_in_url("T1", "U1")
    assert url.endswith("/slack/connect?team=T1&user=U1")


# -- what it shows ------------------------------------------------------------


async def test_the_roster_offers_add_only_to_who_is_not_in_slack():
    home = views.roster_home(
        team_name="Acme",
        teammates=[
            views.RosterEntry(
                pod_id="p1", agent_id="p1", name="Ada", bot_user_id="UADA"
            ),
            views.RosterEntry(pod_id="p2", agent_id="a2", name="Grace"),
            views.RosterEntry(pod_id="p2", agent_id="p2", name="Grace & <co>"),
        ],
        lemma_url="https://lemma.test",
    )
    adds = [
        block["accessory"]["value"]
        for block in home["blocks"]
        if block.get("accessory", {}).get("action_id") == views.ADD_ACTION_ID
    ]
    # One button per agent: a pod's two agents are two teammates.
    assert adds == ["p2:a2", "p2:p2"]
    assert any("<@UADA>" in str(block) for block in home["blocks"])
    assert "Grace &amp; &lt;co&gt;" in str(home["blocks"])


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
        teammates=[views.RosterEntry(pod_id="p1", agent_id="p1", name="Ada")],
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
