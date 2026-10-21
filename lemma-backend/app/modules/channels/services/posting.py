"""Adding a line to a timeline, and everything that follows from it, at once.

``Timeline.post`` is the only way a line is written. In one transaction it
numbers the line, seats whoever said it, records who it names and what it
asks, moves a contact's channel along (a member writing steps in), plans its
deliveries to the outside places it is for, and announces it; pushes to
the members who hear of it follow the commit (``services/pushes``). Nothing
that writes a line decides any of that for itself, so every producer -- a
member, an agent, a workflow, a platform -- gets the same treatment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID


from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import (
    ChannelDeliveryDue,
    ChannelMessagePosted,
)
from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Question,
    SeatedBy,
    SenderKind,
)
from app.modules.channels.infrastructure.cases import CaseRepository
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.lines import (
    LineMisplaced,
    LineRepository,
    NewLine,
)
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.mentions import sync_mentions
from app.modules.channels.infrastructure.questions import (
    NewQuestion,
    QuestionRepository,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.revisions import holding
from app.modules.channels.infrastructure.source_repository import SourceRepository


class ChannelNotFound(LookupError):
    """The channel a line was written for is gone."""


@dataclass(frozen=True, slots=True)
class Post:
    line: NewLine
    question: NewQuestion | None = None
    #: People the line names, already resolved (and allowed to see it).
    mentions: Sequence[Principal] = ()
    #: The pod's agents the line names.
    mentioned_agents: Sequence[UUID] = ()
    #: The outside places it is to be carried to.
    deliver_to: Sequence[UUID] = ()
    #: The pod's own agent, which answers everything in a member's channel.
    pod_agent_id: UUID | None = None


class Timeline:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session
        self.channels = ChannelRepository(uow.session)
        self.lines = LineRepository(uow.session)
        self.members = MemberRepository(uow.session)
        self.questions = QuestionRepository(uow.session)
        self.deliveries = DeliveryRepository(uow.session)
        self.sources = SourceRepository(uow.session)

    async def post(self, channel: Channel, post: Post) -> ChannelMessage | None:
        """Write the line -- once, however often asked -- and what follows it.
        ``None`` for a line with nothing in it."""
        line = post.line
        if not line.text.strip() and not line.parts and post.question is None:
            return None
        await self._in_place(channel, post)
        # The channel's row is held from the line's number to the end of the
        # block, and its head written once, there (``revisions.holding``).
        async with holding(self.session, channel.id):
            try:
                message, written = await self.lines.append(channel.id, line)
            except LookupError as gone:
                raise ChannelNotFound(str(gone)) from gone
            question = await self._ask(channel, message, post.question)
            # Heard again, perhaps now knowing who said it: they are seated.
            await self._seat(channel, message)
            if written:
                await self._name(channel, message, post)
                await self._move_contact(channel, message)
        # Read against the head just written: their own line is read.
        await self._read_own(channel, message)
        if not written:
            return message.model_copy(update={"question": question or message.question})
        agents = await self._answering(channel, message, post)
        await self._deliver(channel, message, post, question)
        await facts.emit(
            self.uow,
            ChannelMessagePosted(
                pod_id=channel.pod_id,
                channel_id=channel.id,
                channel_kind=channel.kind,
                message_id=message.id,
                seq=message.seq,
                sender_kind=message.sender.kind,
                sender_user_id=message.sender.user_id,
                source_id=message.source_id,
                members_only=message.members_only,
                thread_root_id=message.thread_root_id,
                answering_agent_ids=tuple(agents),
                assignee_user_id=channel.assignee_user_id,
            ),
        )
        return message.model_copy(update={"question": question})

    async def _in_place(self, channel: Channel, post: Post) -> None:
        """Every place the line goes to is of this channel. Its thread, the
        line it replies to and the place it was heard at or carried to are
        held to the channel by the database (``lines.LineMisplaced``)."""
        line = post.line
        places = set(post.deliver_to) - {line.source_id, line.target_source_id}
        if places:
            ours = await self.sources.in_channel(channel.id, places)
            if missing := places - ours:
                raise LineMisplaced(
                    f"{sorted(map(str, missing))} do not feed this channel"
                )

    async def _ask(
        self, channel: Channel, message: ChannelMessage, new: NewQuestion | None
    ) -> Question | None:
        if new is None:
            return None
        return await self.questions.ask(
            message_id=message.id,
            channel_id=channel.id,
            pod_id=channel.pod_id,
            new=new,
        )

    async def _seat(self, channel: Channel, message: ChannelMessage) -> None:
        """Whoever said it is in the channel, with when they last spoke there.
        Nobody but its owner is ever seated in a member's own channel."""
        speaker = _seated_speaker(channel, message)
        if speaker is None:
            return
        await self.members.add_person(
            channel,
            speaker,
            # Heard at the platform's place: the platform's to take back.
            seated_by=SeatedBy.PLATFORM if message.heard_outside else SeatedBy.SPOKE,
            spoke=(message.created_at, message.sender.display_name),
        )

    async def _read_own(self, channel: Channel, message: ChannelMessage) -> None:
        """A member has read where they wrote, up to their own line: the
        timeline, or -- for a reply -- its thread, and never the timeline:
        replying is no sign of what else they saw."""
        speaker = _seated_speaker(channel, message)
        if speaker is None or speaker.user_id is None:
            return
        if message.thread_root_id is not None:
            await self.members.mark_thread_read(
                channel_id=channel.id,
                user_id=speaker.user_id,
                root_id=message.thread_root_id,
                seq=message.seq,
            )
            return
        await self.members.mark_read(
            channel_id=channel.id, user_id=speaker.user_id, seq=message.seq
        )

    async def _name(
        self, channel: Channel, message: ChannelMessage, post: Post
    ) -> None:
        """Record who the line names. Naming somebody never puts them in it."""
        await sync_mentions(
            self.session,
            message=message,
            people=post.mentions,
            agents=post.mentioned_agents,
            fresh=True,
        )

    async def _move_contact(self, channel: Channel, message: ChannelMessage) -> None:
        """A contact's case moves with the line: a member writing to them steps
        in and waits on them; the contact writing opens it again. Its own
        tables, and the channel's head through the post's single write."""
        await CaseRepository(self.session).after_line(channel, message)

    async def _answering(
        self, channel: Channel, message: ChannelMessage, post: Post
    ) -> list[UUID]:
        """The agents that answer a member's line written in Lemma -- wherever
        it is carried: those it names, those in the channel that answer every
        line, and -- in a member's own channel with none -- the pod's agent.
        A line heard on a platform is answered there, by ``agent_surfaces``.
        A members' note is answered only by the agents it names -- as a note
        -- never by one answering every line: notes are members talking."""
        sender = message.sender
        if sender.kind is not SenderKind.MEMBER or message.heard_outside:
            return []
        answering = list(dict.fromkeys(post.mentioned_agents))
        if message.members_only:
            return answering
        for member in await self.members.agents(channel.id):
            if (
                member.answers is AgentAnswers.EVERY_LINE
                and member.agent_id is not None
                and member.agent_id not in answering
            ):
                answering.append(member.agent_id)
        if not answering and channel.kind is ChannelKind.DIRECT and post.pod_agent_id:
            answering.append(post.pod_agent_id)
        return answering

    async def _deliver(
        self,
        channel: Channel,
        message: ChannelMessage,
        post: Post,
        question: Question | None,
    ) -> None:
        """Plan the line's sends to the outside places it is for. Pushes to
        the members who hear of it come after the commit (``pushes``)."""
        del question
        if message.members_only:
            return
        for source_id in dict.fromkeys(post.deliver_to):
            delivery = await self.deliveries.plan(
                message_id=message.id,
                channel_id=channel.id,
                seq=message.seq,
                pod_id=channel.pod_id,
                source_id=source_id,
            )
            await facts.emit(
                self.uow,
                ChannelDeliveryDue(
                    pod_id=channel.pod_id,
                    delivery_id=delivery.id,
                    target=delivery.target_key,
                ),
            )


def _seated_speaker(channel: Channel, message: ChannelMessage) -> Principal | None:
    """Who said the line, if they are seated for it: nobody but its owner in a
    member's own channel."""
    speaker = message.sender.principal
    if speaker is None:
        return None
    if channel.kind is ChannelKind.DIRECT and speaker.user_id != channel.member_user_id:
        return None
    return speaker
