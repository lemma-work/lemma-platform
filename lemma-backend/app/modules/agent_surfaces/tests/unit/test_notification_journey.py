"""A message to a member and their answer, followed all the way round.

The requirement is one sentence: the reply to a message lands in the same
conversation the message was written into. Nothing in between -- a quiet day,
a second message, an agent change, the history trim -- may break it.

The message is a line in the member's own channel; its push carries it to
their chat app, into their one conversation with the sending agent there,
which is also where whatever they write back lands. Waking the agent that
asked, once its questions are answered, is ``agent``'s and tested there.

Each step was fixed in the place it lives and tested there, which is exactly
how the journey between them came to have a hole no test could see. So these
follow the whole path with real objects (`MemberPushes`,
`NotificationEgress`, `ConversationBinder`, `SurfaceRouter` and the history
assembly) over in-memory stand-ins for the things that are databases: the
channel seats and the conversation operations `agent` publishes.

The stand-ins implement the *behaviour* the real ones have -- a place is keyed
by who it is, a conversation opened in a channel is found there again --
rather than returning canned answers, because a canned answer certifies only
that the caller asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse

from app.modules.agent.domain.entities import (
    AgentRun,
    Message,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.domain.held_turns import HELD_FOR_A_PERSON
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.runtime_profiles import SYSTEM_LEMMA_PROFILE_ID
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.services.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    assemble_runtime_history,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, PartDelivery
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.member_pushes import MemberPushes, Push
from app.modules.agent_surfaces.services.notification_delivery import Delivered
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _telegram_event,
    build_doubles,
    conversation_operations,  # noqa: F401  (autouse fixture)
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

pytestmark = pytest.mark.asyncio

_CHAT = "777"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- the agent side


class AgentSide:
    """The conversations `agent` owns, as the operations it publishes see them."""

    def __init__(self, *, pod_id: UUID) -> None:
        self.pod_id = pod_id
        #: Where a conversation opened in a channel is found again, as the
        #: conversation's own ``channel_id`` makes it in the database.
        self.seats = InMemoryChannelSeats()
        # name -> id; the pod's own assistant is named by the pod, as it is stored.
        self.agents: dict[str, UUID] = {}
        self.conversations: dict[UUID, SimpleNamespace] = {}
        self.messages: dict[UUID, list[Message]] = {}
        self.runs: dict[UUID, list[AgentRun]] = {}
        #: Conversations a turn was started in, and what it was told.
        self.turns: list[tuple[UUID, str]] = []

    def agent_id_for(self, name: str | None) -> UUID:
        return self.agents.get(name or "", self.pod_id)

    def name_for(self, agent_id: UUID | None) -> str | None:
        return next((n for n, i in self.agents.items() if i == agent_id), None)

    def open(self, *, agent_name: str | None, user_id: UUID, title: str | None):
        conversation = SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            pod_id=self.pod_id,
            agent_id=self.agent_id_for(agent_name),
            title=title,
            updated_at=_now(),
            audience=Audience.member(),
        )
        self.conversations[conversation.id] = conversation
        self.messages[conversation.id] = []
        self.runs[conversation.id] = []
        return conversation

    def _append(self, conversation_id: UUID, **fields) -> Message:
        thread = self.messages[conversation_id]
        message = Message(
            conversation_id=conversation_id,
            sequence=len(thread) + 1,
            **fields,
        )
        thread.append(message)
        self.conversations[conversation_id].updated_at = _now()
        return message

    def start_run(self, conversation_id: UUID, text: str) -> AgentRun:
        """A person's message, and the run that answers it."""
        run = AgentRun(
            conversation_id=conversation_id,
            started_at=_now(),
            agent_runtime=AgentRuntimeConfig(profile_id=SYSTEM_LEMMA_PROFILE_ID),
        )
        self.runs[conversation_id].append(run)
        self._append(
            conversation_id,
            agent_run_id=run.id,
            role=MessageRole.USER,
            kind=MessageKind.TEXT,
            text=text,
        )
        self.turns.append((conversation_id, text))
        return run

    # -- the published operations, bound to this store -----------------------

    async def surface_conversation(self, uow, conversation_id):
        return self.conversations.get(conversation_id)

    async def open_surface_conversation(
        self,
        uow,
        *,
        pod_id,
        agent_name,
        user_id,
        title,
        metadata=None,
        channel_id=None,
        contact_id=None,
        **_,
    ):
        conversation = self.open(agent_name=agent_name, user_id=user_id, title=title)
        if channel_id is not None:
            self.seats.seat(
                conversation.id,
                channel_id=channel_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                person=contact_id or user_id,
            )
        return conversation

    async def append_notification_message(
        self, uow, *, conversation_id, message, notification_id
    ):
        self._append(
            conversation_id,
            agent_run_id=None,
            role=MessageRole.ASSISTANT,
            kind=MessageKind.NOTIFICATION,
            text=message,
            metadata={"notification_id": str(notification_id)},
        )

    # -- what the runner reads -------------------------------------------------

    async def attach_runtime_history_messages(self, runs, *, full_run_ids):
        for run in runs:
            run.messages = [
                m
                for m in self.messages[run.conversation_id]
                if m.agent_run_id == run.id
            ]
        return runs

    async def load_unattached_messages(
        self, conversation_id, *, after_sequence, before_sequence, limit
    ):
        """The contract of the query, not a second opinion on it."""
        matching = [
            message
            for message in self.messages[conversation_id]
            if message.agent_run_id is None
            and (
                message.kind is MessageKind.NOTIFICATION
                or (message.metadata or {}).get(HELD_FOR_A_PERSON) is True
            )
            and (after_sequence is None or message.sequence > after_sequence)
            and (before_sequence is None or message.sequence < before_sequence)
        ]
        return matching[-limit:]

    async def history_for(self, run: AgentRun):
        """What the model is shown for ``run``, split the way the harness splits it."""
        runs = [
            run.model_copy(update={"messages": []})
            for run in self.runs[run.conversation_id]
        ]
        window = RuntimeHistoryWindow(runs=runs, total_runs=len(runs), current_run=run)
        messages = await assemble_runtime_history(
            self,
            window,
            conversation_id=run.conversation_id,
            run_id=run.id,
        )
        return history_and_prompt(messages)


