"""E2E tests for agent surfaces and emulated agent replies."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    HarnessKind,
    HarnessOptions,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    AgentRuntimeProfileModel,
    ConversationModel,
)
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.conversation_service import suppress_agent_run_enqueue
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    FakeGmailServer,
    FakeOutlookServer,
    FakeSlackServer,
    FakeTeamsServer,
    FakeTelegramServer,
    FakeWhatsAppServer,
    MockPlatformMessageStore,
)
from app.modules.identity.infrastructure.models.organization_models import OrganizationMember
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

pytestmark = pytest.mark.e2e


SurfaceContext = SurfaceChatContext | SurfaceReplyContext
FIXTURE_DIR = Path(__file__).with_name("fixtures")
REAL_TEAMS_CHANNEL_ID = "19:3b0dc498aeeb42abba81a2f6dd46ec67@thread.tacv2"
REAL_TEAMS_TENANT_ID = "1b5c589f-1718-42c8-8244-166fbe5dd8fc"
REAL_TEAMS_THREAD_ID = "1776236638028"
E2E_RUNTIME_PROFILE_NAME = "Surface E2E Runtime"
E2E_RUNTIME_MODEL_NAME = "surface-e2e-model"


class EmulatedAgentHarness:
    """Deterministic harness used by surface e2e tests."""

    kind = HarnessKind.LEMMA

    def __init__(self) -> None:
        self.contexts: list[AgentContext] = []
        self.toolset_counts: list[int] = []

    async def run(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: AgentContext,
        options: HarnessOptions,
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        del conversation
        self.contexts.append(ctx)
        self.toolset_counts.append(len(options.toolsets))
        prompt = _latest_user_text(messages)
        platform = getattr(ctx, "surface_platform", None) or "POD"
        reply = f"E2E agent reply [{platform}] from {agent.name}: {prompt}"

        yield AgentEvent(
            type=AgentEventType.TOKEN,
            data=reply[:12],
            agent_run_id=agent_run_id,
        )
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_text(
                reply,
                metadata={"is_final_answer": True, "emulated_surface_e2e": True},
            ),
            agent_run_id=agent_run_id,
        )
        yield AgentEvent(
            type=AgentEventType.COMPLETED,
            data={"conversation_id": str(ctx.conversation_id)},
            agent_run_id=agent_run_id,
        )


@pytest_asyncio.fixture
async def message_store():
    store = MockPlatformMessageStore()
    yield store
    store.clear()


@pytest_asyncio.fixture
async def fake_slack(message_store, fixed_test_user):
    server = FakeSlackServer(fixed_test_user["email"], message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_teams(message_store, fixed_test_user):
    server = FakeTeamsServer(fixed_test_user["email"], message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_whatsapp(message_store):
    server = FakeWhatsAppServer(message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_telegram(message_store):
    server = FakeTelegramServer(message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_gmail(message_store):
    server = FakeGmailServer(message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_outlook(message_store):
    server = FakeOutlookServer(message_store)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def fake_composio_email(message_store, monkeypatch):
    """Intercept Composio email operations so e2e exercises the email send path
    without calling the real Composio SDK.

    Email surfaces are Composio-backed: outbound replies go through
    ``execute_composio_operation`` (OUTLOOK_REPLY_EMAIL / GMAIL_REPLY_TO_THREAD).
    This records each operation to the message store (keyed by platform) and
    returns a success envelope.
    """
    import app.modules.agent_surfaces.platforms.gmail.service as gmail_service
    import app.modules.agent_surfaces.platforms.outlook.service as outlook_service

    async def _record(*, connector_id, operation_name, payload, credentials):
        del credentials
        channel = "OUTLOOK_REPLY" if str(connector_id) == "outlook" else "GMAIL_REPLY"
        message_store.add(
            channel,
            {
                "connector_id": connector_id,
                "operation_name": operation_name,
                "payload": payload,
            },
        )
        return {"id": f"{connector_id}-composio-msg"}

    monkeypatch.setattr(gmail_service, "execute_composio_operation", _record)
    monkeypatch.setattr(outlook_service, "execute_composio_operation", _record)
    return message_store


def _latest_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.role != MessageRole.USER.value:
            continue
        if message.kind == MessageKind.TEXT:
            return message.text or ""
        return message.text or ""
    return ""


def _load_json_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_slack_dm_fixture(
    *,
    text: str | None = None,
    ts: str | None = None,
    thread_ts: str | None = None,
) -> dict:
    payload = _load_json_fixture("slack_dm_event.json")
    event = payload["event"]
    if text is not None:
        event["text"] = text
    if ts is not None:
        event["ts"] = ts
        event["event_ts"] = ts
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    payload["event_id"] = f"Ev{uuid4().hex[:10]}"
    return payload


def _load_teams_channel_mention_fixture(fake_teams: FakeTeamsServer) -> dict:
    payload = _load_json_fixture("teams_channel_mention_event.json")
    payload["serviceUrl"] = fake_teams.service_url
    return payload


async def _create_agent(
    client: AsyncClient,
    pod_id: str,
    *,
    name: str | None = None,
) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": name or f"Surface Agent {uuid4().hex[:8]}",
            "instruction": "Reply briefly. Surface e2e will emulate the model.",
            "agent_runtime": {
                "profile_id": "system:fireworks",
                "model_name": "kimi-k2.6",
            },
            "toolsets": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_surface(
    client: AsyncClient,
    pod_id: str,
    *,
    config: dict,
    agent_name: str | None = None,
) -> dict:
    platform = str(config.get("type", "TELEGRAM")).upper()
    allowed_channel_ids = config.get("allowed_channel_ids") or []
    payload: dict[str, object] = {
        "config": {},
    }
    if config.get("account_id"):
        payload["account_id"] = config["account_id"]
    if allowed_channel_ids:
        payload["config"] = {
            "channels": [{"channel_id": allowed_channel_ids[0]}]
        }
    if agent_name:
        payload["default_agent_name"] = agent_name

    response = await client.put(f"/pods/{pod_id}/surfaces/{platform}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _create_agent_surface(
    client: AsyncClient,
    pod_id: str,
    *,
    config: dict,
) -> tuple[dict, dict]:
    agent = await _create_agent(client, pod_id)
    surface = await _create_surface(
        client,
        pod_id,
        config=config,
        agent_name=agent["name"],
    )
    return agent, surface


async def _ensure_connector(
    db_session: AsyncSession,
    connector_id: str,
    *,
    provider: AuthProvider = AuthProvider.LEMMA,
) -> Connector:
    connector = await db_session.get(Connector, connector_id)
    capability = {"provider": provider.value, "auth_scheme": "OAUTH2"}
    if provider == AuthProvider.COMPOSIO:
        capability["toolkit_slug"] = connector_id
    if connector is None:
        connector = Connector(
            id=connector_id,
            title=connector_id.title(),
            description=f"{connector_id} test app",
            provider_capabilities=[capability],
            is_active=True,
        )
        db_session.add(connector)
        await db_session.flush()
    elif not any(
        item.get("provider") == provider.value
        for item in connector.provider_capabilities or []
    ):
        connector.provider_capabilities = [
            *(connector.provider_capabilities or []),
            capability,
        ]
        await db_session.flush()
    return connector


async def _ensure_connector_account(
    db_session: AsyncSession,
    *,
    user_id: str,
    connector_id: str,
    credentials: dict,
    email: str | None = None,
    provider: AuthProvider = AuthProvider.LEMMA,
    config_source: str = "SYSTEM_DEFAULT",
) -> Account:
    await _ensure_connector(db_session, connector_id, provider=provider)
    organization_id = await db_session.scalar(
        select(OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == UUID(user_id))
        .limit(1)
    )
    assert organization_id is not None
    auth_config = await db_session.scalar(
        select(AuthConfig).where(
            AuthConfig.organization_id == organization_id,
            AuthConfig.connector_id == connector_id,
        )
    )
    if auth_config is None:
        auth_config = AuthConfig(
            organization_id=organization_id,
            connector_id=connector_id,
            name=f"{connector_id} {config_source.lower()}",
            provider=provider.value,
            config_source=config_source,
            status="ACTIVE",
        )
        db_session.add(auth_config)
        await db_session.flush()
    else:
        if auth_config.provider != provider.value:
            auth_config.provider = provider.value
        if auth_config.config_source != config_source:
            auth_config.config_source = config_source
        await db_session.flush()
    stmt = select(Account).where(
        Account.organization_id == organization_id,
        Account.user_id == UUID(user_id),
        Account.connector_id == connector_id,
    )
    account = await db_session.scalar(stmt)
    if account is None:
        account = Account(
            user_id=UUID(user_id),
            organization_id=organization_id,
            auth_config_id=auth_config.id,
            connector_id=connector_id,
            provider_account_id=f"e2e-{connector_id}",
            email=email,
            credentials=credentials,
        )
        db_session.add(account)
    else:
        account.email = email
        account.auth_config_id = auth_config.id
        account.credentials = credentials
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def _ensure_connector_trigger(
    db_session: AsyncSession,
    *,
    connector_id: str,
    trigger_id: str,
    event_type: str,
) -> None:
    await _ensure_connector(db_session, connector_id)
    trigger = await db_session.get(ConnectorTrigger, trigger_id)
    if trigger is None:
        db_session.add(
            ConnectorTrigger(
                id=trigger_id,
                connector_id=connector_id,
                event_type=event_type,
                description=f"{connector_id} {event_type}",
            )
        )
        await db_session.commit()


async def _seed_external_user(
    db_session: AsyncSession,
    *,
    platform: str,
    external_user_id: str,
    resolved_user_id: UUID,
    email: str | None = None,
    phone: str | None = None,
    tenant_id: str | None = None,
) -> None:
    db_session.add(
        AgentSurfaceExternalUser(
            platform=platform,
            tenant_id=tenant_id,
            external_user_id=external_user_id,
            email=email.lower() if email else None,
            phone=phone,
            display_name="Surface Test User",
            raw_profile={},
            resolved_user_id=resolved_user_id,
            last_seen_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()


async def _set_user_mobile_number(
    db_session: AsyncSession,
    *,
    user_id: str,
    mobile_number: str,
    telegram_username: str | None = None,
) -> None:
    user = await db_session.get(User, UUID(user_id))
    assert user is not None
    user.mobile_number = mobile_number
    if telegram_username is not None:
        user.telegram_username = telegram_username
    await db_session.commit()


async def _process_ingress_and_emulate_reply(
    db_session: AsyncSession,
    request: SurfacePlatformWebhookIngress
    | SurfaceDirectWebhookIngress
    | SurfaceScheduleIngress,
    harness: EmulatedAgentHarness,
) -> SurfaceContext:
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(request)
    assert context is not None
    await uow.commit()

    # Run the agent inline below (so its reply is delivered via the in-test fake
    # platform servers); suppress the worker-dispatch event so the shared session
    # worker doesn't also run it and race a duplicate (mock) reply onto the
    # conversation.
    with suppress_agent_run_enqueue():
        await handler.execute_chat(context)
    if isinstance(context, SurfaceChatContext):
        await _run_agent_and_deliver_surface_reply(
            db_session=db_session,
            context=context,
            harness=harness,
        )
    return context


async def _run_agent_and_deliver_surface_reply(
    *,
    db_session: AsyncSession,
    context: SurfaceChatContext,
    harness: EmulatedAgentHarness,
) -> None:
    db_session.expire_all()
    run = await _latest_agent_run(db_session, context.conversation_id)
    assert run is not None
    assert run.status == AgentRunStatus.RUNNING.value
    run_id = run.id
    conversation = await db_session.get(ConversationModel, context.conversation_id)
    assert conversation is not None
    assert conversation.organization_id is not None
    runtime_profile_id = await _ensure_e2e_runtime_profile(
        db_session,
        organization_id=conversation.organization_id,
    )
    run.agent_runtime = {
        "profile_id": runtime_profile_id,
        "model_name": E2E_RUNTIME_MODEL_NAME,
    }
    await db_session.commit()

    runner = AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=HarnessRegistry([harness]),
    )
    await runner.execute(
        agent_run_id=run_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        observer=SurfaceAgentRunProgressObserver(
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
            service_factory=build_surface_event_handler,
        ),
    )

    db_session.expire_all()
    completed = await db_session.get(AgentRunModel, run_id)
    assert completed is not None
    assert completed.status == AgentRunStatus.COMPLETED.value


async def _ensure_e2e_runtime_profile(
    db_session: AsyncSession,
    *,
    organization_id: UUID,
) -> str:
    profile = await db_session.scalar(
        select(AgentRuntimeProfileModel)
        .where(
            AgentRuntimeProfileModel.organization_id == organization_id,
            AgentRuntimeProfileModel.scope == RuntimeProfileScope.ORGANIZATION.value,
            AgentRuntimeProfileModel.name == E2E_RUNTIME_PROFILE_NAME,
        )
        .limit(1)
    )
    if profile is None:
        profile = AgentRuntimeProfileModel(
            organization_id=organization_id,
            user_id=None,
            scope=RuntimeProfileScope.ORGANIZATION.value,
            kind=RuntimeProfileKind.MODEL_PROVIDER.value,
            protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE.value,
            name=E2E_RUNTIME_PROFILE_NAME,
            description="Local runtime profile for surface e2e harness execution.",
            default_model_name=E2E_RUNTIME_MODEL_NAME,
            model_catalog=[
                {
                    "name": E2E_RUNTIME_MODEL_NAME,
                    "display_name": "Surface E2E Model",
                    "provider_model_name": E2E_RUNTIME_MODEL_NAME,
                    "capabilities": ["TEXT", "TOOLS"],
                    "default_model_settings": {},
                    "metadata": {"surface_e2e": True},
                }
            ],
            config={
                "base_url": "https://surface-e2e.invalid/v1",
                "headers": {},
                "model_settings": {},
            },
            credentials={"api_key": "surface-e2e-key"},
            status=RuntimeProfileStatus.ACTIVE.value,
            profile_metadata={"surface_e2e": True},
        )
        db_session.add(profile)
        await db_session.flush()
    return str(profile.id)


async def _latest_agent_run(
    db_session: AsyncSession,
    conversation_id: UUID,
) -> AgentRunModel | None:
    stmt = (
        select(AgentRunModel)
        .where(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at.desc(), AgentRunModel.id.desc())
        .limit(1)
    )
    return await db_session.scalar(stmt)


async def _conversation_by_external_thread(
    client: AsyncClient,
    *,
    pod_id: str,
    external_thread_id: str,
    agent_name: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict | None:
    params = {"agent_name": agent_name} if agent_name else {}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            f"/pods/{pod_id}/conversations",
            params=params,
        )
        assert response.status_code == 200, response.text
        for item in response.json()["items"]:
            metadata = item.get("metadata") or {}
            if metadata.get("external_thread_id") == external_thread_id:
                return item
        await asyncio.sleep(0.1)
    return None


async def _messages_for_conversation(
    client: AsyncClient,
    *,
    pod_id: str,
    conversation_id: str,
) -> list[dict]:
    response = await client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]

def _whatsapp_payload(
    *,
    text: str,
    message_id: str,
    phone_number_id: str,
    waba_id: str,
    sender_phone: str,
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [
                                {
                                    "wa_id": sender_phone,
                                    "profile": {"name": "Surface Test User"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender_phone,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": text},
                                    "timestamp": "1700000000",
                                }
                            ],
                        }
                    }
                ],
            }
        ],
    }


def _telegram_payload(*, text: str, message_id: int, sender_id: int) -> dict:
    return {
        "update_id": message_id + 100000,
        "message": {
            "message_id": message_id,
            "from": {
                "id": sender_id,
                "is_bot": False,
                "first_name": "Surface",
                "last_name": "User",
                "username": "surfaceuser",
            },
            "chat": {"id": sender_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }


def _gmail_payload(
    *,
    sender_email: str,
    assistant_email: str,
    thread_id: str,
    message_id: str,
    text: str,
) -> dict:
    return {
        "data": {
            "thread_id": thread_id,
            "message_id": message_id,
            "sender": f"Surface Test User <{sender_email}>",
            "to": assistant_email,
            "subject": "Surface Gmail E2E",
            "message_text": text,
            "preview": {"body": text, "subject": "Surface Gmail E2E"},
            "payload": {
                "headers": [
                    {"name": "From", "value": f"Surface Test User <{sender_email}>"},
                    {"name": "To", "value": assistant_email},
                    {"name": "Delivered-To", "value": assistant_email},
                    {"name": "Subject", "value": "Surface Gmail E2E"},
                    {
                        "name": "Message-ID",
                        "value": f"<{message_id}@gmail-e2e.test>",
                    },
                ]
            },
        }
    }


def _outlook_payload(
    *,
    sender_email: str,
    assistant_email: str,
    thread_id: str,
    message_id: str,
    text: str,
) -> dict:
    return {
        "data": {
            "id": message_id,
            "conversationId": thread_id,
            "internetMessageId": f"<{message_id}@outlook-e2e.test>",
            "from": {
                "emailAddress": {
                    "address": sender_email,
                    "name": "Surface Test User",
                }
            },
            "replyTo": [
                {
                    "emailAddress": {
                        "address": sender_email,
                        "name": "Surface Test User",
                    }
                }
            ],
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": assistant_email,
                        "name": "Lemma",
                    }
                }
            ],
            "subject": "Surface Outlook E2E",
            "body": {"contentType": "text", "content": text},
            "internetMessageHeaders": [
                {"name": "Message-ID", "value": f"<{message_id}@outlook-e2e.test>"}
            ],
        }
    }
