from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ConversationType,
    SurfaceChannelRoute,
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
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceMessageMetadata,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
)

pytestmark = pytest.mark.asyncio


_ROUTE_AGENT_IDS: dict[str, object] = {}


def _registry(adapter):
    return SimpleNamespace(get=lambda platform: adapter)


def _slack_surface(*, agent_id: UUID | None = None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
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
        agent_id=agent_id if agent_id is not None else uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        mode=SurfaceMode.DM,
        account_id=None,
        config=SurfaceConfig(),
        is_active=True,
    )


def _gmail_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        surface_type=SurfacePlatform.GMAIL,
        mode=SurfaceMode.EMAIL,
        account_id=uuid4(),
        config=SurfaceConfig(),
        surface_identity_email="assistant@gmail.test",
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

    conversation_service = AsyncMock()
    conversation_service.agent_repository = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(name="Surface Agent")
            if any(surface.agent_id for surface in resolved_surfaces)
            else None
        ),
        get_by_pod_and_name=AsyncMock(
            side_effect=lambda *, pod_id, name: SimpleNamespace(
                id=_ROUTE_AGENT_IDS.get(name, uuid4()), name=name
            )
        ),
    )
    if conversation is not None:
        conversation_service.create_conversation.return_value = conversation

    session_model = SimpleNamespace(conversation_metadata={})
    organization_id = uuid4()

    async def _fake_get(model, item_id):
        del item_id
        if getattr(model, "__name__", "") == "Pod":
            return SimpleNamespace(organization_id=organization_id)
        return session_model

    uow = SimpleNamespace(
        session=SimpleNamespace(
            get=AsyncMock(side_effect=_fake_get),
            execute=AsyncMock(return_value=_EmptyExecuteResult()),
            flush=AsyncMock(),
        )
    )

    adapter.enrich_inbound_event.side_effect = lambda *, credentials, event: event
    adapter.unresolved_sender_reply = Mock(return_value=None)
    adapter.linked_sender_confirmation = Mock(return_value=None)
    service = AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository,
        conversation_link_repository=conversation_link_repository,
        conversation_service=conversation_service,
        connector_service=AsyncMock(),
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
    assert context.agent_display_name == "Surface Agent"
    service.conversation_service.create_conversation.assert_not_called()


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
    create_kwargs = service.conversation_service.create_conversation.await_args.kwargs
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
    service.conversation_service.create_conversation.assert_not_called()
    service.conversation_link_repository.update_last_event.assert_awaited_once()


async def test_prepare_webhook_routes_slack_channel_from_surface_config():
    route_agent_id = uuid4()
    surface = _slack_surface()
    surface.mode = SurfaceMode.DM
    surface.config = SurfaceConfig(
        channels=[
            SurfaceChannelRoute(channel_id="C999", agent_name="Channel Agent"),
        ]
    )
    _ROUTE_AGENT_IDS["Channel Agent"] = route_agent_id
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
    assert created_link.routed_agent_id == route_agent_id
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
    service.conversation_service.create_conversation.assert_awaited_once()
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
    service.conversation_service.create_conversation.assert_not_called()


async def test_prepare_surface_context_ignores_self_sent_gmail_message():
    surface = _gmail_surface()
    parsed_event = ParsedInboundSurfaceEvent(
        platform="GMAIL",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="assistant@gmail.test",
        external_thread_id="gmail-thread-1",
        external_message_id="gmail-message-1",
        sender_external_user_id="assistant@gmail.test",
        sender_email="assistant@gmail.test",
        sender_display_name="Lemma",
        message_text="Loopback",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"recipient_email": "assistant@gmail.test"},
    )
    service = _build_service(adapter=AsyncMock(), surfaces=[surface])

    context = await service._prepare_surface_context(
        surface=surface,
        parsed=parsed_event,
        adapter=AsyncMock(),
    )

    assert context is None


