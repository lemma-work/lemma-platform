from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from types import MethodType
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    ConversationType,
    SurfaceCredentialMode,
    SurfaceIdentityPolicy,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceMode,
    SurfacePlatform,
    SurfaceConfig,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.entities import ParsedSurfaceInteraction
from app.modules.agent_surfaces.services import surface_egress
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent.tools.speech.provider import SpeechProviderError
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    EnvelopeFile,
    PartDelivery,
)
from app.modules.agent_surfaces.services.display_resource_content import (
    PodFileDelivery,
    PodFileParts,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceMessageMetadata,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    ResumeOutcome,
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.email_one_reply import (
    EmailOneReplyMixin,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
    IngestedAttachment,
)
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    TelegramMiniApp,
)
from app.modules.agent_surfaces.services.telegram_command_service import (
    handle_telegram_command,
)

pytestmark = pytest.mark.asyncio

#: Where `agent` publishes what a surface does to a conversation, and where the
#: doubles below are installed. On the contract rather than on the surface
#: modules that call it: the operations belong to another module, and the real
#: ones reach a database.
_CONVERSATIONS = "app.modules.agent.contracts.conversations_for_surfaces"
_AGENT_DIRECTORY = "app.modules.agent.contracts.agents"


@pytest.fixture(autouse=True)
def conversation_operations(monkeypatch):
    """`agent`'s conversation operations, doubled for every test in this file.

    Autouse because nearly every path here opens, reads or resumes a
    conversation. Tests reach the doubles through ``agent_conversations``,
    which is the same module the code under test calls.
    """
    doubles = {
        "surface_conversation": AsyncMock(return_value=None),
        "open_surface_conversation": AsyncMock(),
        "start_surface_turn": AsyncMock(return_value=uuid4()),
        "append_notification_message": AsyncMock(),
        "pending_interaction": AsyncMock(return_value=None),
        "pending_question": AsyncMock(return_value=None),
        "pending_approval": AsyncMock(return_value=None),
        "resolve_pending_interaction": AsyncMock(return_value=True),
        "retry_failed_run": AsyncMock(),
        "surface_agent_identity": AsyncMock(return_value=None),
        "conversation_metadata_value": AsyncMock(return_value=None),
        "set_conversation_metadata_value": AsyncMock(),
    }
    for name, double in doubles.items():
        monkeypatch.setattr(f"{_CONVERSATIONS}.{name}", double)
    monkeypatch.setattr(
        f"{_AGENT_DIRECTORY}.agent_name_for_id", AsyncMock(return_value="Surface Agent")
    )


def _pending(kind: str, *, tool_call_id: str, tool_args: dict | None = None):
    """One paused call, in the shape the published operation returns it."""
    return SimpleNamespace(
        tool_call_id=tool_call_id,
        kind=kind,
        tool_args=tool_args or {},
        agent_run_id=uuid4(),
        is_approval=kind == "request_approval",
    )


def _surface_conversation(surface, *, conversation_id: UUID | None = None):
    """A conversation as the published lookup returns it."""
    return SimpleNamespace(
        id=conversation_id or uuid4(),
        user_id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        title=None,
        updated_at=datetime.now(timezone.utc),
    )


def _registry(adapter):
    return SimpleNamespace(get=lambda platform: adapter)


def _slack_surface(*, agent_id: UUID | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=agent_id if agent_id is not None else uuid4(),
        surface_type=SurfacePlatform.SLACK,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        surface_identity_id="U-BOT",
        config=SurfaceConfig(),
        is_active=True,
    )


def _teams_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="teams",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TEAMS,
        mode=SurfaceMode.DM,
        account_id=uuid4(),
        external_tenant_id="tenant-123",
        external_channel_id="19:channel",
        config=SurfaceConfig(),
        is_active=True,
    )


def _telegram_surface(*, agent_id: UUID | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=agent_id if agent_id is not None else uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        mode=SurfaceMode.DM,
        account_id=None,
        config=SurfaceConfig(),
        is_active=True,
    )


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="T123",
        external_channel_id="D123",
        external_thread_id="D123",
        external_message_id="1700000000.000100",
        sender_external_user_id="U123",
        sender_display_name="New User",
        message_text="Hello from Slack",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"channel": "D123"},
    )


def _slack_channel_event(*, channel_id: str = "C999") -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="T123",
        external_channel_id=channel_id,
        external_thread_id="1700000000.000200",
        external_message_id="1700000000.000201",
        sender_external_user_id="U123",
        sender_display_name="New User",
        message_text="Hello from a channel",
        is_dm=False,
        mentioned_agent=True,
        reply_target={"channel": channel_id},
        metadata={"mentioned_user_ids": ["U-BOT"]},
    )


def _telegram_event(*, chat_id: str, message_id: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id=chat_id,
        external_thread_id=chat_id,
        external_message_id=message_id,
        sender_external_user_id="777",
        sender_display_name="Telegram User",
        message_text="Hello from Telegram",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"chat_id": chat_id, "message_id": message_id},
    )


def _conversation(surface: AgentSurfaceEntity, user_id: UUID) -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=user_id,
        title="Surface chat",
        metadata={},
    )


class _EmptyScalarResult:
    def __iter__(self):
        return iter(())

    def first(self):
        return None

    def all(self):
        return []


class _EmptyExecuteResult:
    def scalars(self):
        return _EmptyScalarResult()


def _delivering_adapter(platform: str = "SLACK") -> AsyncMock:
    """An adapter mock that runs the real ``deliver`` over stubbed platform verbs.

    Egress hands the platform one envelope now instead of calling a verb per
    kind of content, so a fully mocked adapter returns a mock from ``deliver``
    and never reaches ``_render_decision`` at all. Binding the real delivery
    methods keeps these tests asserting what they were written to assert: which
    verb the ladder tries, and what it falls back to.
    """
    adapter = AsyncMock()
    adapter.platform = platform
    for cls in BaseSurfaceAdapter.__mro__:
        for name, function in vars(cls).items():
            if name == "deliver" or name.startswith(
                ("_deliver", "_send_text_fallback")
            ):
                setattr(adapter, name, MethodType(function, adapter))
    if _delivers_one_reply(platform):
        # A one-reply platform folds the whole envelope into a single send, so
        # the mock borrows that too -- otherwise `deliver` stops at a mocked
        # `_render_one` and the transport is never reached.
        adapter._render_one = MethodType(EmailOneReplyMixin._render_one, adapter)
    return adapter


def _delivers_one_reply(platform: str) -> bool:
    from app.modules.agent_surfaces.platforms.platform_capabilities import (
        DeliveryCardinality,
        get_platform_capabilities,
    )

    caps = get_platform_capabilities(platform)
    return bool(caps and caps.delivery_cardinality is DeliveryCardinality.ONE)


