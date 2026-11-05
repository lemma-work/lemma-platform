"""Getting a line meant for a member onto a platform, and leaving a way back.

A message to a member is a line in their own channel first, and posting it
plans its push (``channels``). This is the half that carries one push to them
where they are -- choosing the channel (``NotificationChannelResolver``),
opening their conversation and sending (``NotificationEgress``), and the push
that tries each channel in turn (``MemberPushes`` over
``send_on_first_channel``). Whether a push is planned at all, and what its
delivery row records, is followed against the database in
``e2e/test_channel_deliveries_e2e.py``.

That half once shipped calling two ingress methods that were never written,
because every suite ran in a pod with no active surface and returned before
reaching it. The doubles are built with ``create_autospec`` on purpose. A bare
``Mock()`` answers any attribute you ask it for, which reproduces exactly the
blind spot under test: it would have passed happily against a method that did
not exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, PartDelivery
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.domain.ports import (
    ColdEmailThread,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
    cold_thread_seed_id,
)
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.member_pushes import MemberPushes, Push
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    Outbound,
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailBudget,
    PushBudget,
)
from app.modules.agent_surfaces.services.channel_places import place_of
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Member,
    Principal,
    Sender,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = pytest.mark.asyncio


def _outbound(**overrides) -> Outbound:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "recipient_user_id": uuid4(),
        "title": "Standup",
    }
    payload.update(overrides)
    return Outbound(**payload)


class _FakeRedis:
    """A fixed-window counter — all the rate limiter uses."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def eval(self, script, numkeys, key, ttl):
        """``incr_with_ttl``'s script: increment, and expire if not expiring."""
        count = await self.incr(key)
        await self.expire(key, ttl)
        return count


def _place_for(surface: AgentSurfaceEntity, person: str = "U123") -> Source:
    """A private chat this person has written to, recently enough to still be open."""
    return Source(
        id=uuid4(),
        channel_id=uuid4(),
        pod_id=surface.pod_id,
        account_id=uuid4(),
        surface_id=surface.id,
        platform=surface.surface_type.value,
        kind=SourceKind.PERSON,
        external_user_id=person,
        last_inbound_at=datetime.now(timezone.utc),
        delivery_ref={"external_thread_id": "C1"},
    )


def _email_surface(pod_id: UUID | None = None) -> AgentSurfaceEntity:
    """The pod's own mailbox, which belongs to its assistant.

    `agent_id == pod_id` is not a coincidence: the assistant's row id *is* its
    pod's, which is what lets a surface name it through an ordinary foreign key.
    """
    pod_id = pod_id or uuid4()
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        agent_id=pod_id,
        name="resend",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email="pod-1@ops.lemma.work",
    )


class _Conversation:
    def __init__(self, conversation_id: UUID) -> None:
        self.id = conversation_id
        self.updated_at = datetime.now(timezone.utc)


def _egress_double() -> SurfaceEgress:
    """A stand-in that can only answer what the real class actually declares.

    Configured by *reading* each attribute and setting ``return_value``, never
    by assigning a fresh mock over it. That distinction is the entire safety
    property: an autospec mock raises ``AttributeError`` when you read a method
    the class does not have, but happily accepts one you assign. Assigning here
    would rebuild the blind spot this file exists to close.

    It autospecs `SurfaceEgress` rather than the ingress service, and the change
    was not cosmetic: this failed the moment the three methods moved, which is
    the property working.
    """
    double = create_autospec(SurfaceEgress, instance=True)
    double.agent_name_for_surface.return_value = "Ops"
    double.deliver_message.return_value = DeliveryReceipt(
        parts={"text": PartDelivery.NATIVE}, external_message_id="chat-1"
    )
    double.open_cold_email_thread.return_value = None
    return double


#: Where `agent` publishes the conversation operations delivery calls.
_OPERATIONS = "app.modules.agent.contracts.channels"

#: Where a pod's own name is read, for the email ``From`` header.
_POD_NAMES = "app.modules.agent_surfaces.services.group_names.pod_name_for"


@pytest.fixture(autouse=True)
def pod_name(monkeypatch):
    """The pod's display name, doubled where the sender name reads it.

    The real lookup is a query on the pod table. Patched at that seam rather
    than on the callers, so a test can assert what a member sees in the ``From``
    line without a database.
    """
    name = AsyncMock(return_value="Acme")
    monkeypatch.setattr(_POD_NAMES, name)
    return name


@pytest.fixture(autouse=True)
def conversations(monkeypatch):
    """`agent`'s conversation operations, doubled where they are published.

    Autouse because every delivery path here opens or continues a conversation
    and the real operations reach a database. Doubled at their source rather
    than on the modules that call them: they are another module's contract, not
    a part of the unit under test.
    """
    operations = SimpleNamespace(
        surface_conversation=AsyncMock(return_value=None),
        open_surface_conversation=AsyncMock(return_value=_Conversation(uuid4())),
        append_notification_message=AsyncMock(),
        pending_interaction=AsyncMock(return_value=None),
    )
    for name, double in vars(operations).items():
        monkeypatch.setattr(f"{_OPERATIONS}.{name}", double)
    return operations


