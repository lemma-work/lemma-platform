from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
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
from app.modules.agent_surfaces.domain.models import (
    SurfaceSenderProfile,
)
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    ResumeOutcome,
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _ASK_USER_TOOL_ARGS,
    _ASK_USER_TOOL_ARGS_FLAT,
    _ASK_USER_QUESTIONS,
    _REQUEST_APPROVAL_TOOL_ARGS,
    _ask_user_link,
    _conversation,
    _pending,
    _resend_surface,
    _slack_channel_event,
    _slack_event,
    _slack_surface,
    _surface_conversation,
    _teams_surface,
    _telegram_event,
    _telegram_surface,
    build_ingress_service,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    # On the router, which is the one holder of the question now.
    service.router.pod_membership_port = SimpleNamespace(
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
    service = build_ingress_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(),
            external_user_id="999",
            email="member@example.com",
            display_name="Member",
        ),
    )
    service.router.pod_membership_port = SimpleNamespace(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
        adapter=adapter,
        surfaces=surfaces,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=uuid4(),
            external_user_id="777",
            email="signed-in@example.com",
        ),
    )
    service.router.pod_membership_port = SimpleNamespace(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
        ),
    )
    service.router.resolve_route = AsyncMock(return_value=None)

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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    service = build_ingress_service(
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
    service = build_ingress_service(adapter=adapter, surfaces=[surface])

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
    service = build_ingress_service(
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
    service = build_ingress_service(adapter=adapter, surfaces=[surface])
    service.event_dedup_store.claim_message.return_value = False

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})
    )

    assert context is None
    agent_conversations.open_surface_conversation.assert_not_called()


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
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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
    service = build_ingress_service(
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
    service = build_ingress_service(
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


async def test_maybe_resume_pending_interaction_handles_request_approval_approve():
    surface = _slack_surface()
    conversation_id = uuid4()
    parsed_event = _slack_event()
    link = await _ask_user_link(surface, conversation_id, parsed_event)
    adapter = AsyncMock()
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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
    service = build_ingress_service(
        adapter=adapter, surfaces=[surface], existing_link=link
    )
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

    service = build_ingress_service(adapter=AsyncMock(), surfaces=[surface])
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

    service = build_ingress_service(adapter=AsyncMock(), surfaces=[surface])
    service._resolve_credentials = AsyncMock(return_value={})

    with pytest.raises(httpx.HTTPStatusError):
        await service._prepare_surface_context(
            surface=surface, parsed=event, adapter=adapter
        )


def _journal_awaits(obj, entries: list[str], label: str) -> None:
    """Record every awaitable attribute of `obj` into `entries` as it is called."""
    for name in dir(obj):
        if name.startswith("__"):
            continue
        attr = getattr(obj, name, None)
        if not isinstance(attr, AsyncMock):
            continue

        def _wrap(inner=attr, tag=f"{label}.{name}"):
            async def _call(*args, **kwargs):
                entries.append(tag)
                return await inner(*args, **kwargs)

            return _call

        setattr(obj, name, _wrap())


def _journal_named(obj, entries: list[str], label: str, names) -> None:
    """`_journal_awaits` for named attributes, where walking would be unsafe."""
    for name in names:
        inner = getattr(obj, name, None)
        if not isinstance(inner, AsyncMock):
            continue

        def _wrap(inner=inner, tag=f"{label}.{name}"):
            async def _call(*args, **kwargs):
                entries.append(tag)
                return await inner(*args, **kwargs)

            return _call

        setattr(obj, name, _wrap())


def _claim_journal(service) -> list[str]:
    """Every await on the ingress path, in order.

    An ordering, not a count: a commit that lands anywhere before the claim
    satisfies a count, and this path releases several times on its way down --
    so the only assertion that means anything is that the release is the event
    *immediately* before the claim, with nothing in between to re-acquire.
    """
    entries: list[str] = []

    async def _release(*_args, **_kwargs) -> None:
        entries.append("release")

    async def _claim(**_kwargs) -> bool:
        entries.append("claim")
        return True

    # The adapter, the repositories and the identity service -- named rather
    # than walked off `service`, because a repository double *is* an
    # `AsyncMock` and walking the service's attributes would replace
    # `surface_repository` with a function. Everything the path can await
    # between the release and the claim has to be in here, or "the release is
    # the event immediately before the claim" is satisfied by an unrelated
    # earlier one and the test passes with the fix reverted.
    for target, label in (
        (service.adapter_registry.get(SurfacePlatform.SLACK), "adapter"),
        (service.surface_repository, "surfaces"),
        (service.conversation_link_repository, "links"),
        (service.router.identity_service, "identity"),
        (service.router.pod_membership_port, "membership"),
    ):
        if target is not None:
            _journal_awaits(target, entries, label)
    _journal_named(
        service,
        entries,
        "creds",
        (
            "_resolve_credentials",
            "_resolve_credentials_from_context",
            "_resolve_account_credentials",
        ),
    )

    service.uow.session.commit = _release
    service.uow.commit = _release
    service.event_dedup_store.claim_message = _claim
    return entries


async def test_the_routed_dedup_claim_does_not_hold_a_pooled_connection():
    """The Redis claim in the middle of the routed ingress path.

    `claim_message` is a `SET NX EX` against Redis reached with the request's
    unit of work open. Everything above it on this path has only read, so the
    connection genuinely goes back for it; the writes that follow (the
    external-user upsert, the conversation link) re-acquire one, which is the
    correct shape rather than a hold across the round trip.
    """
    surface = _teams_surface()
    user_id = uuid4()
    event = ParsedInboundSurfaceEvent(
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
        reply_target={"team_id": "team-1", "channel_id": "19:channel"},
    )
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = event
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="8:orgid:user-1",
        display_name="Asha",
    )
    service = build_ingress_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="8:orgid:user-1",
            display_name="Asha",
        ),
        conversation=_conversation(surface, user_id),
    )
    entries = _claim_journal(service)

    await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="teams", payload={}, headers={})
    )

    assert "claim" in entries, "the webhook never reached its dedup claim"
    assert entries[entries.index("claim") - 1] == "release", entries


