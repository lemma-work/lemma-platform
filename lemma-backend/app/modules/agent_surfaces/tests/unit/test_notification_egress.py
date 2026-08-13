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
from types import SimpleNamespace
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
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
    cold_thread_seed_id,
)
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


class _FakeRedis:
    """A fixed-window counter — all the rate limiter uses."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None


def _link_for(surface: AgentSurfaceEntity) -> AgentSurfaceConversationLink:
    """A thread this person has written to, recently enough to still be open."""
    return AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform=surface.surface_type.value,
        external_thread_id="C1",
        external_user_id="U123",
        last_inbound_at=datetime.now(timezone.utc),
    )


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


def _thread_for(
    surface: AgentSurfaceEntity, seed: str, recipient: str
) -> ColdEmailThread:
    """What the ingress side really returns, built by the code that builds it.

    Hand-writing the thread here would make the test assert its own arithmetic:
    the coordinates a reply is matched on would come from the fixture, so
    ``build_cold_email_thread`` could derive every one of them wrong and this
    would still be green. The platform send is the only part stubbed.
    """
    return build_cold_email_thread(
        surface=surface,
        recipient_email=recipient,
        sent=ColdEmailSendResult(
            external_thread_id=seed,
            external_message_id="email-9",
            reply_target={"recipient_email": recipient.strip().lower()},
        ),
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
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "Bob@Example.com"
    )
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
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "Bob@Example.com"
    )
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


def _notification_service(
    *,
    provisioner=None,
    surfaces=(),
    recipient_email="bob@example.com",
    external_user=None,
):
    """A NotificationService with only the collaborators channel resolution uses.

    Autospecced against the real classes for the same reason as
    ``_egress_double`` above — and this file previously did the opposite while
    its own docstring condemned it. A bare ``AsyncMock`` answers any attribute,
    so a renamed or never-written method reads as a passing test, which is the
    exact failure this suite exists to catch.
    """
    from app.modules.agent_surfaces.domain.ports import SurfacePodMembershipPort
    from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
        SurfaceRepository,
    )
    from app.modules.agent_surfaces.services.notification_service import (
        NotificationService,
    )

    from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
        ExternalSurfaceUserRepository,
    )

    surface_repo = create_autospec(SurfaceRepository, instance=True)
    surface_repo.list_by_pod.return_value = (list(surfaces), None)
    membership = create_autospec(SurfacePodMembershipPort, instance=True)
    membership.get_user_email.return_value = recipient_email
    # Nobody has written to a chat surface unless a test says they have. A bare
    # AsyncMock here answers with a truthy Mock, which silently turns every chat
    # surface into a deliverable channel and makes "they never messaged the bot"
    # untestable.
    external_users = create_autospec(ExternalSurfaceUserRepository, instance=True)
    external_users.get_by_resolved_user.return_value = external_user

    return NotificationService(
        uow=AsyncMock(),
        notification_repository=AsyncMock(),
        surface_repository=surface_repo,
        conversation_link_repository=_links(),
        external_user_repository=external_users,
        conversation_service=_conversation_service(uuid4()),
        ingress_service=_egress_double(),
        pod_membership_port=membership,
        surface_provisioner=provisioner,
    )


def _email_configured(monkeypatch):
    """What "email is set up" means now: a key and a domain, nothing else.

    There used to be a third thing — RESEND_AUTO_PROVISION_ENABLED — and being
    per-process it could be true where the surfaces catalog runs and false where
    sends run. On dev it was exactly that, so the UI offered email while
    delivery reported "the pod has no active surface".
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")


async def test_a_pod_with_no_surface_is_given_the_system_mailbox(monkeypatch):
    """Otherwise a pod's first notification can reach nobody at all.

    Provisioned here rather than at pod creation on purpose: most pods never
    message anyone, and an address handed out is an address that has to keep
    working.
    """
    _email_configured(monkeypatch)

    surface = _email_surface()
    provisioner = AsyncMock(return_value=(surface, None))
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=surface.pod_id, recipient_user_id=uuid4()
    )

    provisioner.assert_awaited_once()
    assert reason == ""
    assert [c.surface.id for c in channels] == [surface.id]


async def test_the_pod_assistant_gets_a_mailbox_even_when_agents_have_theirs(
    monkeypatch,
):
    """The hole that broke dev, and the reason this is not just a config fix.

    Provisioning used to fire only when the *pod* had no surface at all. Once
    any one agent was given its own mailbox the pod was no longer empty, so it
    never fired again — and the pod assistant, which reaches for a surface with
    no agent of its own, was left permanently unable to message anyone.
    """
    _email_configured(monkeypatch)

    assistant_mailbox = _email_surface()
    provisioner = AsyncMock(return_value=(assistant_mailbox, None))
    service = _notification_service(
        provisioner=provisioner,
        # A pod that is anything but empty — every surface belongs to a named
        # agent, which is what a pod looks like after per-agent mailboxes.
        surfaces=(
            _surface_for(uuid4(), SurfacePlatform.RESEND),
            _surface_for(uuid4(), SurfacePlatform.RESEND),
        ),
    )

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=None
    )

    assert reason == ""
    # Its own, not one borrowed from a named agent: sending from another agent's
    # bot puts the wrong name on the message.
    assert [c.surface.id for c in channels] == [assistant_mailbox.id]
    _, agent_id, _ = provisioner.await_args.args
    assert agent_id is None