# --------------------------------------------------------------- the surface side


class RecordingEgress:
    """The platform: what was sent, into which conversation."""

    def __init__(self, agent_side: AgentSide, *, cold_open: bool = False) -> None:
        self.agent_side = agent_side
        self.cold_open = cold_open
        self.sent: list[tuple[UUID, str]] = []
        self.thread = None

    async def agent_name_for_surface(self, surface):
        return self.agent_side.name_for(surface.agent_id)

    async def deliver_message(
        self, *, conversation_id, message, metadata=None, at=None
    ):
        self.sent.append((conversation_id, message))
        return DeliveryReceipt(
            parts={"text": PartDelivery.NATIVE}, external_message_id="chat-1"
        )

    async def open_cold_email_thread(
        self, *, surface, recipient_email, subject, message, thread_seed_id, metadata
    ):
        if not self.cold_open:
            return None
        self.thread = build_cold_email_thread(
            surface=surface,
            recipient_email=recipient_email,
            sent=ColdEmailSendResult(
                external_thread_id=thread_seed_id,
                external_message_id="mail-1",
                reply_target={"recipient_email": recipient_email},
            ),
        )
        return self.thread


@dataclass(frozen=True, slots=True)
class Asked:
    """A line in the member's own channel, and where its push took it."""

    line: ChannelMessage
    delivered: Delivered


