from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.entities import ConversationType, SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _messages_for_conversation,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e


_SLACK_CONFIG_ACCOUNT_CREDENTIALS = {
    "access_token": "xoxb-config-e2e",
    "scope": "assistant:write,chat:write.customize",
}


async def _slack_config_account(db_session, fixed_test_user, fake_slack):
    return await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            **_SLACK_CONFIG_ACCOUNT_CREDENTIALS,
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )


def _slack_channel_payload(*, text: str, channel_id: str, ts: str) -> dict:
    payload = _load_slack_dm_fixture(text=f"<@U0AGSSTQZLH> {text}", ts=ts)
    event = payload["event"]
    event["type"] = "app_mention"
    event["channel"] = channel_id
    event["channel_type"] = "channel"
    event.pop("assistant_thread", None)
    return payload


async def test_slack_identity_policy_blocks_then_allows_sender_domain(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A restricted surface sends setup guidance without running the agent;
    widening the allow-list to the sender's domain lets the chat through."""
    from app.core.config import settings as app_settings
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-identity-policy",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    _, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    restricted = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"identity": {"allowed_domains": ["blocked.example"]}}},
    )
    assert restricted.status_code == 200, restricted.text

    blocked_payload = _load_slack_dm_fixture(
        text="Should be rejected by identity policy",
        ts="1700000000.300300",
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    blocked_context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="slack", payload=blocked_payload, headers={}
        )
    )
    assert isinstance(blocked_context, SurfaceReplyContext)
    assert blocked_context.reply_kind == "surface_setup"

    sender_domain = fixed_test_user["email"].rsplit("@", 1)[-1]
    allowed = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"identity": {"allowed_domains": [sender_domain]}}},
    )
    assert allowed.status_code == 200, allowed.text

    allowed_payload = _load_slack_dm_fixture(
        text="Allowed after widening the domain policy",
        ts="1700000000.300301",
    )
    allowed_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=allowed_payload, headers={}
        ),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(allowed_context, SurfaceChatContext)
    assert allowed_context.surface_id == UUID(surface["id"])

    # Across appends, not within one: the token buffer flushes on a size *or*
    # time trigger, so the answer can be split at an arbitrary character.
    delivered = await wait_for_slack_text(message_store, "E2E agent reply [SLACK]")
    assert any("E2E agent reply [SLACK]" in text for text in delivered), delivered


async def test_slack_dm_and_channel_surfaces_route_through_shared_webhook(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-slack-e2e",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    dm_agent, dm_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    channel_agent = await _create_agent(
        authenticated_client,
        pod_id,
    )
    route_update = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={
            "config": {
                "channels": [
                    {
                        "channel_id": "C-SUPPORT",
                        "agent_name": channel_agent["name"],
                    }
                ]
            }
        },
    )
    assert route_update.status_code == 200, route_update.text

    dm_payload = _load_slack_dm_fixture(
        text="Hello from Slack DM",
        ts="1700000000.100100",
    )
    raw_body = json.dumps(dm_payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/slack",
        content=raw_body,
        headers=build_slack_signature_headers(
            raw_body=raw_body,
            signing_secret="slack-secret",
        ),
    )
    assert response.status_code == 200, response.text

    dm_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(dm_context, SurfaceChatContext)
    assert dm_context.surface_id == UUID(dm_surface["id"])

    channel_payload = _slack_channel_payload(
        text="Need help in channel",
        channel_id="C-SUPPORT",
        ts="1700000000.200200",
    )
    channel_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=channel_payload,
            headers={},
        ),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(channel_context, SurfaceChatContext)
    assert channel_context.surface_id == UUID(dm_surface["id"])

    dm_conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=dm_agent["name"],
        external_thread_id="1700000000.100100",
    )
    assert dm_conversation is not None
    channel_conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=channel_agent["name"],
        external_thread_id="1700000000.200200",
    )
    assert channel_conversation is not None

    channel_messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=channel_conversation["id"],
    )
    assert "E2E agent reply [SLACK]" in channel_messages[-1]["text"]

    stream_starts = await wait_for_messages(
        message_store, "SLACK_STREAM_START", min_count=2
    )
    assert stream_starts[-2]["channel"] == "D0123456"
    assert stream_starts[-1]["channel"] == "C-SUPPORT"
    delivered = await wait_for_slack_text(message_store, "E2E agent reply [SLACK]")
    assert any("E2E agent reply [SLACK]" in text for text in delivered), delivered