def _build_service(
    *,
    adapter,
    surfaces: list[AgentSurfaceEntity] | None = None,
    resolved_user: ResolvedSurfaceUser | None = None,
    conversation: Conversation | None = None,
    existing_link: AgentSurfaceConversationLink | None = None,
):
    resolved_surfaces = surfaces or []
    surface_repository = AsyncMock()
    surface_repository.list_active_by_type.return_value = resolved_surfaces
    surface_repository.get.side_effect = lambda surface_id: next(
        (surface for surface in resolved_surfaces if surface.id == surface_id),
        None,
    )

    conversation_link_repository = AsyncMock()
    conversation_link_repository.get_by_external_thread.return_value = existing_link
    conversation_link_repository.create.side_effect = lambda link: link
    conversation_link_repository.update_last_event.side_effect = lambda **kwargs: (
        existing_link
    )
    conversation_link_repository.update_conversation.side_effect = lambda **kwargs: (
        existing_link.model_copy(
            update={
                "conversation_id": kwargs["conversation_id"],
                "last_event": kwargs.get("last_event", {}),
                "last_message_id": kwargs.get("last_message_id"),
            }
        )
        if existing_link is not None
        else None
    )

    agent_conversations.surface_agent_identity.return_value = (
        SimpleNamespace(
            id=uuid4(), name="Surface Agent", is_pod_default=False, icon_url=None
        )
        if any(surface.agent_id for surface in resolved_surfaces)
        else None
    )
    if conversation is not None:
        agent_conversations.open_surface_conversation.return_value = conversation

    session_model = SimpleNamespace(conversation_metadata={})
    organization_id = uuid4()

    async def _fake_get(model, item_id):
        del item_id
        if getattr(model, "__name__", "") == "Pod":
            # `is_deleted` is a non-nullable column, so a stand-in without
            # it is a Pod no database could return.
            return SimpleNamespace(organization_id=organization_id, is_deleted=False)
        return session_model

    uow = SimpleNamespace(
        session=SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            execute=AsyncMock(return_value=_EmptyExecuteResult()),
            flush=AsyncMock(),
        ),
        # Egress releases the pooled connection before every platform call, so
        # the double needs the method the real unit of work has. Given rather
        # than made optional in the service: a helper that shrugs at a uow
        # without `commit` would also shrug in production, where that means the
        # connection is quietly held across the send.
        commit=AsyncMock(),
    )

    adapter.enrich_inbound_event.side_effect = lambda *, credentials, event: event
    adapter.unresolved_sender_reply = Mock(return_value=None)
    adapter.linked_sender_confirmation = Mock(return_value=None)
    service = AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository,
        conversation_link_repository=conversation_link_repository,
        adapter_registry=_registry(adapter),
        pod_membership_port=SimpleNamespace(
            get_user_pod_ids=AsyncMock(
                return_value=[surface.pod_id for surface in resolved_surfaces]
            ),
            get_user_email=AsyncMock(return_value="sender@example.com"),
        ),
    )
    service.identity_service = SimpleNamespace(
        resolve=AsyncMock(
            return_value=resolved_user
            or ResolvedSurfaceUser(
                internal_user_id=uuid4(),
                external_user_id="U123",
                email="sender@example.com",
                display_name="Sender",
            )
        )
    )
    service._resolve_credentials = AsyncMock(return_value={})
    service._resolve_credentials_from_context = AsyncMock(return_value={})
    service._resolve_account_credentials = AsyncMock(return_value={})
    service.event_dedup_store = SimpleNamespace(
        claim_message=AsyncMock(return_value=True),
        claim_stranger_reply=AsyncMock(return_value=True),
    )
    return service


async def test_prepare_webhook_returns_signup_context_for_unresolved_user():
    surface = _slack_surface()
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        email="new.user@example.com",
        display_name="New User",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=None,
            external_user_id="U123",
            email="new.user@example.com",
            display_name="New User",
        ),
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.surface_id == surface.id
    assert context.reply_kind == "signup"
    assert context.agent_display_name == "Surface Agent"
    agent_conversations.open_surface_conversation.assert_not_called()


async def test_prepare_webhook_avoids_pod_access_link_for_system_non_member():
    """A signed-up non-member on a shared system Telegram bot should not receive
    a pod-specific URL, because the shared identity can front many pods."""
    surface = _telegram_surface()
    surface.credential_mode = SurfaceCredentialMode.SYSTEM
    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="123",
        external_channel_id="123",
        sender_external_user_id="999",
        message_text="hi",
        is_dm=True,
        reply_target={"chat_id": "123"},
    )
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.unresolved_sender_reply.return_value = None
    adapter.linked_sender_confirmation.return_value = None
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(),
            external_user_id="999",
            email="member@example.com",
            display_name="Member",
        ),
    )
    # Resolved user belongs to no pod -> not a member of the surface's pod.
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[])
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload={}, headers={})
    )

    assert isinstance(context, SurfaceReplyContext)
    assert "Request access" not in (context.reply_message or "")
    assert str(surface.pod_id) not in (context.reply_message or "")
    assert context.reply_kind == "surface_setup"
    assert "set up or select a surface" in (context.reply_message or "")
    agent_conversations.open_surface_conversation.assert_not_called()


