"""Working with data → changing many records, and asking questions."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [
    journey("Working with data"),
    capability("Put records in and get them out"),
]


@pytest.fixture
async def stocked(world):
    alice = await world.person("daniel")
    pod = await alice.works_in("sales")
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("rank", "INTEGER")], shared=True
    )
    await alice.adds_records(
        [{"title": f"row {n}", "rank": n} for n in range(5)],
        to_table=table["name"], in_pod=pod,
    )
    return alice, pod, table["name"]


@scenario("A person changes many records in one request")
@proves("PS-DATA-013")
@covers("record.bulk_update", "record.list")
async def test_many_records_change_at_once(stocked):
    alice, pod, table = stocked
    rows = await alice.records_in(table, in_pod=pod, limit=50)

    await alice.updates_records(
        # A bulk update row is the record itself, keyed by id — not an
        # envelope with the changes nested under `data`.
        [{"id": str(r["id"]), "title": f"renamed {r['rank']}"} for r in rows],
        in_table=table, in_pod=pod,
    )

    after = await alice.records_in(table, in_pod=pod, limit=50)
    assert all(r["title"].startswith("renamed") for r in after), after


@scenario("A person removes many records in one request")
@proves("PS-DATA-013")
@covers("record.bulk_delete", "record.list")
async def test_many_records_go_at_once(stocked):
    alice, pod, table = stocked
    rows = await alice.records_in(table, in_pod=pod, limit=50)
    doomed = [str(r["id"]) for r in rows if r["rank"] < 3]

    await alice.deletes_records(doomed, in_table=table, in_pod=pod)

    remaining = await alice.records_in(table, in_pod=pod, limit=50)
    assert sorted(r["rank"] for r in remaining) == [3, 4], remaining


@scenario("A person changes a table's settings without losing its records")
@proves("PS-DATA-002")
@covers("table.update", "table.get", "record.list")
async def test_a_tables_settings_can_change(stocked):
    alice, pod, table = stocked

    updated = await alice.changes_table(table, in_pod=pod, visibility="RESTRICTED")

    assert updated["visibility"] == "RESTRICTED", updated
    assert len(await alice.records_in(table, in_pod=pod, limit=50)) == 5, (
        "changing a table's reach must not touch its records"
    )


class TestAsking:
    pytestmark = capability("Ask questions across tables")

    @scenario("A person queries their pod's data directly")
    @proves("PS-DATA-020")
    @covers("query.execute")
    async def test_a_query_returns_rows(self, stocked):
        alice, pod, table = stocked

        answer = await alice.asks(f"SELECT title, rank FROM {table} ORDER BY rank", in_pod=pod)

        assert answer is not None, answer
        assert "row 0" in str(answer), answer

    @scenario("A query that would change data is refused")
    @proves("PS-DATA-020")
    @covers("query.execute")
    async def test_a_writing_query_is_refused(self, stocked):
        alice, pod, table = stocked

        await alice.is_refused_query(f"DELETE FROM {table}", in_pod=pod)

        assert len(await alice.records_in(table, in_pod=pod, limit=50)) == 5, (
            "a refused query must not have changed anything"
        )

    @scenario("Someone outside the pod cannot query it")
    @proves("PS-DATA-020")
    @covers("query.execute")
    async def test_an_outsider_cannot_query(self, world, stocked):
        alice, pod, table = stocked
        outsider = await world.person("hannah")

        await outsider.is_refused_query(f"SELECT * FROM {table}", in_pod=pod)