class World:
    """One pod, one surface and the two people either side of a message."""

    def __init__(self, monkeypatch, *, surface: AgentSurfaceEntity, agent_side):
        self.surface = surface
        self.agent_side = agent_side
        self.asker_user_id = uuid4()
        self.recipient_user_id = uuid4()
        self.seats = agent_side.seats
        self.egress = RecordingEgress(agent_side)
        self.uow = SimpleNamespace(commit=AsyncMock())
        self.email = "asha@example.com"
        identities = (
            []
            if surface.surface_type.is_email
            else [SimpleNamespace(external_user_id=_CHAT, tenant_id=None)]
        )
        self.pushes = MemberPushes(
            uow=self.uow,
            surface_repository=SimpleNamespace(
                list_by_pod=AsyncMock(return_value=([surface], None))
            ),
            external_user_repository=SimpleNamespace(
                list_by_resolved_users=AsyncMock(return_value=identities)
            ),
            egress=self.egress,
            pod_membership_port=SimpleNamespace(
                get_pod_member_id=AsyncMock(return_value=uuid4()),
                get_user_display_name=AsyncMock(return_value="Priya"),
                get_user_email=AsyncMock(return_value=self.email),
            ),
            seats=self.seats,
        )
        doubles = build_doubles(adapter=AsyncMock(), surfaces=[surface])
        self.binder = ConversationBinder(
            uow=doubles.uow,
            surface_repository=doubles.surface_repository,
            seats=self.seats,
        )
        self.router = SurfaceRouter(
            uow=doubles.uow,
            surface_repository=doubles.surface_repository,
            seats=self.seats,
            pod_membership_port=SimpleNamespace(),
            identity_service=SimpleNamespace(),
            credential_resolver=SimpleNamespace(),
        )
        # After `build_doubles`, which configures the fixture's mocks in place.
        for name in (
            "surface_conversation",
            "open_surface_conversation",
            "append_notification_message",
        ):
            monkeypatch.setattr(
                f"app.modules.agent.contracts.conversations_for_surfaces.{name}",
                getattr(agent_side, name),
            )
        monkeypatch.setattr(
            "app.modules.agent.contracts.agents.agent_name_for_id",
            AsyncMock(
                side_effect=lambda _session, agent_id: agent_side.name_for(agent_id)
            ),
        )
        # Asserted through, not around: the wake goes to whichever conversation
        # the work item names.
        self.asker_conversation = agent_side.open(
            agent_name=None, user_id=self.asker_user_id, title="Plan the offsite"
        )

    # -- the journey's steps ---------------------------------------------------

    def had_chatted(self, *, hours_ago: float, agent_id: UUID | None = None):
        """The person's ordinary chat with ``agent_id`` -- the surface's, unless
        it has changed since -- last written to `hours_ago` hours back."""
        moment = _now() - timedelta(hours=hours_ago)
        agent_id = agent_id or self.surface.agent_id
        conversation = self.agent_side.open(
            agent_name=self.agent_side.name_for(agent_id),
            user_id=self.recipient_user_id,
            title="Earlier chat",
        )
        conversation.agent_id = agent_id
        conversation.updated_at = moment
        pod_id = self.surface.pod_id
        channel_id = self.seats.channels.setdefault(
            ("DIRECT", pod_id, self.recipient_user_id), uuid4()
        )
        self.seats.add(
            Source(
                id=uuid4(),
                channel_id=channel_id,
                pod_id=pod_id,
                account_id=uuid4(),
                surface_id=self.surface.id,
                platform=self.surface.surface_type.value,
                kind=SourceKind.PERSON,
                external_user_id=_CHAT,
                last_inbound_at=moment,
                updated_at=moment,
            )
        )
        self.seats.seat(
            conversation.id,
            channel_id=channel_id,
            pod_id=pod_id,
            agent_id=agent_id,
            person=self.recipient_user_id,
        )
        return conversation

    def place(self) -> Source:
        [place] = self.seats.sources.values()
        return place

    async def ask(self, *, title="Offsite dates") -> Asked:
        """The asking agent messages the person, as `channel_post` does: a line
        in their own channel, and its push."""
        pod_id, recipient = self.surface.pod_id, self.recipient_user_id
        now = _now()
        channel = Channel(
            id=await self.seats.direct_channel(pod_id=pod_id, user_id=recipient),
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
                self.surface.agent_id or pod_id,
                on_behalf_of=Principal.member(self.asker_user_id),
                display_name=self.agent_side.name_for(self.surface.agent_id),
            ),
            text=f"{title}: does the 14th work for you?",
            created_at=now,
        )
        delivered = await self.pushes.push(
            Push(channel=channel, line=line, recipient_user_id=recipient, member=None)
        )
        return Asked(line=line, delivered=delivered)

    async def reply(self, text="yes"):
        """The person answers on their chat app; ingress binds it to a conversation."""
        if self.surface.surface_type.is_email:
            event = ParsedInboundSurfaceEvent.model_validate(
                {
                    **self.egress.thread.last_event,
                    "external_message_id": "reply-1",
                    "message_text": text,
                    "sender_email": self.email,
                }
            )
        else:
            event = _telegram_event(chat_id=_CHAT, message_id="reply-1").model_copy(
                update={"message_text": text}
            )
        route = await self.router.resolve_route(surface=self.surface, parsed=event)
        bound = await self.binder.bind_conversation(
            surface=self.surface,
            parsed=event,
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=self.recipient_user_id,
                external_user_id=event.sender_external_user_id,
            ),
            route=route,
        )
        return bound, bound.created_title


def _telegram_surface(agent_id: UUID) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=agent_id,
        surface_type=SurfacePlatform.TELEGRAM,
        pod_connector_slug=None,
        config=SurfaceConfig(),
        is_active=True,
    )


@pytest.fixture
def world(monkeypatch) -> World:
    agent_id = uuid4()
    surface = _telegram_surface(agent_id)
    agent_side = AgentSide(pod_id=surface.pod_id)
    agent_side.agents["Surface Agent"] = agent_id
    return World(monkeypatch, surface=surface, agent_side=agent_side)


# --------------------------------------------------------------------- the journey