async def test_prepare_webhook_returns_pod_access_link_for_custom_non_member(
    monkeypatch,
):
    """A custom/bound bot maps to one configured surface, so the pod target is
    explicit and the access link is safe to show."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test/")
    monkeypatch.setattr(
        app_settings,
        "auth_frontend_url",
        "https://auth.example.test/auth/",
    )
    surface = _telegram_surface()
    surface.credential_mode = SurfaceCredentialMode.CUSTOM
    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="123",
        external_channel_id="123",
        sender_external_user_id="999",
        message_text="hi",
        is_dm=True,
        reply_target={"chat_id": "123"},
    )
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.unresolved_sender_reply.return_value = None
    adapter.linked_sender_confirmation.return_value = None
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(),
            external_user_id="999",
            email="member@example.com",
            display_name="Member",
        ),
    )
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[])
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload={},
            headers={},
            receiver_surface_ids=[surface.id],
        )
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "pod_access"
    assert "Request access" in (context.reply_message or "")
    assert context.reply_message.endswith(
        f"https://app.example.test/pod/{surface.pod_id}"
    )
    assert "auth.example.test" not in context.reply_message
    agent_conversations.open_surface_conversation.assert_not_called()


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.TELEGRAM, SurfacePlatform.WHATSAPP]
)
async def test_unresolved_managed_dm_with_multiple_surfaces_gets_one_fallback(
    platform: SurfacePlatform,
):
    surfaces = [
        AgentSurfaceEntity(
            id=uuid4(),
            pod_id=uuid4(),
            name=f"{platform.value.lower()}-{index}",
            agent_id=uuid4(),
            surface_type=platform,
            mode=SurfaceMode.DM,
            account_id=None,
            credential_mode=SurfaceCredentialMode.SYSTEM,
            config=SurfaceConfig(),
            is_active=True,
        )
        for index in range(2)
    ]
    event = ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="dm-123",
        external_thread_id="dm-123",
        external_message_id="message-123",
        sender_external_user_id="external-123",
        message_text="hello",
        is_dm=True,
        reply_target={"chat_id": "dm-123", "sender_wa_id": "external-123"},
    )
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.unresolved_sender_reply.return_value = None
    service = _build_service(
        adapter=adapter,
        surfaces=surfaces,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=None,
            external_user_id="external-123",
        ),
    )
    service.event_dedup_store.claim_message.side_effect = [True, False]

    first = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source=platform.value.lower(), payload={}, headers={}
        )
    )
    second = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source=platform.value.lower(), payload={}, headers={}
        )
    )

    assert isinstance(first, SurfaceReplyContext)
    assert first.reply_kind == "signup"
    assert first.surface_id is None
    assert second is None
    assert (
        service.event_dedup_store.claim_message.await_args.kwargs[
            "surface_installation_id"
        ]
        is None
    )


async def test_resolved_dm_without_matching_surface_gets_setup_link(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test/")
    surfaces = [_telegram_surface(), _telegram_surface()]
    event = _telegram_event(chat_id="dm-setup", message_id="message-setup")
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    service = _build_service(
        adapter=adapter,
        surfaces=surfaces,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(),
            external_user_id="777",
            email="signed-in@example.com",
        ),
    )
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[]),
        get_user_email=AsyncMock(return_value="signed-in@example.com"),
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload={}, headers={})
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "surface_setup"
    assert context.surface_id is None
    assert context.reply_message.endswith("https://app.example.test")


async def test_unroutable_group_remains_silent():
    surfaces = [_slack_surface(), _slack_surface()]
    event = _slack_channel_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    service = _build_service(
        adapter=adapter,
        surfaces=surfaces,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=None,
            external_user_id="U123",
        ),
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert context is None
    service.event_dedup_store.claim_message.assert_not_awaited()


async def test_resolved_dm_with_no_route_gets_setup_reply():
    surface = _slack_surface()
    user_id = uuid4()
    event = _slack_event()
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
        ),
    )
    service._resolve_route = AsyncMock(return_value=None)

    context = await service._prepare_surface_context(
        surface=surface,
        parsed=event,
        adapter=adapter,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
        ),
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "surface_setup"
    assert "set up or select a surface" in context.reply_message


async def test_prepare_webhook_creates_conversation_link_for_resolved_user():
    surface = _teams_surface()
    user_id = uuid4()
    conversation = _conversation(surface, user_id)
    event = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="tenant-123",
        external_channel_id="19:channel",
        external_thread_id="17001",
        external_message_id="17002",
        sender_external_user_id="8:orgid:user-1",
        sender_display_name="Asha",
        message_text="What does this image say?",
        mentioned_agent=True,
        reply_target={"team_id": "team-1", "channel_id": "19:channel"},
        metadata={"attachments": [{"name": "diagram.png"}]},
    )
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="8:orgid:user-1",
        display_name="Asha",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="8:orgid:user-1",
            display_name="Asha",
        ),
        conversation=conversation,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="teams", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.conversation_id == conversation.id
    assert context.agent_name == "Surface Agent"
    assert context.message_metadata.event_metadata["attachments"] == [
        {"name": "diagram.png"}
    ]
    create_kwargs = agent_conversations.open_surface_conversation.await_args.kwargs
    assert create_kwargs["pod_id"] == surface.pod_id
    assert create_kwargs["agent_name"] == "Surface Agent"
    assert create_kwargs["metadata"]["surface_id"] == str(surface.id)
    assert create_kwargs["metadata"]["surface_platform"] == "TEAMS"
    assert create_kwargs["metadata"]["external_thread_id"] == "17001"
    service.conversation_link_repository.create.assert_awaited_once()


async def test_prepare_webhook_reuses_existing_conversation_link():
    surface = _slack_surface()
    user_id = uuid4()
    conversation_id = uuid4()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id="D123",
        external_thread_id="D123",
        external_user_id="U123",
        routed_agent_id=surface.agent_id,
        last_event={},
    )
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            display_name="Sender",
        ),
        existing_link=link,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.conversation_id == conversation_id
    agent_conversations.open_surface_conversation.assert_not_called()
    service.conversation_link_repository.update_last_event.assert_awaited_once()


async def test_prepare_webhook_resets_dm_conversation_when_surface_agent_changes():
    old_agent_id = uuid4()
    new_agent_id = uuid4()
    surface = _slack_surface(agent_id=new_agent_id)
    user_id = uuid4()
    old_conversation_id = uuid4()
    new_conversation = _conversation(surface, user_id)
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=old_conversation_id,
        platform="SLACK",
        external_channel_id="D123",
        external_thread_id="D123",
        external_user_id="U123",
        routed_agent_id=old_agent_id,
        last_event={},
    )
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            display_name="Sender",
        ),
        conversation=new_conversation,
        existing_link=link,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.conversation_id == new_conversation.id
    update = service.conversation_link_repository.update_conversation.await_args.kwargs
    assert update["routed_agent_id"] == new_agent_id
    agent_conversations.open_surface_conversation.assert_awaited_once()
    service.conversation_link_repository.update_last_event.assert_not_called()


async def test_an_allowed_channel_is_answered_by_the_surfaces_own_agent():
    """A channel says *where*, not *who*.

    The stored route below still carries `agent_name` -- a config written when
    one bot could serve several agents. It is not read: the surface has one
    agent, and that is who answers everywhere the surface is allowed. Stale keys
    parse rather than raising, which is what lets old rows keep working without
    a data migration.
    """
    surface = _slack_surface()
    surface.mode = SurfaceMode.DM
    surface.config = SurfaceConfig.model_validate(
        {"channels": [{"channel_id": "C999", "agent_name": "Channel Agent"}]}
    )
    user_id = uuid4()
    conversation = _conversation(surface, user_id)
    event = _slack_channel_event(channel_id="C999")
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            display_name="Sender",
        ),
        conversation=conversation,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.conversation_id == conversation.id
    created_link = service.conversation_link_repository.create.await_args.args[0]
    assert created_link.routed_agent_id == surface.agent_id
    assert created_link.route_key == "channel:C999"
    assert created_link.conversation_kind == "CHANNEL"


async def test_prepare_webhook_applies_identity_allow_domain_policy():
    surface = _slack_surface()
    surface.config = SurfaceConfig(
        identity=SurfaceIdentityPolicy(allowed_domains=["example.com"])
    )
    user_id = uuid4()
    conversation = _conversation(surface, user_id)
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        email="sender@example.com",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
            display_name="Sender",
        ),
        conversation=conversation,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)


async def test_prepare_webhook_allows_identity_email_without_deny_list():
    surface = _slack_surface()
    surface.config = SurfaceConfig(
        identity=SurfaceIdentityPolicy(allowed_domains=["example.com"])
    )
    user_id = uuid4()
    conversation = _conversation(surface, user_id)
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        email="sender@example.com",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
            display_name="Sender",
        ),
        conversation=conversation,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)


async def test_prepare_webhook_ignores_unconfigured_slack_channel():
    surface = _slack_surface()
    surface.mode = SurfaceMode.DM
    event = _slack_channel_event(channel_id="C404")
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    service = _build_service(adapter=adapter, surfaces=[surface])

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert context is None
    service.conversation_link_repository.create.assert_not_awaited()


async def test_prepare_webhook_resets_stale_dm_conversation_link():
    surface = _slack_surface()
    user_id = uuid4()
    old_conversation_id = uuid4()
    new_conversation = _conversation(surface, user_id)
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=old_conversation_id,
        platform="SLACK",
        external_channel_id="D123",
        external_thread_id="D123",
        external_user_id="U123",
        last_event={},
    )
    link.updated_at = datetime.now(timezone.utc) - timedelta(hours=25)
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        display_name="Sender",
    )
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            display_name="Sender",
        ),
        conversation=new_conversation,
        existing_link=link,
    )

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert isinstance(context, SurfaceChatContext)
    assert context.conversation_id == new_conversation.id
    agent_conversations.open_surface_conversation.assert_awaited_once()
    service.conversation_link_repository.update_conversation.assert_awaited_once()
    service.conversation_link_repository.update_last_event.assert_not_called()


async def test_prepare_webhook_ignores_duplicate_external_message():
    surface = _slack_surface()
    event = _slack_event()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123",
        display_name="Sender",
    )
    service = _build_service(adapter=adapter, surfaces=[surface])
    service.event_dedup_store.claim_message.return_value = False

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert context is None
    agent_conversations.open_surface_conversation.assert_not_called()


async def test_execute_chat_sends_direct_replies():
    parsed_event = _slack_event()
    adapter = AsyncMock()
    service = _build_service(adapter=adapter)
    service._resolve_credentials_from_context = AsyncMock(
        return_value={"bot_token": "test-token", "access_token": "test-token"}
    )
    signup_context = SurfaceReplyContext(
        platform="SLACK",
        agent_display_name="Lemma",
        reply_message="Please sign up",
        event=parsed_event,
    )
    direct_context = SurfaceReplyContext(
        platform="SLACK",
        agent_display_name="Lemma",
        reply_message="Linked",
        reply_metadata={"reply_markup": {"remove_keyboard": True}},
        event=parsed_event,
    )

    await service.execute_chat(signup_context)
    await service.execute_chat(direct_context)

    assert adapter.send_message.await_count == 2
    assert adapter.send_message.await_args.kwargs["metadata"]["reply_markup"] == {
        "remove_keyboard": True
    }


async def test_execute_chat_names_the_surface_that_cannot_answer_a_stranger(caplog):
    """A surface with no credentials ignores every unrecognised sender.

    PS-SURF-012 says someone with no access is told how to get it rather than
    failing silently, and this branch is the one most likely to fire -- a
    surface whose account expired. The incident counter alone said "the fallback
    dependency is degraded" after three of them, without naming which surface,
    which is the one fact needed to fix it.
    """
    surface_id = uuid4()
    parsed_event = _telegram_event(chat_id="123", message_id="missing-creds")
    adapter = AsyncMock()
    service = _build_service(adapter=adapter)
    service._resolve_credentials_from_context = AsyncMock(
        return_value={"bot_token": ""}
    )
    context = SurfaceReplyContext(
        platform="TELEGRAM",
        surface_id=surface_id,
        reply_kind="signup",
        reply_message="Please sign up",
        event=parsed_event,
    )

    with caplog.at_level("WARNING"):
        await service.execute_chat(context)

    adapter.send_message.assert_not_awaited()
    assert "surface_fallback_no_credentials" in caplog.text
    assert str(surface_id) in caplog.text


async def test_execute_chat_logs_delivery_failure_without_secret(monkeypatch):
    parsed_event = _telegram_event(chat_id="123", message_id="failed-delivery")
    adapter = AsyncMock()
    adapter.send_message.side_effect = RuntimeError("provider exposed secret-token")
    service = _build_service(adapter=adapter)
    service._resolve_credentials_from_context = AsyncMock(
        return_value={"bot_token": "secret-token"}
    )
    incident = Mock()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.fallback_reply_service._fallback_incident",
        incident,
    )
    context = SurfaceReplyContext(
        platform="TELEGRAM",
        reply_kind="surface_setup",
        reply_message="Set up a surface",
        event=parsed_event,
    )

    await service.execute_chat(context)

    incident.record_failure.assert_called_once_with(error_type="RuntimeError")
    assert "secret-token" not in repr(incident.record_failure.call_args)


async def test_execute_chat_starts_agent_run_with_surface_metadata():
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    parsed_event = _slack_event()
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter, surfaces=[surface], conversation=conversation
    )
    context = SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text="Hello from Slack",
        message_metadata=SurfaceMessageMetadata(
            surface_platform="SLACK",
            sender_display_name="New User",
            event_metadata={"attachments": [{"name": "notes.txt"}]},
        ),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        message_external_message_id="1700000000.000100",
        event=parsed_event,
    )

    await service.execute_chat(context)

    adapter.add_processing_indicator.assert_awaited_once()
    agent_conversations.start_surface_turn.assert_awaited_once()
    kwargs = agent_conversations.start_surface_turn.await_args.kwargs
    assert kwargs["conversation_id"] == conversation.id
    assert kwargs["pod_id"] == surface.pod_id
    assert kwargs["agent_name"] is None
    assert kwargs["message_metadata"]["surface_platform"] == "SLACK"
    assert kwargs["message_metadata"]["external_message_id"] == "1700000000.000100"


def _slack_chat_context(surface, conversation, text: str) -> SurfaceChatContext:
    return SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text=text,
        message_metadata=SurfaceMessageMetadata(surface_platform="SLACK"),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        event=_slack_event(),
    )


async def test_a_message_arriving_mid_run_is_not_acknowledged():
    """One message is routinely several webhooks on a chat surface, so an
    acknowledgement per bubble was the agent narrating its own plumbing. The
    run already going is told instead — see PendingUserMessagesCapability."""
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter, surfaces=[surface], conversation=conversation
    )
    # ``None`` is how the operation says no new run was needed: the message was
    # handed to the one already going.
    agent_conversations.start_surface_turn.return_value = None

    for _ in range(3):
        await service.execute_chat(_slack_chat_context(surface, conversation, "photo"))

    adapter.send_message.assert_not_awaited()


@pytest.mark.parametrize("command", ["/start", "/help"])
async def test_telegram_help_points_to_bound_mini_app_button(command, monkeypatch):
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    app_id = uuid4()
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_command_service."
        "_telegram_mini_app_for_context",
        AsyncMock(
            return_value=TelegramMiniApp(
                app_id=app_id,
                name="field-log",
                url="https://field-log.apps.example.test",
            )
        ),
    )
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Logger",
        message_text=command,
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=service._uow_factory,
        uow=service.uow,
    )

    assert handled is True
    sent = adapter.send_message.await_args.kwargs
    assert (
        "Open Field Log from the app button beside the message field" in sent["message"]
    )
    assert "metadata" not in sent


async def test_telegram_help_does_not_claim_unavailable_local_app_button(monkeypatch):
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.telegram_command_service."
        "_telegram_mini_app_for_context",
        AsyncMock(
            return_value=TelegramMiniApp(
                app_id=uuid4(),
                name="field-log",
                url=None,
            )
        ),
    )
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Logger",
        message_text="/help",
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=service._uow_factory,
        uow=service.uow,
    )

    assert handled is True
    sent = adapter.send_message.await_args.kwargs
    assert "app button beside the message field" not in sent["message"]


async def test_telegram_app_command_is_not_a_special_command():
    surface = _telegram_surface()
    event = _telegram_event(chat_id="42", message_id="7")
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    context = SurfaceChatContext(
        platform=SurfacePlatform.TELEGRAM,
        pod_id=surface.pod_id,
        conversation_id=uuid4(),
        user_id=uuid4(),
        surface_id=surface.id,
        surface_config=surface.config,
        message_text="/app",
        message_metadata=SurfaceMessageMetadata(surface_platform="TELEGRAM"),
        message_user_id=uuid4(),
        event=event,
    )

    handled = await handle_telegram_command(
        context=context,
        adapter=adapter,
        credentials={"bot_token": "secret"},
        uow_factory=service._uow_factory,
        uow=service.uow,
    )

    assert handled is False
    adapter.send_message.assert_not_awaited()


async def test_execute_chat_factory_mode_holds_no_session_during_io(monkeypatch):
    """In worker (uow_factory) mode, execute_chat holds NO DB session during the
    external I/O (processing indicator, file ingest); the connection is taken
    only for the credential read and the message-write tail."""
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    parsed_event = _slack_event()
    adapter = AsyncMock()
    adapter.fetch_thread_context = AsyncMock(return_value=[])

    class _RecordingFactory:
        def __init__(self) -> None:
            self.active = 0
            self.opened = 0

        @asynccontextmanager
        async def __call__(self):
            self.active += 1
            self.opened += 1
            try:
                yield SimpleNamespace(session=SimpleNamespace())
            finally:
                self.active -= 1

    factory = _RecordingFactory()

    # Stub credential resolution + auth so the short UoWs do no real DB work.
    class _StubResolver:
        def __init__(self, *, uow) -> None:
            pass

        async def for_platform(self, platform, account_id, *, surface=None):
            return {}

    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.ingress_service.SurfaceCredentialResolver",
        _StubResolver,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.services.surface_inbound_message.create_authorization_data_service",
        lambda uow: SimpleNamespace(
            build_user_context=AsyncMock(return_value=SimpleNamespace())
        ),
    )

    indicator_active: list[int] = []
    ingest_active: list[int] = []
    write_active: list[int] = []

    async def _record_indicator(**_kwargs):
        indicator_active.append(factory.active)

    async def _record_ingest(**_kwargs):
        ingest_active.append(factory.active)
        return AttachmentIngest()

    adapter.add_processing_indicator.side_effect = _record_indicator

    async def _record_write(*_args, **_kwargs):
        write_active.append(factory.active)

    agent_conversations.start_surface_turn.side_effect = _record_write

    service = AgentSurfaceIngressService(
        uow_factory=factory,
        adapter_registry=_registry(adapter),
        file_ingest_service=SimpleNamespace(ingest_attachments=_record_ingest),
    )

    context = SurfaceChatContext(
        platform="SLACK",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text="Hello from Slack",
        message_metadata=SurfaceMessageMetadata(
            surface_platform="SLACK",
            sender_display_name="New User",
            event_metadata={},
        ),
        message_user_id=conversation.user_id,
        message_external_user_id="U123",
        message_external_message_id="1700000000.000100",
        event=parsed_event,
    )

    await service.execute_chat(context)

    # External I/O ran with NO open DB session.
    assert indicator_active == [0]
    assert ingest_active == [0]
    # The message write ran INSIDE a short UoW.
    assert write_active == [1]
    agent_conversations.start_surface_turn.assert_awaited_once()
    # Two short UoWs total: credential read + message-write tail.
    assert factory.opened == 2
    assert factory.active == 0


async def test_send_processing_indicator_for_conversation_uses_last_surface_event():
    surface = _teams_surface()
    conversation_id = uuid4()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="TEAMS",
        external_channel_id="19:channel",
        external_thread_id="17001",
        external_user_id="8:orgid:user-1",
        last_event=ParsedInboundSurfaceEvent(
            platform="TEAMS",
            conversation_type=ConversationType.EXTERNAL_GROUP,
            tenant_id="tenant-123",
            external_channel_id="19:channel",
            external_thread_id="17001",
            external_message_id="17002",
            sender_external_user_id="8:orgid:user-1",
            sender_display_name="Asha",
            message_text="hello",
            mentioned_agent=True,
            reply_target={"conversation_id": "conversation-1"},
        ).model_dump(mode="json"),
    )
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    sent = await service.send_processing_indicator_for_conversation(
        conversation_id=conversation_id,
        metadata={"progress_text": "Checking the calendar"},
    )

    assert sent is True
    adapter.add_processing_indicator.assert_awaited_once()
    assert (
        adapter.add_processing_indicator.await_args.kwargs["metadata"]["progress_text"]
        == "Checking the calendar"
    )


async def test_send_agent_message_for_conversation_sends_surface_message():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    sent = await service.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message="assistant update",
    )

    assert sent is True
    adapter.send_message.assert_awaited_once()
    assert adapter.send_message.await_args.kwargs["message"] == "assistant update"


async def test_send_agent_message_strips_thinking_tokens_before_delivery():
    """Model reasoning/thinking tags must never reach a surface as a chat
    message. The ingress service strips them as a final safety net."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    # Build the message with literal thinking tags (constructed programmatically
    # so the tags survive in source without being stripped as markup).
    open_tag, close_tag = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)
    raw_message = f"Let me check that. {open_tag}internal reasoning{close_tag} Here is your answer."

    sent = await service.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message=raw_message,
    )

    assert sent is True
    delivered = adapter.send_message.await_args.kwargs["message"]
    assert "<think" not in delivered.lower()
    assert "internal reasoning" not in delivered
    assert "Here is your answer." in delivered
    assert "Let me check that." in delivered


