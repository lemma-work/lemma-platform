"""``agent_pauses``: opening a pause, reading what is waiting, ending one once.

One read, :meth:`Pauses.pending`, answers every "what is this conversation (or
run) waiting on" -- which pause a reply routes to, which a new message
supersedes, whether a resume may start, what a card shows. It replaced ten
queries over tool-call messages matched by tool name, each with its own idea
of what counted as answered.

Every pause comes back with its call (the message a card renders from) and
whether a return has landed for it. Endings are written by :meth:`save`, fenced
on the status the pause was read with, so of two racing callers exactly one
wins. The inbox reads across one person's conversations live here too, so the
inbox and the history list's badge cannot disagree about what counts.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Select,
    and_,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import aliased

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.pause_opening import PauseOpening
from app.modules.agent.domain.pauses import ALL_KINDS, ASKED_KINDS, DECIDED, Pause
from app.modules.agent.domain.pending_interactions import (
    AGENT_HOST_PERMISSION_DECISION_WINDOW,
    PendingInteraction,
    PendingInteractionCursor,
    PendingInteractionKind,
    clipped_summary,
)
from app.modules.agent.domain.state_kinds import PauseKind, PauseStatus
from app.modules.agent.domain.value_objects import ConversationStatus, MessageKind
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import (
    AgentModel,
    AgentPauseModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

_P = AgentPauseModel
_READ_CAP = 500


class Awaiting(Enum):
    """Which pauses :meth:`Pauses.pending` returns. None of them has a return."""

    #: Nobody has decided it: what a reply routes to and a message supersedes.
    DECISION = "decision"
    #: Decided, its return not landed yet -- an approved tool still running.
    RETURN = "return"
    #: Either: the card stays up until the return is durable.
    EITHER = "either"
    #: Every pause, answered or not.
    ANY = "any"


def _returned() -> ColumnElement[bool]:
    """Correlated on ``agent_pauses``: its call has a return."""
    # Aliased per call rather than at import: aliasing configures the mappers,
    # and at import time not every model they relate to is defined yet.
    answer = aliased(MessageModel)
    return exists().where(
        answer.conversation_id == _P.conversation_id,
        answer.tool_call_id == _P.tool_call_id,
        answer.kind == MessageKind.TOOL_RETURN.value,
    )


def _first_call_id() -> ScalarSelect[UUID]:
    """Correlated on ``agent_pauses``: the call that opened it, the first written."""
    return (
        select(MessageModel.id)
        .where(
            MessageModel.conversation_id == _P.conversation_id,
            MessageModel.tool_call_id == _P.tool_call_id,
            MessageModel.kind == MessageKind.TOOL_CALL.value,
        )
        .order_by(MessageModel.sequence)
        .limit(1)
        .scalar_subquery()
    )


def _awaiting(awaiting: Awaiting) -> list[ColumnElement[bool]]:
    if awaiting is Awaiting.ANY:
        return []
    statuses = {
        Awaiting.DECISION: {PauseStatus.PENDING},
        Awaiting.RETURN: DECIDED,
        Awaiting.EITHER: {PauseStatus.PENDING, *DECIDED},
    }[awaiting]
    return [_P.status.in_(statuses), ~_returned()]


class Pauses(UnitOfWorkRepository):
    """Every pause of the agent's runs: opened, read and ended here."""

    def _select(self) -> Select[tuple[AgentPauseModel, MessageModel | None, bool]]:
        call = aliased(MessageModel)
        return (
            select(_P, call, _returned().label("returned"))
            .outerjoin(call, call.id == _first_call_id())
            .order_by(call.sequence, _P.created_at)
            # A conversation pauses on a handful at a time; this only bounds a
            # read that has gone wrong.
            .limit(_READ_CAP)
        )

    @staticmethod
    def _pause(row: Row[tuple[AgentPauseModel, MessageModel | None, bool]]) -> Pause:
        model, call, returned = row
        pause = model.to_entity()
        pause.call = call.to_entity() if call is not None else None
        pause.returned = bool(returned)
        return pause

    async def open(self, pause: Pause) -> bool:
        """Write a new pause; False when its call already opened one."""
        result = await self.session.execute(
            insert(_P)
            .values(
                id=pause.id,
                created_at=pause.created_at,
                updated_at=pause.updated_at,
                conversation_id=pause.conversation_id,
                run_id=pause.run_id,
                pod_id=pause.pod_id,
                tool_call_id=pause.tool_call_id,
                tool_name=pause.tool_name,
                kind=pause.kind,
                **_P.values_of(pause),
            )
            .on_conflict_do_nothing(index_elements=["conversation_id", "tool_call_id"])
        )
        await self.session.flush()
        return result.rowcount > 0

    async def open_for_call(
        self,
        opening: PauseOpening,
        *,
        conversation_id: UUID,
        run_id: UUID,
        pod_id: UUID,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        """Open the pause a call being written opens (``Messages.append_message``)."""
        await self.open(
            Pause.opened_by(
                opening,
                conversation_id=conversation_id,
                run_id=run_id,
                pod_id=pod_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
        )

    async def get(self, *, conversation_id: UUID, tool_call_id: str) -> Pause | None:
        """The pause a call opened, whatever its status."""
        row = (
            await self.session.execute(
                self._select().where(
                    _P.conversation_id == conversation_id,
                    _P.tool_call_id == tool_call_id,
                )
            )
        ).first()
        return self._pause(row) if row is not None else None

    async def by_id(self, pause_id: UUID, *, lock: bool = False) -> Pause | None:
        """A pause by its own id -- a wait's timer names it so. ``lock`` takes
        the row lock: the conversation's first, as every ending takes them."""
        if lock:
            await self.session.execute(
                select(_P.id).where(_P.id == pause_id).with_for_update()
            )
        row = (
            await self.session.execute(self._select().where(_P.id == pause_id))
        ).first()
        return self._pause(row) if row is not None else None

    async def pending(
        self,
        conversation_id: UUID | None = None,
        *,
        run_id: UUID | None = None,
        kinds: Collection[PauseKind] = ALL_KINDS,
        awaiting: Awaiting = Awaiting.DECISION,
        limit: int | None = None,
    ) -> list[Pause]:
        """A conversation's (or a run's) pauses of ``kinds``, oldest first."""
        if conversation_id is None and run_id is None:
            raise ValueError("pending() needs a conversation or a run")
        stmt = self._select().where(_P.kind.in_(list(kinds)), *_awaiting(awaiting))
        if conversation_id is not None:
            stmt = stmt.where(_P.conversation_id == conversation_id)
        if run_id is not None:
            stmt = stmt.where(_P.run_id == run_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [self._pause(row) for row in (await self.session.execute(stmt)).all()]

    async def save(self, pause: Pause) -> bool:
        """Write what the pause became, if nobody moved it since it was read."""
        result = await self.session.execute(
            update(_P)
            .where(_P.id == pause.id, _P.status == pause.read_as)
            .values(**_P.values_of(pause), updated_at=func.now())
            .returning(_P.id)
            .execution_options(synchronize_session=False)
        )
        won = result.scalar_one_or_none() is not None
        if won:
            pause.saved()
        return won

    async def claim_execution(self, pause: Pause) -> bool:
        """Win the right to run an approved tool. True for exactly one caller.

        A conditional UPDATE, so Postgres arbitrates, on the database's clock.
        The caller commits before running the tool: an uncommitted claim is not
        a claim, and a retried job (streaq requeues one cancelled in the
        shutdown grace; ``xautoclaim`` reclaims one whose worker died) must find
        it taken.
        """
        if not pause.claim_execution():
            return False
        result = await self.session.execute(
            update(_P)
            .where(_P.id == pause.id, _P.execution_claimed_at.is_(None))
            .values(execution_claimed_at=func.now())
            .returning(_P.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def close_by_return(
        self, *, conversation_id: UUID, tool_call_id: str
    ) -> None:
        """A call's own return closes the pause it opened but never paused on.

        An approval granted earlier, a question that would not validate, a call
        cut off by its run ending: it returned, so nobody is asked. Not a wait,
        whose row the tool writes only once it really waits.
        """
        await self.session.execute(
            update(_P)
            .where(
                _P.conversation_id == conversation_id,
                _P.tool_call_id == tool_call_id,
                _P.status == PauseStatus.PENDING,
                _P.kind != PauseKind.WAIT,
            )
            .values(
                status=PauseStatus.CANCELLED,
                resolved_at=func.now(),
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )

    async def tool_return(
        self, *, conversation_id: UUID, tool_call_id: str
    ) -> MessageEntity | None:
        """The return for a pause's call, or None.

        The idempotency guard for a resume: once it exists, an approved tool has
        already run and must not run again.
        """
        row = await self.session.scalar(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.tool_call_id == tool_call_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
            )
            .order_by(MessageModel.sequence)
            .limit(1)
        )
        return row.to_entity() if row is not None else None

    async def tool_call_id_of_message(
        self, *, conversation_id: UUID, message_id: UUID
    ) -> str | None:
        """The approvals list hands out a call's message id; a decision is
        addressed by its tool_call_id. This reaches the call from either."""
        return await self.session.scalar(
            select(MessageModel.tool_call_id).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.id == message_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
            )
        )

    async def database_now(self) -> datetime:
        return await self.session.scalar(select(func.now()))

    # -- what is waiting on a person, across their conversations ---------------

    async def waiting_on_person(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        cursor: PendingInteractionCursor | None = None,
        limit: int = 50,
    ) -> tuple[list[PendingInteraction], PendingInteractionCursor | None]:
        """This person's pauses in this pod, longest-waiting first.

        Their own conversations only, sub-agents' included: a child's question
        sits in a conversation they own and would otherwise be found by nobody.
        Archiving is how a person says "not this one", so archived ones are
        left out.
        """
        call = aliased(MessageModel)
        stmt = (
            select(
                call.id,
                call.created_at,
                _P.tool_call_id,
                _P.kind,
                _summary_text(call).label("summary"),
                ConversationModel.id.label("conversation_id"),
                ConversationModel.title,
                ConversationModel.status,
                ConversationModel.agent_id,
                AgentModel.name.label("agent_name"),
            )
            .select_from(_P)
            .join(ConversationModel, ConversationModel.id == _P.conversation_id)
            .join(
                call,
                and_(
                    call.conversation_id == _P.conversation_id,
                    call.tool_call_id == _P.tool_call_id,
                    call.kind == MessageKind.TOOL_CALL.value,
                ),
            )
            .outerjoin(AgentModel, AgentModel.id == ConversationModel.agent_id)
            .where(
                ConversationModel.user_id == user_id,
                ConversationModel.pod_id == pod_id,
                ConversationModel.is_archived.is_(False),
                *_waiting_on_person(),
            )
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(call.created_at, call.id)
                > tuple_(
                    literal(cursor.created_at, call.created_at.type),
                    literal(cursor.message_id, call.id.type),
                )
            )
        rows = list(
            (
                await self.session.execute(
                    stmt.order_by(call.created_at, call.id).limit(limit + 1)
                )
            ).all()
        )
        page = [_interaction(row) for row in rows[:limit]]
        next_cursor = (
            PendingInteractionCursor(
                created_at=page[-1].created_at, message_id=page[-1].message_id
            )
            if len(rows) > limit and page
            else None
        )
        return page, next_cursor

    async def conversations_waiting_on_person(
        self, conversation_ids: Sequence[UUID]
    ) -> set[UUID]:
        """Which of these conversations hold a pause waiting on a person."""
        if not conversation_ids:
            return set()
        result = await self.session.execute(
            select(ConversationModel.id).where(
                ConversationModel.id.in_(list(conversation_ids)),
                exists().where(
                    _P.conversation_id == ConversationModel.id, *_waiting_on_person()
                ),
            )
        )
        return set(result.scalars())


def _waiting_on_person() -> list[ColumnElement[bool]]:
    """Correlated on ``agent_pauses`` and ``agent_conversations``.

    The conversation's status is half of it. A paused run leaves it WAITING; a
    parked remote question or an Agent Host permission request holds a run
    that is still RUNNING. A pause in a conversation that is neither was
    overtaken -- the run failed or was stopped around it. A permission request
    is further bounded by the host's own timeout, unless Lemma ended its turn
    for it (the parked window): then it is answered with a resume, not by the
    host, and stays answerable.
    """
    waiting_or_running = ConversationModel.status.in_(
        [ConversationStatus.WAITING.value, ConversationStatus.RUNNING.value]
    )
    host_permission = _P.kind == PauseKind.HOST_PERMISSION
    suspended_while_parked = exists().where(
        AgentRunModel.id == _P.run_id,
        AgentRunModel.suspended_while_parked.is_not(None),
    )
    return [
        _P.kind.in_(list(ASKED_KINDS)),
        *_awaiting(Awaiting.DECISION),
        or_(
            and_(~host_permission, waiting_or_running),
            and_(
                host_permission,
                ConversationModel.status == ConversationStatus.RUNNING.value,
                _P.created_at > func.now() - AGENT_HOST_PERMISSION_DECISION_WINDOW,
            ),
            and_(
                host_permission,
                ConversationModel.status == ConversationStatus.WAITING.value,
                suspended_while_parked,
            ),
        ),
    ]


def _summary_text(call: type[MessageModel]) -> ColumnElement[str | None]:
    """The question asked, or the approval's title, read in place: a permission
    request carries the gated call's whole input, and the list needs a line."""
    args = call.tool_args
    return func.coalesce(
        args[("questions", 0, "question")].astext,
        # `ask_user` as a hand-built or older call stored it, wrapped.
        args[("request", "questions", 0, "question")].astext,
        args["title"].astext,
    )


def _interaction(row: Row) -> PendingInteraction:
    return PendingInteraction(
        message_id=row.id,
        tool_call_id=row.tool_call_id,
        kind=PendingInteractionKind.of(row.kind),
        summary=clipped_summary(row.summary),
        created_at=row.created_at,
        conversation_id=row.conversation_id,
        conversation_title=row.title,
        conversation_status=ConversationStatus(row.status) if row.status else None,
        agent_id=row.agent_id,
        agent_name=row.agent_name,
    )


__all__ = ["Awaiting", "Pauses"]