async def test_the_unrouted_dedup_claim_does_not_hold_a_pooled_connection():
    """The same claim on the fallback path, where only a commit will do.

    A DM to a shared system bot that matches no surface still records the
    sender's identity before it decides how to answer -- so the session is dirty
    by the time the claim runs, `safe_to_release` correctly refuses, and
    `connection_released` would hand nothing back. The commit is the only thing
    that actually returns the connection.
    """
    surfaces = [
        AgentSurfaceEntity(
            id=uuid4(),
            pod_id=uuid4(),
            name=f"telegram-{index}",
            agent_id=uuid4(),
            surface_type=SurfacePlatform.TELEGRAM,
            mode=SurfaceMode.DM,
            account_id=None,
            credential_mode=SurfaceCredentialMode.SYSTEM,
            config=SurfaceConfig(),
            is_active=True,
        )
        for index in range(2)
    ]
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="dm-123",
        external_thread_id="dm-123",
        external_message_id="message-123",
        sender_external_user_id="external-123",
        message_text="hello",
        is_dm=True,
        reply_target={"chat_id": "dm-123"},
    )
    adapter.unresolved_sender_reply.return_value = None
    service = build_ingress_service(
        adapter=adapter,
        surfaces=surfaces,
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=None,
            external_user_id="external-123",
        ),
    )
    entries = _claim_journal(service)

    context = await service.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload={}, headers={})
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.surface_id is None, "this must be the unrouted fallback path"
    assert "claim" in entries, "the webhook never reached its dedup claim"
    assert entries[entries.index("claim") - 1] == "release", entries
