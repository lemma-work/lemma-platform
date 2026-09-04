"""What an in-flight run is holding and what it has already spent.

Both used to live only in the worker's memory, and both were lost when the
worker was. A SIGKILL stranded the *reservation* until the window rolled over,
permanently shrinking that person's allowance; it also threw away the tokens the
run had already bought, which is `PS-OPS-003`'s "however the run ended". The
process that cleans up after a dead worker had nothing to release and nothing to
bill.

Both now live on the run's row, and both are taken under a row lock rather than
merely read -- the worker and the orphan reconciler can reach for the same run,
and a hold released twice hands back allowance that was only ever taken once.

Spend is keyed by attempt -- ``{"<attempt id>": {"input_tokens": 4000, ...}}``.
A reclaimed run is the *same* run under a new attempt, so a flat total would
have to be read before it could be added to, and two workers briefly
overlapping would lose one of their halves. Keyed, each write is absolute and
therefore idempotent: an attempt writing twice says the same thing, and no
attempt can overwrite another's.

Free functions over a session rather than methods, because
`ConversationRepository` is a file whose length is ratcheted and none of this
is about conversations.
"""

from __future__ import annotations

from uuid import UUID

from dataclasses import dataclass

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array as pg_array
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel


async def store_usage_reservation(
    session: AsyncSession, *, agent_run_id: UUID, reservation: JsonObject | None
) -> None:
    """Record (or clear) the spend reservation this run is holding."""
    await session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == agent_run_id)
        .values(usage_reservation=reservation)
    )


async def claim_usage_reservation(
    session: AsyncSession, *, agent_run_id: UUID
) -> JsonObject | None:
    """Take the run's reservation, leaving nothing behind for a second taker.

    Locked read then clear, in the caller's transaction: the worker and the
    orphan reconciler can both reach for the same reservation, and releasing it
    twice would hand back allowance that was only ever taken once. Whoever wins
    the row lock gets the handle; the loser reads ``None`` and does nothing.

    ``RETURNING`` on the update would not do -- Postgres returns the *new* value
    of the column, which is the null we just wrote.
    """
    result = await session.execute(
        select(AgentRunModel.usage_reservation)
        .where(
            AgentRunModel.id == agent_run_id,
            AgentRunModel.usage_reservation.is_not(None),
        )
        .with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        return None
    await store_usage_reservation(session, agent_run_id=agent_run_id, reservation=None)
    return reservation


async def store_attempt_usage(
    session: AsyncSession,
    *,
    agent_run_id: UUID,
    attempt_id: str,
    usage: JsonObject,
) -> None:
    """Record what this attempt has spent so far, replacing its own last word.

    ``jsonb_set`` rather than read-modify-write, so the statement is complete in
    itself: a worker reclaiming a run it thinks is abandoned, while the previous
    worker is still finishing a request, cannot erase the half it did not read.
    """
    await session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == agent_run_id)
        .values(
            usage_accumulated=func.jsonb_set(
                func.coalesce(AgentRunModel.usage_accumulated, literal({}, JSONB)),
                # One level deep on purpose. `jsonb_set` creates only the *last*
                # segment of a path, so a nested `{attempts, <id>}` silently
                # writes nothing until something else has created `attempts`
                # first -- which is exactly the sort of quiet no-op a metering
                # column must not have.
                pg_array([literal(attempt_id)]),
                # `literal(usage, JSONB)` rather than casting a dumped string:
                # casting already-serialized JSON double-encodes it, and the
                # column ends up holding a JSON *string* of the object instead
                # of the object. `set_conversation_metadata_key` carries the
                # same warning for the same reason.
                literal(usage, JSONB),
                True,
            )
        )
    )


async def claim_accumulated_usage(
    session: AsyncSession, *, agent_run_id: UUID
) -> JsonObject | None:
    """Take everything the run has spent, leaving nothing for a second taker.

    Locked read then clear, in the caller's transaction, for the same reason the
    reservation is: the worker finishing a run and the reconciler reaping it can
    arrive together, and billing the same tokens twice is worse than the gap it
    would be closing.
    """
    result = await session.execute(
        select(AgentRunModel.usage_accumulated)
        .where(
            AgentRunModel.id == agent_run_id,
            AgentRunModel.usage_accumulated.is_not(None),
        )
        .with_for_update()
    )
    accumulated = result.scalar_one_or_none()
    if accumulated is None:
        return None
    await session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == agent_run_id)
        .values(usage_accumulated=None)
    )
    return accumulated


def summed_attempts(accumulated: JsonObject | None) -> JsonObject:
    """One total from however many attempts the run took.

    Reads defensively, because a run in flight across a deploy carries whatever
    the previous release stored, and a reconciler that raised on an unfamiliar
    shape would strand the very spend it exists to bill.
    """
    if not isinstance(accumulated, dict):
        return {}
    total: dict[str, object] = {}
    for attempt in accumulated.values():
        if not isinstance(attempt, dict):
            continue
        for field, value in attempt.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[field] = _as_number(total.get(field)) + value
            elif field not in total and isinstance(value, str):
                # A name rather than a count -- the model this attempt used.
                # First one wins: a run that switched models mid-flight is
                # already an oddity, and picking one is better than picking the
                # last arbitrarily.
                total[field] = value
    return total


def _as_number(value: object) -> float:
    return (
        value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    )


@dataclass(frozen=True, slots=True)
class RunAttribution:
    """Who a run belongs to, for a caller that only has its id.

    The worker holds all of this in memory while a run is alive. The reconciler
    arrives after that worker is gone, with nothing but a row, and still has to
    write a usage record that says whose spend this was.
    """

    organization_id: UUID | None
    pod_id: UUID | None
    user_id: UUID | None
    agent_id: UUID | None
    conversation_id: UUID
    runtime_profile: JsonObject | None


async def attribution_for_run(
    session: AsyncSession, *, agent_run_id: UUID
) -> RunAttribution | None:
    """Everything a usage record needs about a run, read from its row.

    Deliberately not the ORM entity: `list_stale_active_runs` selects ids only
    and says why -- stale legacy runtime JSON must not stop a run being marked
    terminal -- so this reads the same few columns rather than hydrating a run
    that may not deserialize.
    """
    from app.modules.agent.infrastructure.models import ConversationModel

    result = await session.execute(
        select(
            ConversationModel.organization_id,
            ConversationModel.pod_id,
            ConversationModel.user_id,
            AgentRunModel.agent_id,
            AgentRunModel.conversation_id,
            AgentRunModel.agent_runtime,
        )
        .join(ConversationModel, ConversationModel.id == AgentRunModel.conversation_id)
        .where(AgentRunModel.id == agent_run_id)
    )
    row = result.first()
    if row is None:
        return None
    runtime = row[5] if isinstance(row[5], dict) else None
    return RunAttribution(
        organization_id=row[0],
        pod_id=row[1],
        user_id=row[2],
        agent_id=row[3],
        conversation_id=row[4],
        runtime_profile=runtime,
    )