async def test_send_agent_message_returns_false_when_only_thinking_tokens():
    """If the entire message is thinking content, nothing is sent."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    open_tag, close_tag = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)
    raw_message = f"{open_tag}All reasoning, no answer{close_tag}"

    sent = await service.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message=raw_message,
    )

    assert sent is False
    adapter.send_message.assert_not_awaited()


async def test_send_display_resource_for_conversation_sends_render_plan():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    sent = await service.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request={"type": "TABLE", "name": "deals"},
        tool_call_id="tool-display-1",
        tool_output={"success": True},
    )

    assert sent is True
    adapter._render_resource.assert_awaited_once()
    render_plan = adapter._render_resource.await_args.kwargs["render_plan"]
    assert isinstance(render_plan, SurfaceDisplayRenderPlan)
    assert render_plan.title == "Table: deals"
    assert render_plan.primary_action is not None
    assert "/pod/" in render_plan.primary_action.url
    assert "tab=deals" in render_plan.primary_action.url


async def test_a_delivered_file_carries_no_caption(monkeypatch):
    """A file goes out as the file, and nothing is written on it.

    The caption used to be the file's own name — which Telegram, WhatsApp and
    Slack all print on the bubble already, so the one line a media message can
    carry said only what the reader could see. Anything worth saying about the
    file is its own message.
    """
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    adapter._render_file.return_value = True
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    resolve = AsyncMock(
        return_value=PodFileParts(
            files=[
                EnvelopeFile(
                    file_name="shiplog.pdf",
                    content=b"%PDF",
                    mime_type="application/pdf",
                )
            ],
            facts=PodFileDelivery(delivered=True),
        )
    )
    monkeypatch.setattr(surface_egress, "resolve_pod_file_parts", resolve)

    sent = await service.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request={"type": "FILE", "path": "/me/reports/shiplog.pdf"},
        tool_call_id="tool-file-caption",
    )

    assert sent is True
    assert resolve.await_args.kwargs["caption"] is None


async def _ask_user_link(surface, conversation_id, parsed_event):
    return AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )


_ASK_USER_QUESTIONS = [
    {
        "question": "Pick a color",
        "header": "color",
        "options": [{"label": "Red"}, {"label": "Blue"}],
    }
]
# Wrapped shape (hand-built / legacy). pydantic-ai actually flattens the single
# `request: AskUserRequest` param, so production persists the FLAT shape below.
_ASK_USER_TOOL_ARGS = {"request": {"questions": _ASK_USER_QUESTIONS}}
_ASK_USER_TOOL_ARGS_FLAT = {"questions": _ASK_USER_QUESTIONS}


async def test_send_questions_for_conversation_renders_native_then_falls_back():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_choices.return_value = True
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_question.return_value = _pending(
        "ask_user", tool_call_id="tool-1", tool_args=_ASK_USER_TOOL_ARGS
    )

    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    plan = adapter._render_choices.await_args.kwargs["question_plan"]
    assert isinstance(plan, SurfaceQuestionRenderPlan)
    assert [q.header for q in plan.questions] == ["color"]
    assert plan.callback_id == f"{conversation_id}|tool-1"
    adapter.send_message.assert_not_awaited()

    # When native render returns False, it falls back to a formatted text message.
    adapter._render_choices.return_value = False
    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_send_questions_reads_flattened_pydantic_ai_args():
    """Regression: pydantic-ai flattens ask_user's single-model param, so the
    persisted args are {"questions": [...]} (NOT {"request": {...}}). The question
    must still be delivered — reading tool_args["request"] here swallowed it in
    production (no card, no text, run stuck WAITING)."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_choices.return_value = True
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_question.return_value = _pending(
        # The real production shape: pydantic-ai flattens the single model
        # parameter, so the args are the model's own fields.
        "ask_user",
        tool_call_id="tool-1",
        tool_args=_ASK_USER_TOOL_ARGS_FLAT,
    )

    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    plan = adapter._render_choices.await_args.kwargs["question_plan"]
    assert [q.header for q in plan.questions] == ["color"]

    # Native False → guaranteed text fallback still fires with the flat shape.
    adapter._render_choices.return_value = False
    await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_ask_user_request_dict_accepts_both_shapes():
    from app.modules.agent_surfaces.services.pending_interaction_resume import (
        _ask_user_request_dict,
    )

    assert _ask_user_request_dict(_ASK_USER_TOOL_ARGS_FLAT) == {
        "questions": _ASK_USER_QUESTIONS
    }
    assert _ask_user_request_dict(_ASK_USER_TOOL_ARGS) == {
        "questions": _ASK_USER_QUESTIONS
    }
    assert _ask_user_request_dict({"foo": 1}) is None
    assert _ask_user_request_dict("not-a-dict") is None
    assert _ask_user_request_dict(None) is None


