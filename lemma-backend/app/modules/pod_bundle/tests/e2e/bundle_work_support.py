"""Shared pieces of the pod-bundle work-item e2e tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from lemma_pod_bundle import pack_bundle

COLUMNS = [
    {"name": "id", "type": "UUID", "required": True},
    {"name": "title", "type": "TEXT", "required": True},
]


def tables_bundle(tmp_path: Path, names: list[str]) -> bytes:
    """A bundle of one empty table per name: one apply step each."""
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    (root / "tables").mkdir(parents=True)
    (root / "pod.json").write_text(
        json.dumps({"name": "Work Bundle", "format_version": 2, "variables": {}})
    )
    for name in names:
        folder = root / "tables" / name
        folder.mkdir()
        (folder / f"{name}.json").write_text(
            json.dumps({"name": name, "primary_key_column": "id", "columns": COLUMNS})
        )
    return pack_bundle(root)


async def scalar(sql: str, **params: object) -> Any:
    async with async_session_maker() as session:
        return await session.scalar(text(sql), params)


async def rows(sql: str, **params: object) -> list[Any]:
    async with async_session_maker() as session:
        return list((await session.execute(text(sql), params)).all())


async def execute(sql: str, **params: object) -> None:
    async with async_session_maker() as session, session.begin():
        await session.execute(text(sql), params)


async def item(dedup_key: str) -> tuple | None:
    found = await rows(
        "SELECT state, attempts, lease_losses, kind, ns, concurrency_key "
        "FROM work_items WHERE dedup_key = :k AND state <> 'dead'",
        k=dedup_key,
    )
    return tuple(found[0]) if found else None


async def table_names(pod_id: str) -> list[str]:
    found = await rows(
        "SELECT table_name FROM datastore_tables WHERE pod_id = :p",
        p=UUID(pod_id),
    )
    return sorted(row[0] for row in found)


async def pause_admin(pod_id: str, paused: bool = True) -> None:
    """Hold (or release) the pod's admin queue, so its items stay queued."""
    await execute(
        "INSERT INTO work_queues (class, ns, max_running, paused) "
        "VALUES ('admin', :ns, 1, :paused) "
        "ON CONFLICT (class, ns) DO UPDATE SET paused = EXCLUDED.paused",
        ns=f"pod:{pod_id}",
        paused=paused,
    )