async def test_a_reply_after_a_quiet_day_reaches_the_message(world):
    """The whole path, with the person silent for over a day before it starts.

    They last wrote 30 hours ago, the agent asks them something, and they
    answer a few minutes later: the answer lands with the question.
    """
    ordinary_chat = world.had_chatted(hours_ago=30)

    # (A) the asker messages the person: a line in their channel, pushed into
    # their one conversation with the agent, however long it has been quiet,
    # without pretending they wrote.
    asked = await world.ask()

    assert asked.line.channel_id == world.place().channel_id
    assert asked.delivered.platform == "TELEGRAM"
    delivered_into = asked.delivered.conversation_id
    assert delivered_into == ordinary_chat.id
    assert delivered_into != world.asker_conversation.id, (
        "a message is the recipient's conversation, never the asker's"
    )
    assert world.place().last_inbound_at < _now() - timedelta(hours=29), (
        "an outbound message must not count as the person having written"
    )

    # (B) they answer. Ingress must bind the answer to that same conversation.
    bound, created_title = await world.reply("yes, the 14th works")

    assert bound.conversation_id == delivered_into, (
        "the reply was cut into a new conversation, away from the question it answers"
    )
    assert created_title is None
    assert len(world.agent_side.conversations) == 2  # their chat, and the asker's

    # (C) the agent handling the reply can see what was asked ...
    run = world.agent_side.start_run(delivered_into, "yes, the 14th works")
    history, prompt = await world.agent_side.history_for(run)
    # Their words, after the note of when they sent them.
    assert prompt.endswith("</notes>\n\nyes, the 14th works")
    assert isinstance(history[-1], ModelResponse), "the question must precede the reply"
    assert "does the 14th work" in history[-1].parts[0].content
    assert not any(isinstance(message, ModelRequest) for message in history)


async def test_a_reply_days_later_still_reaches_the_question(world):
    """The agent can only record an answer where the question was delivered."""
    world.had_chatted(hours_ago=90)
    asked = await world.ask()

    bound, _ = await world.reply("sorry, only just saw this: yes")

    assert bound.conversation_id == asked.delivered.conversation_id


async def test_a_second_message_joins_the_conversation_of_an_unanswered_first(world):
    """Two questions, one conversation: the person's reply sees both.

    Sent hours apart, the second used to open a conversation of its own, so the
    reply to either landed where the other one's question was invisible and the
    first could never be recorded.
    """
    world.had_chatted(hours_ago=1)
    first = await world.ask(title="Offsite dates")
    world.agent_side.conversations[first.delivered.conversation_id].updated_at = (
        _now() - timedelta(hours=2)
    )

    second = await world.ask(title="Offsite venue")

    assert second.delivered.conversation_id == first.delivered.conversation_id
    bound, _ = await world.reply("the 14th, and the loft")
    assert bound.conversation_id == first.delivered.conversation_id


async def test_a_surface_whose_agent_changed_gets_the_reply_with_the_message(world):
    """The message is the surface's *current* agent's, and so is the reply.

    The person's chat with the agent the surface used to answer as is not where
    either goes: that is another agent's conversation.
    """
    earlier = world.had_chatted(hours_ago=1, agent_id=uuid4())

    asked = await world.ask()
    bound, _ = await world.reply("yes")

    assert asked.delivered.conversation_id != earlier.id
    assert bound.conversation_id == asked.delivered.conversation_id


async def test_a_message_older_than_the_carried_history_stays_out(world):
    """The lower bound survives for a conversation that really was cut."""
    agent_side = world.agent_side
    conversation = agent_side.open(
        agent_name=None, user_id=world.recipient_user_id, title="Long chat"
    )
    await agent_side.append_notification_message(
        None,
        conversation_id=conversation.id,
        message="an ancient report",
        notification_id=uuid4(),
    )
    for index in range(MAX_HISTORY_AGENT_RUNS + 5):
        agent_side.start_run(conversation.id, f"message {index}")
        if index == MAX_HISTORY_AGENT_RUNS:
            await agent_side.append_notification_message(
                None,
                conversation_id=conversation.id,
                message="a recent reminder",
                notification_id=uuid4(),
            )
    current = agent_side.start_run(conversation.id, "yes")

    history, prompt = await agent_side.history_for(current)

    text = " ".join(str(part.content) for message in history for part in message.parts)
    assert prompt.endswith("</notes>\n\nyes")
    assert "a recent reminder" in text
    assert "an ancient report" not in text


# ------------------------------------------------------------------------- email


async def test_an_email_reply_to_an_agents_mailbox_stays_with_the_message(
    monkeypatch,
):
    """A cold-open email is a place in the person's channel, and the
    conversation it was sent from is that agent's -- so the reply, routed to
    the same agent, continues it."""
    agent_id = uuid4()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="resend",
        agent_id=agent_id,
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email="ops.pod@ops.test",
        is_active=True,
    )
    agent_side = AgentSide(pod_id=surface.pod_id)
    agent_side.agents["Surface Agent"] = agent_id
    world = World(monkeypatch, surface=surface, agent_side=agent_side)
    world.egress.cold_open = True

    asked = await world.ask()

    assert asked.delivered.platform == "RESEND"
    assert world.place().kind is SourceKind.PERSON
    assert world.place().channel_id == asked.line.channel_id
    bound, created_title = await world.reply("yes")

    assert bound.conversation_id == asked.delivered.conversation_id
    assert created_title is None
