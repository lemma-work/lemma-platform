"""Finding the conversation an ask opened, and delivering its answer once.

An ask lives on the conversation it opened in the pod being asked, in its
``ask`` column (``domain/pod_asks.py``), so both questions here are about that
column. The thread lookup is a containment match the ``ix_agent_conv_ask`` GIN
index answers.

The delivery claim is one ``UPDATE ... RETURNING`` whose ``WHERE`` restates the
state it moves the ask out of -- "not yet delivered for this run" -- so the
asker waiting inline and the completion event racing it cannot both deliver the
same answer, and neither has to take a lock to know.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.pod_asks import AskMode, PodAsk
from app.modules.agent.domain.value_objects import ConversationStatus, MessageRole
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.identity.contracts.organizations import (
    organization_member_ids_for_user,
)
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.pod.contracts.user_pods import (
    AttachablePod,
    list_attachable_pods,
    pods_by_ids,
)

_DELIVERED_RUN_KEY = "delivered_run_id"
#: A thread in one of these still has a run going, or one paused on someone.
_BUSY_STATUSES = (
    ConversationStatus.RUNNING.value,
    ConversationStatus.STOP_REQUESTED.value,
    ConversationStatus.WAITING.value,
)
#: One conversation keeps one thread per pod it has asked, and an ask can only
#: go to a pod the person belongs to. A conversation past this many is not one
#: anybody is having, and its oldest threads wait for the next finished run.
MAX_THREADS_READ = 100


class PodAskQueries:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.session = uow.session

    async def find_thread(
        self,
        *,
        to_pod_id: UUID,
        from_conversation_id: UUID,
        owner_user_id: UUID,
        mode: AskMode,
    ) -> UUID | None:
        """The conversation in ``to_pod_id`` already answering asks from that one.

        Per mode and per owner: an ask made as one person never continues a
        thread that belongs to another, and an ask over a link never continues
        one a person opened, whatever their metadata says.
        """
        return await self.session.scalar(
            select(ConversationModel.id)
            .where(
                ConversationModel.pod_id == to_pod_id,
                ConversationModel.user_id == owner_user_id,
                ConversationModel.ask.contains(
                    {
                        "from_conversation_id": str(from_conversation_id),
                        "mode": mode.value,
                    }
                ),
            )
            .order_by(ConversationModel.created_at.desc(), ConversationModel.id.desc())
            .limit(1)
        )

    async def list_threads(self, *, from_conversation_id: UUID) -> list[UUID]:
        """The conversations, in any pod, answering asks from that one.

        Not scoped to an owner: a thread over a link belongs to the link's
        steward, not to whoever owns the asking conversation. Each thread's ask
        is checked against the asking conversation before anything is delivered.
        Capped at ``MAX_THREADS_READ``.
        """
        rows = await self.session.scalars(
            select(ConversationModel.id)
            .where(
                ConversationModel.ask.contains(
                    {"from_conversation_id": str(from_conversation_id)}
                ),
            )
            .order_by(ConversationModel.created_at, ConversationModel.id)
            .limit(MAX_THREADS_READ)
        )
        return list(rows)

    async def busy_link_threads(
        self, *, to_pod_id: UUID, from_pod_id: UUID
    ) -> list[tuple[UUID, UUID]]:
        """``(conversation, owner)`` of each link thread here still working."""
        rows = await self.session.execute(
            select(ConversationModel.id, ConversationModel.user_id).where(
                ConversationModel.pod_id == to_pod_id,
                ConversationModel.status.in_(_BUSY_STATUSES),
                ConversationModel.ask.contains(
                    {"from_pod_id": str(from_pod_id), "mode": AskMode.LINK.value}
                ),
            )
        )
        return [(conversation_id, owner) for conversation_id, owner in rows]

    async def link_runs_since(
        self, *, to_pod_id: UUID, from_pod_id: UUID, since: datetime
    ) -> int:
        """How many runs asks over the link from ``from_pod_id`` started here."""
        count = await self.session.scalar(
            select(func.count(AgentRunModel.id))
            .join(
                ConversationModel,
                ConversationModel.id == AgentRunModel.conversation_id,
            )
            .where(
                ConversationModel.pod_id == to_pod_id,
                ConversationModel.ask.contains(
                    {"from_pod_id": str(from_pod_id), "mode": AskMode.LINK.value}
                ),
                AgentRunModel.created_at >= since,
            )
        )
        return int(count or 0)

    async def latest_turn_source(self, *, conversation_id: UUID) -> str | None:
        """``source`` on the newest user-side message: what started this turn."""
        metadata = await self.session.scalar(
            select(MessageModel.message_metadata)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
            )
            .order_by(MessageModel.sequence.desc())
            .limit(1)
        )
        source = metadata.get("source") if isinstance(metadata, dict) else None
        return source if isinstance(source, str) and source else None

    async def set_ask(self, conversation_id: UUID, ask: PodAsk) -> None:
        """Replace the ask a thread is answering: a further ask in the same
        thread, from the same conversation, continues it."""
        await self.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(ask=ask.to_json())
        )
        await self.session.flush()

    async def claim_delivery(self, *, conversation_id: UUID, run_id: UUID) -> bool:
        """Mark this run's answer delivered; ``False`` if it already was."""
        delivered = ConversationModel.ask[_DELIVERED_RUN_KEY].astext
        claimed = await self.session.scalar(
            update(ConversationModel)
            .where(
                ConversationModel.id == conversation_id,
                ConversationModel.ask.is_not(None),
                delivered.is_distinct_from(str(run_id)),
            )
            .values(
                ask=func.jsonb_set(
                    ConversationModel.ask,
                    array([_DELIVERED_RUN_KEY]),
                    literal(str(run_id), JSONB),
                    True,
                )
            )
            .returning(ConversationModel.id)
        )
        await self.session.flush()
        return claimed is not None


