"""Full surface agent e2e — real worker + system:lemma runtime.

Nothing is simulated except the inbound webhook payload (POSTed to the real,
auth-excluded webhook endpoint) and the external platform's HTTP API (a local
fake server that captures what the agent sends back — we cannot call the real
Telegram Bot API from a test). The production streaq worker subprocess runs the
agent through ``system:lemma``: deterministic FunctionModel by default, or the
real configured provider only when ``E2E_LLM_MODE=real`` is requested.

Run:

    uv run pytest \
        app/modules/agent_surfaces/tests/e2e/test_full_real_surface_e2e.py -m e2e

In real-LLM mode it skips automatically when system:lemma credentials are absent.
"""

from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import asyncio
import base64
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _load_teams_channel_mention_fixture,
    _messages_for_conversation,
    _wait_for_conversation_message,
    _resend_payload,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_resend_svix_headers,
    build_slack_signature_headers,
    build_telegram_secret_headers,
    build_whatsapp_signature_headers,
    slack_delivered,
    wait_for_messages,
    wait_for_slack_replies,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

# Worker + queued run round-trip. Real-LLM mode can be slower, so keep room.
# Must stay well under the shard's pytest-timeout (120s in CI), or a reply
# that never arrives kills the test before the wait returns — turning every
# delivery failure into an opaque "Timeout (>120.0s)" with no clue which
# assertion was waiting or what did arrive. A real worker round trip is
# seconds; this only has to outlast a slow runner, not the whole test.
REAL_REPLY_TIMEOUT = 60.0
_RESEND_SIGNING_SECRET = "whsec_" + base64.b64encode(
    b"resend-e2e-signing-secret"
).decode("ascii")


def _is_teams_text_message(item: dict) -> bool:
    body = item.get("body") or {}
    return body.get("type") == "message" and bool(str(body.get("text") or "").strip())


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        del account, config
        return f"surface-e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        del account, provider_id

    async def get_schedule(self, account, provider_id: str):
        del account, provider_id
        return None


async def _wait_for_composio_execution(
    server,
    *,
    tool_slug: str,
    timeout_seconds: float = REAL_REPLY_TIMEOUT,
) -> dict:
    async def probe() -> dict | None:
        return next(
            (
                execution
                for execution in reversed(server.executions)
                if execution["tool_slug"] == tool_slug
            ),
            None,
        )

    match = await eventually(
        label=f"Composio execution for {tool_slug}",
        probe=probe,
        done=lambda match: match is not None,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )
    assert match is not None
    return match


