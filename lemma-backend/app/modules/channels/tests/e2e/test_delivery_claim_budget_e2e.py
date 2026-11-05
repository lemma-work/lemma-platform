"""Claiming a delivery holds its shape however long the channel's history.

Every send is claimed, and the claim refuses a row while an older line's row
to the same target has yet to go -- the order a place, or a member, reads a
channel in. That question is asked of each claim, of each settled send
(``next_for_target``) and of every due row the sweeper hands out (``due``),
so it must cost a probe of the target's due rows, not a walk back along the
channel's history: thousands of older lines, each sent long ago, are rows the
answer never needs to read.

A contact's channel here has a few thousand lines, each sent to its place and
pushed to a member, and the newest few still to go to both. Each statement is
explained again with the parameters it ran with (as
``test_channel_read_budgets_e2e``): no sequential scan of the deliveries or
the lines, the lines not read at all, and the deliveries read bounded by the
rows still due -- never by the history.

Which index the planner walks over a handful of due rows is its call, and it
changes with whatever else the database holds: the deployment's due index is
shared, and other tests leave rows in it. So the bound is not a count of rows
this plan happens to read but what any plan over the due rows can: each once,
and again by each candidate's "anything older to this target?" probe. The
history is in no due index, so walking it reads thousands -- far past that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.tests.e2e.channel_pod import Pod, contact_place
from app.modules.channels.tests.e2e.channel_scale import Plan, Statement, explain

pytestmark = pytest.mark.e2e

#: Lines already sent, to the place and to the member.
HISTORY = 3_000
#: The newest lines, still to go to both.
STILL_DUE = 3

_TABLES = ("channel_message_deliveries", "channel_messages")


async def _seed(session: AsyncSession, pod: Pod) -> tuple[UUID, UUID, UUID]:
    """The channel's history; its place, and the member pushed to."""
    source, _ = await contact_place(session, pod)
    member = UUID(pod.editor["id"])
    params = {
        "channel": source.channel_id,
        "pod": UUID(pod.id),
        "writer": UUID(pod.owner["id"]),
        "source": source.id,
        "member": member,
        "history": HISTORY,
        "last": HISTORY + STILL_DUE,
    }
    await session.execute(
        text(
            "INSERT INTO channel_messages (id, channel_id, pod_id, seq, sender_kind, "
            "  sender_user_id, body, target_source_id, created_at, updated_at) "
            "SELECT gen_random_uuid(), :channel, :pod, n + 1, 'MEMBER', :writer, "
            "  'line ' || n, :source, now(), now() "
            "FROM generate_series(1, :last) AS n"
        ),
        params,
    )
    for target in ("source_id", "recipient_user_id"):
        value = ":source" if target == "source_id" else ":member"
        await session.execute(
            text(
                "INSERT INTO channel_message_deliveries (id, message_id, channel_id, "
                f"  pod_id, seq, {target}, status, next_attempt_at, sent_at, "
                "  created_at, updated_at) "
                f"SELECT gen_random_uuid(), m.id, m.channel_id, m.pod_id, m.seq, {value}, "
                "  CASE WHEN m.seq > :history + 1 THEN 'PENDING' ELSE 'SENT' END, "
                "  CASE WHEN m.seq > :history + 1 THEN now() END, "
                "  CASE WHEN m.seq > :history + 1 THEN NULL ELSE now() END, "
                "  now(), now() "
                "FROM channel_messages m WHERE m.channel_id = :channel AND m.seq > 1"
            ),
            params,
        )
    await session.commit()
    # Planned with the statistics production has: uncommitted, the ANALYZE is
    # rolled back and the explaining connection plans on defaults, where the
    # recipient's whole-history index ties with its due one.
    await session.execute(text("ANALYZE channel_message_deliveries"))
    await session.execute(text("ANALYZE channel_messages"))
    await session.commit()
    return source.channel_id, source.id, member


async def _due_ids(channel_id: UUID) -> list[UUID]:
    """The still-due deliveries, oldest line first."""
    async with async_session_maker() as session:
        rows = await session.scalars(
            text(
                "SELECT id FROM channel_message_deliveries WHERE channel_id = :c "
                "AND status = 'PENDING' ORDER BY seq, source_id NULLS LAST"
            ),
            {"c": channel_id},
        )
        return list(rows)


