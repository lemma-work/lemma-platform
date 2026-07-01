from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from slack_sdk.web.async_client import AsyncWebClient

from app.core.authorization.context import ActorType, Context
from app.core.authorization.service import AuthorizationDataService
from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfaceMode,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)

pytestmark = pytest.mark.asyncio

_REPO_ROOT = Path(__file__).resolve().parents[5]
_SLACK_EVENTS_DIR = _REPO_ROOT / "exp" / "slack_events"
_FALLBACK_SAMPLE_EVENTS = {
    "user_dm.json": {
        "payload": {
            "type": "event_callback",
            "team_id": "T0123456",
            "authorizations": [{"user_id": "U0AGSSTQZLH"}],
            "event": {
                "type": "message",
                "user": "U0123456",
                "text": "Hello from sample DM",
                "channel": "D0123456",
                "channel_type": "im",
                "ts": "1700000000.000100",
            },
        }
    },
    "bot_mention.json": {
        "payload": {
            "type": "event_callback",
            "team_id": "T0123456",
            "authorizations": [{"user_id": "U0AGSSTQZLH"}],
            "event": {
                "type": "message",
                "user": "U0123456",
                "text": "<@U0AGSSTQZLH> hello from sample channel",
                "channel": "C0123456",
                "channel_type": "channel",
                "ts": "1700000000.000200",
            },
        }
    },
}


def _load_event_payload(filename: str) -> dict:
    path = _SLACK_EVENTS_DIR / filename
    if path.exists():
        return json.loads(path.read_text())["payload"]
    return _FALLBACK_SAMPLE_EVENTS[filename]["payload"]


async def _reply_stream(*chunks: str):
    for chunk in chunks:
        yield SimpleNamespace(type="token", data=chunk)


def _build_service(*, surface, conversation_service, monkeypatch):
    uow = SimpleNamespace(session=AsyncMock())
    surface_repository = AsyncMock()
    surface_repository.list_active_by_type.return_value = [surface]
    conversation_service.agent_repository.get = AsyncMock(
        return_value=SimpleNamespace(name="Slack Surface Assistant")
    )
    conversation_link_repository = AsyncMock()
    conversation_link_repository.get_by_external_thread.return_value = None
    conversation_link_repository.create.side_effect = lambda link: link
    service = AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository,
        conversation_link_repository=conversation_link_repository,
        conversation_service=conversation_service,
        connector_service=AsyncMock(),
        pod_membership_port=SimpleNamespace(
            get_user_pod_ids=AsyncMock(return_value=[surface.pod_id]),
            get_user_email=AsyncMock(return_value="sender@example.com"),
        ),
    )
    service.identity_service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=ResolvedSurfaceUser(
                internal_user_id=surface.agent_id,
                external_user_id="U-RESOLVED",
                email="sender@example.com",
                display_name="Sample Sender",
            )
        )
    )
    service._resolve_credentials = AsyncMock(
        return_value={
            "access_token": "xoxb-test",
            "scope": "assistant:write,chat:write.customize,reactions:write",
        }
    )
    service._resolve_credentials_from_context = AsyncMock(
        return_value={
            "access_token": "xoxb-test",
            "scope": "assistant:write,chat:write.customize,reactions:write",
        }
    )
    service._resolve_account_credentials = AsyncMock(return_value={})
    service.event_dedup_store = SimpleNamespace(
        claim_message=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        AuthorizationDataService,
        "build_user_context",
        AsyncMock(
            return_value=Context(
                actor_type=ActorType.USER,
                actor_id=str(surface.agent_id),
                user_id=surface.agent_id,
                pod_id=surface.pod_id,
                authorizer=AsyncMock(),
            )
        ),
    )
    return service