async def test_slack_channel_mention_injects_recent_thread_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A Slack channel mention fetches the recent thread messages and hands them
    to the agent as background context (continuity in a shared thread)."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")

    async def fake_replies(self, *, channel, ts, limit, **kwargs):
        return {
            "ok": True,
            "messages": [
                {
                    "user": "U-ALICE",
                    "text": "Can someone summarize the incident?",
                    "ts": "1700000000.100100",
                },
                {
                    "user": "U-BOB",
                    "text": "It started around 2pm after the deploy.",
                    "ts": "1700000000.150150",
                },
            ],
        }

    monkeypatch.setattr(AsyncWebClient, "conversations_replies", fake_replies)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-ctx-e2e",
            "scope": "chat:write,channels:history",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    route_update = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"channels": [{"channel_id": "C-SUPPORT"}]}},
    )
    assert route_update.status_code == 200, route_update.text

    channel_payload = _slack_channel_payload(
        text="what happened during the incident?",
        channel_id="C-SUPPORT",
        ts="1700000000.300300",
    )
    pod_id_str = pod_id
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=channel_payload, headers={}
        ),
        script=[script_text("noted")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id_str,
        conversation_id=str(context.conversation_id),
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    channel_context = (user_message.get("metadata") or {}).get("channel_context")
    assert channel_context, channel_context
    assert any("incident" in (m.get("text") or "") for m in channel_context)
    assert any("2pm after the deploy" in (m.get("text") or "") for m in channel_context)


async def test_slack_channel_setup_modal_open_then_submit_routes_channel(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """The "Choose who answers" button opens the real modal (live agent
    names), and submitting it persists a channel route that a later mention
    actually uses -- the full ``surface_configuration.py`` dispatch, not just
    a direct DB write."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import build_surface_event_handler

    pod_id = test_pod["id"]
    account = await _slack_config_account(db_session, fixed_test_user, fake_slack)
    _, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    specialist = await _create_agent(
        authenticated_client, pod_id, name="Specialist Agent"
    )

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))

    open_payload = {
        "type": "block_actions",
        "trigger_id": "trigger-channel-setup-1",
        "team": {"id": "T0123456"},
        "user": {"id": "U0123456"},
        "channel": {"id": "C-SUPPORT"},
        "actions": [{"action_id": "lemma_channel_setup", "value": "C-SUPPORT"}],
    }
    handled = await handler.try_handle_channel_setup(
        SurfacePlatformWebhookIngress(source="slack", payload=open_payload, headers={})
    )
    assert handled is True

    opens = await wait_for_messages(message_store, "SLACK_VIEWS_OPEN", min_count=1)
    view_repr = str(opens[-1]["view"])
    assert "lemma_channel_setup_view" in view_repr
    assert "C-SUPPORT" in view_repr
    assert specialist["name"] in view_repr
    assert "Pod assistant" in view_repr

    submit_payload = {
        "type": "view_submission",
        "team": {"id": "T0123456"},
        "user": {"id": "U0123456"},
        "view": {
            "callback_id": "lemma_channel_setup_view",
            "private_metadata": json.dumps(
                {"channel_id": "C-SUPPORT", "surface_id": str(surface["id"])}
            ),
            "state": {
                "values": {
                    "lemma_channel_agent": {
                        "lemma_channel_agent_select": {
                            "selected_option": {"value": specialist["name"]}
                        }
                    }
                }
            },
        },
    }
    submitted = await handler.try_handle_channel_setup(
        SurfacePlatformWebhookIngress(
            source="slack", payload=submit_payload, headers={}
        )
    )
    assert submitted is True

    updated = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/{surface['name']}"
    )
    assert updated.status_code == 200, updated.text
    routes = updated.json()["config"]["channels"]
    assert any(
        route["channel_id"] == "C-SUPPORT" and route["agent_name"] == specialist["name"]
        for route in routes
    ), routes

    # Prove the route is live, not merely persisted: a mention in that
    # channel now reaches the specialist agent rather than the surface
    # default.
    channel_payload = _slack_channel_payload(
        text="need the specialist",
        channel_id="C-SUPPORT",
        ts="1700000000.400400",
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=channel_payload, headers={}
        ),
        script=[script_text("On it [SPECIALIST]")],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=specialist["name"],
        external_thread_id="1700000000.400400",
    )
    assert conversation is not None


async def test_slack_dm_agent_modal_open_then_submit_routes_dm(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """The DM "who answers you?" modal opens with live agent names, and
    submitting it changes only *this* person's DM routing -- proven by a
    follow-up DM actually reaching the chosen agent."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import build_surface_event_handler

    pod_id = test_pod["id"]
    account = await _slack_config_account(db_session, fixed_test_user, fake_slack)
    _, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    specialist = await _create_agent(
        authenticated_client, pod_id, name="DM Specialist Agent"
    )

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))

    open_payload = {
        "type": "block_actions",
        "trigger_id": "trigger-dm-setup-1",
        "team": {"id": "T0123456"},
        "user": {"id": "U0123456"},
        "actions": [{"action_id": "lemma_dm_agent_setup"}],
    }
    handled = await handler.try_handle_channel_setup(
        SurfacePlatformWebhookIngress(source="slack", payload=open_payload, headers={})
    )
    assert handled is True

    opens = await wait_for_messages(message_store, "SLACK_VIEWS_OPEN", min_count=1)
    view_repr = str(opens[-1]["view"])
    assert "lemma_dm_agent_view" in view_repr
    assert specialist["name"] in view_repr

    submit_payload = {
        "type": "view_submission",
        "team": {"id": "T0123456"},
        "user": {"id": "U0123456"},
        "view": {
            "callback_id": "lemma_dm_agent_view",
            "private_metadata": str(surface["id"]),
            "state": {
                "values": {
                    "lemma_dm_agent": {
                        "lemma_dm_agent_select": {
                            "selected_option": {"value": specialist["name"]}
                        }
                    }
                }
            },
        },
    }
    submitted = await handler.try_handle_channel_setup(
        SurfacePlatformWebhookIngress(
            source="slack", payload=submit_payload, headers={}
        )
    )
    assert submitted is True

    # Submitting also republishes the Home tab for this viewer.
    publishes = await wait_for_messages(message_store, "SLACK_VIEWS_PUBLISH", min_count=1)
    assert specialist["name"] in str(publishes[-1]["view"])

    dm_payload = _load_slack_dm_fixture(
        text="hello after picking my agent", ts="1700000000.500500"
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[script_text("DM specialist here")],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=specialist["name"],
        external_thread_id="1700000000.500500",
    )
    assert conversation is not None


