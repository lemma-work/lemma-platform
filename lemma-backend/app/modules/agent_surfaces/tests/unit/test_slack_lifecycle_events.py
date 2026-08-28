from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.entities import SurfaceLifecycleKind
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

pytestmark = pytest.mark.asyncio

_BOT = "U_BOT"


def _joined(*, user: str, inviter: str | None = "U_HUMAN") -> dict:
    event: dict = {
        "type": "member_joined_channel",
        "user": user,
        "channel": "C_SALES",
        "channel_type": "C",
    }
    if inviter is not None:
        event["inviter"] = inviter
    return {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": _BOT, "is_bot": True}],
        "event": event,
    }


async def test_bot_joining_a_channel_is_a_setup_moment_naming_the_inviter():
    """The invite is the trigger, and the inviter is who to ask."""
    parsed = SlackMessageParser().parse_lifecycle(_joined(user=_BOT))

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.JOINED_CHANNEL
    assert parsed.external_channel_id == "C_SALES"
    assert parsed.actor_external_user_id == "U_HUMAN"
    assert parsed.tenant_id == "T1"


async def test_a_colleague_joining_is_not_our_business():
    """member_joined_channel fires for every human too — only the bot counts."""
    assert SlackMessageParser().parse_lifecycle(_joined(user="U_SOMEONE")) is None


async def test_bot_joining_without_a_recorded_inviter_is_still_a_join():
    """Joining a channel via chat:write.public has no inviter; don't drop it."""
    parsed = SlackMessageParser().parse_lifecycle(_joined(user=_BOT, inviter=None))

    assert parsed is not None
    assert parsed.actor_external_user_id is None


async def test_app_home_opened_is_a_lifecycle_event_naming_the_viewer():
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": _BOT}],
        "event": {"type": "app_home_opened", "user": "U_HUMAN", "channel": "D1"},
    }
    parsed = SlackMessageParser().parse_lifecycle(payload)

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.HOME_OPENED
    assert parsed.actor_external_user_id == "U_HUMAN"


async def test_a_message_is_not_a_lifecycle_event_and_vice_versa():
    """The two contracts must not overlap, or one event does two things."""
    parser = SlackMessageParser()
    message_payload = {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": _BOT}],
        "event": {
            "type": "message",
            "user": "U_HUMAN",
            "channel": "C_SALES",
            "text": "hello",
            "ts": "100.0",
        },
    }

    assert parser.parse_lifecycle(message_payload) is None
    assert parser.parse(message_payload) is not None
    assert parser.parse(_joined(user=_BOT)) is None


async def test_lifecycle_parsing_never_raises_on_malformed_payloads():
    """A surprise payload must not take down the webhook."""
    parser = SlackMessageParser()
    assert parser.parse_lifecycle({}) is None
    assert parser.parse_lifecycle({"type": "event_callback"}) is None
    assert (
        parser.parse_lifecycle(
            {"type": "event_callback", "event": {"type": "member_joined_channel"}}
        )
        is None
    )