def _egress(egress_port, *, seats=None) -> NotificationEgress:
    return NotificationEgress(
        egress=egress_port, uow=AsyncMock(), seats=seats or InMemoryChannelSeats()
    )


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


async def test_the_conversation_is_opened_under_the_surface_agents_name(
    conversations,
):
    """Direct regression for the reported crash.

    ``agent_name_for_surface`` was private, and delivery called it as though it
    were not. Because the double is autospecced, this test fails outright if the
    method ever goes back to being underscored.
    """
    surface = _email_surface()
    outbound = _outbound()
    conversation_id = uuid4()
    conversations.open_surface_conversation.return_value = _Conversation(
        conversation_id
    )
    egress_port = _egress_double()

    egress = _egress(egress_port)

    channel_id = uuid4()
    opened = await egress.open_conversation(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        outbound=outbound,
        channel_id=channel_id,
    )

    assert opened == conversation_id
    egress_port.agent_name_for_surface.assert_awaited_once_with(surface)
    _, kwargs = conversations.open_surface_conversation.call_args
    assert kwargs["agent_name"] == "Ops"
    # The recipient owns the conversation, never the asker — their reply has to
    # run under their own permissions.
    assert kwargs["user_id"] == outbound.recipient_user_id
    assert kwargs["channel_id"] == channel_id


async def test_a_cold_email_places_its_thread_where_the_reply_will_arrive():
    """The claim the whole ask/answer loop over email rests on.

    An inbound reply is placed by its surface, the thread and the sender's
    address. Any one of them wrong and the reply lands at a place of its own,
    in a conversation the message never reached, and the asking run waits
    for an answer that silently cannot arrive.
    """
    surface = _email_surface()
    outbound = _outbound()
    conversation_id = uuid4()
    seed = cold_thread_seed_id(notification_id=outbound.id, surface=surface)
    thread = _thread_for(surface, seed, "Bob@Example.com")

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = thread
    seats = InMemoryChannelSeats()
    egress = _egress(egress_port, seats=seats)

    sent = await egress.send(
        DeliveryChannel(surface=surface, email_address="Bob@Example.com"),
        conversation_id=conversation_id,
        outbound=outbound,
        message="What did you ship?",
    )

    assert sent is not None
    [place] = seats.sources.values()
    assert place.surface_id == surface.id
    assert place.platform == "RESEND"
    assert place.kind is SourceKind.PERSON
    # Lowercased, because that is what the inbound parser records as the sender.
    assert place.external_user_id == "bob@example.com"
    # In the recipient's own channel, where their conversation with the agent is.
    direct = ("DIRECT", outbound.pod_id, outbound.recipient_user_id)
    assert seats.channels[direct] == place.channel_id
    # How to write there again is the mail just sent. They have not written to
    # us; claiming otherwise would let an outbound masquerade as inbound
    # activity when ranking someone's channels.
    assert place.delivery_ref == thread.last_event
    assert place.last_inbound_at is None


async def test_a_cold_emails_place_is_the_one_their_reply_is_heard_at():
    """One place per correspondent on the inbox: the reply to a cold email is
    heard at the place the email was sent from, not at a second one."""
    surface = _email_surface()
    outbound = _outbound()
    seed = cold_thread_seed_id(notification_id=outbound.id, surface=surface)
    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "bob@example.com"
    )
    seats = InMemoryChannelSeats()
    await _egress(egress_port, seats=seats).send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=uuid4(),
        outbound=outbound,
        message="What did you ship?",
    )
    [sent_from] = seats.sources.values()

    reply = place_of(
        surface=surface,
        parsed=ParsedInboundSurfaceEvent(
            platform=SurfacePlatform.RESEND,
            conversation_type=ConversationType.EXTERNAL_DM,
            external_channel_id=seed,
            external_thread_id=seed,
            sender_external_user_id="bob@example.com",
            message_text="Shipped the importer.",
            is_dm=True,
        ),
        route=ResolvedSurfaceRoute(
            pod_id=outbound.pod_id,
            agent_id=None,
            agent_name=None,
            agent_display_name="Assistant",
            conversation_kind="EMAIL",
            route_key="email",
        ),
        external_user_id="bob@example.com",
    )

    assert (reply.external_channel_id, reply.external_user_id) == (
        sent_from.external_channel_id,
        sent_from.external_user_id,
    )


async def test_a_platform_that_cannot_cold_open_is_reported_not_crashed():
    """A platform that replies through a provider message id it does not have.

    They are still offered as channels because ``can_cold_open`` is a property
    of email, not of the credential behind it. A clean False is what turns that
    into an explanation instead of a stack trace.
    """
    surface = _email_surface()
    seats = InMemoryChannelSeats()
    egress = _egress(_egress_double(), seats=seats)  # cold open returns None

    sent = await egress.send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=uuid4(),
        outbound=_outbound(),
        message="hello",
    )

    assert sent is None
    assert not seats.sources


async def test_no_place_is_made_when_the_send_raises():
    """Order of operations: never make a place for a thread that does not exist.

    A place made before the send would swallow the person's reply into a
    conversation nothing was ever delivered to — strictly worse than no place,
    because the reply looks handled.
    """
    egress_port = _egress_double()
    egress_port.open_cold_email_thread.side_effect = OSError("smtp down")
    seats = InMemoryChannelSeats()
    egress = _egress(egress_port, seats=seats)

    with pytest.raises(OSError):
        await egress.send(
            DeliveryChannel(surface=_email_surface(), email_address="bob@example.com"),
            conversation_id=uuid4(),
            outbound=_outbound(),
            message="hello",
        )

    assert not seats.sources