@contextmanager
def _captured() -> Iterator[list[Statement]]:
    """Every statement naming the deliveries that runs while the block does."""
    found: list[Statement] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if "channel_message_deliveries" in statement and "EXPLAIN" not in statement:
            found.append(Statement(statement, tuple(parameters or ())))

    event.listen(Engine, "before_cursor_execute", before)
    try:
        yield found
    finally:
        event.remove(Engine, "before_cursor_execute", before)


async def _plans(
    engine: AsyncEngine, act: Callable[[DeliveryRepository], Awaitable[object]]
) -> list[Plan]:
    """What ``act`` asked, each statement explained -- in a transaction rolled
    back, so a claim changes nothing."""
    with _captured() as statements:
        async with async_session_maker() as session:
            await act(DeliveryRepository(session))
            await session.rollback()
    assert statements, "nothing asked of the deliveries"
    return await explain(engine, statements)


async def _due_ceiling() -> int:
    """The most deliveries any plan over the deployment's due rows reads: each
    once, and again by each candidate's probe of the same rows."""
    async with async_session_maker() as session:
        due = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM channel_message_deliveries "
                    "WHERE status IN ('PENDING', 'FAILED')"
                )
            )
            or 0
        )
    ceiling = due * (due + 2)
    # Otherwise the bound could not tell a probe from a walk of the history.
    assert ceiling < HISTORY // 2, (
        f"{due} deliveries are due in this database: too many for the history "
        f"of {HISTORY} to stand out -- raise HISTORY"
    )
    return ceiling


def _assert_probes(name: str, plans: list[Plan], *, ceiling: int) -> None:
    for plan in plans:
        tree = f"{name}: {' '.join(plan.sql.split())[:160]}\n{plan.tree()}"
        print(f"\n  {tree}")
        scanned = [
            node["Relation Name"]
            for node in plan.nodes()
            if node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") in _TABLES
            and int(node.get("Actual Loops", 0)) > 0
        ]
        assert not scanned, f"{name} scans {scanned} whole:\n{tree}"
        assert plan.rows_read("channel_messages") == 0, (
            f"{name} walks the channel's lines:\n{tree}"
        )
        read = plan.rows_read("channel_message_deliveries")
        assert read <= ceiling, (
            f"{name} touched {read} deliveries, more than {ceiling}:\n{tree}"
        )


async def test_a_claim_probes_the_targets_due_rows_not_the_channels_history(
    pod: Pod, db_session: AsyncSession, db_manager
):
    channel_id, source_id, member = await _seed(db_session, pod)
    due = await _due_ids(channel_id)
    assert len(due) == 2 * STILL_DUE
    oldest, newest = due[0], due[-1]
    engine = db_manager.engine

    claimable = await _plans(engine, lambda deliveries: deliveries.claim(oldest))
    behind = await _plans(engine, lambda deliveries: deliveries.claim(newest))
    sweep = await _plans(engine, lambda deliveries: deliveries.due(limit=100))
    ceiling = await _due_ceiling()

    async with async_session_maker() as session:
        repository = DeliveryRepository(session)
        first = await repository.get(oldest)
        last = await repository.get(newest)
        assert first is not None and last is not None
        assert first.source_id == source_id and last.recipient_user_id == member
        # The oldest of each target goes; the rest wait behind it. (The sweep
        # is the deployment's: other tests' rows may be due too.)
        ours = set(due)
        handed = [d for d in await repository.due(limit=100) if d in ours]
        assert sorted(handed) == sorted(due[:2])
    following = await _plans(
        engine, lambda deliveries: deliveries.next_for_target(first)
    )
    pushes = await _plans(engine, lambda deliveries: deliveries.next_for_target(last))

    for name, plans in (
        ("claim", claimable),
        ("claim behind an older one", behind),
        ("due", sweep),
        ("next for a place", following),
        ("next for a member", pushes),
    ):
        _assert_probes(name, plans, ceiling=ceiling)