async def test_an_agent_from_before_per_agent_mailboxes_gets_one(monkeypatch):
    """Agents created before mailboxes existed never went through creation.

    There is no backfill by design, so the send path is the only thing that can
    give them one. Without this they are in the same position as the assistant.
    """
    _email_configured(monkeypatch)

    mailbox = _email_surface()
    provisioner = AsyncMock(return_value=(mailbox, None))
    older_agent = uuid4()
    service = _notification_service(
        provisioner=provisioner,
        surfaces=(_surface_for(uuid4(), SurfacePlatform.RESEND),),
    )

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=older_agent
    )

    assert reason == ""
    assert [c.surface.id for c in channels] == [mailbox.id]
    _, agent_id, _ = provisioner.await_args.args
    assert agent_id == older_agent


async def test_a_chat_only_agent_falls_back_to_email_it_does_not_yet_have(
    monkeypatch,
):
    """"Email always works" was not true when the agent had no mailbox.

    An agent whose only surface is a Slack bot the recipient never wrote to
    could reach nobody, and reported NEVER_INTERACTED — which reads as the
    recipient's fault for a problem we can fix ourselves.
    """
    _email_configured(monkeypatch)

    agent_id = uuid4()
    mailbox = _email_surface()
    provisioner = AsyncMock(return_value=(mailbox, None))
    service = _notification_service(
        provisioner=provisioner,
        surfaces=(_surface_for(agent_id, SurfacePlatform.SLACK),),
    )

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=agent_id
    )

    assert reason == ""
    assert [c.surface.id for c in channels] == [mailbox.id]


async def test_an_agent_that_already_has_a_mailbox_is_not_given_a_second(
    monkeypatch,
):
    """No channel from an existing mailbox means the *recipient* has no address.

    Minting another would not fix that, and would leave the pod holding two
    addresses for one agent.
    """
    _email_configured(monkeypatch)

    agent_id = uuid4()
    provisioner = AsyncMock(return_value=(_email_surface(), None))
    service = _notification_service(
        provisioner=provisioner,
        surfaces=(_surface_for(agent_id, SurfacePlatform.RESEND),),
        recipient_email=None,
    )

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=agent_id
    )

    provisioner.assert_not_awaited()
    assert channels == []
    assert reason == UndeliverableReason.NO_EMAIL_ADDRESS