async def test_redelivering_the_same_line_reuses_its_thread():
    """The seed is derived from the line's id precisely for this.

    A retried worker job must not stack a second conversation on somebody who
    has not even replied to the first.
    """
    surface = _email_surface()
    outbound = _outbound()
    seed = cold_thread_seed_id(notification_id=outbound.id, surface=surface)

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "Bob@Example.com"
    )
    seats = InMemoryChannelSeats()
    egress = _egress(egress_port, seats=seats)

    for _ in range(2):
        sent = await egress.send(
            DeliveryChannel(surface=surface, email_address="bob@example.com"),
            conversation_id=uuid4(),
            outbound=outbound,
            message="What did you ship?",
        )
        assert sent is not None

    assert len(seats.sources) == 1


async def test_a_channel_with_a_live_thread_replies_into_it():
    """The warm path: a chat the person wrote in never touches cold open, and
    the message goes to that chat."""
    surface = _email_surface()
    conversation_id = uuid4()
    place = _place_for(surface, "u1")
    egress_port = _egress_double()

    egress = _egress(egress_port)

    sent = await egress.send(
        DeliveryChannel(surface=surface, source=place, external_user_id="u1"),
        conversation_id=conversation_id,
        outbound=_outbound(),
        message="What did you ship?",
    )

    assert sent is not None
    assert sent.external_message_id == "chat-1"
    egress_port.deliver_message.assert_awaited_once()
    sent_to = egress_port.deliver_message.await_args.kwargs["at"]
    assert sent_to is place
    egress_port.open_cold_email_thread.assert_not_awaited()


# ------------------------------------------------- lazy surface provisioning


def _collaborators(
    *, surfaces=(), recipient_email="bob@example.com", external_user=None
):
    """What channel resolution reads, autospecced against the real classes.

    For the same reason as ``_egress_double`` above. A bare ``AsyncMock``
    answers any attribute, so a renamed or never-written method reads as a
    passing test, which is the exact failure this suite exists to catch.
    """
    surface_repo = create_autospec(SurfaceRepository, instance=True)
    surface_repo.list_by_pod.return_value = (list(surfaces), None)
    membership = create_autospec(SurfacePodMembershipPort, instance=True)
    membership.get_user_email.return_value = recipient_email
    membership.get_pod_member_id.return_value = uuid4()
    membership.get_user_display_name.return_value = "Ada Member"
    # Nobody has written to a chat surface unless a test says they have. A bare
    # AsyncMock here answers with a truthy Mock, which silently turns every chat
    # surface into a deliverable channel and makes "they never messaged the bot"
    # untestable.
    external_users = create_autospec(ExternalSurfaceUserRepository, instance=True)
    external_users.list_by_resolved_users.return_value = (
        [external_user] if external_user is not None else []
    )
    return surface_repo, external_users, membership


def _resolver(*, provisioner=None, seats=None, **collaborators):
    surface_repo, external_users, membership = _collaborators(**collaborators)
    return NotificationChannelResolver(
        seats=seats or InMemoryChannelSeats(),
        surface_repository=surface_repo,
        external_user_repository=external_users,
        pod_membership_port=membership,
        surface_provisioner=provisioner,
    )


def _pushes(
    *, provisioner=None, email_budget=None, push_budget=None, **collaborators
) -> MemberPushes:
    """`MemberPushes` over the same doubles, its places in memory."""
    surface_repo, external_users, membership = _collaborators(**collaborators)
    return MemberPushes(
        uow=AsyncMock(),
        surface_repository=surface_repo,
        external_user_repository=external_users,
        egress=_egress_double(),
        pod_membership_port=membership,
        surface_provisioner=provisioner,
        seats=InMemoryChannelSeats(),
        email_budget=email_budget,
        push_budget=push_budget,
    )


