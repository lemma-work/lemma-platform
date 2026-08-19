"""Working with data → finding the records you want without reading all of them."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [
    journey("Working with data"),
    capability("Put records in and get them out"),
]


@pytest.fixture
async def table_of_twenty(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title"), column("rank", "INTEGER")], shared=True
    )
    await alice.adds_records(
        [{"title": f"row {n:02d}", "rank": n} for n in range(20)],
        to_table=table["name"],
        in_pod=pod,
    )
    return alice, pod, table["name"]


@scenario("A person pages through records without reading them all")
@proves("PS-DATA-011")
@covers("record.list")
async def test_paging_returns_every_record_once(table_of_twenty):
    alice, pod, table = table_of_twenty

    first = await alice.records_in(table, in_pod=pod, limit=8, offset=0)
    second = await alice.records_in(table, in_pod=pod, limit=8, offset=8)
    third = await alice.records_in(table, in_pod=pod, limit=8, offset=16)

    assert [len(first), len(second), len(third)] == [8, 8, 4]
    seen = [r["title"] for page in (first, second, third) for r in page]
    assert len(set(seen)) == 20, (
        f"paging an unchanging table must return each row exactly once; "
        f"got {len(seen)} rows, {len(set(seen))} distinct"
    )


@scenario("A person sorts records by a column")
@proves("PS-DATA-011")
@covers("record.list")
async def test_records_can_be_sorted(table_of_twenty):
    alice, pod, table = table_of_twenty

    ascending = await alice.records_in(
        table, in_pod=pod, limit=20, sort=alice.sorted_by("rank")
    )

    ranks = [r["rank"] for r in ascending]
    assert ranks == sorted(ranks), ranks


@scenario("A page is bounded so a large table cannot be pulled by accident")
@proves("PS-DATA-011")
@covers("record.list")
async def test_a_page_is_bounded(table_of_twenty):
    alice, pod, table = table_of_twenty

    everything = await alice.records_in(table, in_pod=pod, limit=100000)

    assert len(everything) <= 1000, (
        f"an unbounded page lets one request pull a whole table; got {len(everything)}"
    )