async def test_setup_prompt_is_ephemeral_to_the_inviter(monkeypatch):
    """Setup is a conversation with one person, not channel noise."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.modules.agent_surfaces.platforms.slack.blocks import (
        CHANNEL_SETUP_ACTION_ID,
    )
    from app.modules.agent_surfaces.platforms.slack.home import SlackHomeSurface

    sent: list[dict] = []
    posted: list[dict] = []

    async def fake_ephemeral(self, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    async def fake_post(self, **kwargs):  # pragma: no cover - must not run
        posted.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postEphemeral", fake_ephemeral)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_post)

    home = SlackHomeSurface(credentials={"access_token": "xoxb-test"})
    delivered = await home.send_channel_setup_prompt(
        channel_id="C_SALES", user_id="U_HUMAN", channel_name="sales"
    )

    assert delivered is True
    assert sent[0]["user"] == "U_HUMAN"
    assert sent[0]["channel"] == "C_SALES"
    assert posted == []  # never visible to the channel
    button = sent[0]["blocks"][1]["elements"][0]
    assert button["action_id"] == CHANNEL_SETUP_ACTION_ID
    # The button carries the channel it configures, for the modal that follows.
    assert button["value"] == "C_SALES"


async def test_setup_prompt_needs_somebody_to_ask(monkeypatch):
    """chat:write.public self-joins have no inviter — stay silent."""
    from app.modules.agent_surfaces.platforms.slack.home import SlackHomeSurface

    home = SlackHomeSurface(credentials={"access_token": "xoxb-test"})
    assert (
        await home.send_channel_setup_prompt(channel_id="C_SALES", user_id="") is False
    )


def _setup_button_payload() -> dict:
    return {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": "U_HUMAN"},
        "trigger_id": "trig-123",
        "channel": {"id": "C_SALES"},
        "actions": [
            {"action_id": "lemma_channel_setup", "value": "C_SALES", "type": "button"}
        ],
    }


def _submit_payload(selected: str) -> dict:
    return {
        "type": "view_submission",
        "team": {"id": "T1"},
        "user": {"id": "U_HUMAN"},
        "view": {
            "callback_id": "lemma_channel_setup_view",
            "private_metadata": "C_SALES",
            "state": {
                "values": {
                    "lemma_channel_agent": {
                        "lemma_channel_agent_select": {
                            "selected_option": {"value": selected}
                        }
                    }
                }
            },
        },
    }


async def test_setup_button_yields_the_trigger_needed_to_open_a_modal():
    """trigger_id expires in ~3s, so it must survive parsing intact."""
    parsed = SlackMessageParser().parse_channel_setup(_setup_button_payload())

    assert parsed == {
        "kind": "open",
        "trigger_id": "trig-123",
        "channel_id": "C_SALES",
        "tenant_id": "T1",
        "actor_external_user_id": "U_HUMAN",
        "surface_id": None,
    }


async def test_submitting_an_agent_names_it():
    parsed = SlackMessageParser().parse_channel_setup(_submit_payload("sales-agent"))

    assert parsed["kind"] == "submit"
    assert parsed["channel_id"] == "C_SALES"
    assert parsed["agent_name"] == "sales-agent"


async def test_pod_assistant_submits_as_no_named_agent():
    """The pod assistant *is* an empty agent name on the route, not a name."""
    parsed = SlackMessageParser().parse_channel_setup(
        _submit_payload("__pod_assistant__")
    )

    assert parsed["agent_name"] is None


async def test_unrelated_interactions_are_not_channel_setup():
    """An ask_user answer shares the transport but not the meaning."""
    parser = SlackMessageParser()
    assert parser.parse_channel_setup({"type": "block_actions", "actions": []}) is None
    assert (
        parser.parse_channel_setup(
            {"type": "view_submission", "view": {"callback_id": "something_else"}}
        )
        is None
    )
    assert parser.parse_channel_setup({}) is None


async def test_modal_offers_the_pod_assistant_first_then_agents():
    from app.modules.agent_surfaces.platforms.slack.blocks import channel_setup_modal

    view = channel_setup_modal(
        channel_id="C_SALES", channel_label="sales", agent_names=["a1", "a2"]
    )

    assert view["callback_id"] == "lemma_channel_setup_view"
    # A view_submission carries no channel of its own — it rides private_metadata.
    assert '"channel_id":"C_SALES"' in view["private_metadata"]
    options = view["blocks"][1]["element"]["options"]
    assert [o["value"] for o in options] == ["__pod_assistant__", "a1", "a2"]
    assert "#sales" in view["blocks"][0]["text"]["text"]


async def test_multi_pod_channel_choice_carries_the_surface_through_the_modal():

    from app.modules.agent_surfaces.platforms.slack.blocks import (
        channel_setup_prompt_blocks,
    )

    prompt = channel_setup_prompt_blocks(
        channel_id="C_SALES",
        surface_choices=[("Sales pod", "00000000-0000-0000-0000-000000000001")],
    )
    click = _setup_button_payload()
    click["actions"][0]["value"] = prompt[1]["elements"][0]["value"]
    opened = SlackMessageParser().parse_channel_setup(click)
    assert opened["surface_id"] == "00000000-0000-0000-0000-000000000001"

    from app.modules.agent_surfaces.platforms.slack.blocks import channel_setup_modal

    view = channel_setup_modal(
        channel_id="C_SALES",
        channel_label="sales",
        agent_names=["sales-agent"],
        surface_id=opened["surface_id"],
    )
    submitted_payload = _submit_payload("sales-agent")
    submitted_payload["view"]["private_metadata"] = view["private_metadata"]
    submitted = SlackMessageParser().parse_channel_setup(submitted_payload)
    assert submitted["channel_id"] == "C_SALES"
    assert submitted["surface_id"] == opened["surface_id"]


async def test_app_home_surface_selector_parses_an_explicit_choice():
    from app.modules.agent_surfaces.platforms.slack.home_blocks import app_home_view

    view = app_home_view(
        pod_name=None,
        dm_agent_name=None,
        channel_routes=[],
        surface_choices=[("Sales pod", "00000000-0000-0000-0000-000000000001")],
    )
    button = view["blocks"][2]["elements"][0]
    parsed = SlackMessageParser().parse_channel_setup(
        {
            "type": "block_actions",
            "team": {"id": "T1"},
            "user": {"id": "U_HUMAN"},
            "actions": [button],
        }
    )
    assert parsed == {
        "kind": "select_surface",
        "surface_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "T1",
        "actor_external_user_id": "U_HUMAN",
    }


async def test_pod_assistant_route_is_not_the_surface_default():
    """Picking the default responder must not silently mean "whatever agent3 is".

    It is a conversation with *no* agent. Storing it as an empty
    agent_name made it indistinguishable from an unconfigured route, so it fell
    through to ``surface.agent_id`` — and a channel set to the pod assistant
    kept answering as the surface's default agent.
    """
    from app.modules.agent_surfaces.domain.entities import SurfaceChannelRoute

    pod_assistant = SurfaceChannelRoute(channel_id="C1", use_pod_assistant=True)
    unconfigured = SurfaceChannelRoute(channel_id="C2")
    named = SurfaceChannelRoute(channel_id="C3", agent_name="sales-agent")

    # Three distinct states, not two.
    assert pod_assistant.use_pod_assistant is True
    assert pod_assistant.agent_name is None
    assert unconfigured.use_pod_assistant is False
    assert unconfigured.agent_name is None
    assert named.use_pod_assistant is False
    assert named.agent_name == "sales-agent"


async def test_home_dm_picker_round_trip_parses():
    parser = SlackMessageParser()
    opened = parser.parse_channel_setup(
        {
            "type": "block_actions",
            "team": {"id": "T1"},
            "user": {"id": "U_HUMAN"},
            "trigger_id": "trig-9",
            "actions": [{"action_id": "lemma_dm_agent_setup", "type": "button"}],
        }
    )
    assert opened == {
        "kind": "open_dm",
        "trigger_id": "trig-9",
        "tenant_id": "T1",
        "actor_external_user_id": "U_HUMAN",
    }

    submitted = parser.parse_channel_setup(
        {
            "type": "view_submission",
            "team": {"id": "T1"},
            "user": {"id": "U_HUMAN"},
            "view": {
                "callback_id": "lemma_dm_agent_view",
                "state": {
                    "values": {
                        "lemma_dm_agent": {
                            "lemma_dm_agent_select": {
                                "selected_option": {"value": "sales-agent"}
                            }
                        }
                    }
                },
            },
        }
    )
    assert submitted["kind"] == "submit_dm"
    assert submitted["agent_name"] == "sales-agent"
    assert submitted["actor_external_user_id"] == "U_HUMAN"


async def test_dm_picker_is_per_person_not_per_workspace():
    """Two people in one Slack can talk to different agents."""
    from app.modules.agent_surfaces.domain.entities import SurfaceSlackConfig

    config = SurfaceSlackConfig(dm_agent_by_user={"U_A": "agent-a"})
    assert config.agent_for_user("U_A") == "agent-a"
    assert config.agent_for_user("U_B") is None  # falls back to the surface default


async def test_home_lists_agents_and_apps():
    from app.modules.agent_surfaces.platforms.slack.home_blocks import app_home_view

    view = app_home_view(
        pod_name="Test1",
        dm_agent_name="agent3",
        channel_routes=[("C1", None)],
        agents=[("agent3", "Handles ops questions")],
        apps=[("Dashboard", "https://d.test")],
    )
    rendered = str(view)
    assert "Test1" in rendered
    assert "agent3" in rendered
    assert "Handles ops questions" in rendered
    assert "https://d.test" in rendered
    # The personal setting is directly actionable from the tab.
    assert "lemma_dm_agent_setup" in rendered


async def test_only_modal_opening_clicks_take_the_synchronous_fast_lane():
    """Regression for `expired_trigger_id`.

    Slack kills a trigger_id ~3s after the click. Everything else goes through
    a Redis queue and a worker, which is routinely slower than that — so the
    modal has to be opened inside the HTTP request instead. Only those clicks
    may take that path; anything else belongs on the queue.
    """
    from app.modules.agent_surfaces.api.controllers.webhook_ingest import (
        _opens_a_slack_modal,
    )

    def click(action_id: str) -> dict:
        return {"type": "block_actions", "actions": [{"action_id": action_id}]}

    assert _opens_a_slack_modal(click("lemma_dm_agent_setup")) is True
    assert _opens_a_slack_modal(click("lemma_channel_setup")) is True
    # An ask_user answer resumes a run; it is not time-critical.
    assert _opens_a_slack_modal(click("lemma_form_submit")) is False
    assert _opens_a_slack_modal({"type": "event_callback"}) is False
    assert _opens_a_slack_modal({}) is False


async def test_choosing_the_pod_assistant_is_distinct_from_never_choosing():
    """Third time this distinction bit: absence is not a choice.

    Deleting the entry made "I picked the pod assistant" identical to "I never
    picked", so the pick silently resolved to the surface's default agent — the
    Home tab kept saying `agent3` and DMs kept going there.
    """
    from app.modules.agent_surfaces.domain.entities import SurfaceSlackConfig

    picked_assistant = SurfaceSlackConfig(
        dm_agent_by_user={"U_A": SurfaceSlackConfig.POD_ASSISTANT}
    )
    picked_agent = SurfaceSlackConfig(dm_agent_by_user={"U_A": "sales-agent"})
    never_picked = SurfaceSlackConfig()

    assert picked_assistant.chose_pod_assistant("U_A") is True
    assert picked_assistant.agent_for_user("U_A") is None

    assert picked_agent.chose_pod_assistant("U_A") is False
    assert picked_agent.agent_for_user("U_A") == "sales-agent"

    assert never_picked.chose_pod_assistant("U_A") is False
    assert never_picked.agent_for_user("U_A") is None
    # ...and the two "None" answers above mean different things.
    assert picked_assistant.choice_for_user("U_A") != never_picked.choice_for_user(
        "U_A"
    )


async def test_pod_assistant_dm_does_not_wear_the_default_agents_name():
    """A person who picked the pod assistant must not be answered by `agent3`.

    Only *channel* routes were checked, so the per-person DM choice fell through
    to the surface default and the reply was authored with that agent's name
    and avatar.
    """
    from types import SimpleNamespace
    from app.modules.agent_surfaces.domain.entities import (
        SurfaceConfig,
        SurfacePlatform,
        SurfaceSlackConfig,
    )
    from app.modules.agent_surfaces.services.ingress_service import (
        AgentSurfaceIngressService,
    )

    config = SurfaceConfig(
        slack=SurfaceSlackConfig(
            dm_agent_by_user={"U_A": SurfaceSlackConfig.POD_ASSISTANT}
        )
    )
    surface = SimpleNamespace(
        surface_type=SurfacePlatform.SLACK,
        config=config,
        channel_route_for=lambda **_: None,
    )
    chose = SimpleNamespace(
        surface=surface,
        link=SimpleNamespace(external_user_id="U_A", external_channel_id="D1"),
    )
    did_not = SimpleNamespace(
        surface=surface,
        link=SimpleNamespace(external_user_id="U_B", external_channel_id="D2"),
    )

    check = AgentSurfaceIngressService._routes_to_pod_assistant
    assert check(None, chose) is True
    assert check(None, did_not) is False


async def test_a_dedicated_bot_refuses_a_dm_picker_left_open_on_a_stale_home_tab():
    """Slack keeps a published Home tab until it is republished.

    So the "Change" button outlives the decision to make this bot one agent's
    own — anyone whose Home tab was rendered before it can still press it. The
    modal must not open there: it would offer a choice this bot cannot honour.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.agent_surfaces.domain.entities import SurfaceConfig
    from app.modules.agent_surfaces.services.surface_configuration import (
        SurfaceConfigurationMixin,
    )

    class _Service(SurfaceConfigurationMixin):
        def __init__(self):
            self._visible_agents = AsyncMock(return_value=[])

    surface = SimpleNamespace(
        config=SurfaceConfig.model_validate({"slack": {"dedicated_to_agent": True}})
    )
    adapter = AsyncMock()

    await _Service()._open_dm_setup(
        adapter, {}, {"trigger_id": "trig-9"}, surface, None
    )
    adapter.open_dm_agent_modal.assert_not_awaited()

    # ...and a modal already open cannot be submitted into dead storage either.
    await _Service()._submit_dm_setup(
        adapter, {}, {"agent_name": "someone-else"}, surface, None
    )
    adapter.publish_home_view.assert_not_awaited()