def _push(
    *,
    pod_id: UUID | None = None,
    recipient_user_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    actor_agent_id: UUID | None = None,
    agent_name: str | None = None,
    notify_via: tuple[str, ...] | None = None,
) -> Push:
    """A line the pod's agent posted in a member's own channel, pushed to them.

    ``actor_user_id`` is the member it was said for -- by default the
    recipient themself, as for a note the pod writes them."""
    pod_id = pod_id or uuid4()
    recipient = recipient_user_id or uuid4()
    now = datetime.now(timezone.utc)
    channel = Channel(
        id=uuid4(),
        pod_id=pod_id,
        kind=ChannelKind.DIRECT,
        visibility=Visibility.PRIVATE,
        member_user_id=recipient,
        created_at=now,
        last_line_at=now,
    )
    line = ChannelMessage(
        id=uuid4(),
        channel_id=channel.id,
        pod_id=pod_id,
        seq=1,
        sender=Sender.agent(
            actor_agent_id or pod_id,
            on_behalf_of=Principal.member(actor_user_id or recipient),
            display_name=agent_name,
        ),
        text="What did you ship yesterday?",
        created_at=now,
    )
    return Push(
        channel=channel,
        line=line,
        recipient_user_id=recipient,
        member=Member(channel_id=channel.id, user_id=recipient, notify_via=notify_via),
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
    """Otherwise a pod's first message can reach nobody at all.

    Provisioned here rather than at pod creation on purpose: most pods never
    message anyone, and an address handed out is an address that has to keep
    working.
    """
    _email_configured(monkeypatch)

    surface = _email_surface()
    provisioner = AsyncMock(return_value=(surface, None))
    resolver = _resolver(provisioner=provisioner, surfaces=())

    channels, reason = await resolver.resolve(
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
    resolver = _resolver(
        provisioner=provisioner,
        # A pod that is anything but empty — every surface belongs to a named
        # agent, which is what a pod looks like after per-agent mailboxes.
        surfaces=(
            _surface_for(uuid4(), SurfacePlatform.RESEND),
            _surface_for(uuid4(), SurfacePlatform.RESEND),
        ),
    )

    channels, reason = await resolver.resolve(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=None
    )

    assert reason == ""
    # Its own, not one borrowed from a named agent: sending from another agent's
    # bot puts the wrong name on the message.
    assert [c.surface.id for c in channels] == [assistant_mailbox.id]
    # Named by its row's id, which is its pod's -- callers used to say "the
    # assistant" by passing nothing, and the resolver now normalises that to
    # the id before anything looks a surface up.
    pod_id, agent_id, _ = provisioner.await_args.args
    assert agent_id == pod_id


async def test_an_agent_from_before_per_agent_mailboxes_gets_one(monkeypatch):
    """Agents created before mailboxes existed never went through creation.

    There is no backfill by design, so the send path is the only thing that can
    give them one. Without this they are in the same position as the assistant.
    """
    _email_configured(monkeypatch)

    mailbox = _email_surface()
    provisioner = AsyncMock(return_value=(mailbox, None))
    older_agent = uuid4()
    resolver = _resolver(
        provisioner=provisioner,
        surfaces=(_surface_for(uuid4(), SurfacePlatform.RESEND),),
    )

    channels, reason = await resolver.resolve(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=older_agent
    )

    assert reason == ""
    assert [c.surface.id for c in channels] == [mailbox.id]
    _, agent_id, _ = provisioner.await_args.args
    assert agent_id == older_agent


async def test_a_chat_only_agent_falls_back_to_email_it_does_not_yet_have(
    monkeypatch,
):
    """ "Email always works" was not true when the agent had no mailbox.

    An agent whose only surface is a Slack bot the recipient never wrote to
    could reach nobody, and reported NEVER_INTERACTED — which reads as the
    recipient's fault for a problem we can fix ourselves.
    """
    _email_configured(monkeypatch)

    agent_id = uuid4()
    mailbox = _email_surface()
    provisioner = AsyncMock(return_value=(mailbox, None))
    resolver = _resolver(
        provisioner=provisioner,
        surfaces=(_surface_for(agent_id, SurfacePlatform.SLACK),),
    )

    channels, reason = await resolver.resolve(
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
    resolver = _resolver(
        provisioner=provisioner,
        surfaces=(_surface_for(agent_id, SurfacePlatform.RESEND),),
        recipient_email=None,
    )

    channels, reason = await resolver.resolve(
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
    resolver = _resolver(provisioner=provisioner, surfaces=())

    channels, reason = await resolver.resolve(pod_id=uuid4(), recipient_user_id=uuid4())

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
    resolver = _resolver(provisioner=provisioner, surfaces=())

    channels, reason = await resolver.resolve(pod_id=uuid4(), recipient_user_id=uuid4())

    assert channels == []
    assert reason.startswith(UndeliverableReason.MAILBOX_PROVISION_FAILED)
    assert "AgentSurfaceError" in reason
    assert "resend refused" not in reason


# --------------------------------------------------- routing follows the agent


def _surface_for(agent_id, platform=SurfacePlatform.TELEGRAM, *, pod_id=None):
    """One surface, owned by one agent -- there is no other kind.

    ``pod_id`` matters when the owner is meant to be the pod's own assistant,
    whose row id *is* its pod's: pass the same value for both.
    """
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id or uuid4(),
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        agent_id=agent_id,
        # Tenant-scoped platforms match only their own tenant's identities.
        external_workspace_id="T123" if platform is SurfacePlatform.SLACK else None,
        external_tenant_id="tenant-1" if platform is SurfacePlatform.TEAMS else None,
    )


async def test_an_agent_never_reaches_out_through_another_agents_bot():
    """The identity argument, as an assertion.

    A pod's other agents have their own Telegram bots and Slack apps and their
    own relationships with people. Borrowing one puts the wrong name on the
    message and asks the recipient to trust a bot this agent never earned.
    """
    mine, theirs = uuid4(), uuid4()
    resolver = _resolver(
        surfaces=(_surface_for(mine), _surface_for(theirs, SurfacePlatform.SLACK))
    )
    channels, reason = await resolver.resolve(
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
    resolver = _resolver(surfaces=(_surface_for(uuid4()),))

    channels, reason = await resolver.resolve(
        pod_id=uuid4(), recipient_user_id=uuid4(), actor_agent_id=uuid4()
    )

    assert channels == []
    assert reason == UndeliverableReason.EMAIL_NOT_CONFIGURED


async def test_an_agents_own_mailbox_is_used_when_it_has_no_chat_surface():
    """The default case: most agents are email-only."""
    agent_id = uuid4()
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND)
    resolver = _resolver(surfaces=(mailbox,))

    channels, reason = await resolver.resolve(
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
    pod_id = uuid4()
    resolver = _resolver(
        surfaces=(_surface_for(pod_id, SurfacePlatform.RESEND, pod_id=pod_id),)
    )

    # Autospecced against the Protocol: reading a method it no longer declares
    # raises, so this is what proves the lookup is gone rather than just unused.
    with pytest.raises(AttributeError):
        # The expression is the assertion: the attribute must not exist.
        resolver.membership.get_user_default_surface_ids  # noqa: B018

    channels, _ = await resolver.resolve(
        pod_id=pod_id, recipient_user_id=uuid4(), actor_agent_id=None
    )

    assert channels, "pod-assistant delivery still resolves without preferences"


# ------------------------------------------------ the shared sending domain


async def test_the_pod_assistant_mailbox_is_named_for_the_pod_not_the_assistant(
    monkeypatch,
):
    """`acme@`, not `pod-default.acme@`.

    The name carried on a message from the assistant is its internal one,
    `pod_default`. Passing it through produced `pod-default.personal@…` on dev —
    a working address, but not one to ask a person to type, and not the shape
    the pod's own mailbox is supposed to have.
    """
    _email_configured(monkeypatch)

    pod_id = uuid4()
    provisioner = AsyncMock(return_value=(_email_surface(pod_id), None))
    resolver = _resolver(provisioner=provisioner, surfaces=())

    await resolver.resolve(
        pod_id=pod_id,
        recipient_user_id=uuid4(),
        actor_agent_id=pod_id,
        agent_name="pod_default",
    )

    pod_id, agent_id, agent_name = provisioner.await_args.args
    assert agent_id == pod_id
    # The name is what the address is built from, and the assistant's stored
    # name is `pod_default` -- an internal identifier. None keeps the mailbox
    # the pod's own: `acme@`, not `pod-default.acme@`.
    assert agent_name is None


async def test_a_named_agent_still_gets_its_own_name_in_the_address(monkeypatch):
    """The exemption is for the assistant only — `curator.acme@` is right."""
    _email_configured(monkeypatch)

    agent_id = uuid4()
    provisioner = AsyncMock(return_value=(_email_surface(), None))
    resolver = _resolver(provisioner=provisioner, surfaces=())

    await resolver.resolve(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        actor_agent_id=agent_id,
        agent_name="curator",
    )

    _, passed_agent_id, agent_name = provisioner.await_args.args
    assert passed_agent_id == agent_id
    assert agent_name == "curator"


async def test_a_cold_open_carries_the_senders_name_to_the_platform():
    """Email puts the sender in the From line, so it has to travel.

    ``attribute()`` writes "On behalf of Deepak" into the body, which is
    invisible until the message is opened — and it never names the agent, so the
    From line is the only place "Priya" appears at all. The sender column is
    what a person scans in a list, and it named the deployment rather than the
    agent.

    ``email_sender_name`` is the one that reaches the header, and for this
    surface — the pod's own mailbox — it is the pod. The chat name stays "Priya"
    beside it, because a chat platform puts that on the bot.
    """
    surface = _email_surface()
    outbound = _outbound()
    conversation_id = uuid4()
    seed = cold_thread_seed_id(notification_id=outbound.id, surface=surface)

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "bob@example.com"
    )

    egress = _egress(egress_port)

    await egress.send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=conversation_id,
        outbound=outbound,
        message="What did you ship?",
        agent_name="Priya",
    )

    metadata = egress_port.open_cold_email_thread.await_args.kwargs["metadata"]
    assert metadata["agent_display_name"] == "Priya"
    assert metadata["email_sender_name"] == "Acme"
    # Not the actor: "On behalf of Ada Member" is in the body, where a full name
    # fits. Sending it here as well is the header the report was about.
    assert "actor_display_name" not in metadata


async def test_a_named_agents_cold_mail_wears_its_name_when_the_caller_gave_none():
    """A caller may leave ``agent_name`` out; the surface still names the agent.

    Channel resolution picks the named agent's mailbox whether or not the caller
    said who was writing, and without this the From line fell back to the
    deployment's name on exactly the mail that had an agent to name.
    """
    surface = _email_surface().model_copy(update={"agent_id": uuid4()})
    outbound = _outbound()
    seed = cold_thread_seed_id(notification_id=outbound.id, surface=surface)

    egress_port = _egress_double()
    egress_port.open_cold_email_thread.return_value = _thread_for(
        surface, seed, "bob@example.com"
    )
    egress = _egress(egress_port)

    await egress.send(
        DeliveryChannel(surface=surface, email_address="bob@example.com"),
        conversation_id=uuid4(),
        outbound=outbound,
        message="What did you ship?",
        agent_name=None,
    )

    metadata = egress_port.open_cold_email_thread.await_args.kwargs["metadata"]
    assert metadata["email_sender_name"] == "Ops"
    egress_port.agent_name_for_surface.assert_awaited_once_with(surface)


async def test_an_unknown_agent_name_is_absent_rather_than_None():
    """A present key beats ``setdefault``, and would unname every chat bot.

    ``SurfaceDelivery.metadata_for`` fills ``agent_display_name`` from the
    surface with ``setdefault``, so writing an explicit None here does not mean
    "we don't know" — it wins, and the reply goes out with no name and no icon
    on a platform that had both.
    """
    surface = _email_surface()
    place = _place_for(surface)
    conversation_id = uuid4()
    egress_port = _egress_double()

    egress = _egress(egress_port)

    await egress.send(
        DeliveryChannel(surface=surface, external_user_id="U123", source=place),
        conversation_id=conversation_id,
        outbound=_outbound(),
        message="What did you ship?",
        agent_name=None,
    )

    kwargs = egress_port.deliver_message.await_args.kwargs
    assert "agent_display_name" not in kwargs["metadata"]


# ------------------------------------------------- one push, to one member


def _cold_opens(surface: AgentSurfaceEntity):
    """The platform's cold open, threading under whatever seed it was given."""

    async def open_thread(**kwargs) -> ColdEmailThread:
        return _thread_for(surface, kwargs["thread_seed_id"], kwargs["recipient_email"])

    return open_thread


async def test_a_member_who_left_the_pod_is_not_pushed():
    """Membership fails closed: the push names somebody no longer in the pod,
    and nothing goes."""
    pushes = _pushes(surfaces=(_email_surface(),))
    pushes.membership.get_pod_member_id.return_value = None

    delivered = await pushes.push(_push())

    assert delivered.sent is False
    assert "no longer in this pod" in (delivered.reason or "")
    pushes.egress.egress.open_cold_email_thread.assert_not_awaited()


async def test_a_push_nothing_can_carry_says_why(monkeypatch):
    """Undeliverable is an outcome with a reason, never a raised send -- here,
    the actionable one: email is not set up."""
    from app.core.config import settings as core_settings

    monkeypatch.setattr(core_settings, "resend_api_key", None)
    pushes = _pushes(provisioner=AsyncMock(), surfaces=())

    delivered = await pushes.push(_push())

    assert delivered.sent is False
    assert delivered.reason == UndeliverableReason.EMAIL_NOT_CONFIGURED


async def test_the_push_budget_skips_the_push():
    """A runaway loop must not put forty messages on a colleague's phone; the
    lines it writes wait in their channel."""
    surface = _email_surface()
    pushes = _pushes(
        surfaces=(surface,), push_budget=PushBudget(limit=0, redis=_FakeRedis())
    )

    delivered = await pushes.push(_push(pod_id=surface.pod_id))

    assert delivered.sent is False
    assert "this hour" in (delivered.reason or "")
    pushes.egress.egress.open_cold_email_thread.assert_not_awaited()


async def test_the_daily_email_cap_stops_the_email(monkeypatch):
    """Running out of email budget is a delivery outcome, not a failed call.

    What we decline is putting it on a domain every other pod also sends from,
    so it comes back as a reason rather than an exception.
    """
    _email_configured(monkeypatch)
    surface = _email_surface()
    pushes = _pushes(
        surfaces=(surface,), email_budget=EmailBudget(limit=0, redis=_FakeRedis())
    )

    delivered = await pushes.push(_push(pod_id=surface.pod_id))

    assert delivered.sent is False
    assert "emails today" in (delivered.reason or "")
    # Never reached the platform: the point is that the mail does not go out.
    pushes.egress.egress.open_cold_email_thread.assert_not_awaited()


async def test_a_chat_channel_does_not_spend_the_email_budget(monkeypatch):
    """The budget exists to protect one shared domain.

    A line carried on Slack costs that domain nothing, so charging it would let
    ordinary chat traffic exhaust the ceiling that stops a mail flood.
    """
    _email_configured(monkeypatch)
    redis = _FakeRedis()
    agent_id = uuid4()
    chat_surface = _surface_for(agent_id, SurfacePlatform.SLACK)
    pushes = _pushes(
        surfaces=(chat_surface,),
        external_user=SimpleNamespace(external_user_id="U123", tenant_id="T123"),
        email_budget=EmailBudget(limit=0, redis=redis),
    )
    pushes.channels.seats.add(_place_for(chat_surface))

    delivered = await pushes.push(
        _push(pod_id=chat_surface.pod_id, actor_agent_id=agent_id)
    )

    assert delivered.platform == "SLACK"
    assert redis.counts == {}


async def test_the_way_a_member_chose_beats_where_they_last_spoke(monkeypatch):
    """``notify_via`` is the member's own answer to "where do I hear of this
    channel": email, though the chat they last wrote on would have won."""
    _email_configured(monkeypatch)
    agent_id = uuid4()
    chat = _surface_for(agent_id, SurfacePlatform.TELEGRAM)
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND, pod_id=chat.pod_id)
    pushes = _pushes(surfaces=(chat, mailbox), external_user=_identity("U123"))
    pushes.channels.seats.add(_place_for(chat))
    pushes.egress.egress.open_cold_email_thread.side_effect = _cold_opens(mailbox)

    by_default = await pushes.push(_push(pod_id=chat.pod_id, actor_agent_id=agent_id))
    chosen = await pushes.push(
        _push(pod_id=chat.pod_id, actor_agent_id=agent_id, notify_via=("EMAIL",))
    )

    assert by_default.platform == "TELEGRAM"
    assert chosen.platform == "RESEND"


async def test_the_copy_is_committed_before_the_platform_send(
    monkeypatch, conversations
):
    """Commit has to happen between writing the copy and sending it.

    Two things ride on the ordering. "Persist before send" only means anything
    if the copy is committed -- an uncommitted one is not a message the agent
    handling their reply can read. And the send is a platform API call that can
    take seconds, so holding the transaction across it pins a pooled connection
    for that whole time.

    Asserted as an ordering rather than a timing, because the ordering is the
    property; the mocked send here is instant and always will be.
    """
    _email_configured(monkeypatch)
    agent_id = uuid4()
    chat_surface = _surface_for(agent_id, SurfacePlatform.SLACK)
    pushes = _pushes(
        surfaces=(chat_surface,),
        external_user=SimpleNamespace(external_user_id="U123", tenant_id="T123"),
    )
    pushes.channels.seats.add(_place_for(chat_surface))

    order: list[str] = []
    pushes.uow.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    conversations.append_notification_message.side_effect = lambda *_a, **_k: (
        order.append("persist")
    )
    platform = pushes.egress.egress.deliver_message
    platform.side_effect = lambda **_k: (
        order.append("send") or DeliveryReceipt(parts={"text": PartDelivery.NATIVE})
    )

    await pushes.push(_push(pod_id=chat_surface.pod_id, actor_agent_id=agent_id))

    assert "send" in order, "the test never reached the platform send"
    between = order[order.index("persist") : order.index("send")]
    assert "commit" in between, f"connection held across the send; order was {order}"


# ------------------------------------------- the channel the agent chose


def _identity(external_user_id: str, *, user_id=None):
    """One cached platform identity. ``tenant_id`` is what scopes it."""
    return SimpleNamespace(
        external_user_id=external_user_id, tenant_id="T123", resolved_user_id=user_id
    )


async def test_a_named_channel_beats_the_ranking_that_would_have_won():
    """The whole point of the argument: the agent knows something we do not.

    Left alone, chat outranks email and the Telegram thread wins — the right
    default, because it is where they last spoke to us. An agent told "she is
    off this week, put it in writing" has a reason that no signal in the
    database carries, so naming a channel has to beat the ranking outright
    rather than nudge it.
    """
    agent_id = uuid4()
    chat = _surface_for(agent_id, SurfacePlatform.TELEGRAM)
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND)
    resolver = _resolver(surfaces=(chat, mailbox), external_user=_identity("U123"))
    resolver.seats.add(_place_for(chat))
    recipient = uuid4()

    by_default, _ = await resolver.resolve(
        pod_id=chat.pod_id, recipient_user_id=recipient, actor_agent_id=agent_id
    )
    chosen, reason = await resolver.resolve(
        pod_id=chat.pod_id,
        recipient_user_id=recipient,
        actor_agent_id=agent_id,
        channel="email",
    )

    assert [c.surface.id for c in by_default] == [chat.id, mailbox.id]
    assert reason == ""
    assert [c.surface.id for c in chosen] == [mailbox.id]


