"""Shared fixtures and helpers for the agent_surfaces e2e suite (fake platform
servers, connector/surface/agent setup, inbound payload builders). Agent runs
themselves are driven by the real harness via ``scripted_llm.py``, not by
anything in this module."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    FakeGmailServer,
    FakeOutlookServer,
    FakeResendServer,
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
async def fake_resend(message_store):
    server = FakeResendServer(message_store)
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
    toolsets: list[str] | None = None,
) -> dict:
    """Create a surface e2e test agent.

    ``toolsets`` defaults to ``[]`` (matches the previous hard-coded behavior:
    no generic tools available). Pass e.g. ``["USER_INTERACTION"]`` or
    ``["USER_INTERACTION", "SPEECH"]`` to opt a test into ``ask_user``/
    ``request_approval``/``display_resource``/``say`` — these are gated by the
    agent's own toolsets, unlike platform-specific tools (``gmail_reply_email``,
    etc.) which are always attached to a surface conversation regardless.
    """
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": name or f"Surface Agent {uuid4().hex[:8]}",
            "instruction": "Reply briefly. Surface e2e will emulate the model.",
            "agent_runtime": {
                "profile_id": "system:fireworks",
                "model_name": "kimi-k2.6",
            },
            "toolsets": toolsets if toolsets is not None else [],
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
    name: str | None = None,
) -> dict:
    platform = str(config.get("type", "TELEGRAM")).upper()
    allowed_channel_ids = config.get("allowed_channel_ids") or []
    payload: dict[str, object] = {
        "platform": platform,
        "config": {},
    }
    if name:
        payload["name"] = name
    if config.get("account_id"):
        payload["account_id"] = config["account_id"]
    if allowed_channel_ids:
        payload["config"] = {
            "channels": [{"channel_id": allowed_channel_ids[0]}]
        }
    if agent_name:
        payload["default_agent_name"] = agent_name

    response = await client.post(f"/pods/{pod_id}/surfaces", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _create_agent_surface(
    client: AsyncClient,
    pod_id: str,
    *,
    config: dict,
    toolsets: list[str] | None = None,
) -> tuple[dict, dict]:
    agent = await _create_agent(client, pod_id, toolsets=toolsets)
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


async def _seed_pod_file(
    db_session: AsyncSession,
    *,
    user_id: str,
    pod_id: str,
    name: str,
    content: bytes,
    directory_path: str = "/me/reports",
) -> str:
    """Write a real pod file via the datastore file service and return its path.

    Used to give ``display_resource(type=FILE, path=...)`` a real file to
    reference — no AgentBox/sandbox involvement, matching how the real tool
    resolves a path.
    """
    from app.core.authorization.current import (
        reset_current_context,
        set_current_context,
    )
    from app.core.authorization.factory import create_authorization_data_service
    from app.modules.datastore.api.dependencies import build_file_service

    uow = SqlAlchemyUnitOfWork(db_session)
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(ctx)
    try:
        entity = await build_file_service(uow).create_file(
            pod_id=pod_id,
            name=name,
            file_content=content,
            ctx=ctx,
            directory_path=directory_path,
            search_enabled=False,
        )
        await uow.commit()
        return entity.path
    finally:
        reset_current_context(token)


@pytest_asyncio.fixture
async def fake_speech_provider(monkeypatch):
    """Deterministic TTS so ``say`` can be scripted as a real tool call without
    a real speech-provider credential. Delivery/format negotiation still runs
    for real — only synthesis is faked."""
    import app.modules.agent.tools.speech.provider as speech_provider_module
    import app.modules.agent.tools.speech.speech as speech_module

    class _FakeSpeechProvider:
        async def synthesize(
            self, text: str, *, voice: str | None = None, output_format: str = "mp3"
        ) -> bytes:
            del voice, output_format
            return b"FAKE-AUDIO-" + text.encode("utf-8")[:64]

    fake = _FakeSpeechProvider()
    # speech.py imports get_speech_provider by value (`from ... import
    # get_speech_provider`), so its own module-local name must be patched too
    # — patching only the defining module leaves speech.py's call sites bound
    # to the original function.
    monkeypatch.setattr(
        speech_provider_module, "get_speech_provider", lambda *a, **k: fake
    )
    monkeypatch.setattr(speech_module, "get_speech_provider", lambda *a, **k: fake)
    return fake


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


def _resend_payload(
    *,
    sender_email: str,
    assistant_address: str,
    message_id: str,
    text: str,
    subject: str = "Surface Resend E2E",
) -> dict:
    """Already-normalized Resend inbound shape (matches what the production
    webhook controller's ``_normalize_resend_inbound`` produces from the raw
    ``email.received`` envelope) — this is what ``ResendInboundParser.parse``
    consumes directly."""
    return {
        "from": sender_email,
        "to": assistant_address,
        "subject": subject,
        "text": text,
        "message_id": f"<{message_id}@resend-e2e.test>",
        "in_reply_to": None,
        "references": [],
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
