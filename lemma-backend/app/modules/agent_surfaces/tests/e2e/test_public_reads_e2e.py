"""A page reads a table the pod opened for reads, end to end.

A member opens a table's chosen columns to people outside -- anyone, or
confirmed contacts. A page holding only a widget's public key reads every row's
open columns, as the member who opened it, in the order they chose, and nothing
else. And the refusals: a table that takes rows from outside, a contact-owned
or per-member table, a column that names a member, a stranger on a contacts-only
table, a closed table -- each said the same way to the page.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.agent_surfaces.tests.e2e.test_web_widgets_e2e import (
    SHOP,
    _as,
    _latest_code,
    _page,
    _session,
    _widget,
)

pytestmark = pytest.mark.e2e


async def _slots(client: AsyncClient, pod_id: str, name: str, **extra) -> str:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": extra.pop("enable_rls", False),
            "columns": [
                {"name": "id", "type": "UUID", "default": "gen_random_uuid()"},
                {"name": "starts_at", "type": "DATETIME", "required": True},
                {"name": "kind", "type": "ENUM", "options": ["chat", "demo"]},
                {"name": "owner", "type": "USER"},
                {"name": "private_note", "type": "TEXT"},
            ],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    for starts_at, kind in (
        ("2026-10-16T05:30:00Z", "demo"),
        ("2026-10-15T13:30:00Z", "chat"),
    ):
        added = await client.post(
            f"/pods/{pod_id}/datastore/tables/{name}/records",
            json={"data": {"starts_at": starts_at, "kind": kind, "private_note": "x"}},
        )
        assert added.status_code == 201, added.text
    return name


async def _open_reads(client: AsyncClient, pod_id: str, table: str, **body):
    return await client.put(
        f"/pods/{pod_id}/datastore/tables/{table}/public-reads",
        json={
            "audience": "anyone",
            "columns": ["starts_at", "kind"],
            "order_by": "starts_at",
            **body,
        },
    )


async def _read(client: AsyncClient, key: str, table: str, headers=None):
    return await client.get(
        f"/public/web/{key}/rows",
        params={"table": table},
        headers=headers or _page(),
    )


async def test_a_page_reads_the_open_columns_of_every_row_in_order(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _slots(authenticated_client, pod_id, "open_slots")

    before = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table}/public-reads"
    )
    assert before.status_code == 200, before.text
    assert before.json()["audience"] is None
    offered = [c["name"] for c in before.json()["offered"]]
    assert "owner" not in offered and "private_note" in offered

    opened = await _open_reads(authenticated_client, pod_id, table)
    assert opened.status_code == 200, opened.text
    assert opened.json()["columns"] == ["starts_at", "kind"]
    assert opened.json()["order_by"] == "starts_at"

    key = (await _widget(authenticated_client, pod_id, name="Booking"))["public_key"]
    read = await _read(authenticated_client, key, table)
    assert read.status_code == 200, read.text
    assert read.headers["access-control-allow-origin"] == SHOP
    body = read.json()
    assert [c["name"] for c in body["columns"]] == ["starts_at", "kind"]
    assert [row["kind"] for row in body["rows"]] == ["chat", "demo"]
    assert all(set(row) == {"starts_at", "kind"} for row in body["rows"])
    assert body["rows"][0]["starts_at"].startswith("2026-10-15T13:30:00")

    closed = await authenticated_client.delete(
        f"/pods/{pod_id}/datastore/tables/{table}/public-reads"
    )
    assert closed.status_code == 204
    gone = await _read(authenticated_client, key, table)
    assert gone.status_code == 404
    assert gone.json()["code"] == "table_closed"


async def test_a_table_is_read_from_outside_or_takes_rows_never_both(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _slots(authenticated_client, pod_id, "slots_both")

    took = await authenticated_client.put(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows",
        json={"audience": "anyone", "columns": ["starts_at"]},
    )
    assert took.status_code == 200, took.text
    refused = await _open_reads(authenticated_client, pod_id, table)
    assert refused.status_code == 422
    assert "never read back" in refused.text

    await authenticated_client.delete(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows"
    )
    opened = await _open_reads(authenticated_client, pod_id, table)
    assert opened.status_code == 200, opened.text
    again = await authenticated_client.put(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows",
        json={"audience": "anyone", "columns": ["starts_at"]},
    )
    assert again.status_code == 422
    assert "Close it to reads first" in again.text


async def test_only_tables_whose_rows_belong_to_nobody_open_for_reads(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    per_member = await _slots(
        authenticated_client, pod_id, "slots_mine", enable_rls=True
    )
    refused = await _open_reads(authenticated_client, pod_id, per_member)
    assert refused.status_code == 422
    assert "only their own rows" in refused.text

    plain = await _slots(authenticated_client, pod_id, "slots_owner")
    member = await _open_reads(
        authenticated_client, pod_id, plain, columns=["starts_at", "owner"]
    )
    assert member.status_code == 422
    assert "can't be shown owner" in member.text

    unordered = await _open_reads(
        authenticated_client, pod_id, plain, columns=["kind"], order_by="starts_at"
    )
    assert unordered.status_code == 422


async def test_a_contacts_only_table_reads_for_a_confirmed_visitor_alone(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _slots(authenticated_client, pod_id, "slots_contacts")
    opened = await _open_reads(authenticated_client, pod_id, table, audience="contacts")
    assert opened.status_code == 200, opened.text
    widget = await _widget(authenticated_client, pod_id, name="Clients door")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)

    stranger = await _read(authenticated_client, key, table, headers=_as(visitor))
    assert stranger.status_code == 404
    assert stranger.json()["code"] == "table_closed"

    email = f"reads-{widget['id'][:8]}@client.example"
    await authenticated_client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    verified = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": _latest_code(email)},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text

    read = await _read(authenticated_client, key, table, headers=_as(verified.json()))
    assert read.status_code == 200, read.text
    assert len(read.json()["rows"]) == 2
