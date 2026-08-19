"""Working with data → defining tables, and putting records in and out.

Proves promises in
[docs/product/journeys/working-with-data.md](../../../../docs/product/journeys/working-with-data.md).
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [journey("Working with data"), capability("Define tables")]


@pytest.fixture
async def pod(world):
    """An owner with a pod, which nearly every scenario here needs."""
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person creates a table by declaring its columns")
@proves("PS-DATA-001")
@covers("table.create", "table.get", "table.list", "table.created")
async def test_a_table_is_created_from_its_columns(pod):
    alice, the_pod = pod

    table = await alice.creates_a_table(
        in_pod=the_pod,
        columns=[column("subject", required=True), column("body"), column("votes", "INTEGER")],
    )

    reopened = await alice.opens_table(table["name"], in_pod=the_pod)
    declared = {c["name"] for c in reopened["columns"]}
    assert {"subject", "body", "votes"} <= declared, reopened
    assert reopened["primary_key_column"] == "id", (
        "a table the person did not give a key to still gets one"
    )


@scenario("A table with a name already in use is refused")
@proves("PS-DATA-001")
@covers("table.create")
async def test_a_duplicate_table_name_is_refused(pod):
    alice, the_pod = pod
    table = await alice.creates_a_table(in_pod=the_pod)

    await alice.is_refused_creating_a_table(in_pod=the_pod, named=table["name"])


@scenario("A column name that is not a plain identifier is refused")
@proves("PS-DATA-001")
@covers("table.create")
async def test_a_bad_column_name_is_refused(pod):
    alice, the_pod = pod

    await alice.is_refused_creating_a_table(
        in_pod=the_pod, columns=[column("not a valid name!")]
    )


@scenario("A table's shape can change without losing what is in it")
@proves("PS-DATA-002")
@covers("table.column.add", "table.column.remove", "record.list")
async def test_adding_and_removing_columns_keeps_the_records(pod):
    alice, the_pod = pod
    table = await alice.creates_a_table(
        in_pod=the_pod, columns=[column("subject"), column("body")]
    )
    name = table["name"]
    await alice.adds_record({"subject": "first", "body": "hello"}, to_table=name, in_pod=the_pod)

    await alice.adds_column(column("priority"), to_table=name, in_pod=the_pod)

    rows = await alice.records_in(name, in_pod=the_pod)
    assert len(rows) == 1, rows
    assert rows[0]["subject"] == "first"
    assert rows[0].get("priority") is None, "a new column starts empty on existing rows"

    await alice.removes_column("body", from_table=name, in_pod=the_pod)

    rows = await alice.records_in(name, in_pod=the_pod)
    assert len(rows) == 1, "removing a column must not remove the record"
    assert rows[0]["subject"] == "first"
    assert "body" not in rows[0]


@scenario("A column name already used on the table is refused")
@proves("PS-DATA-002")
@covers("table.column.add")
async def test_a_duplicate_column_is_refused(pod):
    alice, the_pod = pod
    table = await alice.creates_a_table(in_pod=the_pod, columns=[column("subject")])

    await alice.is_refused_adding_column(
        column("subject"), to_table=table["name"], in_pod=the_pod
    )


@scenario("Deleting a table removes the table and every record in it")
@proves("PS-DATA-003")
@covers("table.delete", "table.get", "table.list")
async def test_deleting_a_table_takes_its_records(pod):
    alice, the_pod = pod
    table = await alice.creates_a_table(in_pod=the_pod)
    name = table["name"]
    await alice.adds_record({"title": "doomed"}, to_table=name, in_pod=the_pod)

    await alice.deletes_table(name, in_pod=the_pod)

    await alice.cannot_find_table(name, in_pod=the_pod)
    listed = {t["name"] for t in await alice.tables_in(the_pod)}
    assert name not in listed


class TestRecords:
    pytestmark = capability("Put records in and get them out")

    @scenario("A person adds a record and gets it back")
    @proves("PS-DATA-010")
    @covers("record.create", "record.get", "record.list")
    async def test_a_record_goes_in_and_comes_back(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("subject"), column("votes", "INTEGER")]
        )

        created = await alice.adds_record(
            {"subject": "checkout is broken", "votes": 3},
            to_table=table["name"],
            in_pod=the_pod,
        )

        assert created["id"], created
        rows = await alice.records_in(table["name"], in_pod=the_pod)
        assert [r["subject"] for r in rows] == ["checkout is broken"]
        assert rows[0]["votes"] == 3

    @scenario("A value of the wrong type for its column is refused")
    @proves("PS-DATA-010")
    @covers("record.create")
    async def test_a_wrongly_typed_value_is_refused(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("votes", "INTEGER")]
        )

        await alice.is_refused_adding_record(
            {"votes": "not a number"}, to_table=table["name"], in_pod=the_pod
        )

    @scenario("A missing required value is refused")
    @proves("PS-DATA-010")
    @covers("record.create")
    async def test_a_missing_required_value_is_refused(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("subject", required=True), column("body")]
        )

        await alice.is_refused_adding_record(
            {"body": "no subject"}, to_table=table["name"], in_pod=the_pod
        )

    @scenario("A person updates only the columns they named")
    @proves("PS-DATA-012")
    @covers("record.update", "record.get")
    async def test_an_update_leaves_untouched_columns_alone(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("subject"), column("body")]
        )
        record = await alice.adds_record(
            {"subject": "original", "body": "keep me"},
            to_table=table["name"],
            in_pod=the_pod,
        )

        await alice.updates_record(
            record, data={"subject": "changed"}, in_table=table["name"], in_pod=the_pod
        )

        rows = await alice.records_in(table["name"], in_pod=the_pod)
        assert rows[0]["subject"] == "changed"
        assert rows[0]["body"] == "keep me", "an unmentioned column must not be cleared"

    @scenario("A person deletes a record and the rest of the table is untouched")
    @proves("PS-DATA-012")
    @covers("record.delete", "record.list")
    async def test_deleting_one_record_leaves_the_others(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
        doomed = await alice.adds_record(
            {"title": "doomed"}, to_table=table["name"], in_pod=the_pod
        )
        await alice.adds_record({"title": "survivor"}, to_table=table["name"], in_pod=the_pod)

        await alice.deletes_record(doomed, in_table=table["name"], in_pod=the_pod)

        rows = await alice.records_in(table["name"], in_pod=the_pod)
        assert [r["title"] for r in rows] == ["survivor"]

    @scenario("Many records go in as one request")
    @proves("PS-DATA-013")
    @covers("record.bulk_create", "record.list")
    async def test_a_bulk_write_lands(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])

        await alice.adds_records(
            [{"title": f"row {n}"} for n in range(5)],
            to_table=table["name"],
            in_pod=the_pod,
        )

        rows = await alice.records_in(table["name"], in_pod=the_pod)
        assert len(rows) == 5, rows

    @scenario("If one record in a bulk write is rejected, none of them are applied")
    @proves("PS-DATA-013")
    @covers("record.bulk_create", "record.list")
    async def test_a_bulk_write_is_all_or_nothing(self, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("title"), column("votes", "INTEGER")]
        )

        await alice.is_refused_adding_records(
            [
                {"title": "fine", "votes": 1},
                {"title": "broken", "votes": "not a number"},
                {"title": "also fine", "votes": 3},
            ],
            to_table=table["name"],
            in_pod=the_pod,
        )

        rows = await alice.records_in(table["name"], in_pod=the_pod)
        assert rows == [], (
            "a partially-applied bulk write leaves the caller unable to tell what "
            f"landed; found {rows}"
        )


class TestWhoCanSeeData:
    pytestmark = capability("Records respect who is asking")

    @scenario("Someone outside the pod cannot read its tables")
    @proves("PS-DATA-014")
    @covers("table.get", "record.list")
    async def test_an_outsider_cannot_read_a_table(self, world, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(in_pod=the_pod)
        await alice.adds_record({"title": "secret"}, to_table=table["name"], in_pod=the_pod)

        outsider = await world.new_person("outsider")

        response = await outsider.api.call(
            "GET", f"/pods/{the_pod['id']}/datastore/tables/{table['name']}/records"
        )
        assert response.status_code >= 400, (
            f"someone outside the organization read pod records ({response.status_code})"
        )

    @scenario("On a shared table, a pod viewer reads every row but writes none")
    @proves("PS-DATA-014")
    @covers("record.list", "record.create")
    async def test_a_viewer_reads_a_shared_table_but_does_not_write(self, world, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(
            in_pod=the_pod, columns=[column("title")], shared=True
        )
        await alice.adds_record({"title": "visible"}, to_table=table["name"], in_pod=the_pod)

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=alice.organization))
        await alice.adds(bob, to_pod=the_pod, as_role="POD_VIEWER")

        rows = await bob.records_in(table["name"], in_pod=the_pod)
        assert [r["title"] for r in rows] == ["visible"]

        await bob.is_refused_adding_record(
            {"title": "nope"}, to_table=table["name"], in_pod=the_pod
        )

    @scenario("On a per-owner table, one member does not see another's rows")
    @proves("PS-DATA-015")
    @covers("record.list", "record.create")
    async def test_per_owner_rows_stay_with_their_owner(self, world, pod):
        alice, the_pod = pod
        # No `shared=True`, so rows belong to whoever wrote them.
        table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
        await alice.adds_record({"title": "alice's"}, to_table=table["name"], in_pod=the_pod)

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=alice.organization))
        await alice.adds(bob, to_pod=the_pod, as_role="POD_EDITOR")

        assert await bob.records_in(table["name"], in_pod=the_pod) == [], (
            "an owner-scoped row must not be visible to another member"
        )

        await bob.adds_record({"title": "bob's"}, to_table=table["name"], in_pod=the_pod)
        assert [r["title"] for r in await bob.records_in(table["name"], in_pod=the_pod)] == [
            "bob's"
        ]
        # Alice administers the pod, but the default view is still her own rows.
        # Seeing everyone's is a deliberate ask, not a side effect of being admin.
        assert [
            r["title"] for r in await alice.records_in(table["name"], in_pod=the_pod)
        ] == ["alice's"]

    @scenario("An admin can ask for every member's rows, and a member cannot")
    @proves("PS-DATA-016")
    @covers("record.list")
    async def test_admin_mode_shows_every_row_and_is_gated(self, world, pod):
        alice, the_pod = pod
        table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
        await alice.adds_record({"title": "alice's"}, to_table=table["name"], in_pod=the_pod)

        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=alice.organization))
        await alice.adds(bob, to_pod=the_pod, as_role="POD_EDITOR")
        await bob.adds_record({"title": "bob's"}, to_table=table["name"], in_pod=the_pod)

        everyones = await alice.records_in(
            table["name"], in_pod=the_pod, everyones=True
        )
        assert sorted(r["title"] for r in everyones) == ["alice's", "bob's"]

        await bob.is_refused_everyones_records(table["name"], in_pod=the_pod)
