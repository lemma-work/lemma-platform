"""A notification and its answer, followed all the way round.

The requirement is one sentence: the reply to a notification lands in the same
conversation the notification was written into, and the agent that asked is woken
in *its* conversation when the answer is recorded. Nothing in between -- a quiet
day, a second notification, an agent change, the history trim -- may break it.

A notification is written into the person's one conversation with the sending
agent, in their own channel; that is also where whatever they write back lands.

Each of those was fixed in the place it lives and tested there, which is exactly
how the journey between them came to have a hole no test could see. So these
follow the whole path with real objects (`NotificationService`,
`NotificationEgress`, `ConversationBinder`, `SurfaceRouter`, the history
assembly and the settled-event handler) over in-memory stand-ins for the two
things that are databases: the channel seats and notification repository, and
the conversation operations `agent` publishes.

The stand-ins implement the *behaviour* the real ones have -- a place is keyed
by who it is, a conversation opened in a channel is found there again, a
conversation has messages in one sequence -- rather than returning canned
answers, because a canned answer certifies only that the caller asked. The SQL behind them is covered against a real database in
`tests/e2e/test_notification_reply_journey_e2e.py`.
"""

from __future__ import annotations

from app.modules.agent.domain.outsiders import Audience

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse

from app.modules.test_support.channel_seats_double import InMemoryChannelSeats
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    RecordingNotificationWork,
)
from app.modules.channels.contracts import Source, SourceKind
from app.modules.test_support.channel_lines_double import RecordingChannelLines
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.domain.runtime_profiles import SYSTEM_LEMMA_PROFILE_ID
from app.modules.agent.domain.entities import (
    AgentRun,
    Message,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)
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
from app.modules.agent_surfaces.contracts.notification_facts import (
    NotificationSettled,
)
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
)
from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
    expire_one,
)
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _telegram_event,
    build_doubles,
    conversation_operations,  # noqa: F401  (autouse fixture)
)