async def test_prepare_surface_context_ignores_self_sent_outlook_after_enrich():
    # Outlook triggers deliver a minimal payload with no sender; the sender is
    # only known after enrichment. The surface must still drop its own outgoing
    # reply (sender == surface identity) instead of looping on the signup reply.
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        surface_type=SurfacePlatform.OUTLOOK,
        mode=SurfaceMode.EMAIL,
        account_id=uuid4(),
        config=SurfaceConfig(),
        surface_identity_email="assistant@outlook.test",
        is_active=True,
    )
    minimal_event = ParsedInboundSurfaceEvent(
        platform="OUTLOOK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="assistant@outlook.test",
        external_thread_id="outlook-thread-1",
        external_message_id="outlook-message-1",
        sender_external_user_id=None,
        sender_email=None,
        message_text="",
        is_dm=True,
        mentioned_agent=True,
        reply_target={},
    )
    enriched_event = minimal_event.model_copy(
        update={
            "sender_external_user_id": "assistant@outlook.test",
            "sender_email": "assistant@outlook.test",
            "message_text": "Email subject: Re\n\nLoopback body",
        }
    )
    adapter = AsyncMock()
    adapter.enrich_inbound_event.side_effect = lambda *, credentials, event: (
        enriched_event
    )

    service = _build_service(adapter=AsyncMock(), surfaces=[surface])
    service.event_dedup_store = AsyncMock(claim_message=AsyncMock(return_value=True))
    service._resolve_credentials = AsyncMock(return_value={})

    context = await service._prepare_surface_context(
        surface=surface,
        parsed=minimal_event,
        adapter=adapter,
    )

    assert context is None
    adapter.enrich_inbound_event.assert_awaited_once()


async def test_execute_chat_sends_direct_replies():
    parsed_event = _slack_event()
    adapter = AsyncMock()
    service = _build_service(adapter=adapter)
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
    service.conversation_service.add_user_message_and_start_run.assert_awaited_once()
    kwargs = (
        service.conversation_service.add_user_message_and_start_run.await_args.kwargs
    )
    assert kwargs["conversation_id"] == conversation.id
    assert kwargs["pod_id"] == surface.pod_id
    assert kwargs["agent_name"] is None
    assert kwargs["message_metadata"]["surface_platform"] == "SLACK"
    assert kwargs["message_metadata"]["external_message_id"] == "1700000000.000100"


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
    adapter = AsyncMock()
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
    adapter = AsyncMock()
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
    adapter = AsyncMock()
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
    adapter.send_display_resource.assert_awaited_once()
    render_plan = adapter.send_display_resource.await_args.kwargs["render_plan"]
    assert isinstance(render_plan, SurfaceDisplayRenderPlan)
    assert render_plan.title == "Table: deals"
    assert render_plan.primary_action is not None
    assert "/pod/" in render_plan.primary_action.url
    assert "tab=deals" in render_plan.primary_action.url


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


_ASK_USER_TOOL_ARGS = {
    "request": {
        "questions": [
            {
                "question": "Pick a color",
                "header": "color",
                "options": [{"label": "Red"}, {"label": "Blue"}],
            }
        ]
    }
}


async def test_send_questions_for_conversation_renders_native_then_falls_back():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    adapter.send_questions.return_value = True
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    service.conversation_service.get_pending_ask_user.return_value = {
        "tool_call_id": "tool-1",
        "tool_args": _ASK_USER_TOOL_ARGS,
        "agent_run_id": uuid4(),
    }

    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    plan = adapter.send_questions.await_args.kwargs["question_plan"]
    assert isinstance(plan, SurfaceQuestionRenderPlan)
    assert [q.header for q in plan.questions] == ["color"]
    assert plan.callback_id == f"{conversation_id}|tool-1"
    adapter.send_message.assert_not_awaited()

    # When native render returns False, it falls back to a formatted text message.
    adapter.send_questions.return_value = False
    sent = await service.send_questions_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-1"
    )
    assert sent is True
    assert "Pick a color" in adapter.send_message.await_args.kwargs["message"]


async def test_handle_interaction_resumes_via_approval_path():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    owner = link.external_user_id
    conversation = SimpleNamespace(user_id=uuid4(), pod_id=surface.pod_id)
    service.conversation_service.conversation_repository = SimpleNamespace(
        get_conversation=AsyncMock(return_value=conversation)
    )

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

    service.conversation_service.resolve_user_approval_internal.assert_awaited_once()
    kwargs = service.conversation_service.resolve_user_approval_internal.await_args.kwargs
    assert kwargs["approval_id"] == "tool-1"
    assert kwargs["decision"] == AgentRunApprovalDecision.APPROVE_ONCE
    # "Other" free text overrides the selected option for that question.
    assert kwargs["response"] == {"answers": {"color": "Teal"}}
    # An ask_user answer must NOT be injected as a plain user message.
    service.conversation_service.add_user_message_and_start_run.assert_not_awaited()


