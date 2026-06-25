from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    EmulatedAgentHarness,
    _conversation_by_external_thread,
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _messages_for_conversation,
    _process_ingress_and_emulate_reply,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_harnesses import (
    AskUserHarness,
    RecordPromptHarness,
    StreamingHarness,
    run_latest_agent_run,
)

pytestmark = pytest.mark.e2e


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
    """A surface restricted to another email domain ignores the sender; widening
    the allow-list to the sender's domain lets the chat through."""
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
    restricted = await authenticated_client.put(
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
    assert blocked_context is None

    sender_domain = fixed_test_user["email"].rsplit("@", 1)[-1]
    allowed = await authenticated_client.put(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"identity": {"allowed_domains": [sender_domain]}}},
    )
    assert allowed.status_code == 200, allowed.text

    harness = EmulatedAgentHarness()
    allowed_payload = _load_slack_dm_fixture(
        text="Allowed after widening the domain policy",
        ts="1700000000.300301",
    )
    allowed_context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=allowed_payload, headers={}
        ),
        harness,
    )
    assert isinstance(allowed_context, SurfaceChatContext)
    assert allowed_context.surface_id == UUID(surface["id"])

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "E2E agent reply [SLACK]" in slack_messages[-1]["text"]


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
    route_update = await authenticated_client.put(
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

    harness = EmulatedAgentHarness()
    dm_context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        harness,
    )
    assert isinstance(dm_context, SurfaceChatContext)
    assert dm_context.surface_id == UUID(dm_surface["id"])

    channel_payload = _slack_channel_payload(
        text="Need help in channel",
        channel_id="C-SUPPORT",
        ts="1700000000.200200",
    )
    channel_context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=channel_payload,
            headers={},
        ),
        harness,
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

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=2)
    assert slack_messages[-2]["channel"] == "D0123456"
    assert slack_messages[-1]["channel"] == "C-SUPPORT"
    assert "E2E agent reply [SLACK]" in slack_messages[-1]["text"]


def _slack_ask_user_submission_payload(
    *, callback_id: str, user_id: str, channel_id: str, header: str, label: str
) -> dict:
    """A Slack block_actions submission answering a native ask_user question.

    The native render keys the select by the question header (block_id) and uses
    the option label as its value, so the answer flattens to {header: label}.
    """
    return {
        "type": "block_actions",
        "user": {"id": user_id},
        "team": {"id": "T0123456"},
        "channel": {"id": channel_id},
        "container": {"message_ts": "1700000000.700700"},
        "message": {"ts": "1700000000.700700"},
        "actions": [
            {
                "action_id": "lemma_form_submit",
                "value": callback_id,
                "action_ts": "1700000000.700800",
            }
        ],
        "state": {
            "values": {
                header: {
                    header: {
                        "type": "static_select",
                        "selected_option": {"value": label},
                    }
                }
            }
        },
    }


async def test_slack_ask_user_renders_natively_then_resumes_with_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """ask_user renders as native Slack choices; a block_actions answer resumes
    the paused run with a structured AskUserResponse (not a plain message)."""
    import urllib.parse

    from app.core.config import settings as app_settings
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-ask-e2e",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )

    # Inbound DM establishes the conversation + link; the agent calls ask_user,
    # which pauses the run (WAITING) and renders the question on Slack.
    dm_payload = _load_slack_dm_fixture(text="which color?", ts="1700000000.600600")
    harness = AskUserHarness(
        questions=[
            {
                "question": "Pick a color",
                "header": "color",
                "options": [{"label": "Red"}, {"label": "Blue"}],
            }
        ]
    )
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)
    sender_id = dm_payload["event"]["user"]
    channel_id = dm_payload["event"]["channel"]

    # The question was rendered natively to Slack (a select keyed by header, with
    # the options as choices) — not delivered as a plain text answer.
    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    rendered = json.dumps(slack_messages)
    assert "Pick a color" in rendered
    assert "Blue" in rendered and "static_select" in rendered

    # The user answers via a native block_actions submission.
    submission = _slack_ask_user_submission_payload(
        callback_id=f"{conversation_id}|{harness.tool_call_id}",
        user_id=sender_id,
        channel_id=channel_id,
        header="color",
        label="Blue",
    )
    form_body = urllib.parse.urlencode({"payload": json.dumps(submission)}).encode(
        "utf-8"
    )
    headers = build_slack_signature_headers(
        raw_body=form_body, signing_secret="slack-secret"
    )
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    resp = await authenticated_client.post(
        "/surfaces/webhooks/slack", content=form_body, headers=headers
    )
    assert resp.status_code == 200, resp.text

    # Drive the interaction handler directly (no FastStream worker in e2e). This
    # resumes via the approval path, synthesizing the AskUserResponse.
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    handled = await handler.try_handle_interaction(
        SurfacePlatformWebhookIngress(source="slack", payload=submission, headers={})
    )
    assert handled is True
    await uow.commit()

    # The approval path created a fresh resume run; execute it with the same
    # harness, which now sees the synthesized answer in history.
    await run_latest_agent_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        harness=harness,
    )

    # The agent received a structured answer keyed by the question header.
    assert harness.resumed_answer is not None
    assert harness.resumed_answer.get("answers") == {"color": "Blue"}

    # No plain "submitted form" user message was injected (old behavior is gone).
    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    assert not any("Submitted form" in json.dumps(m) for m in messages)


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
    route_update = await authenticated_client.put(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"channels": [{"channel_id": "C-SUPPORT"}]}},
    )
    assert route_update.status_code == 200, route_update.text

    channel_payload = _slack_channel_payload(
        text="what happened during the incident?",
        channel_id="C-SUPPORT",
        ts="1700000000.300300",
    )
    harness = RecordPromptHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=channel_payload, headers={}
        ),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    assert harness.metadatas, "agent run did not record message metadata"
    channel_context = harness.metadatas[-1].get("channel_context")
    assert channel_context, channel_context
    assert any("incident" in (m.get("text") or "") for m in channel_context)
    assert any("2pm after the deploy" in (m.get("text") or "") for m in channel_context)


async def test_slack_streams_progress_via_chat_update(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """Tool activity streams as an edited Slack message (chat.update) and the
    placeholder is deleted before the final answer."""
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.services import progress_observer as _po

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    # Disable the inter-update throttle so both progress comments stream as edits.
    monkeypatch.setattr(_po, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-stream-e2e",
            "scope": "chat:write",
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

    dm_payload = _load_slack_dm_fixture(text="do some work", ts="1700000000.800800")
    harness = StreamingHarness(
        comments=["Searching the web", "Reading the results"],
        final_text="Here is the answer.",
    )
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)

    # A placeholder was posted then edited in place (chat.update), and removed
    # before the final answer (chat.delete).
    updates = await wait_for_messages(message_store, "SLACK_UPDATE", min_count=1)
    assert any("Reading the results" in json.dumps(u) for u in updates)
    deletes = await wait_for_messages(message_store, "SLACK_DELETE", min_count=1)
    assert deletes
    final = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Here is the answer." in final[-1]["text"]