async def test_handle_interaction_resumes_via_approval_path():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    owner = link.external_user_id
    conversation = _surface_conversation(surface, conversation_id=conversation_id)
    agent_conversations.surface_conversation.return_value = conversation

    interaction = ParsedSurfaceInteraction(
        platform=SurfacePlatform.SLACK,
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=owner,
        callback_id=f"{conversation_id}|tool-1",
        values={"color": "Red", "color__other": "Teal"},
        dedup_id="m-1",
    )
    await service.handle_interaction(interaction)

    agent_conversations.resolve_pending_interaction.assert_awaited_once()
    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["approval_id"] == "tool-1"
    assert kwargs["decision"] == AgentRunApprovalDecision.APPROVE_ONCE
    # "Other" free text overrides the selected option for that question.
    assert kwargs["response"] == {"answers": {"color": "Teal"}}
    # An ask_user answer must NOT be injected as a plain user message.
    agent_conversations.start_surface_turn.assert_not_awaited()


@pytest.mark.parametrize(
    "link_id, sender_id, why",
    [
        ("U-owner", "U-mallory", "somebody else in the channel"),
        (None, "U-mallory", "a link that never learned who it belongs to"),
        ("U-owner", None, "a payload that named no submitter"),
    ],
)
async def test_a_tap_we_cannot_attribute_resolves_nothing(link_id, sender_id, why):
    """The control in front of a native Approve, exercised end to end.

    It runs between the replay-dedup claim and `resolve_user_approval_internal`,
    so whatever it lets through is what executes. Two of these three used to be
    allowed: the match returned True whenever *either* id was empty, and both
    are empty in ordinary traffic.
    """
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    link.external_user_id = link_id
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.surface_conversation.return_value = _surface_conversation(
        surface
    )

    await service.handle_interaction(
        ParsedSurfaceInteraction(
            platform=SurfacePlatform.SLACK,
            external_channel_id=parsed_event.external_channel_id,
            external_thread_id=parsed_event.external_thread_id,
            external_user_id=sender_id,
            callback_id=f"{conversation_id}|tool-1",
            values={"decision": "APPROVE_ONCE"},
            dedup_id="m-refused",
        )
    )

    agent_conversations.resolve_pending_interaction.assert_not_awaited()
    agent_conversations.start_surface_turn.assert_not_awaited()
    # And the person is told, or the button just looks broken.
    said = adapter.acknowledge_interaction.await_args.kwargs["text"]
    assert "reply" in said.lower(), f"{why}: should point at the typed reply"