async def test_sample_slack_dm_event_runs_assistant_and_posts_reply(monkeypatch):
    payload = _load_event_payload("user_dm.json")
    event = payload["event"]
    sent_payloads: list[dict] = []
    status_updates: list[dict] = []

    async def fake_users_info(self, *, user: str):
        assert user == event["user"]
        return {
            "user": {
                "id": user,
                "profile": {
                    "email": "sender@example.com",
                    "display_name": "Sample Sender",
                },
            }
        }

    async def fake_chat_post_message(self, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True}

    async def fake_assistant_threads_set_status(self, **kwargs):
        status_updates.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "users_info", fake_users_info)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_chat_post_message)
    monkeypatch.setattr(
        AsyncWebClient,
        "assistant_threads_setStatus",
        fake_assistant_threads_set_status,
    )

    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type="SLACK",
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        external_workspace_id=payload["team_id"],
        surface_identity_id=payload["authorizations"][0]["user_id"],
        config=SurfaceConfig(),
        is_active=True,
    )
    conversation = Conversation(
        id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=surface.agent_id,
        title="Slack DM Conversation",
        metadata={},
    )
    conversation_service = AsyncMock()
    conversation_service.create_conversation.return_value = conversation
    conversation_service.add_user_message_and_start_run = AsyncMock()
    service = _build_service(
        surface=surface,
        conversation_service=conversation_service,
        monkeypatch=monkeypatch,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})
    )

    assert context is not None
    assert context.mode == "chat"
    assert context.message_text == event["text"]
    assert context.message_external_message_id == event["ts"]
    assert "external_context" not in context.message_metadata.event_metadata

    create_kwargs = conversation_service.create_conversation.await_args.kwargs
    assert create_kwargs["pod_id"] == surface.pod_id
    assert create_kwargs["agent_name"] == "Slack Surface Assistant"
    assert create_kwargs["metadata"]["surface_id"] == str(surface.id)
    assert create_kwargs["metadata"]["surface_platform"] == "SLACK"
    assert create_kwargs["metadata"]["external_thread_id"] == event["ts"]

    await service.execute_chat(context)

    conversation_service.add_user_message_and_start_run.assert_awaited_once()
    message_kwargs = conversation_service.add_user_message_and_start_run.await_args.kwargs
    assert message_kwargs["conversation_id"] == conversation.id
    assert message_kwargs["content"] == event["text"]
    assert status_updates == [
        {
            "channel_id": event["channel"],
            "thread_ts": event["ts"],
            "status": "is taking a look...",
            "loading_messages": ["Taking a look..."],
        }
    ]
    assert sent_payloads == []


async def test_sample_slack_app_mention_event_replies_in_thread(monkeypatch):
    payload = _load_event_payload("bot_mention.json")
    event = payload["event"]
    sent_payloads: list[dict] = []
    added_reactions: list[dict] = []

    async def fake_users_info(self, *, user: str):
        assert user == event["user"]
        return {
            "user": {
                "id": user,
                "profile": {
                    "email": "sender@example.com",
                    "display_name": "Mention Sender",
                },
            }
        }

    async def fake_chat_post_message(self, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True}

    async def fake_reactions_add(self, **kwargs):
        added_reactions.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "users_info", fake_users_info)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_chat_post_message)
    monkeypatch.setattr(AsyncWebClient, "reactions_add", fake_reactions_add)

    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type="SLACK",
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        external_workspace_id=payload["team_id"],
        external_channel_id=event["channel"],
        surface_identity_id=payload["authorizations"][0]["user_id"],
        config=SurfaceConfig(),
        is_active=True,
    )
    conversation = Conversation(
        id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=surface.agent_id,
        title="Slack Mention Conversation",
        metadata={},
    )
    conversation_service = AsyncMock()
    conversation_service.create_conversation.return_value = conversation
    conversation_service.add_user_message_and_start_run = AsyncMock()
    service = _build_service(
        surface=surface,
        conversation_service=conversation_service,
        monkeypatch=monkeypatch,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})
    )

    assert context is not None
    assert context.mode == "chat"
    assert context.message_text == event["text"]
    assert context.message_external_message_id == event["ts"]
    assert "external_context" not in context.message_metadata.event_metadata

    create_kwargs = conversation_service.create_conversation.await_args.kwargs
    assert create_kwargs["pod_id"] == surface.pod_id
    assert create_kwargs["agent_name"] == "Slack Surface Assistant"
    assert create_kwargs["metadata"]["external_thread_id"] == event["ts"]
    assert create_kwargs["metadata"]["external_channel_id"] == event["channel"]

    await service.execute_chat(context)

    conversation_service.add_user_message_and_start_run.assert_awaited_once()
    message_kwargs = conversation_service.add_user_message_and_start_run.await_args.kwargs
    assert message_kwargs["conversation_id"] == conversation.id
    assert message_kwargs["content"] == event["text"]
    assert added_reactions == [
        {
            "channel": event["channel"],
            "name": "eyes",
            "timestamp": event["ts"],
        }
    ]
    assert sent_payloads == []