async def test_slack_home_tab_publishes_pod_and_agents(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """Opening App Home publishes a real view built from the pod's name and
    its visible agents (the lifecycle path, not the config-submit path)."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import build_surface_event_handler

    pod_id = test_pod["id"]
    account = await _slack_config_account(db_session, fixed_test_user, fake_slack)
    agent, _surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    home_payload = {
        "type": "event_callback",
        "team_id": "T0123456",
        "api_app_id": "A0123456",
        "event": {"type": "app_home_opened", "user": "U0123456", "channel": "D0123456"},
        "event_id": "EvHomeOpen0001",
        "authorizations": [
            {"team_id": "T0123456", "user_id": "U0AGSSTQZLH", "is_bot": True}
        ],
    }
    handled = await handler.try_handle_lifecycle(
        SurfacePlatformWebhookIngress(source="slack", payload=home_payload, headers={})
    )
    assert handled is True

    publishes = await wait_for_messages(message_store, "SLACK_VIEWS_PUBLISH", min_count=1)
    assert publishes[-1]["user_id"] == "U0123456"
    view_repr = str(publishes[-1]["view"])
    assert test_pod["name"] in view_repr
    assert agent["name"] in view_repr


async def test_slack_set_suggested_prompts_and_thread_title_require_assistant_scope(
    fake_slack,
    message_store,
):
    """``set_suggested_prompts``/``set_thread_title`` are complete, unit-tested
    features not yet wired into a live agent's send flow -- exercised here
    directly against ``SlackHomeSurface`` (real HTTP to ``fake_slack``), per
    their own contract: both require ``assistant:write`` and both apply only
    inside a DM thread."""
    from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
    from app.modules.agent_surfaces.platforms.slack.home import SlackHomeSurface

    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.SLACK,
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="T0123456",
        external_thread_id="1700000000.100100",
        sender_external_user_id="U0123456",
        message_text="hi",
        is_dm=True,
        reply_target={"channel": "D0123456", "thread_ts": "1700000000.100100"},
    )

    surface = SlackHomeSurface(
        credentials={
            "access_token": "xoxb-prompts-e2e",
            "scope": "assistant:write",
            "api_base_url": fake_slack.base_url,
        }
    )
    prompts_ok = await surface.set_suggested_prompts(
        event=event,
        prompts=[
            ("Summarize", "Summarize the latest report"),
            ("Draft", "Draft a reply"),
        ],
        title="Try asking",
    )
    assert prompts_ok is True
    title_ok = await surface.set_thread_title(
        event=event, title="Weekly report follow-up"
    )
    assert title_ok is True

    prompt_calls = message_store.get_all("SLACK_SUGGESTED_PROMPTS")
    assert prompt_calls[-1]["channel_id"] == "D0123456"
    assert prompt_calls[-1]["thread_ts"] == "1700000000.100100"
    assert "Summarize the latest report" in str(prompt_calls[-1]["prompts"])

    title_calls = message_store.get_all("SLACK_THREAD_TITLE")
    assert title_calls[-1]["channel_id"] == "D0123456"
    assert title_calls[-1]["title"] == "Weekly report follow-up"

    # Without `assistant:write`, both are silent no-ops -- an older-install
    # workspace keeps Slack's default thread naming and gets no chips.
    limited_surface = SlackHomeSurface(
        credentials={
            "access_token": "xoxb-prompts-e2e-2",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
        }
    )
    assert (
        await limited_surface.set_suggested_prompts(event=event, prompts=[("A", "B")])
        is False
    )
    assert (
        await limited_surface.set_thread_title(event=event, title="Should not apply")
        is False
    )


# ---------------------------------------------------------------------------
# SlackMessageParser direct coverage. Like TeamsMessageParser, this is a pure
# function of a raw Slack payload dict -> a parsed dataclass (or None): no
# fake-server round trip is needed to exercise its filtering/edge branches.
# ---------------------------------------------------------------------------


def test_slack_parser_parse_rejects_non_message_and_filtered_events():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    assert parser.parse({"type": "url_verification"}) is None
    assert (
        parser.parse(
            {"type": "event_callback", "event": {"type": "reaction_added"}}
        )
        is None
    )
    assert (
        parser.parse(
            {
                "type": "event_callback",
                "event": {"type": "message", "subtype": "message_changed"},
            }
        )
        is None
    )
    assert (
        parser.parse(
            {
                "type": "event_callback",
                "event": {"type": "message", "channel": "", "text": "hi"},
            }
        )
        is None
    )
    assert (
        parser.parse(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C1",
                    "text": "hi",
                    "ts": "",
                },
            }
        )
        is None
    )


def test_slack_parser_parse_logs_and_reraises_on_unexpected_shape():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    with pytest.raises(AttributeError):
        parser.parse({"type": "event_callback", "event": "not-a-dict"})


def test_slack_parser_lifecycle_member_joined_channel_branches():
    from app.modules.agent_surfaces.domain.entities import SurfaceLifecycleKind
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    # Not an event_callback at all.
    assert parser.parse_lifecycle({"type": "block_actions"}) is None

    # A colleague (not the bot itself) joining a channel is not ours to react to.
    someone_else_joined = {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": "BOTUSER1"}],
        "event": {
            "type": "member_joined_channel",
            "user": "SOMEONE_ELSE",
            "channel": "C-x",
        },
    }
    assert parser.parse_lifecycle(someone_else_joined) is None

    # The bot itself joined, but the event carries no channel id.
    bot_joined_no_channel = {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": "BOTUSER1"}],
        "event": {
            "type": "member_joined_channel",
            "user": "BOTUSER1",
            "channel": "",
        },
    }
    assert parser.parse_lifecycle(bot_joined_no_channel) is None

    # The bot itself joined a real channel -- a genuine setup moment.
    bot_joined = {
        "type": "event_callback",
        "team_id": "T1",
        "authorizations": [{"user_id": "BOTUSER1"}],
        "event": {
            "type": "member_joined_channel",
            "user": "BOTUSER1",
            "channel": "C-joined",
            "inviter": "U-inviter",
        },
    }
    lifecycle = parser.parse_lifecycle(bot_joined)
    assert lifecycle is not None
    assert lifecycle.kind == SurfaceLifecycleKind.JOINED_CHANNEL
    assert lifecycle.tenant_id == "T1"
    assert lifecycle.external_channel_id == "C-joined"
    assert lifecycle.actor_external_user_id == "U-inviter"

    # An unexpected payload shape fails soft (returns None) rather than raising.
    assert (
        parser.parse_lifecycle({"type": "event_callback", "event": "not-a-dict"})
        is None
    )


def test_slack_parser_authorized_bot_user_id_direct():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    assert (
        parser._authorized_bot_user_id(
            {
                "authorizations": [
                    "not-a-dict",
                    {"user_id": ""},
                    {"user_id": "BOT1"},
                ]
            }
        )
        == "BOT1"
    )
    assert parser._authorized_bot_user_id({"authorizations": []}) is None


def test_slack_parser_parse_interaction_edge_cases():
    from app.modules.agent_surfaces.platforms.slack.models import (
        SLACK_FORM_SUBMIT_ACTION_ID,
    )
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    # No approval/submit action among the block actions at all.
    assert (
        parser.parse_interaction(
            {"type": "block_actions", "actions": [{"action_id": "unrelated"}]}
        )
        is None
    )

    # A submit action carrying an empty callback (value) is not actionable.
    assert (
        parser.parse_interaction(
            {
                "type": "block_actions",
                "actions": [
                    {"action_id": SLACK_FORM_SUBMIT_ACTION_ID, "value": ""}
                ],
            }
        )
        is None
    )

    # An unexpected payload shape raises inside the try block; the handler
    # logs and fails soft with None.
    assert (
        parser.parse_interaction(
            {
                "type": "block_actions",
                "actions": [
                    {"action_id": SLACK_FORM_SUBMIT_ACTION_ID, "value": "cb-1"}
                ],
                "state": "not-a-dict",
            }
        )
        is None
    )


def test_slack_parser_normalize_context_message_edge_cases():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    assert parser.normalize_context_message(None) is None
    # Note: `_message_text` always returns a non-empty fallback ("[File
    # shared]") even for a blank message with no attachments, so the
    # `if not text: return None` guard below it can never actually fire via
    # this path -- it is dead code, not a case this test can reach.
    assert parser.normalize_context_message({"text": ""}) == {
        "text": "[File shared]"
    }
    with pytest.raises(AttributeError):
        parser.normalize_context_message(
            {"text": "hi", "user_profile": "not-a-dict"}
        )


def test_slack_parser_extract_file_attachments_edge_cases():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    results = parser.extract_file_attachments(
        {
            "files": [
                "not-a-dict",
                {
                    "id": "",
                    "url_private": "",
                    "url_private_download": "",
                    "permalink": "",
                    "name": "",
                    "title": "",
                },
                {"id": "F1", "name": "report.pdf"},
            ]
        }
    )

    assert len(results) == 1
    assert results[0].id == "F1"


def test_slack_parser_unwrap_payload_nested_shapes():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()
    inner = {"type": "event_callback", "event": {"type": "message"}}

    assert parser._unwrap_payload({"payload": inner}) is inner
    assert parser._unwrap_payload({"data": inner}) is inner


def test_slack_parser_extract_text_from_blocks_and_mentions():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()
    event = {
        "blocks": [
            {
                "elements": [
                    {
                        "elements": [
                            {"type": "text", "text": "Hello "},
                            {"type": "user", "user_id": "U123"},
                            {"type": "other"},
                        ]
                    }
                ]
            }
        ]
    }

    text = parser._extract_text_from_blocks(event)
    assert text == "Hello <@U123>"

    mentioned = parser._extract_mentioned_user_ids(event, "hi <@U999> there")
    assert mentioned == ["U999", "U123"]


def test_slack_parser_message_text_and_filename_from_url():
    from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser

    parser = SlackMessageParser()

    assert parser._message_text("", "") == "[File shared]"
    assert parser._message_text("", "attachment info") == "attachment info"

    assert (
        parser._filename_from_url("https://example.test/path/report.pdf")
        == "report.pdf"
    )
    assert parser._filename_from_url("") == ""


def test_slack_parser_extract_input_value_and_flatten_state():
    from app.modules.agent_surfaces.platforms.slack.parser import (
        _extract_slack_input_value,
        _flatten_block_state_values,
    )

    assert _extract_slack_input_value("not-a-dict") == "not-a-dict"
    assert _extract_slack_input_value({"value": "typed text"}) == "typed text"
    assert _extract_slack_input_value(
        {"selected_options": [{"value": "a"}, "not-a-dict", {"value": "b"}]}
    ) == ["a", "b"]
    assert (
        _extract_slack_input_value({"selected_date": "2024-01-01"}) == "2024-01-01"
    )
    assert _extract_slack_input_value({}) is None

    flattened = _flatten_block_state_values(
        {
            "block_empty": {},
            "block_bad": "not-a-dict",
            "block_ok": {"action1": {"value": "hello"}},
        }
    )
    assert flattened == {"block_ok": "hello"}
