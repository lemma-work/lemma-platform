"""Where a call's rows come from: inline, a pod file, or a pod table.

Read in the caller's unit of work, with its authority -- a file through the
datastore's pod-file contract, a table under the caller's row-level security --
and then parsed after that unit is closed, so a large file is never decoded
while a pooled connection waits.

The decisions module refuses more rows than its limit; this refuses them first,
before a table is read past it, and says how to split the work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import JsonValue

from app.core.concurrency.offload import run_blocking
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.models import (
    MAX_INLINE_ITEMS,
    InputRefused,
    TableRows,
)
from app.modules.agent.tools.decisions.rows import parse_rows
from app.modules.agent.tools.decisions.seams import PodSession
from app.modules.agent.tools.pod.pod_common import resolve_pod_path


class RowSource(Protocol):
    """The row arguments `decide` and `test_decider` share."""

    @property
    def items(self) -> list[JsonObject | str] | str | None: ...

    @property
    def file(self) -> str | None: ...

    @property
    def table(self) -> TableRows | None: ...

    @property
    def key(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Read:
    """Rows as the unit of work left them. A file's are still bytes."""

    rows: list[JsonValue]
    #: The field naming each row: the caller's, or a table's primary key.
    key: str | None
    #: What the rows are, as the results name it: `items`, a table, a path.
    source: str
    input_path: str | None = None
    content: bytes | None = None
    #: A table's matching rows past this batch, which a later call can take.
    remaining: int = 0
    next_offset: int = 0


def chosen_source(request: RowSource) -> str | None:
    """The row source the call named, or None; more than one is refused."""
    named = [
        name
        for name, value in (
            ("items", request.items),
            ("file", request.file),
            ("table", request.table),
        )
        if value is not None
    ]
    if len(named) > 1:
        raise InputRefused(
            f"Give rows from exactly one source; got {', '.join(named)}."
        )
    return named[0] if named else None


async def read_rows(
    pod: PodSession, deps: BaseAgentContext, request: RowSource, cap: int
) -> Read:
    """The rows the call named, read with the session's authority."""
    if request.file is not None:
        path = resolve_pod_path(deps, request.file.strip())
        return Read(
            rows=[],
            key=request.key,
            source=path,
            input_path=path,
            content=await pod.read_pod_file(path),
        )
    if request.table is not None:
        return await _read_table(pod, request.table, request.key, cap)
    return Read(rows=_inline(request.items, cap), key=request.key, source="items")


def _inline(items: list[JsonObject | str] | str | None, cap: int) -> list[JsonValue]:
    rows = items if isinstance(items, list) else []
    limit = min(MAX_INLINE_ITEMS, cap)
    if len(rows) > limit:
        raise InputRefused(
            f"{len(rows)} items is more than one call takes inline (at most "
            f"{limit}). Write them to a CSV or JSONL file with pod_write_file "
            "and pass `file`, or put them in a table and pass `table`."
        )
    return cast(list[JsonValue], rows)


async def _read_table(
    pod: PodSession, source: TableRows, key: str | None, cap: int
) -> Read:
    if source.limit is not None and source.limit > cap:
        raise InputRefused(
            f"`table.limit` is {source.limit}; one call decides at most {cap} "
            "rows. Work through the table in batches of that size with `limit` "
            "and `offset`."
        )
    batch = await pod.read_table(source, limit=source.limit or cap)
    matching = max(batch.total - source.offset, 0)
    if source.limit is None and matching > cap:
        raise InputRefused(
            f"{matching} rows of `{source.table_name}` match, and one call "
            f"decides at most {cap}. Narrow `filters`, or decide them in "
            f"batches: `limit` {cap} with `offset` 0, then {cap}, and so on."
        )
    return Read(
        rows=batch.rows,
        key=key or batch.primary_key,
        source=source.table_name,
        remaining=max(matching - len(batch.rows), 0),
        next_offset=source.offset + len(batch.rows),
    )


async def rows_of(read: Read, cap: int) -> list[JsonValue]:
    """The rows, a file's parsed now that no unit of work is held open."""
    if read.content is None or read.input_path is None:
        return read.rows
    rows = await run_blocking(parse_rows, read.input_path, read.content, limit=cap)
    if len(rows) > cap:
        raise InputRefused(
            f"`{read.input_path}` has more than {cap} rows, and one call decides "
            "at most that many. Split the file, or load it into a table and "
            "decide it in batches with `table.limit` and `table.offset`."
        )
    return rows
