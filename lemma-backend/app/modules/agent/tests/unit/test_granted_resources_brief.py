"""A named agent's granted resources render the same bytes on every rebuild.

This part of the brief sits in the system prompt, in front of the whole
replayed history, so a reordering between two rebuilds re-reads all of it --
and the cap on how many grants are shown made the order decide *which* ones
the agent was told about.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.authorization.context import ResourceType
from app.modules.agent.services.context_brief.lines import MAX_RESOURCES
from app.modules.agent.services.context_brief.granted_resources import (
    GrantedResourcesBrief,
)
from app.modules.datastore.contracts import DatastoreTableNotFoundError
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
)

pytestmark = pytest.mark.unit

_TABLE = "datastore_table"


def _table(name: str) -> SimpleNamespace:
    # A real column: the brief renders it with the datastore's own
    # `describe_column`, which reads the typed schema.
    column = ColumnSchema(name="title", type=DatastoreDataType.TEXT, required=True)
    return SimpleNamespace(
        table_name=name,
        columns=[column],
        primary_key_column="id",
        enable_rls=False,
        visibility="POD",
    )


class _Tables:
    """A pod with more tables than any listing page, read by name."""

    def __init__(self, names: Sequence[str], *, missing: frozenset[str] = frozenset()):
        self._names = set(names) - missing
        self.listed = False

    async def list_tables(self, pod_id, ctx, limit=100, cursor=None):
        self.listed = True
        return [_table(name) for name in sorted(self._names)[:limit]], None

    async def get_tables(
        self, pod_id: UUID, table_names: Sequence[str], ctx: object
    ) -> Mapping[str, object]:
        for name in sorted(table_names):
            if name not in self._names:
                raise DatastoreTableNotFoundError(f"Table '{name}' not found")
        return {name: _table(name) for name in table_names}

    async def get_table(self, pod_id: UUID, table_name: str, ctx: object) -> object:
        if table_name not in self._names:
            raise DatastoreTableNotFoundError(f"Table '{table_name}' not found")
        return _table(table_name)


def _brief(
    rows: list[tuple[str, UUID, str]],
    names: dict[tuple[ResourceType, UUID], str],
    tables: _Tables,
) -> GrantedResourcesBrief:
    class _Repo:
        def __init__(self, uow):
            del uow

        async def get_agent_grants(self, **kwargs):
            return list(rows)

        async def resolve_resource_names(self, **kwargs):
            return names

    class _Authz:
        def __init__(self, uow):
            del uow

        async def build_user_context(self, **kwargs):
            return SimpleNamespace(user_id=kwargs["user_id"])

    @asynccontextmanager
    async def _uow():
        yield SimpleNamespace()

    return GrantedResourcesBrief(
        _uow,
        repository=_Repo,
        authorization=_Authz,
        tables=lambda uow: tables,
    )


def _grants(count: int) -> tuple[list, dict]:
    rows: list[tuple[str, UUID, str]] = []
    names: dict[tuple[ResourceType, UUID], str] = {}
    for index in range(count):
        table_id = uuid4()
        names[(ResourceType.DATASTORE_TABLE, table_id)] = f"table_{index:03d}"
        rows.append((_TABLE, table_id, "datastore.table.read"))
        rows.append((_TABLE, table_id, "datastore.record.write"))
    return rows, names


async def _render(rows, names, tables) -> str:
    lines = await _brief(rows, names, tables).lines(
        agent=SimpleNamespace(id=uuid4()), pod_id=uuid4(), user_id=uuid4()
    )
    return "\n".join(lines)


@pytest.mark.anyio
async def test_the_same_grants_in_any_order_render_the_same_brief():
    rows, names = _grants(MAX_RESOURCES + 10)
    tables = _Tables(list(names.values()))
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)

    assert await _render(rows, names, tables) == await _render(shuffled, names, tables)


@pytest.mark.anyio
async def test_a_granted_table_past_the_pods_first_page_keeps_its_schema():
    """The schemas came from the pod's first page of tables, filtered to the
    granted ones, so a grant on the 51st table had no schema line."""
    pod_tables = [f"aaa_{index:03d}" for index in range(60)]
    late = "zzz_granted"
    table_id = uuid4()
    rows = [(_TABLE, table_id, "datastore.table.read")]
    names = {(ResourceType.DATASTORE_TABLE, table_id): late}
    tables = _Tables([*pod_tables, late])

    rendered = await _render(rows, names, tables)

    assert f"- {late} (pk=id" in rendered
    assert not tables.listed


@pytest.mark.anyio
async def test_a_grant_on_a_table_that_is_gone_is_listed_without_a_schema():
    rows, names = _grants(2)
    gone = names[next(iter(names))]
    tables = _Tables(list(names.values()), missing=frozenset({gone}))

    rendered = await _render(rows, names, tables)

    assert f"- {_TABLE}: {gone}  (grants:" in rendered
    assert "table_001 (pk=id" in rendered