async def _create_system_lemma_agent(client: AsyncClient, pod_id: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Surface Greeter",
            "instruction": (
                "You are a helpful assistant on a chat surface. Reply with a "
                "short, friendly one or two sentence answer to the user."
            ),
            "agent_runtime": {"profile_id": "system:lemma"},
            "toolsets": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_telegram_webhook_surface_registers_and_replies_with_real_agent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker  # real streaq worker subprocess consuming surface events
    from app.core.config import settings as app_settings

    # Webhook registration requires a public HTTPS api_url; the worker reaches
    # the fake Telegram API via the account's api_base_url (no monkeypatch can
    # cross into the subprocess).
    monkeypatch.setattr(app_settings, "api_url", "https://surface-e2e.test")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "e2e-telegram-bot-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )

    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    surface_id = surface["id"]

    # Registration happened in-process during create: delete-then-set ordering,
    # verified by getWebhookInfo.
    assert fake_telegram.webhook_calls == ["deleteWebhook", "setWebhook"]

    # Map the Telegram sender to the pod-owning user so ingress resolves them.
    sender_id = 5550001
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    surface_row = await db_session.get(AgentSurface, UUID(surface_id))
    assert surface_row is not None and surface_row.webhook_secret
    secret = get_secret_cipher().decrypt_str(surface_row.webhook_secret)

    payload = _telegram_payload(text="Hi there!", message_id=1, sender_id=sender_id)
    response = await authenticated_client.post(
        f"/surfaces/{surface_id}/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers=build_telegram_secret_headers(secret),
    )
    assert response.status_code == 200, response.text

    # The worker ran the agent and the observer delivered one reply to the
    # fake Telegram API for the right chat.
    messages = await wait_for_messages(
        message_store, "TELEGRAM", min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert messages, "no Telegram reply was delivered by the real agent run"
    assert messages[-1]["chat_id"] == str(sender_id)
    assert (messages[-1].get("text") or "").strip()


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_telegram_webhook_multi_turn_reuses_conversation_with_real_agent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker  # real streaq worker subprocess consuming surface events
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://surface-e2e.test")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "e2e-telegram-bot-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    surface_id = surface["id"]
    sender_id = 5550002
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    surface_row = await db_session.get(AgentSurface, UUID(surface_id))
    assert surface_row is not None and surface_row.webhook_secret
    secret = get_secret_cipher().decrypt_str(surface_row.webhook_secret)

    async def _send(text: str, message_id: int) -> None:
        payload = _telegram_payload(
            text=text, message_id=message_id, sender_id=sender_id
        )
        resp = await authenticated_client.post(
            f"/surfaces/{surface_id}/webhook",
            content=json.dumps(payload).encode("utf-8"),
            headers=build_telegram_secret_headers(secret),
        )
        assert resp.status_code == 200, resp.text

    await _send("Hello!", 1)
    await wait_for_messages(
        message_store, "TELEGRAM", min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    await _send("And what can you help with?", 2)
    messages = await wait_for_messages(
        message_store, "TELEGRAM", min_count=2, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert len(messages) >= 2
    assert all(m["chat_id"] == str(sender_id) for m in messages)

    # Both turns landed in the same surface conversation (reused across turns).
    convo = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id=str(sender_id),
        agent_name=agent_name,
    )
    assert convo is not None


async def test_telegram_native_polling_reaches_outbox_worker_and_provider(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    test_redis_url,
    worker,
    monkeypatch,
):
    _ = worker
    from app.core.config import settings as app_settings
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent_surfaces.services.event_receiver_service import (
        SurfaceEventReceiverService,
    )

    monkeypatch.setattr(app_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(surface_settings, "enable_slack_socket_mode", False)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "e2e-native-polling-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TELEGRAM",
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        agent_name=agent_name,
    )
    sender_id = 5550099
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    fake_telegram.queue_update(
        _telegram_payload(
            text="Hello through native polling",
            message_id=99,
            sender_id=sender_id,
        )
    )

    receiver = SurfaceEventReceiverService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        scan_interval_seconds=0.05,
        redis_url=test_redis_url,
    )
    receiver_task = asyncio.create_task(receiver.run())
    try:
        messages = await wait_for_messages(
            message_store,
            "TELEGRAM",
            min_count=1,
            timeout_seconds=REAL_REPLY_TIMEOUT,
        )
        assert messages[-1]["chat_id"] == str(sender_id)
        assert str(messages[-1].get("text") or "").strip()
        polls = message_store.get_all("TELEGRAM_GET_UPDATES")
        assert polls and any(item["count"] == 1 for item in polls)
    finally:
        await receiver.stop()
        await asyncio.wait_for(receiver_task, timeout=10)

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id=str(sender_id),
    )
    assert conversation is not None


async def test_slack_signed_webhook_is_deduplicated_and_replies_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-e2e-secret")
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-surface-worker-e2e",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        agent_name=agent_name,
    )

    payload = _load_slack_dm_fixture(
        text="Reply through the real Slack worker path",
        ts="1700000000.901001",
    )
    raw_body = json.dumps(payload).encode("utf-8")
    headers = build_slack_signature_headers(
        raw_body=raw_body,
        signing_secret="slack-e2e-secret",
    )
    first = await authenticated_client.post(
        "/surfaces/webhooks/slack", content=raw_body, headers=headers
    )
    duplicate = await authenticated_client.post(
        "/surfaces/webhooks/slack", content=raw_body, headers=headers
    )
    assert first.status_code == duplicate.status_code == 200

    # Two identical webhooks, one reply — whichever way Slack carried it.
    replies = await wait_for_slack_replies(
        message_store, min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert len(replies) == 1, f"the duplicate was not deduplicated: {replies}"
    assert replies[0]["channel"] == "D0123456"
    assert "".join(slack_delivered(message_store)).strip()

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="1700000000.901001",
    )
    assert conversation is not None


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_slack_channel_attachment_and_history_are_persisted_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-e2e-secret")
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-slack-channel-e2e",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "SLACK",
            "account_id": str(account.id),
            "allowed_channel_ids": ["C-SUPPORT"],
        },
        agent_name=agent_name,
    )

    payload = _load_slack_dm_fixture(
        text="<@U0AGSSTQZLH> Review the attached customer brief",
        ts="1700000000.777002",
    )
    event = payload["event"]
    event.update(
        {
            "type": "app_mention",
            "channel": "C-SUPPORT",
            "channel_type": "channel",
            "files": [
                {
                    "id": "F-SURFACE-E2E",
                    "name": "slack-customer-brief.txt",
                    "mimetype": "text/plain",
                    "filetype": "txt",
                    "size": 34,
                }
            ],
        }
    )
    event.pop("assistant_thread", None)
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/slack",
        content=raw_body,
        headers=build_slack_signature_headers(
            raw_body=raw_body,
            signing_secret="slack-e2e-secret",
        ),
    )
    assert response.status_code == 200, response.text

    downloads = await wait_for_messages(
        message_store,
        "SLACK_FILE_DOWNLOAD",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    assert downloads[-1]["file_id"] == "F-SURFACE-E2E"
    assert downloads[-1]["_authorization"] == "Bearer xoxb-slack-channel-e2e"
    replies = await wait_for_messages(
        message_store,
        "SLACK_REPLIES",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    assert replies[-1]["channel"] == "C-SUPPORT"
    await wait_for_slack_replies(
        message_store, min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="1700000000.777002",
    )
    assert conversation is not None
    persisted = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=conversation["id"],
    )
    inbound = next(item for item in persisted if item["role"] == "user")
    metadata = inbound.get("metadata") or {}
    assert metadata["ingested_files"]
    assert [item["text"] for item in metadata["channel_context"]] == [
        "Thread root context",
        "Thread follow-up context",
    ]


async def test_slack_native_socket_receiver_acknowledges_and_replies_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.domain.entities import SurfacePlatform
    from app.modules.agent_surfaces.services.event_receiver_service import (
        NativeReceiverCandidate,
        SlackSocketReceiverRunner,
    )
    from slack_sdk.socket_mode import aiohttp as socket_mode_module

    monkeypatch.setattr(app_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_slack_socket_mode", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-native-socket-e2e",
            "app_token": "xapp-native-socket-e2e",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "SLACK",
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        agent_name=agent_name,
    )

    payload = _load_slack_dm_fixture(
        text="Hello through Slack Socket Mode",
        ts="1700000000.902001",
    )
    acknowledgements: list[str] = []

    class FakeSocketModeClient:
        def __init__(self, *, app_token, web_client):
            assert app_token == "xapp-native-socket-e2e"
            assert web_client is not None
            self.socket_mode_request_listeners = []

        async def send_socket_mode_response(self, response) -> None:
            acknowledgements.append(str(response.envelope_id))

        async def connect(self) -> None:
            request = SimpleNamespace(
                type="events_api",
                payload=payload,
                envelope_id="surface-e2e-envelope-1",
            )
            for listener in self.socket_mode_request_listeners:
                await listener(self, request)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        socket_mode_module,
        "SocketModeClient",
        FakeSocketModeClient,
    )
    runner = SlackSocketReceiverRunner(
        NativeReceiverCandidate(
            key=f"slack:{account.id}:native-e2e",
            platform=SurfacePlatform.SLACK,
            surface_ids=(UUID(surface["id"]),),
            credential_label=str(account.id),
            credentials={
                "app_token": "xapp-native-socket-e2e",
                "bot_token": "xoxb-native-socket-e2e",
            },
        )
    )
    runner_task = asyncio.create_task(runner.run())
    try:
        replies = await wait_for_slack_replies(
            message_store, min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
        )
        assert replies, "the socket receiver's run never replied to Slack"
        assert replies[-1]["channel"] == "D0123456"
        assert acknowledgements == ["surface-e2e-envelope-1"]
    finally:
        runner_task.cancel()
        await asyncio.gather(runner_task, return_exceptions=True)

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="1700000000.902001",
    )
    assert conversation is not None


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_whatsapp_account_webhook_replies_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="whatsapp",
        credentials={
            "access_token": "whatsapp-worker-token",
            "phone_number_id": "1234567890",
            "waba_id": "waba-worker-e2e",
            "app_secret": "whatsapp-worker-secret",
            "verify_token": "whatsapp-worker-verify",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "WHATSAPP",
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        agent_name=agent_name,
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550550123",
    )

    payload = _whatsapp_payload(
        text="Reply through the real WhatsApp worker path",
        message_id="wamid-worker-e2e-001",
        phone_number_id="1234567890",
        waba_id="waba-worker-e2e",
        sender_phone="15550550123",
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        f"/surfaces/{surface['id']}/webhook",
        content=raw_body,
        headers=build_whatsapp_signature_headers(
            raw_body=raw_body,
            app_secret="whatsapp-worker-secret",
        ),
    )
    assert response.status_code == 200, response.text

    messages = await wait_for_messages(
        message_store,
        "WHATSAPP",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
        predicate=lambda item: item.get("type") == "text",
    )
    final_messages = [item for item in messages if item.get("type") == "text"]
    assert final_messages
    assert final_messages[-1]["to"] == "15550550123"
    assert str(final_messages[-1]["text"].get("body") or "").strip()

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="15550550123@1234567890",
    )
    assert conversation is not None