@dataclass(frozen=True, slots=True)
class Teammate:
    """Another pod this one can ask, and how."""

    pod_id: UUID
    name: str
    description: str | None
    icon_url: str | None = None
    #: As the person: they are in both pods.
    through_you: bool = True
    #: Over a link that pod's people made, with nobody present.
    connected: bool = False

    @property
    def reach(self) -> list[str]:
        """How it can be asked, in the words the brief and the tool use."""
        return [
            word
            for word, reachable in (
                ("through the person", self.through_you),
                ("connected", self.connected),
            )
            if reachable
        ]


#: How many other pods are offered at once. Past this the person belongs to more
#: pods than an agent can usefully choose between in one list.
MAX_TEAMMATES = 50


async def askable_pods(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID | None,
    organization_id: UUID | None,
    pod_id: UUID,
) -> list[Teammate]:
    """The other pods this one can ask: through the person, or over a link.

    One answer for the agent's tools, its brief and the About page, so what the
    page says it can ask is exactly what the tool will let it ask. ``user_id``
    is None for a run nobody started, which can only ask over links.
    """
    if organization_id is None:
        return []
    yours: list[AttachablePod] = []
    if user_id is not None:
        memberships = await organization_member_ids_for_user(uow, user_id=user_id)
        yours = await list_attachable_pods(
            session=uow.session,
            organization_member_ids=memberships,
            organization_id=organization_id,
            limit=MAX_TEAMMATES + 1,
        )
    linked = {
        row.answering_pod_id
        for row in await PodLinkQueries(uow).outgoing(asking_pod_id=pod_id)
    }
    by_id = {pod.id: pod for pod in yours if pod.id != pod_id}
    through = set(by_id)
    unseen = [linked_id for linked_id in linked if linked_id not in by_id]
    for pod in await pods_by_ids(session=uow.session, pod_ids=unseen):
        by_id[pod.id] = pod
    return [
        Teammate(
            pod_id=pod.id,
            name=pod.name,
            description=pod.description,
            icon_url=pod.icon_url,
            through_you=pod.id in through,
            connected=pod.id in linked,
        )
        for pod in by_id.values()
    ][:MAX_TEAMMATES]