@pytest.mark.parametrize(
    "decision_value, expected",
    [
        ("APPROVE_ONCE", AgentRunApprovalDecision.APPROVE_ONCE),
        ("DENY", AgentRunApprovalDecision.DENY),
        ("APPROVE_FOR_SESSION", AgentRunApprovalDecision.APPROVE_FOR_SESSION),
    ],
)
async def test_handle_interaction_routes_approval_decision(decision_value, expected):
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    owner = link.external_user_id
    conversation = _surface_conversation(surface, conversation_id=conversation_id)
    agent_conversations.surface_conversation.return_value = conversation

    interaction = ParsedSurfaceInteraction(
        platform=SurfacePlatform.SLACK,
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=owner,
        callback_id=f"{conversation_id}|tool-9",
        approval_decision=decision_value,
        dedup_id="m-approval-1",
    )
    await service.handle_interaction(interaction)

    agent_conversations.resolve_pending_interaction.assert_awaited_once()
    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["approval_id"] == "tool-9"
    assert kwargs["decision"] == expected
    # An approval button carries a decision, not an answer payload.
    assert kwargs["response"] == {}


async def test_handle_retry_resolves_conversation_from_current_thread_link():
    surface = _telegram_surface()
    user_id = uuid4()
    conversation = _conversation(surface, user_id)
    event = _telegram_event(chat_id="123", message_id="5")
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation.id,
        platform="TELEGRAM",
        external_channel_id="123",
        external_thread_id="123",
        external_user_id="777",
        routed_agent_id=surface.agent_id,
        last_event=event.model_dump(mode="json"),
    )
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        existing_link=link,
    )
    service.conversation_link_repository.find_surface_id_for_external_thread.return_value = surface.id
    agent_conversations.surface_conversation.return_value = conversation
    service._refresh_interaction_conversation = AsyncMock(
        return_value=(link, conversation, False)
    )
    interaction = ParsedSurfaceInteraction(
        platform=SurfacePlatform.TELEGRAM,
        external_channel_id="123",
        external_thread_id="123",
        external_user_id="777",
        action="retry",
        dedup_id="cbq-retry",
    )

    await service.handle_interaction(interaction)
    retry = agent_conversations.retry_failed_run

    service.conversation_link_repository.get_by_conversation_id.assert_not_awaited()
    service.conversation_link_repository.get_by_external_thread.assert_awaited_once_with(
        surface_id=surface.id,
        platform="TELEGRAM",
        external_channel_id="123",
        external_thread_id="123",
        external_user_id="777",
    )
    retry.assert_awaited_once()
    assert retry.await_args.kwargs["conversation_id"] == conversation.id
    adapter.acknowledge_interaction.assert_awaited_once_with(
        credentials={},
        interaction=interaction,
        text="Retrying…",
        clear_actions=True,
    )


async def test_refresh_retry_uses_normal_agent_change_reset_policy():
    old_agent_id = uuid4()
    new_agent_id = uuid4()
    surface = _telegram_surface(agent_id=new_agent_id)
    user_id = uuid4()
    old_conversation = _conversation(surface, user_id).model_copy(
        update={"agent_id": old_agent_id}
    )
    new_conversation = _conversation(surface, user_id)
    event = _telegram_event(chat_id="123", message_id="5")
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=old_conversation.id,
        platform="TELEGRAM",
        external_channel_id="123",
        external_thread_id="123",
        external_user_id="777",
        # Even if denormalized route metadata was already updated, the actual
        # conversation's agent remains authoritative for deciding to reset.
        routed_agent_id=new_agent_id,
        last_event=event.model_dump(mode="json"),
    )
    adapter = AsyncMock()
    service = _build_service(
        adapter=adapter,
        surfaces=[surface],
        conversation=new_conversation,
        existing_link=link,
    )
    agent_conversations.surface_conversation.return_value = new_conversation

    refreshed = await service._refresh_interaction_conversation(
        link=link,
        surface=surface,
        conversation=old_conversation,
    )

    assert refreshed is not None
    refreshed_link, refreshed_conversation, restarted = refreshed
    assert restarted is True
    assert refreshed_link.conversation_id == new_conversation.id
    assert refreshed_conversation is new_conversation
    agent_conversations.open_surface_conversation.assert_awaited_once()
    update = service.conversation_link_repository.update_conversation.await_args.kwargs
    assert update["conversation_id"] == new_conversation.id
    assert update["routed_agent_id"] == new_agent_id


_REQUEST_APPROVAL_TOOL_ARGS = {
    "tool_name": "pod_write_record",
    "title": "Write a record",
    "reason": "The agent wants to write a record to your table.",
    "args": {"table_id": "tbl-1", "data": {"col": "val"}},
}


async def test_send_approval_prompt_renders_native_buttons():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = True  # platform rendered native buttons
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    sent = await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    assert sent is True
    # Native render is attempted; the plan carries Approve + Deny and the callback.
    plan = adapter._render_decision.await_args.kwargs["approval_plan"]
    assert [b.decision for b in plan.buttons] == ["APPROVE_ONCE", "DENY"]
    assert plan.callback_id == f"{conversation_id}|tool-2"
    assert plan.title == "Write a record"
    # No permission_ids on this call → no approve-for-session button.
    assert all(b.decision != "APPROVE_FOR_SESSION" for b in plan.buttons)
    # When native buttons render, we do NOT also post the text prompt.
    adapter.send_message.assert_not_awaited()


async def test_an_older_unanswered_question_does_not_shadow_the_approval():
    """The bug this pairing exists to catch.

    A conversation can hold more than one unresolved pause. An `ask_user`
    nobody ever tapped stays unresolved forever, and being older it is what
    "what is this conversation waiting on" returns — so the approval renderer,
    which asked that question and then discarded anything that was not an
    approval, delivered nothing at all and left the run WAITING with nobody
    told. On a chat surface, where one conversation stands for the whole
    relationship with a person, that is permanent: dev's standing Telegram chat
    stopped rendering approval cards entirely.

    Wired through a stand-in that filters the way the real lookup does, so this
    fails if the renderer goes back to asking the unfiltered question.
    """
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    adapter.deliver.return_value = DeliveryReceipt(
        parts={"decision": PartDelivery.NATIVE}
    )
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link

    stale_question = _pending("ask_user", tool_call_id="tool-ask")
    the_approval = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )
    # Oldest first, exactly as `oldest_unresolved_pause` walks them.
    pauses = [stale_question, the_approval]

    agent_conversations.pending_interaction.return_value = pauses[0]
    agent_conversations.pending_approval.return_value = next(
        (pause for pause in pauses if pause.is_approval), None
    )

    sent = await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )

    assert sent is True
    # Through `deliver`, not `send_approval`: the per-content outbound verbs
    # became `_render_*` hooks only `deliver` calls, and this assertion was
    # left naming a method nothing invokes -- so it read `await_args` off a
    # never-awaited mock and died on None rather than checking the plan.
    plan = adapter.deliver.await_args.kwargs["envelope"].decision
    assert plan.title == "Write a record"
    assert [b.decision for b in plan.buttons] == ["APPROVE_ONCE", "DENY"]


async def test_send_approval_prompt_falls_back_to_text():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = False  # platform has no native buttons
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    sent = await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    assert sent is True
    msg = adapter.send_message.await_args.kwargs["message"]
    assert "Write a record" in msg
    assert "approve" in msg.lower()
    assert "deny" in msg.lower()


async def test_send_approval_prompt_adds_session_button_with_permission_ids():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    adapter._render_decision.return_value = True
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_approval.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args={**_REQUEST_APPROVAL_TOOL_ARGS, "permission_ids": ["perm-1"]},
    )

    await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    plan = adapter._render_decision.await_args.kwargs["approval_plan"]
    assert [b.decision for b in plan.buttons] == [
        "APPROVE_ONCE",
        "DENY",
        "APPROVE_FOR_SESSION",
    ]


async def test_send_approval_prompt_skips_when_no_pending():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_approval.return_value = None

    sent = await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id
    )
    assert sent is False
    adapter.send_message.assert_not_awaited()