async def test_whatsapp_document_is_downloaded_and_persisted_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="whatsapp",
        credentials={
            "access_token": "whatsapp-document-token",
            "phone_number_id": "1234567890",
            "waba_id": "waba-document-e2e",
            "app_secret": "whatsapp-document-secret",
            "verify_token": "whatsapp-document-verify",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "WHATSAPP",
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        agent_name=agent_name,
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550550456",
    )

    payload = _whatsapp_payload(
        text="",
        message_id="wamid-document-e2e-001",
        phone_number_id="1234567890",
        waba_id="waba-document-e2e",
        sender_phone="15550550456",
    )
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message.pop("text", None)
    message["type"] = "document"
    message["document"] = {
        "id": "whatsapp-media-e2e-001",
        "filename": "whatsapp-customer-brief.txt",
        "mime_type": "text/plain",
        "file_size": 36,
    }
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        f"/surfaces/{surface['id']}/webhook",
        content=raw_body,
        headers=build_whatsapp_signature_headers(
            raw_body=raw_body,
            app_secret="whatsapp-document-secret",
        ),
    )
    assert response.status_code == 200, response.text

    downloads = await wait_for_messages(
        message_store,
        "WHATSAPP_MEDIA_DOWNLOAD",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    assert downloads[-1]["media_id"] == "whatsapp-media-e2e-001"
    assert downloads[-1]["_authorization"] == "Bearer whatsapp-document-token"
    await wait_for_messages(
        message_store,
        "WHATSAPP",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="15550550456@1234567890",
    )
    assert conversation is not None
    inbound = await _wait_for_conversation_message(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=conversation["id"],
        predicate=lambda item: (
            item.get("role") == "user"
            and bool((item.get("metadata") or {}).get("ingested_files"))
        ),
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    assert (inbound.get("metadata") or {})["ingested_files"]


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_teams_authenticated_dm_replies_through_bot_framework_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    e2e_settings,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache

    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-account-token",
            "user_data": {"tenant_id": "1b5c589f-1718-42c8-8244-166fbe5dd8fc"},
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TEAMS", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    await _seed_external_user(
        db_session,
        platform="TEAMS",
        tenant_id="1b5c589f-1718-42c8-8244-166fbe5dd8fc",
        external_user_id="b20e77ef-bd6b-4636-9f5b-20dd28beba24",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # The production adapter checks its shared Redis OAuth cache before making
    # a network request. Seeding the Bot Framework token keeps this journey
    # deterministic while retaining the real adapter and HTTP delivery path.
    token_cache = RedisJsonCache(
        e2e_settings.redis_url,
        key_prefix="surface:teams-token",
        ttl_seconds=3600,
    )
    await token_cache.set_raw(
        "botframework.com:https://api.botframework.com/.default",
        "teams-bot-token",
    )
    await token_cache.close()

    payload = _load_teams_channel_mention_fixture(fake_teams)
    payload["id"] = "teams-dm-worker-001"
    payload["text"] = "Reply through the real Teams worker path"
    payload["conversation"] = {
        "conversationType": "personal",
        "tenantId": "1b5c589f-1718-42c8-8244-166fbe5dd8fc",
        "id": "teams-dm-conversation-e2e",
    }
    payload["channelData"] = {"tenant": {"id": "1b5c589f-1718-42c8-8244-166fbe5dd8fc"}}
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert response.status_code == 200, response.text

    messages = await wait_for_messages(
        message_store,
        "TEAMS",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
        predicate=_is_teams_text_message,
    )
    text_messages = [
        item["body"]
        for item in messages
        if item.get("body", {}).get("type") == "message"
        and str(item.get("body", {}).get("text") or "").strip()
    ]
    assert text_messages
    assert text_messages[-1]["text"].strip()

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="teams-dm-conversation-e2e",
    )
    assert conversation is not None


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_teams_channel_mention_ingests_attachment_and_channel_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    e2e_settings,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache

    tenant_id = "1b5c589f-1718-42c8-8244-166fbe5dd8fc"
    channel_id = "19:3b0dc498aeeb42abba81a2f6dd46ec67@thread.tacv2"
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-account-token",
            "graph_api_base_url": fake_teams.graph_base_url,
            "bot_service_base_url": fake_teams.service_url,
            "user_data": {"tenant_id": tenant_id},
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [channel_id],
        },
        agent_name=agent_name,
    )
    await _seed_external_user(
        db_session,
        platform="TEAMS",
        tenant_id=tenant_id,
        external_user_id="b20e77ef-bd6b-4636-9f5b-20dd28beba24",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    token_cache = RedisJsonCache(
        e2e_settings.redis_url,
        key_prefix="surface:teams-token",
        ttl_seconds=3600,
    )
    await token_cache.set_raw(
        "botframework.com:https://api.botframework.com/.default",
        "teams-bot-token",
    )
    await token_cache.set_raw(
        f"{tenant_id}:https://graph.microsoft.com/.default",
        "teams-graph-token",
    )
    await token_cache.close()

    payload = _load_teams_channel_mention_fixture(fake_teams)
    payload["attachments"].append(
        {
            "contentType": "application/vnd.microsoft.teams.file.download.info",
            "contentUrl": fake_teams.attachment_url("attachment-e2e-001"),
            "name": "customer-brief.txt",
            "content": {
                "downloadUrl": fake_teams.attachment_url("attachment-e2e-001"),
                "fileType": "txt",
                "fileSize": 36,
            },
        }
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert response.status_code == 200, response.text

    await wait_for_messages(
        message_store,
        "TEAMS_ATTACHMENT",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    graph_calls = await wait_for_messages(
        message_store,
        "TEAMS_GRAPH",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
    )
    assert graph_calls[-1]["channel_id"] == channel_id
    teams_messages = await wait_for_messages(
        message_store,
        "TEAMS",
        min_count=1,
        timeout_seconds=REAL_REPLY_TIMEOUT,
        predicate=_is_teams_text_message,
    )
    assert any(
        str(item.get("body", {}).get("text") or "").strip()
        for item in teams_messages
        if item.get("body", {}).get("type") == "message"
    )

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="1776236638028",
    )
    assert conversation is not None
    persisted = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=conversation["id"],
    )
    inbound = next(item for item in persisted if item["role"] == "user")
    metadata = inbound.get("metadata") or {}
    assert metadata["ingested_files"]
    assert metadata["channel_context"][0]["author"] == (
        "Earlier Participant (other participant)"
    )


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
@pytest.mark.slow
async def test_resend_signed_webhook_replies_via_worker(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker
    from app.core.config import settings as app_settings

    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    # The signing secret lives in core settings, not `SurfaceSettings` — the
    # Resend API key and sender identity were already there, and splitting one
    # provider's config across two Settings classes is what produced two
    # secrets, two API keys and two answers to "which domain do we send from".
    monkeypatch.setattr(
        app_settings,
        "resend_webhook_secret",
        _RESEND_SIGNING_SECRET,
    )

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-worker-token",
            "api_base_url": fake_resend.api_base,
        },
        email="surface@resend.test",
        provider=AuthProvider.LEMMA,
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    surface_row = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_row is not None and surface_row.surface_identity_email

    payload = _resend_payload(
        sender_email=fixed_test_user["email"],
        assistant_address=surface_row.surface_identity_email,
        message_id="resend-worker-e2e-001",
        text="Reply through the real Resend worker path",
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/resend",
        content=raw_body,
        headers=build_resend_svix_headers(
            raw_body=raw_body,
            signing_secret=_RESEND_SIGNING_SECRET,
            svix_id="resend-worker-e2e-001",
        ),
    )
    assert response.status_code == 200, response.text

    messages = await wait_for_messages(
        message_store, "RESEND", min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert messages[-1]["to"] == [fixed_test_user["email"]]
    assert str(messages[-1].get("text") or "").strip()

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent_name,
        external_thread_id="<resend-worker-e2e-001@resend-e2e.test>",
    )
    assert conversation is not None


# Platform variation, not a distinct journey: same ingress -> conversation
# -> worker -> outbound reply as the Slack webhook test, with a different
# payload shape. Nothing is deleted and no assertion is weakened -- this runs
# nightly in the protected lane instead of in front of the merge button.