async def test_an_unreachable_named_channel_is_refused_not_rerouted():
    """Sending it on email anyway would be worse than not sending it.

    The agent asked for Telegram for a reason it does not restate, and it never
    finds out the message went somewhere else — it reads DELIVERED and carries
    on. So the send stops, and the refusal names both facts the agent needs:
    that nothing went out, and what would have worked instead.
    """
    agent_id = uuid4()
    chat = _surface_for(agent_id, SurfacePlatform.TELEGRAM)
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND)
    # Nobody has written to the bot, so there is no thread to reply into.
    resolver = _resolver(surfaces=(chat, mailbox))

    channels, reason = await resolver.resolve(
        pod_id=chat.pod_id,
        recipient_user_id=uuid4(),
        actor_agent_id=agent_id,
        channel="telegram",
    )

    assert channels == []
    assert "have not messaged this agent on Telegram" in reason
    assert "Nothing was sent elsewhere" in reason
    assert "email would reach them" in reason


async def test_asking_for_a_channel_the_agent_does_not_have_says_so():
    """A different fix to a different person: connect one, or pick another."""
    agent_id = uuid4()
    resolver = _resolver(surfaces=(_surface_for(agent_id, SurfacePlatform.RESEND),))

    channels, reason = await resolver.resolve(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        actor_agent_id=agent_id,
        channel="slack",
    )

    assert channels == []
    assert "no Slack surface" in reason
    assert "email would reach them" in reason