async def test_an_email_surface_delivers_the_question_in_its_one_reply():
    """Email is asked, not suppressed. The prompt rides in the reply as text."""
    surface = _resend_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="RESEND",
        external_channel_id=parsed_event.external_channel_id,
        external_thread_id=parsed_event.external_thread_id,
        external_user_id=parsed_event.sender_external_user_id,
        last_event=parsed_event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter("RESEND")
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    agent_conversations.pending_question.return_value = _pending(
        "ask_user", tool_call_id="tool-1", tool_args=_ASK_USER_TOOL_ARGS
    )

    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )

    assert sent is True
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_send_to_member_reuses_existing_thread():
    """surface.send reaches a pod member by reusing their existing thread."""
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = _delivering_adapter()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    service.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[
                SimpleNamespace(external_user_id=link.external_user_id, tenant_id=None)
            ]
        )
    )
    service.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=link)
    )

    undeliverable = await service.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="Your report is ready.",
    )
    assert undeliverable is None
    assert "Your report is ready." in adapter.send_message.await_args.kwargs["message"]


async def test_send_to_member_uses_requested_surface_latest_thread():
    surface = _telegram_surface()
    user_id = uuid4()
    older_link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="older-chat",
        external_thread_id="older-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="older-chat", message_id="older-message"
        ).model_dump(mode="json"),
    )
    latest_link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="latest-chat",
        external_thread_id="latest-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="latest-chat", message_id="latest-message"
        ).model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    service = _build_service(
        adapter=adapter, surfaces=[surface], existing_link=older_link
    )
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    service.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="777", tenant_id=None)]
        )
    )
    service.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=latest_link)
    )
    service.conversation_link_repository.get_by_conversation_id.return_value = (
        latest_link
    )

    undeliverable = await service.send_to_member(
        surface=surface,
        user_id=user_id,
        message="Use the newest thread.",
    )

    assert undeliverable is None
    service.conversation_link_repository.get_latest_by_surface_and_external_user.assert_awaited_once_with(
        surface_id=surface.id,
        external_user_id="777",
    )
    event = adapter.send_message.await_args.kwargs["event"]
    assert event.external_thread_id == "latest-chat"
    assert event.reply_target["chat_id"] == "latest-chat"


async def test_send_to_member_does_not_confuse_system_and_custom_threads():
    system_surface = _telegram_surface()
    custom_surface = _telegram_surface()
    user_id = uuid4()
    system_link = AgentSurfaceConversationLink(
        surface_id=system_surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="system-chat",
        external_thread_id="system-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="system-chat", message_id="system-message"
        ).model_dump(mode="json"),
    )
    custom_link = AgentSurfaceConversationLink(
        surface_id=custom_surface.id,
        conversation_id=uuid4(),
        platform="TELEGRAM",
        external_channel_id="custom-chat",
        external_thread_id="custom-chat",
        external_user_id="777",
        last_event=_telegram_event(
            chat_id="custom-chat", message_id="custom-message"
        ).model_dump(mode="json"),
    )
    links_by_surface = {
        system_surface.id: system_link,
        custom_surface.id: custom_link,
    }
    links_by_conversation = {
        system_link.conversation_id: system_link,
        custom_link.conversation_id: custom_link,
    }
    adapter = _delivering_adapter()
    service = _build_service(
        adapter=adapter,
        surfaces=[system_surface, custom_surface],
        existing_link=system_link,
    )
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(
            return_value=[system_surface.pod_id, custom_surface.pod_id]
        )
    )
    service.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="777", tenant_id=None)]
        )
    )
    service.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(
            side_effect=lambda *, surface_id, external_user_id: links_by_surface[
                surface_id
            ]
        )
    )
    service.conversation_link_repository.get_by_conversation_id.side_effect = (
        lambda conversation_id: links_by_conversation[conversation_id]
    )

    custom_sent = await service.send_to_member(
        surface=custom_surface,
        user_id=user_id,
        message="custom only",
    )
    system_sent = await service.send_to_member(
        surface=system_surface,
        user_id=user_id,
        message="system only",
    )

    assert custom_sent is None
    assert system_sent is None
    first_event = adapter.send_message.await_args_list[0].kwargs["event"]
    second_event = adapter.send_message.await_args_list[1].kwargs["event"]
    assert first_event.external_thread_id == "custom-chat"
    assert second_event.external_thread_id == "system-chat"


async def test_send_to_member_says_the_person_is_not_in_the_pod():
    surface = _slack_surface()
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[uuid4()])  # a different pod
    )

    undeliverable = await service.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="x",
    )
    # One 404 for six causes told a caller nothing: "no reachable conversation"
    # is not what happened to somebody who is not in the pod at all.
    assert undeliverable == UndeliverableReason.NOT_A_POD_MEMBER
    adapter.send_message.assert_not_awaited()


async def test_send_to_member_says_they_have_never_written_in():
    surface = _slack_surface()
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    service.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[SimpleNamespace(external_user_id="U-MEMBER", tenant_id=None)]
        )
    )
    service.conversation_link_repository.get_latest_by_surface_and_external_user = (
        AsyncMock(return_value=None)
    )

    undeliverable = await service.send_to_member(
        surface=surface,
        user_id=uuid4(),
        message="x",
    )
    assert undeliverable == UndeliverableReason.never_interacted_on("SLACK")
    adapter.send_message.assert_not_awaited()


async def test_maybe_resume_pending_interaction_handles_request_approval_approve():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = _surface_conversation(surface, conversation_id=conversation_id)
    agent_conversations.surface_conversation.return_value = conversation
    agent_conversations.pending_interaction.return_value = _pending(
        "request_approval",
        tool_call_id="tool-2",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    ctx = SimpleNamespace(
        conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id
    )
    resumed = await maybe_resume_pending_interaction(ctx, "approve", uow=service.uow)
    assert resumed is ResumeOutcome.CONSUMED
    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["approval_id"] == "tool-2"
    assert kwargs["decision"] == AgentRunApprovalDecision.APPROVE_ONCE


async def test_maybe_resume_pending_interaction_handles_request_approval_deny():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = _surface_conversation(surface, conversation_id=conversation_id)
    agent_conversations.surface_conversation.return_value = conversation
    agent_conversations.pending_interaction.return_value = _pending(
        "request_approval",
        tool_call_id="tool-3",
        tool_args=_REQUEST_APPROVAL_TOOL_ARGS,
    )

    ctx = SimpleNamespace(
        conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id
    )
    resumed = await maybe_resume_pending_interaction(ctx, "no", uow=service.uow)
    assert resumed is ResumeOutcome.CONSUMED
    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["decision"] == AgentRunApprovalDecision.DENY


async def test_maybe_resume_pending_interaction_parses_numbered_ask_user_option():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = _surface_conversation(surface, conversation_id=conversation_id)
    agent_conversations.surface_conversation.return_value = conversation
    agent_conversations.pending_interaction.return_value = _pending(
        "ask_user", tool_call_id="tool-4", tool_args=_ASK_USER_TOOL_ARGS
    )

    ctx = SimpleNamespace(
        conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id
    )
    # "2" → second option label "Blue"
    resumed = await maybe_resume_pending_interaction(ctx, "2", uow=service.uow)
    assert resumed is ResumeOutcome.CONSUMED
    kwargs = agent_conversations.resolve_pending_interaction.await_args.kwargs
    assert kwargs["decision"] == AgentRunApprovalDecision.APPROVE_ONCE
    assert kwargs["response"] == {"answers": {"color": "Blue"}}


async def test_transcribe_voice_attachments_joins_caption_and_voice(monkeypatch):
    import app.modules.agent.tools.speech.provider as speech_provider

    class _Result:
        text = "schedule a meeting tomorrow"
        detected_language = "en"
        duration_seconds = 3.2

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            return _Result()

    monkeypatch.setattr(speech_provider, "get_speech_provider", lambda: _Provider())
    service = _build_service(adapter=AsyncMock(), surfaces=[_slack_surface()])
    ingested = [
        IngestedAttachment(
            path="/me/telegram/note.ogg",
            name="note.ogg",
            mime="audio/ogg",
            content_type="voice",
            audio_bytes=b"OGG",
        )
    ]

    # Voice-only message → transcript becomes the whole prompt.
    meta: dict = {}
    text = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="", metadata=meta
    )
    assert text == "schedule a meeting tomorrow"
    assert meta["voice_transcripts"][0]["path"] == "/me/telegram/note.ogg"
    assert meta["voice_transcripts"][0]["detected_language"] == "en"

    # Caption + voice → both, caption first.
    text2 = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="fyi:", metadata={}
    )
    assert text2 == "fyi:\n\nschedule a meeting tomorrow"

    # The type word is not a caption. WhatsApp media carries none of its own,
    # so the parser falls back to the name of the kind of file it was -- and
    # every voice note reached the model as "audio\n\n<what they said>", which
    # reads as the person having typed the word "audio" first. Seven such
    # messages on dev, every one of them.
    text3 = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="voice", metadata={}
    )
    assert text3 == "schedule a meeting tomorrow"

    # A word somebody really typed survives, even where it looks like one.
    text4 = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="voice memo for you", metadata={}
    )
    assert text4 == "voice memo for you\n\nschedule a meeting tomorrow"