async def test_unconfigured_email_says_so_rather_than_blaming_the_pod(monkeypatch):
    """The message that cost a day of looking in the wrong place.

    A deployment with no mail domain reported "the pod has no active surface",
    which is about pods and surfaces; the actual problem was two environment
    variables.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", None)
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", None)

    provisioner = AsyncMock(return_value=(_email_surface(), None))
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4()
    )

    provisioner.assert_not_awaited()
    assert channels == []
    assert reason == UndeliverableReason.EMAIL_NOT_CONFIGURED


async def test_a_failed_provision_is_undeliverable_not_an_exception(monkeypatch):
    """Delivery already has a name for "we could not reach them".

    Letting a provisioning failure escape would turn a handled outcome — the row
    exists, the inbox has it — into a raised send.

    The reason names the *kind* of failure, never the exception's own text: that
    text is free-form from a provider or the database and can carry a key or
    somebody's address. A type is bounded and safe, and is what makes the row
    readable when the log has had its ``error`` field stripped.
    """
    from app.modules.agent_surfaces.domain.errors import AgentSurfaceError

    _email_configured(monkeypatch)

    provisioner = AsyncMock(side_effect=AgentSurfaceError("resend refused"))
    service = _notification_service(provisioner=provisioner, surfaces=())

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4()
    )

    assert channels == []
    assert reason.startswith(UndeliverableReason.MAILBOX_PROVISION_FAILED)
    assert "AgentSurfaceError" in reason
    assert "resend refused" not in reason


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
    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=theirs
    )

    # Their surface is a chat one the recipient has never used, so no channel —
    # but crucially the resolution never even considered *mine*.
    assert all(c.surface.agent_id != mine for c in channels)
    assert reason != UndeliverableReason.NO_SURFACE_FOR_AGENT


async def test_an_agent_with_no_surface_says_so_when_email_cannot_help(monkeypatch):
    """Distinct from "the pod has nothing" — the pod here has plenty.

    With email configured this agent would be given a mailbox; the reason only
    survives when there is genuinely nothing left to try.
    """
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", None)
    service = _notification_service(surfaces=(_surface_for(uuid4()),))

    channels, reason = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=uuid4()
    )

    assert channels == []
    assert reason == UndeliverableReason.EMAIL_NOT_CONFIGURED


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

    # Autospecced against the Protocol: reading a method it no longer declares
    # raises, so this is what proves the lookup is gone rather than just unused.
    with pytest.raises(AttributeError):
        service.membership.get_user_default_surface_ids

    channels, _ = await service.resolve_channels(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=None
    )

    assert channels, "pod-assistant delivery still resolves without preferences"


# ------------------------------------------------ the shared sending domain


async def test_the_daily_email_cap_stops_the_send_but_not_the_notification(
    monkeypatch,
):
    """Running out of email budget is a delivery outcome, not a failed call.

    The pod asked for something real and the recipient still has it in their
    Lemma inbox; what we decline is putting it on a domain every other pod also
    sends from. So it comes back as an undeliverable reason rather than an
    exception out of ``notify``.
    """
    from app.modules.agent_surfaces.services.notification_rate_limiter import (
        NotificationRateLimiter,
    )

    _email_configured(monkeypatch)

    surface = _email_surface()
    service = _notification_service(surfaces=(surface,))
    service.rate_limiter = NotificationRateLimiter(email_limit=0, redis=_FakeRedis())
    service.notifications.update = AsyncMock(side_effect=lambda entity: entity)

    notification = _notification(pod_id=surface.pod_id)
    delivered = await service.deliver(notification)

    assert delivered.delivery_status == NotificationDeliveryStatus.FAILED
    assert "emails today" in (delivered.delivery_error or "")
    # Never reached the platform: the point is that the mail does not go out.
    service.ingress.open_cold_email_thread.assert_not_awaited()


async def test_a_chat_channel_does_not_spend_the_email_budget(monkeypatch):
    """The budget exists to protect one shared domain.

    A notification delivered on Slack costs that domain nothing, so charging it
    would let ordinary chat traffic exhaust the ceiling that stops a mail flood.
    """
    from app.modules.agent_surfaces.services.notification_rate_limiter import (
        NotificationRateLimiter,
    )

    _email_configured(monkeypatch)

    redis = _FakeRedis()
    agent_id = uuid4()
    chat_surface = _surface_for(agent_id, SurfacePlatform.SLACK)
    service = _notification_service(
        surfaces=(chat_surface,),
        external_user=SimpleNamespace(external_user_id="U123"),
    )
    link = _link_for(chat_surface)
    service.channels.links.get_latest_by_surface_and_external_user = AsyncMock(
        return_value=link
    )
    # A live conversation on that thread, so delivery continues it rather than
    # opening a new one — the DM-reset check compares real timestamps.
    service.conversation_service.conversation_repository.get_conversation = AsyncMock(
        return_value=_Conversation(link.conversation_id)
    )
    service.rate_limiter = NotificationRateLimiter(email_limit=0, redis=redis)
    service.notifications.update = AsyncMock(side_effect=lambda entity: entity)

    delivered = await service.deliver(
        _notification(pod_id=chat_surface.pod_id, actor_agent_id=agent_id)
    )

    assert delivered.delivery_status == NotificationDeliveryStatus.DELIVERED
    assert redis.counts == {}


async def test_the_pod_assistant_mailbox_is_named_for_the_pod_not_the_assistant(
    monkeypatch,
):
    """`acme@`, not `pod-default.acme@`.

    The name carried on a notification from the assistant is its internal one,
    `pod_default`. Passing it through produced `pod-default.personal@…` on dev —
    a working address, but not one to ask a person to type, and not the shape
    the pod's own mailbox is supposed to have.
    """
    _email_configured(monkeypatch)

    provisioner = AsyncMock(return_value=(_email_surface(), None))
    service = _notification_service(provisioner=provisioner, surfaces=())

    await service.resolve_channels(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        actor_agent_id=None,
        agent_name="pod_default",
    )

    _, agent_id, agent_name = provisioner.await_args.args
    assert agent_id is None
    assert agent_name is None


async def test_a_named_agent_still_gets_its_own_name_in_the_address(monkeypatch):
    """The exemption is for the assistant only — `curator.acme@` is right."""
    _email_configured(monkeypatch)

    agent_id = uuid4()
    provisioner = AsyncMock(return_value=(_email_surface(), None))
    service = _notification_service(provisioner=provisioner, surfaces=())

    await service.resolve_channels(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        actor_agent_id=agent_id,
        agent_name="curator",
    )

    _, passed_agent_id, agent_name = provisioner.await_args.args
    assert passed_agent_id == agent_id
    assert agent_name == "curator"
