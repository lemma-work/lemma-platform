"""Operator CLI for the work table.

    python -m app.core.infrastructure.work.admin ls [--ns NS] [--class C] [--kind K]
    python -m app.core.infrastructure.work.admin pause NS [--class C]
    python -m app.core.infrastructure.work.admin resume NS [--class C]
    python -m app.core.infrastructure.work.admin retry [--ns NS] [--kind K] [--id ID]

``retry`` puts dead items back to ``queued`` with every budget reset and the
dedup key kept; a dead item whose key already has a queued twin is deleted
instead, because the twin already stands for that work.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import close_engine, get_session_maker
from app.core.infrastructure.work.config import default_max_running
from app.core.infrastructure.work.types import WorkClass, validate_namespace
from app.core.infrastructure.work.writer import wake_channel

_LIST = text(
    """
    SELECT class, ns, kind, state, count(*) AS n,
           EXTRACT(EPOCH FROM now() - min(run_at)) AS oldest_seconds
      FROM work_items
     WHERE (CAST(:ns AS text) IS NULL OR ns = :ns)
       AND (CAST(:cls AS text) IS NULL OR class = :cls)
       AND (CAST(:kind AS text) IS NULL OR kind = :kind)
     GROUP BY class, ns, kind, state
     ORDER BY class, ns, kind, state
     LIMIT 500
    """
)
_SET_PAUSED = text(
    """
    INSERT INTO work_queues (class, ns, max_running, paused)
    VALUES (:cls, :ns, :cap, :paused)
    ON CONFLICT (class, ns) DO UPDATE SET paused = EXCLUDED.paused
    """
)
_DEAD = text(
    """
    SELECT id, class FROM work_items
     WHERE state = 'dead'
       AND (CAST(:ns AS text) IS NULL OR ns = :ns)
       AND (CAST(:kind AS text) IS NULL OR kind = :kind)
       AND (CAST(:id AS uuid) IS NULL OR id = :id)
     FOR UPDATE
    """
)
_RETRY = text(
    """
    UPDATE work_items
       SET state = 'queued', run_at = now(), attempts = 0, lease_losses = 0,
           first_unavailable_at = NULL, error_class = NULL
     WHERE id = :id AND state = 'dead'
    """
)


async def list_work(
    session: AsyncSession, *, ns: str | None, cls: str | None, kind: str | None
) -> list[str]:
    rows = await session.execute(_LIST, {"ns": ns, "cls": cls, "kind": kind})
    return [
        f"{r._mapping['class']:<12} {r.ns:<44} {r.kind:<32} {r.state:<8} "
        f"{r.n:>7} oldest={float(r.oldest_seconds or 0):.0f}s"
        for r in rows
    ]


async def set_paused(
    session: AsyncSession, ns: str, *, paused: bool, cls: WorkClass | None
) -> int:
    """Pause or resume ``ns`` in one class or all; creates queue rows as needed."""
    validate_namespace(ns)
    classes = [cls] if cls else list(WorkClass)
    for each in classes:
        await session.execute(
            _SET_PAUSED,
            {
                "cls": each.value,
                "ns": ns,
                "cap": default_max_running(each),
                "paused": paused,
            },
        )
        if not paused:
            await session.execute(
                text("SELECT pg_notify(:channel, '')"), {"channel": wake_channel(each)}
            )
    return len(classes)


async def retry_dead(
    session: AsyncSession,
    *,
    ns: str | None = None,
    kind: str | None = None,
    item_id: UUID | None = None,
) -> tuple[int, int]:
    """Requeue matching dead items; returns (requeued, coalesced into a queued twin)."""
    rows = (await session.execute(_DEAD, {"ns": ns, "kind": kind, "id": item_id})).all()
    requeued = coalesced = 0
    for row in rows:
        try:
            async with session.begin_nested():
                await session.execute(_RETRY, {"id": row.id})
            requeued += 1
        except IntegrityError:
            await session.execute(
                text("DELETE FROM work_items WHERE id = :id"), {"id": row.id}
            )
            coalesced += 1
    for cls in {row._mapping["class"] for row in rows}:
        await session.execute(
            text("SELECT pg_notify(:channel, '')"),
            {"channel": wake_channel(WorkClass(cls))},
        )
    return requeued, coalesced


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and steer the work table.")
    commands = parser.add_subparsers(dest="command", required=True)
    ls = commands.add_parser("ls", help="Backlog by class, namespace, kind and state.")
    ls.add_argument("--ns")
    ls.add_argument("--class", dest="cls")
    ls.add_argument("--kind")
    for name in ("pause", "resume"):
        command = commands.add_parser(name, help=f"{name.title()} a namespace's work.")
        command.add_argument("ns")
        command.add_argument(
            "--class", dest="cls", choices=[c.value for c in WorkClass]
        )
    retry = commands.add_parser("retry", help="Requeue dead items, budgets reset.")
    retry.add_argument("--ns")
    retry.add_argument("--kind")
    retry.add_argument("--id", type=UUID)
    return parser


async def _dispatch(args: argparse.Namespace, session: AsyncSession) -> str:
    if args.command == "ls":
        lines = await list_work(session, ns=args.ns, cls=args.cls, kind=args.kind)
        return "\n".join(lines) or "no work"
    if args.command in ("pause", "resume"):
        cls = WorkClass(args.cls) if args.cls else None
        count = await set_paused(
            session, args.ns, paused=args.command == "pause", cls=cls
        )
        return f"{args.command}d {args.ns} in {count} class(es)"
    if args.ns is None and args.kind is None and args.id is None:
        raise SystemExit("retry needs --ns, --kind or --id")
    requeued, coalesced = await retry_dead(
        session, ns=args.ns, kind=args.kind, item_id=args.id
    )
    return f"requeued {requeued}, coalesced {coalesced}"


async def _run() -> None:
    args = _parser().parse_args()
    try:
        async with get_session_maker()() as session:
            output = await _dispatch(args, session)
            await session.commit()
        print(output)
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(_run())