@pytest.mark.parametrize(
    "failure",
    [
        SpeechProviderError("deepgram down"),
        # A provider that breaks its own interface must not cost the person
        # their message either -- it is reported, not propagated.
        TimeoutError("the client raised something the interface never promised"),
    ],
    ids=["declared_failure", "undeclared_failure"],
)
async def test_transcribe_voice_falls_back_when_provider_fails(monkeypatch, failure):
    import app.modules.agent.tools.speech.provider as speech_provider

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            raise failure

    monkeypatch.setattr(speech_provider, "get_speech_provider", lambda: _Provider())
    service = _build_service(adapter=AsyncMock(), surfaces=[_slack_surface()])
    ingested = [
        IngestedAttachment(
            path="/me/telegram/note.ogg",
            name="note.ogg",
            mime="audio/ogg",
            content_type="voice",
            audio_bytes=b"OGG",
        )
    ]
    meta: dict = {}
    text = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="", metadata=meta
    )
    # Voice-only message never becomes an empty prompt.
    assert text == "[voice message]"
    assert meta["voice_transcription_failed"] is True


async def test_transcribe_noop_without_audio(monkeypatch):
    service = _build_service(adapter=AsyncMock(), surfaces=[_slack_surface()])
    ingested = [
        IngestedAttachment(
            path="/me/slack/report.pdf", name="report.pdf", mime="application/pdf"
        )
    ]
    text = await service._transcribe_voice_attachments(
        ingested=ingested, original_text="see attached", metadata={}
    )
    assert text == "see attached"


async def test_send_processing_indicator_for_conversation_stops_without_link():
    surface = _teams_surface()
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    service.conversation_link_repository.get_by_conversation_id.return_value = None

    sent = await service.send_processing_indicator_for_conversation(
        conversation_id=uuid4(),
    )

    assert sent is False
    adapter.add_processing_indicator.assert_not_awaited()


def _resend_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="resend",
        surface_type=SurfacePlatform.RESEND,
        mode=SurfaceMode.EMAIL,
        config=SurfaceConfig(),
        surface_identity_email="agent.pod@ops.test",
        is_active=True,
    )


async def test_a_permanent_credential_refusal_is_not_retried_forever():
    """A 401 does not become a 200 by trying again.

    On dev a Resend key restricted to sending answered `GET /emails/receiving`
    with 401. Enrichment raised to make the delivery retryable — correct for a
    timeout or a 429 — and the worker spent eight attempts on an answer that
    could not change, burying the cause under repeats of itself.
    """
    import httpx

    surface = _resend_surface()
    event = ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="agent.pod@ops.test",
        external_thread_id="<seed@ops.test>",
        external_message_id="<reply-1@example.com>",
        sender_external_user_id="bob@example.com",
        sender_email="bob@example.com",
        message_text="",
        is_dm=True,
        mentioned_agent=True,
        reply_target={},
    )
    refused = httpx.HTTPStatusError(
        "401",
        request=httpx.Request("GET", "https://api.resend.com/emails/receiving/x"),
        response=httpx.Response(401, json={"name": "restricted_api_key"}),
    )
    adapter = AsyncMock()
    adapter.enrich_inbound_event.side_effect = refused

    service = _build_service(adapter=AsyncMock(), surfaces=[surface])
    service._resolve_credentials = AsyncMock(return_value={})

    context = await service._prepare_surface_context(
        surface=surface, parsed=event, adapter=adapter
    )

    # Dropped, not raised: raising is what schedules the next identical attempt.
    assert context is None


async def test_a_transient_failure_is_still_raised_so_it_retries():
    """The distinction that makes the above safe: 429 and 5xx do change."""
    import httpx

    surface = _resend_surface()
    event = ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="agent.pod@ops.test",
        external_thread_id="<seed@ops.test>",
        external_message_id="<reply-2@example.com>",
        sender_external_user_id="bob@example.com",
        sender_email="bob@example.com",
        message_text="",
        is_dm=True,
        mentioned_agent=True,
        reply_target={},
    )
    adapter = AsyncMock()
    adapter.enrich_inbound_event.side_effect = httpx.HTTPStatusError(
        "429",
        request=httpx.Request("GET", "https://api.resend.com/emails/receiving/x"),
        response=httpx.Response(429, json={"name": "rate_limit_exceeded"}),
    )

    service = _build_service(adapter=AsyncMock(), surfaces=[surface])
    service._resolve_credentials = AsyncMock(return_value={})

    with pytest.raises(httpx.HTTPStatusError):
        await service._prepare_surface_context(
            surface=surface, parsed=event, adapter=adapter
        )


async def test_a_whole_ingest_failure_still_tells_the_agent_the_files_arrived():
    """A blown-up ingest must not read to the agent as "they sent no files".

    Per-file failures are already reported through ``failed_files``. When
    ``ingest_attachments`` itself raises, the report was thrown away with the
    files: the agent answered the text alone and looked like it ignored the
    photo -- the exact outcome ``failed_files`` exists to prevent.
    """
    surface = _slack_surface(agent_id=None)
    conversation = _conversation(surface, uuid4())
    service = _build_service(
        adapter=AsyncMock(), surfaces=[surface], conversation=conversation
    )
    service.file_ingest_service = SimpleNamespace(
        ingest_attachments=AsyncMock(side_effect=RuntimeError("datastore is down"))
    )
    context = _slack_chat_context(surface, conversation, "here you go")
    context.event.metadata["attachments"] = [
        {"name": "receipt.pdf"},
        {"name": "photo.jpg"},
    ]

    await service.execute_chat(context)

    kwargs = agent_conversations.start_surface_turn.await_args.kwargs
    failed = kwargs["message_metadata"]["failed_files"]
    assert [item["name"] for item in failed] == ["receipt.pdf", "photo.jpg"]


async def test_send_to_member_says_the_surface_is_switched_off():
    surface = _slack_surface()
    surface.status = AgentSurfaceStatus.INACTIVE
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])

    undeliverable = await service.send_to_member(
        surface=surface, user_id=uuid4(), message="x"
    )

    assert undeliverable == UndeliverableReason.SURFACE_NOT_ACTIVE
    adapter.send_message.assert_not_awaited()


async def test_send_to_member_says_the_person_is_in_another_workspace():
    """Slack ids are per workspace, so "never written in" would be a lie here.

    They have written in — to a different Slack workspace than the one this
    surface is connected to, which is not something they can fix by messaging
    the bot again.
    """
    surface = _slack_surface().model_copy(update={"external_workspace_id": "T-HOME"})
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface])
    service.pod_membership_port = SimpleNamespace(
        get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
    )
    service.external_user_repository = AsyncMock(
        list_by_resolved_users=AsyncMock(
            return_value=[
                SimpleNamespace(external_user_id="U-MEMBER", tenant_id="T-ELSEWHERE")
            ]
        )
    )

    undeliverable = await service.send_to_member(
        surface=surface, user_id=uuid4(), message="x"
    )

    assert undeliverable == UndeliverableReason.wrong_tenant_on("SLACK")
    assert undeliverable != UndeliverableReason.never_interacted_on("SLACK")
    adapter.send_message.assert_not_awaited()