_REQUEST_APPROVAL_TOOL_ARGS = {
    "tool_name": "pod_write_record",
    "title": "Write a record",
    "reason": "The agent wants to write a record to your table.",
    "args": {"table_id": "tbl-1", "data": {"col": "val"}},
}


async def test_send_approval_prompt_for_conversation_sends_text():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    service.conversation_service.get_pending_user_interaction.return_value = {
        "tool_call_id": "tool-2",
        "kind": "request_approval",
        "tool_args": _REQUEST_APPROVAL_TOOL_ARGS,
        "agent_run_id": uuid4(),
    }

    sent = await service.send_approval_prompt_for_conversation(
        conversation_id=conversation_id, tool_call_id="tool-2"
    )
    assert sent is True
    msg = adapter.send_message.await_args.kwargs["message"]
    assert "Write a record" in msg
    assert "approve" in msg.lower()
    assert "deny" in msg.lower()


async def test_send_approval_prompt_skips_when_no_pending():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    service.conversation_link_repository.get_by_conversation_id.return_value = link
    service.conversation_service.get_pending_user_interaction.return_value = None

    sent = await service.send_approval_prompt_for_conversation(conversation_id=conversation_id)
    assert sent is False
    adapter.send_message.assert_not_awaited()


async def test_maybe_resume_pending_interaction_handles_request_approval_approve():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = SimpleNamespace(user_id=uuid4(), pod_id=surface.pod_id)
    service.conversation_service.conversation_repository = SimpleNamespace(
        get_conversation=AsyncMock(return_value=conversation)
    )
    service.conversation_service.get_pending_user_interaction.return_value = {
        "tool_call_id": "tool-2",
        "kind": "request_approval",
        "tool_args": _REQUEST_APPROVAL_TOOL_ARGS,
        "agent_run_id": uuid4(),
    }

    ctx = SimpleNamespace(conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id)
    resumed = await service._maybe_resume_pending_interaction(ctx, "approve")
    assert resumed is True
    kwargs = service.conversation_service.resolve_user_approval_internal.await_args.kwargs
    assert kwargs["approval_id"] == "tool-2"
    assert kwargs["decision"] == AgentRunApprovalDecision.APPROVE_ONCE


async def test_maybe_resume_pending_interaction_handles_request_approval_deny():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = SimpleNamespace(user_id=uuid4(), pod_id=surface.pod_id)
    service.conversation_service.conversation_repository = SimpleNamespace(
        get_conversation=AsyncMock(return_value=conversation)
    )
    service.conversation_service.get_pending_user_interaction.return_value = {
        "tool_call_id": "tool-3",
        "kind": "request_approval",
        "tool_args": _REQUEST_APPROVAL_TOOL_ARGS,
        "agent_run_id": uuid4(),
    }

    ctx = SimpleNamespace(conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id)
    resumed = await service._maybe_resume_pending_interaction(ctx, "no")
    assert resumed is True
    kwargs = service.conversation_service.resolve_user_approval_internal.await_args.kwargs
    assert kwargs["decision"] == AgentRunApprovalDecision.DENY


async def test_maybe_resume_pending_interaction_parses_numbered_ask_user_option():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = _build_service(adapter=adapter, surfaces=[surface], existing_link=link)
    conversation = SimpleNamespace(user_id=uuid4(), pod_id=surface.pod_id)
    service.conversation_service.conversation_repository = SimpleNamespace(
        get_conversation=AsyncMock(return_value=conversation)
    )
    service.conversation_service.get_pending_user_interaction.return_value = {
        "tool_call_id": "tool-4",
        "kind": "ask_user",
        "tool_args": _ASK_USER_TOOL_ARGS,
        "agent_run_id": uuid4(),
    }

    ctx = SimpleNamespace(conversation_id=conversation_id, user_id=uuid4(), pod_id=surface.pod_id)
    # "2" → second option label "Blue"
    resumed = await service._maybe_resume_pending_interaction(ctx, "2")
    assert resumed is True
    kwargs = service.conversation_service.resolve_user_approval_internal.await_args.kwargs
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


async def test_transcribe_voice_falls_back_when_provider_fails(monkeypatch):
    import app.modules.agent.tools.speech.provider as speech_provider

    class _Provider:
        async def transcribe(self, audio_bytes, *, mime, language=None):
            raise RuntimeError("deepgram down")

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
        IngestedAttachment(path="/me/slack/report.pdf", name="report.pdf", mime="application/pdf")
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
