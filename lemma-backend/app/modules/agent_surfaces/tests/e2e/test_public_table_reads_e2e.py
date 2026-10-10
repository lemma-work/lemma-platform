"""A page reads a table the pod marked Public, end to end.

A page holding only a widget's public key reads every row of a Public table, in
the order it asks for, with the same authority the pod's chat has for that
visitor: Public, and nothing else. A table that is not Public, a per-member
table, and a table that doesn't exist are refused in one same answer.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.agent_surfaces.tests.e2e.test_web_widgets_e2e import (
    SHOP,
    _as,
    _page,
    _session,
    _widget,
)

pytestmark = pytest.mark.e2e


async def _slots(
    client: AsyncClient, pod_id: str, name: str, *, visibility: str, **extra
) -> str:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "visibility": visibility,
            "enable_rls": extra.pop("enable_rls", False),
            "columns": [
                {"name": "id", "type": "UUID", "default": "gen_random_uuid()"},
                {"name": "starts_at", "type": "DATETIME", "required": True},
                {"name": "kind", "type": "ENUM", "options": ["chat", "demo"]},
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
            json={"data": {"starts_at": starts_at, "kind": kind}},
        )
        assert added.status_code == 201, added.text
    return name


async def _read(client: AsyncClient, key: str, table: str, headers=None, **params):
    return await client.get(
        f"/public/web/{key}/rows",
        params={"table": table, **params},
        headers=headers or _page(),
    )


async def test_a_page_reads_every_row_of_a_public_table_in_the_order_it_asks(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _slots(
        authenticated_client, pod_id, "open_slots", visibility="PUBLIC"
    )
    key = (await _widget(authenticated_client, pod_id, name="Booking"))["public_key"]

    read = await _read(authenticated_client, key, table, order_by="starts_at")
    assert read.status_code == 200, read.text
    assert read.headers["access-control-allow-origin"] == SHOP
    body = read.json()
    assert {c["name"] for c in body["columns"]} >= {"id", "starts_at", "kind"}
    assert [row["kind"] for row in body["rows"]] == ["chat", "demo"]
    assert body["rows"][0]["starts_at"].startswith("2026-10-15T13:30:00")

    latest_first = await _read(
        authenticated_client, key, table, order_by="starts_at", desc="true"
    )
    assert [row["kind"] for row in latest_first.json()["rows"]] == ["demo", "chat"]

    visitor = await _session(authenticated_client, key)
    as_visitor = await _read(authenticated_client, key, table, headers=_as(visitor))
    assert as_visitor.status_code == 200, as_visitor.text
    assert len(as_visitor.json()["rows"]) == 2


async def test_anything_not_public_reads_the_same_as_a_table_that_isnt_there(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    private = await _slots(authenticated_client, pod_id, "team_slots", visibility="POD")
    per_member = await _slots(
        authenticated_client, pod_id, "my_slots", visibility="PUBLIC", enable_rls=True
    )
    key = (await _widget(authenticated_client, pod_id, name="Door"))["public_key"]

    answers = [
        await _read(authenticated_client, key, name)
        for name in (private, per_member, "no_such_table")
    ]
    assert [a.status_code for a in answers] == [404, 404, 404]
    assert {a.json()["code"] for a in answers} == {"table_closed"}
    assert len({a.json()["message"] for a in answers}) == 1


async def test_an_order_by_a_column_the_table_lacks_is_refused(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _slots(
        authenticated_client, pod_id, "slots_order", visibility="PUBLIC"
    )
    key = (await _widget(authenticated_client, pod_id, name="Order"))["public_key"]

    refused = await _read(authenticated_client, key, table, order_by="price")
    assert refused.status_code == 422
    assert refused.json()["code"] == "bad_order"
