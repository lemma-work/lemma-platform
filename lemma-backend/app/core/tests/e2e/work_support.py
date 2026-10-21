"""Shared fixtures and demo handlers for the work runtime's real-Postgres tests.

The demo handlers exist only here: each test builds its own ``WorkRegistry`` so
no test kind leaks into the process registry a worker would run.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.writer import WorkWriter

POD_A = "pod:aaaaaaaa-0000-0000-0000-000000000001"
POD_B = "pod:bbbbbbbb-0000-0000-0000-000000000002"


class Demo(BaseModel):
    tag: str = ""


@dataclass
class WorkDb:
    engine: AsyncEngine
    sessions: async_sessionmaker

    async def scalar(self, sql: str, **params: object) -> object:
        async with self.sessions() as session:
            return await session.scalar(text(sql), params)

    async def execute(self, sql: str, **params: object) -> None:
        async with self.sessions() as session:
            await session.execute(text(sql), params)
            await session.commit()

    async def count(self, where: str = "true", **params: object) -> int:
        value = await self.scalar(
            f"SELECT count(*) FROM work_items WHERE {where}", **params
        )
        return int(value or 0)

    async def effects(self, tag: str | None = None) -> list[str]:
        async with self.sessions() as session:
            rows = await session.execute(
                text(
                    "SELECT tag FROM work_e2e_effects "
                    "WHERE CAST(:tag AS text) IS NULL OR tag = :tag ORDER BY id"
                ),
                {"tag": tag},
            )
            return [row.tag for row in rows]

    async def item(self, item_id: UUID) -> dict[str, object] | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM work_items WHERE id = :id"), {"id": item_id}
                )
            ).first()
        return None if row is None else dict(row._mapping)

    async def enqueue(
        self,
        registry: WorkRegistry,
        kind: str,
        ns: str = POD_A,
        payload: BaseModel | None = None,
        **options: object,
    ) -> UUID | None:
        async with self.sessions() as session:
            item_id = await WorkWriter(session, registry=registry).add(
                kind,
                ns,
                payload,
                **options,  # type: ignore[arg-type]
            )
            await session.commit()
        return item_id

    async def expire_leases(self) -> None:
        await self.execute(
            "UPDATE work_items SET lease_until = now() - interval '1 second' "
            "WHERE state = 'running'"
        )


async def write_effect(session: object, tag: str, item_id: UUID | None = None) -> None:
    await session.execute(  # type: ignore[attr-defined]
        text("INSERT INTO work_e2e_effects (tag, item_id) VALUES (:tag, :item_id)"),
        {"tag": tag, "item_id": item_id},
    )