async def test_asking_for_chat_mints_no_mailbox_but_still_offers_one(monkeypatch):
    """Two halves of the same judgement about a pod that has no surface yet.

    A mailbox is what "reach them somehow" falls back to. Minting one to answer
    "reach them on Telegram" hands out an address nobody asked for, and an
    address handed out is one that has to keep working forever. But the refusal
    still has to name email, because dropping the argument *would* mint one and
    the message would go — an agent told nothing can reach them gives up on
    someone who was one argument away.
    """
    _email_configured(monkeypatch)
    provisioner = AsyncMock(return_value=(_email_surface(), None))
    resolver = _resolver(provisioner=provisioner, surfaces=())

    channels, reason = await resolver.resolve(
        pod_id=uuid4(),
        recipient_user_id=uuid4(),
        actor_agent_id=uuid4(),
        channel="telegram",
    )

    assert channels == []
    provisioner.assert_not_awaited()
    assert "no Telegram surface" in reason
    assert "email would reach them" in reason


async def test_the_workspace_they_actually_use_is_found_among_their_identities():
    """A pod with two Slack workspaces had one of them permanently unreachable.

    Slack ids are per workspace, so this person is two rows, and delivery used
    to take whichever was seen most recently and look for a thread under it. On
    the other surface that id matches nothing — so the surface they are actively
    chatting on yielded no channel, and the reason handed back said they had
    never messaged us.
    """
    agent_id = uuid4()
    seen_last = _surface_for(agent_id, SurfacePlatform.SLACK)
    where_they_talk = _surface_for(
        agent_id, SurfacePlatform.SLACK, pod_id=seen_last.pod_id
    )
    resolver = _resolver(surfaces=(seen_last, where_they_talk))
    resolver.external_users.list_by_resolved_users = AsyncMock(
        # Freshest first, which is the order the repository returns.
        return_value=[_identity("U-OTHER"), _identity("U-THEIRS")]
    )
    resolver.seats.add(_place_for(where_they_talk, "U-THEIRS"))

    channels, reason = await resolver.resolve(
        pod_id=seen_last.pod_id, recipient_user_id=uuid4(), actor_agent_id=agent_id
    )

    assert reason == ""
    assert [c.surface.id for c in channels] == [where_they_talk.id]