async def _no_outside_origin(_conversation_id):
    """No conversation here answers people outside the pod."""
    return


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

    async def load_unattached_notifications(
        self, conversation_id, *, after_sequence, before_sequence, limit
    ):
        """The contract of the query, not a second opinion on it."""
        matching = [
            message
            for message in self.messages[conversation_id]
            if message.agent_run_id is None
            and message.kind is MessageKind.NOTIFICATION
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


class InMemoryNotifications:
    def __init__(self) -> None:
        self.rows: dict[UUID, NotificationEntity] = {}

    async def create(self, notification):
        self.rows[notification.id] = notification.model_copy(deep=True)
        return notification

    async def get(self, notification_id):
        row = self.rows.get(notification_id)
        return row.model_copy(deep=True) if row else None

    async def update(self, notification):
        self.rows[notification.id] = notification.model_copy(deep=True)
        return notification

    async def list_open_for_conversation(self, conversation_id):
        return [
            row
            for row in self.rows.values()
            if row.delivery_conversation_id == conversation_id
            and row.status is NotificationStatus.OPEN
            and row.expects_response
        ]

    async def count_open_from_origin_conversation(self, conversation_id):
        return sum(
            1
            for row in self.rows.values()
            if row.origin_kind is NotificationOriginKind.AGENT_RUN
            and row.origin_conversation_id == conversation_id
            and row.status is NotificationStatus.OPEN
            and row.expects_response
        )


class RecordingEgress:
    """The platform: what was sent, into which conversation."""

    def __init__(self, agent_side: AgentSide, *, cold_open: bool = False) -> None:
        self.agent_side = agent_side
        self.cold_open = cold_open
        self.sent: list[tuple[UUID, str]] = []
        self.thread = None

    async def agent_name_for_surface(self, surface):
        return self.agent_side.name_for(surface.agent_id)

    async def send_agent_message_for_conversation(
        self, *, conversation_id, message, metadata=None, at=None
    ):
        self.sent.append((conversation_id, message))
        return True

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


class World:
    """One pod, one surface and the two people either side of a notification."""

    def __init__(self, monkeypatch, *, surface: AgentSurfaceEntity, agent_side):
        self.surface = surface
        self.agent_side = agent_side
        self.asker_user_id = uuid4()
        self.recipient_user_id = uuid4()
        self.notifications = InMemoryNotifications()
        self.seats = agent_side.seats
        self.egress = RecordingEgress(agent_side)
        self.events: list = []
        self.uow = SimpleNamespace(
            commit=AsyncMock(),
            collect_events=MagicMock(side_effect=self.events.extend),
        )
        # The settled fact is emitted, and each notification's expiry armed, as
        # work items on the unit of work; recorded here instead.
        self.work = RecordingNotificationWork(self.events)
        self.email = "asha@example.com"
        identities = (
            []
            if surface.surface_type.is_email
            else [SimpleNamespace(external_user_id=_CHAT, tenant_id=None)]
        )
        self.service = NotificationService(
            outside_origin_reader=_no_outside_origin,
            uow=self.uow,
            notification_repository=self.notifications,
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
            work=self.work,
            seats=self.seats,
            lines=RecordingChannelLines(),
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
        # the handler names.
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
                surface_id=self.surface.id,
                platform=self.surface.surface_type.value,
                kind=SourceKind.PRIVATE,
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

    async def ask(
        self, *, expects_response=True, title="Offsite dates"
    ) -> NotificationEntity:
        """The asking agent messages the person."""
        return await self.service.notify(
            pod_id=self.surface.pod_id,
            recipient_user_id=self.recipient_user_id,
            title=title,
            body=f"{title}: does the 14th work for you?",
            origin_kind=NotificationOriginKind.AGENT_RUN,
            origin_id=uuid4(),
            origin_conversation_id=self.asker_conversation.id,
            actor_user_id=self.asker_user_id,
            actor_agent_id=self.surface.agent_id,
            agent_name=self.agent_side.name_for(self.surface.agent_id),
            background_instruction="Record whether they can make the 14th.",
            expects_response=expects_response,
        )

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

    async def settle(self) -> list[UUID]:
        """Everything the settled events start, as the agent's item runs them."""
        woken: list[UUID] = []

        def factory(uow):
            async def deliver(*, conversation_id, pod_id):
                self.agent_side.start_run(conversation_id, "everyone has replied")
                woken.append(conversation_id)
                return True

            return deliver

        # The agent's `agent.replies_arrived` item does exactly this with the
        # settled conversation (`agent/events/run_followup_work.deliver_replies`).
        for event in [e for e in self.events if isinstance(e, NotificationSettled)]:
            await factory(None)(
                conversation_id=event.conversation_id, pod_id=event.pod_id
            )
        self.events.clear()
        return woken


def _telegram_surface(agent_id: UUID) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=agent_id,
        surface_type=SurfacePlatform.TELEGRAM,
        account_id=None,
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


async def test_a_reply_after_a_quiet_day_reaches_the_notification_and_wakes_the_asker(
    world,
):
    """The whole path, with the person silent for over a day before it starts.

    They last wrote 30 hours ago, the agent asks them something, and they
    answer a few minutes later: the answer lands with the question.
    """
    ordinary_chat = world.had_chatted(hours_ago=30)

    # (A) the asker messages the person: into their one conversation with the
    # agent, however long it has been quiet, without pretending they wrote.
    notification = await world.ask()

    assert notification.delivery_status is NotificationDeliveryStatus.DELIVERED
    delivered_into = notification.delivery_conversation_id
    assert delivered_into == ordinary_chat.id
    assert delivered_into != world.asker_conversation.id, (
        "a notification is the recipient's conversation, never the asker's"
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

    # ... and is told which request the reply answers, and how to record it.
    [open_request] = await world.notifications.list_open_for_conversation(
        bound.conversation_id
    )
    assert open_request.id == notification.id
    assert open_request.background_instruction

    # It records the answer; the asker is woken *in its own conversation*.
    await world.service.respond(
        pod_id=notification.pod_id,
        notification_id=notification.id,
        responder_user_id=notification.recipient_user_id,
        summary="They can make the 14th.",
    )

    woken = await world.settle()

    assert woken == [world.asker_conversation.id]
    assert [c for c, _ in world.agent_side.turns if c == world.asker_conversation.id]
    assert len(world.agent_side.conversations) == 2, "the wake opened a conversation"


async def test_a_report_is_still_read_against_when_it_was_delivered_recently(world):
    """ "Yes" to a reminder, a day after the person last spoke, is an answer."""
    world.had_chatted(hours_ago=30)
    notification = await world.ask(expects_response=False, title="Report ready")
    world.notifications.rows[notification.id].delivered_at = _now() - timedelta(
        hours=20
    )

    bound, created_title = await world.reply("thanks, send me the numbers")

    assert bound.conversation_id == notification.delivery_conversation_id
    assert created_title is None


async def test_an_unanswered_question_holds_its_conversation_until_it_expires(world):
    """The agent can only record an answer where the question was delivered."""
    world.had_chatted(hours_ago=90)
    notification = await world.ask()
    world.notifications.rows[notification.id].delivered_at = _now() - timedelta(
        hours=70
    )

    bound, _ = await world.reply("sorry, only just saw this: yes")

    assert bound.conversation_id == notification.delivery_conversation_id


async def test_a_second_notification_joins_the_conversation_of_an_unanswered_first(
    world,
):
    """Two questions, one conversation: the person's reply sees both.

    Sent hours apart, the second used to open a conversation of its own, so the
    reply to either landed where the other one's request was invisible and the
    first could never be recorded.
    """
    world.had_chatted(hours_ago=1)
    first = await world.ask(title="Offsite dates")
    world.agent_side.conversations[first.delivery_conversation_id].updated_at = (
        _now() - timedelta(hours=2)
    )

    second = await world.ask(title="Offsite venue")

    assert second.delivery_conversation_id == first.delivery_conversation_id
    bound, _ = await world.reply("the 14th, and the loft")
    open_requests = await world.notifications.list_open_for_conversation(
        bound.conversation_id
    )
    assert {n.id for n in open_requests} == {first.id, second.id}


async def test_a_notification_after_the_first_is_answered_joins_the_same_conversation(
    world,
):
    """There is nothing to split: the person has one conversation with the agent."""
    world.had_chatted(hours_ago=1)
    first = await world.ask(title="Offsite dates")
    await world.service.respond(
        pod_id=first.pod_id,
        notification_id=first.id,
        responder_user_id=first.recipient_user_id,
        summary="The 14th.",
    )

    second = await world.ask(title="Offsite venue")

    assert second.delivery_conversation_id == first.delivery_conversation_id
    bound, _ = await world.reply("the loft")
    assert bound.conversation_id == second.delivery_conversation_id


async def test_a_surface_whose_agent_changed_gets_the_reply_in_the_notification(
    world,
):
    """The notification is the surface's *current* agent's, and so is the reply.

    The person's chat with the agent the surface used to answer as is not where
    either goes: that is another agent's conversation.
    """
    earlier = world.had_chatted(hours_ago=1, agent_id=uuid4())

    notification = await world.ask()
    bound, _ = await world.reply("yes")

    assert notification.delivery_conversation_id != earlier.id
    assert bound.conversation_id == notification.delivery_conversation_id


async def test_a_notification_older_than_the_carried_history_stays_out(world):
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


# ------------------------------------------------------------------ the asker's side


async def test_the_asker_is_woken_only_when_the_last_ask_is_settled(world):
    world.had_chatted(hours_ago=1)
    first = await world.ask(title="Offsite dates")
    second = await world.ask(title="Offsite venue")

    await world.service.respond(
        pod_id=first.pod_id,
        notification_id=first.id,
        responder_user_id=first.recipient_user_id,
        summary="The 14th.",
    )
    assert await world.settle() == [], "woken with a question still outstanding"

    await world.service.respond(
        pod_id=second.pod_id,
        notification_id=second.id,
        responder_user_id=second.recipient_user_id,
        summary="The loft.",
    )
    assert await world.settle() == [world.asker_conversation.id]


async def test_an_fyi_the_asker_sent_does_not_keep_it_asleep(world):
    """Nothing is owed for an FYI, so it cannot be what the asker waits on."""
    world.had_chatted(hours_ago=1)
    question = await world.ask(title="Offsite dates")
    await world.ask(expects_response=False, title="Heads up")

    await world.service.respond(
        pod_id=question.pod_id,
        notification_id=question.id,
        responder_user_id=question.recipient_user_id,
        summary="The 14th.",
    )

    assert await world.settle() == [world.asker_conversation.id]


async def test_an_ask_nobody_answers_wakes_the_asker_when_it_expires(world):
    """Expiry is as settled as an answer, and has to say so.

    The count treats EXPIRED as no longer outstanding, but only recording an
    answer announced it -- so a question nobody ever answered left the asking
    conversation waiting for good.
    """
    world.had_chatted(hours_ago=1)
    answered = await world.ask(title="Offsite dates")
    ignored = await world.ask(title="Offsite venue")
    await world.service.respond(
        pod_id=answered.pod_id,
        notification_id=answered.id,
        responder_user_id=answered.recipient_user_id,
        summary="The 14th.",
    )
    assert await world.settle() == []
    world.notifications.rows[ignored.id].expires_at = _now() - timedelta(minutes=1)

    assert await expire_one(world.uow, world.notifications, ignored.id, work=world.work)

    assert await world.settle() == [world.asker_conversation.id]


async def test_an_fyi_that_expires_wakes_nobody(world):
    world.had_chatted(hours_ago=1)
    fyi = await world.ask(expects_response=False, title="Heads up")
    world.notifications.rows[fyi.id].expires_at = _now() - timedelta(minutes=1)

    assert await expire_one(world.uow, world.notifications, fyi.id, work=world.work)

    assert await world.settle() == []


# ------------------------------------------------------------------------- email


async def test_an_email_reply_to_another_agents_mailbox_stays_in_the_notification(
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

    notification = await world.ask()

    assert notification.delivery_status is NotificationDeliveryStatus.DELIVERED
    assert world.place().kind is SourceKind.PRIVATE
    bound, created_title = await world.reply("yes")

    assert bound.conversation_id == notification.delivery_conversation_id
    assert created_title is None
