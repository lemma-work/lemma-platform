"""Getting a notification onto a platform, and leaving a way back.

Everything here is about the half of delivery that had no tests at all, which
is why it shipped calling two ingress methods that were never written. The
existing suites all run in a pod with no active surface, so ``deliver()``
returns before reaching any of this and both ``AttributeError``s were invisible.

The doubles are built with ``create_autospec`` on purpose. A bare ``Mock()``
answers any attribute you ask it for, which reproduces exactly the blind spot
under test: it would have passed happily against a method that did not exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, create_autospec
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.services.cold_email_thread import cold_thread_seed_id
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress

pytestmark = pytest.mark.asyncio


def _notification(**overrides) -> NotificationEntity:
    payload = {
        "pod_id": uuid4(),
        "recipient_user_id": uuid4(),
        "recipient_pod_member_id": uuid4(),
        "origin_kind": NotificationOriginKind.AGENT_RUN,
        "title": "Standup",
        "body": "What did you ship yesterday?",
    }
    payload.update(overrides)
    return NotificationEntity(**payload)


def _email_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="resend",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email="pod-1@ops.asur.work",
    )


class _Conversation:
    def __init__(self, conversation_id: UUID) -> None:
        self.id = conversation_id
        self.updated_at = datetime.now(timezone.utc)


def _egress_double() -> AgentSurfaceIngressService:
    """A stand-in that can only answer what the real class actually declares.

    Configured by *reading* each attribute and setting ``return_value``, never
    by assigning a fresh mock over it. That distinction is the entire safety
    property: an autospec mock raises ``AttributeError`` when you read a method
    the class does not have, but happily accepts one you assign. Assigning here
    would rebuild the blind spot this file exists to close.
    """
    double = create_autospec(AgentSurfaceIngressService, instance=True)
    double.agent_name_for_surface.return_value = "Ops"
    double.send_agent_message_for_conversation.return_value = True
    double.open_cold_email_thread.return_value = None
    return double


def _conversation_service(conversation_id: UUID):
    service = AsyncMock()
    service.create_conversation = AsyncMock(return_value=_Conversation(conversation_id))
    return service


def _links():
    links = AsyncMock()
    links.get_by_external_thread = AsyncMock(return_value=None)
    links.create = AsyncMock(side_effect=lambda link: link)
    return links


def _thread_for(surface: AgentSurfaceEntity, seed: str) -> ColdEmailThread:
    return ColdEmailThread(
        external_thread_id=seed,
        external_channel_id=surface.surface_identity_email,
        external_message_id="email-9",
        last_event={"platform": "RESEND"},
    )


# ------------------------------------------------------------------- the bugs


async def test_the_conversation_is_opened_under_the_surface_agents_name():
    """Direct regression for the reported crash.

    ``agent_name_for_surface`` was private, and delivery called it as though it
    were not. Because the double is autospecced, this test fails outright if the
    method ever goes back to being underscored.
    """
    surface = _email_surface()
    notification = _notification()
    conversation_id = uuid4()
    egress_port = _egress_double()

    egress = NotificationEgress(
        egress=egress_port,
        conversation_service=_conversation_service(conversation_id),
        conversation_link_repository=_links(),
    )

    opened = await egress.open_conversation(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        notification=notification,
    )

    assert opened == conversation_id
    egress_port.agent_name_for_surface.assert_awaited_once_with(surface)
    _, kwargs = egress.conversation_service.create_conversation.call_args
    assert kwargs["agent_name"] == "Ops"
    # The recipient owns the conversation, never the asker — their reply has to
    # run under their own permissions.
    assert kwargs["user_id"] == notification.recipient_user_id


async def test_a_cold_email_leaves_a_link_the_reply_will_match():
    """The claim the whole ask/answer loop over email rests on.

    An inbound reply is matched by ``get_by_external_thread`` on exactly these
    five coordinates. Any one of them wrong and the reply opens a brand-new
    conversation, the ``background_instruction`` never reaches the replying
    agent, and the asking run waits for an answer that silently cannot arrive.
    """
    surface = _email_surface()
    notification = _notification()
    conversation_id = uuid4()
    seed = cold_thread_seed_id(notification_id=notification.id, surface=surface)

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(surface, seed)
    links = _links()

    egress = NotificationEgress(
        egress=egress_port,
        conversation_service=_conversation_service(conversation_id),
        conversation_link_repository=links,
    )

    sent = await egress.send(
        DeliveryChannel(surface=surface, email_address="Bob@Example.com"),
        conversation_id=conversation_id,
        notification=notification,
        message="What did you ship?",
    )

    assert sent is True
    created = links.create.await_args.args[0]
    assert created.surface_id == surface.id
    assert created.conversation_id == conversation_id
    assert created.platform == "RESEND"
    assert created.external_channel_id == "pod-1@ops.asur.work"
    assert created.external_thread_id == seed
    # Lowercased, because that is what the inbound parser records as the sender.
    assert created.external_user_id == "bob@example.com"
    # They have not written to us; claiming otherwise would let an outbound
    # masquerade as inbound activity when ranking someone's channels.
    assert created.last_inbound_at is None


async def test_a_platform_that_cannot_cold_open_is_reported_not_crashed():
    """Outlook and Composio-backed Gmail reply through a message id they lack.

    They are still offered as channels because ``can_cold_open`` is a property
    of email, not of the credential behind it. A clean False is what turns that
    into an explanation instead of a stack trace.
    """
    surface = _email_surface()
    links = _links()
    egress = NotificationEgress(
        egress=_egress_double(),  # open_cold_email_thread returns None
        conversation_service=_conversation_service(uuid4()),
        conversation_link_repository=links,
    )

    sent = await egress.send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=uuid4(),
        notification=_notification(),
        message="hello",
    )

    assert sent is False
    links.create.assert_not_awaited()


async def test_no_link_is_written_when_the_send_raises():
    """Order of operations: never point a link at a thread that does not exist.

    A link written before the send would swallow the person's reply into a
    conversation nothing was ever delivered to — strictly worse than no link,
    because the reply looks handled.
    """
    egress_port = _egress_double()
    egress_port.open_cold_email_thread.side_effect = OSError("smtp down")
    links = _links()

    egress = NotificationEgress(
        egress=egress_port,
        conversation_service=_conversation_service(uuid4()),
        conversation_link_repository=links,
    )

    with pytest.raises(OSError):
        await egress.send(
            DeliveryChannel(surface=_email_surface(), email_address="bob@example.com"),
            conversation_id=uuid4(),
            notification=_notification(),
            message="hello",
        )

    links.create.assert_not_awaited()


async def test_redelivering_the_same_notification_reuses_its_thread():
    """The seed is derived from the notification id precisely for this.

    A retried worker job must not stack a second conversation on somebody who
    has not even replied to the first.
    """
    surface = _email_surface()
    notification = _notification()
    seed = cold_thread_seed_id(notification_id=notification.id, surface=surface)

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(surface, seed)
    links = _links()
    links.get_by_external_thread.return_value = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="RESEND",
        external_thread_id=seed,
    )

    egress = NotificationEgress(
        egress=egress_port,
        conversation_service=_conversation_service(uuid4()),
        conversation_link_repository=links,
    )

    sent = await egress.send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=uuid4(),
        notification=notification,
        message="What did you ship?",
    )

    assert sent is True
    links.create.assert_not_awaited()


async def test_a_channel_with_a_live_thread_replies_into_it():
    """The warm path: a chat surface with a link never touches cold open."""
    surface = _email_surface()
    conversation_id = uuid4()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="TELEGRAM",
        external_thread_id="t1",
    )
    egress_port = _egress_double()

    egress = NotificationEgress(
        egress=egress_port,
        conversation_service=_conversation_service(conversation_id),
        conversation_link_repository=_links(),
    )

    sent = await egress.send(
        DeliveryChannel(surface=surface, link=link, external_user_id="u1"),
        conversation_id=conversation_id,
        notification=_notification(),
        message="What did you ship?",
    )

    assert sent is True
    egress_port.send_agent_message_for_conversation.assert_awaited_once()
    egress_port.open_cold_email_thread.assert_not_awaited()


# ------------------------------------------------- lazy surface provisioning


def _notification_service(*, provisioner=None, surfaces=()):
    """A NotificationService with only the collaborators channel resolution uses."""
    from app.modules.agent_surfaces.services.notification_service import (
        NotificationService,
    )

    surface_repo = AsyncMock()
    surface_repo.list_by_pod = AsyncMock(return_value=(list(surfaces), None))
    membership = AsyncMock()
    membership.get_user_email = AsyncMock(return_value="bob@example.com")

    return NotificationService(
        uow=AsyncMock(),
        notification_repository=AsyncMock(),
        surface_repository=surface_repo,
        conversation_link_repository=_links(),
        external_user_repository=AsyncMock(),
        conversation_service=_conversation_service(uuid4()),
        ingress_service=_egress_double(),
        pod_membership_port=membership,
        surface_provisioner=provisioner,
    )


async def test_a_pod_with_no_surface_is_given_the_system_mailbox(monkeypatch):
    """Otherwise a pod's first notification can reach nobody at all.

    Provisioned here rather than at pod creation on purpose: most pods never
    message anyone, and an address handed out is an address that has to keep
    working.
    """
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_auto_provision_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_api_key", "re_test")

    surface = _email_surface()
    provisioner = AsyncMock(return_value=surface)
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=surface.pod_id, recipient_user_id=uuid4()
    )

    provisioner.assert_awaited_once()
    assert reason == ""
    assert [c.surface.id for c in channels] == [surface.id]


async def test_provisioning_stays_off_until_it_is_configured(monkeypatch):
    """It is outward-facing: every pod would send from one shared Resend domain."""
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "resend_auto_provision_enabled", False)
    provisioner = AsyncMock(return_value=_email_surface())
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4()
    )

    provisioner.assert_not_awaited()
    assert channels == []
    assert "no active surface" in reason.lower()


async def test_a_failed_provision_is_undeliverable_not_an_exception(monkeypatch):
    """Delivery already has a name for "we could not reach them".

    Letting a provisioning failure escape would turn a handled outcome — the row
    exists, the inbox has it — into a raised send.
    """
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.domain.errors import AgentSurfaceError

    monkeypatch.setattr(surface_settings, "resend_auto_provision_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_api_key", "re_test")

    provisioner = AsyncMock(side_effect=AgentSurfaceError("resend refused"))
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4()
    )

    assert channels == []
    assert "no active surface" in reason.lower()


# --------------------------------------------------- routing follows the agent


def _surface_for(agent_id, platform=SurfacePlatform.TELEGRAM):
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        agent_id=agent_id,
    )
    return surface


async def test_an_agent_never_reaches_out_through_another_agents_bot():
    """The identity argument, as an assertion.

    A pod's other agents have their own Telegram bots and Slack apps and their
    own relationships with people. Borrowing one puts the wrong name on the
    message and asks the recipient to trust a bot this agent never earned.
    """
    mine, theirs = uuid4(), uuid4()
    service = _notification_service(
        surfaces=(_surface_for(mine), _surface_for(theirs, SurfacePlatform.SLACK))
    )
    service.external_users.get_by_resolved_user = AsyncMock(return_value=None)

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=theirs
    )

    # Their surface is a chat one the recipient has never used, so no channel —
    # but crucially the resolution never even considered *mine*.
    assert all(c.surface.agent_id != mine for c in channels)
    assert reason != UndeliverableReason.NO_SURFACE_FOR_AGENT


async def test_an_agent_with_no_surface_of_its_own_says_so():
    """Distinct from "the pod has nothing" — the pod here has plenty."""
    service = _notification_service(surfaces=(_surface_for(uuid4()),))

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=uuid4()
    )

    assert channels == []
    assert reason == UndeliverableReason.NO_SURFACE_FOR_AGENT


async def test_an_agents_own_mailbox_is_used_when_it_has_no_chat_surface():
    """The default case: most agents are email-only."""
    agent_id = uuid4()
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND)
    service = _notification_service(surfaces=(mailbox,))

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=agent_id
    )

    assert reason == ""
    assert [c.surface.id for c in channels] == [mailbox.id]
    assert channels[0].email_address == "bob@example.com"


async def test_the_recipients_own_preference_no_longer_steers_delivery():
    """Egress stopped consulting it; the port method is gone entirely.

    Inbound routing still honours a user's default surface — that question is
    genuinely "which of our surfaces did this person mean to talk to".
    """
    service = _notification_service(surfaces=(_surface_for(None, SurfacePlatform.RESEND),))

    channels, _ = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=None
    )

    assert channels, "pod-assistant delivery still resolves without preferences"