async def test_reachability_says_what_the_agent_can_choose_between():
    """What `list_pod_members` shows, so that choosing is a read not a guess.

    Per person, because reachability is per person: the same agent can hold a
    Telegram thread with one colleague and nothing but an address for another.
    """
    agent_id = uuid4()
    chat = _surface_for(agent_id, SurfacePlatform.TELEGRAM)
    mailbox = _surface_for(agent_id, SurfacePlatform.RESEND)
    priya, bob = uuid4(), uuid4()
    resolver = _resolver(surfaces=(chat, mailbox))
    resolver.external_users.list_by_resolved_users = AsyncMock(
        return_value=[_identity("U-PRIYA", user_id=priya)]
    )
    resolver.seats.add(_place_for(chat, "U-PRIYA"))

    reach = await resolver.reachable_channels(
        pod_id=chat.pod_id,
        recipients={priya: "priya@example.com", bob: None},
        actor_agent_id=agent_id,
    )

    assert reach == {priya: ["email", "telegram"], bob: []}


# ------------------------------------------------- what the header ends up saying


async def _push_and_read_header(monkeypatch, *, actor_user_id, recipient_user_id):
    """Push one line by cold email and hand back what went to the platform."""
    _email_configured(monkeypatch)

    surface = _email_surface()
    pushes = _pushes(surfaces=(surface,))
    pushes.egress.egress.open_cold_email_thread.side_effect = _cold_opens(surface)

    await pushes.push(
        _push(
            pod_id=surface.pod_id,
            recipient_user_id=recipient_user_id,
            actor_user_id=actor_user_id,
            agent_name="Priya",
        )
    )
    return pushes.egress.egress.open_cold_email_thread.await_args.kwargs


async def test_a_message_to_its_own_asker_carries_no_header(monkeypatch):
    """ "On behalf of Deepak" told Deepak nothing, and read as a third party.

    The header exists to resolve one ambiguity: under a bot you trust, whose
    authority is this? When the reader *is* that authority there is nothing to
    resolve.
    """
    same_person = uuid4()
    kwargs = await _push_and_read_header(
        monkeypatch, actor_user_id=same_person, recipient_user_id=same_person
    )

    assert kwargs["message"] == "What did you ship yesterday?"
    # Not in the metadata either: the From line is the pod's name, and the
    # person's own name on a message they asked for is what the body omits.
    assert "actor_display_name" not in kwargs["metadata"]


async def test_a_colleagues_authority_is_still_named(monkeypatch):
    """The phishing case, and the reason the line is not simply deleted.

    One bot, one name, one avatar: an agent acting for Deepak and the same agent
    acting for someone else are indistinguishable without this.
    """
    kwargs = await _push_and_read_header(
        monkeypatch, actor_user_id=uuid4(), recipient_user_id=uuid4()
    )

    assert kwargs["message"].startswith("On behalf of Ada Member:")
    # Never the agent: the bot it arrives from is already the answer to that.
    assert "Priya" not in kwargs["message"]
    # And the body is the only place the person is named. The From line says the
    # pod, which is the whole of the header's job.
    assert "actor_display_name" not in kwargs["metadata"]
    assert kwargs["metadata"]["email_sender_name"] == "Acme"
    # The subject is the message's first line, not a title nobody wrote.
    assert kwargs["subject"] == "What did you ship yesterday?"
