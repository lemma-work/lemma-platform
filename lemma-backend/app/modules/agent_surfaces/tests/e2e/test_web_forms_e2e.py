"""A form built from a table, end to end: no function, one row per answer.

A member picks a table and ticks its columns; the page is told how to draw the
form; a visitor's answers become one row, added as the member who looks after
the form, with only the ticked columns -- and, on a contact-owned table, the
contact the visitor proved they are. And the refusals: a column the table needs
left off the form, an answer that does not fit, a column nobody ticked.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.agent_surfaces.tests.e2e.test_web_widgets_e2e import (
    SHOP,
    _latest_code,
    _session,
    _text,
    _widget,
)

pytestmark = pytest.mark.e2e


async def _signups(client: AsyncClient, pod_id: str, **extra) -> str:
    name = extra.pop("name", "signups")
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "default": "gen_random_uuid()"},
                {"name": "full_name", "type": "TEXT", "required": True},
                {"name": "work_email", "type": "TEXT"},
                {"name": "seats", "type": "INTEGER"},
                {"name": "track", "type": "ENUM", "options": ["Design", "Code"]},
                {"name": "internal_notes", "type": "TEXT"},
            ],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    return name


async def _rows(client: AsyncClient, pod_id: str, table: str) -> list[dict]:
    listed = await client.get(f"/pods/{pod_id}/datastore/tables/{table}/records")
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


def _form(table: str, **extra) -> dict:
    return {
        "table": table,
        "fields": [
            {"column": "full_name", "label": "Your name"},
            {"column": "work_email", "required": True},
            {"column": "seats"},
            {"column": "track"},
        ],
        "intro": "Saturday, 10am.",
        "confirmation": "See you Saturday.",
        **extra,
    }


async def test_a_form_on_a_table_adds_one_row_per_answer(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id)

    offered = await authenticated_client.get(
        f"/pods/{pod_id}/web-widgets/form-columns", params={"table": table}
    )
    assert offered.status_code == 200, offered.text
    columns = {c["name"]: c for c in offered.json()["columns"]}
    assert "id" not in columns and "created_at" not in columns
    assert columns["work_email"]["suggested_input"] == "email"
    assert columns["full_name"]["required"] is True

    widget = await _widget(
        authenticated_client,
        pod_id,
        name="Workshop sign-up",
        kind="form",
        form=_form(table),
    )
    assert widget["form_function"] is None
    assert widget["form"]["table"] == table
    assert widget["page_url"].endswith(f"/public/web/{widget['public_key']}/page")
    key = widget["public_key"]

    session = await _session(authenticated_client, key)
    drawn = session["form"]
    assert drawn["title"] == "Workshop sign-up"
    assert drawn["intro"] == "Saturday, 10am."
    assert [(f["name"], f["input"], f["required"]) for f in drawn["fields"]] == [
        ("full_name", "text", True),
        ("work_email", "email", True),
        ("seats", "number", False),
        ("track", "choice", False),
    ]
    assert table not in str(drawn)

    sent = await authenticated_client.post(
        f"/public/web/{key}/submit",
        **_text(
            {
                "session": session["session"],
                "input": {
                    "full_name": "Ana Ruiz",
                    "work_email": "ana@client.example",
                    "seats": "2",
                    "track": "Code",
                    "internal_notes": "VIP, comp her ticket",
                },
            }
        ),
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["result"] == {"message": "See you Saturday."}

    rows = await _rows(authenticated_client, pod_id, table)
    assert len(rows) == 1
    row = rows[0]
    assert (row["full_name"], row["work_email"], row["seats"], row["track"]) == (
        "Ana Ruiz",
        "ana@client.example",
        2,
        "Code",
    )
    # A column nobody ticked is not the visitor's to fill.
    assert row["internal_notes"] is None


async def test_answers_that_do_not_fit_are_refused_before_anything_is_written(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, name="refusals")
    widget = await _widget(
        authenticated_client, pod_id, name="Refusals", kind="form", form=_form(table)
    )
    key = widget["public_key"]
    session = await _session(authenticated_client, key)

    for answers, says in [
        ({"work_email": "ana@client.example"}, "Your name is required"),
        ({"full_name": "Ana", "work_email": "nope"}, "email address"),
        (
            {"full_name": "Ana", "work_email": "a@b.co", "track": "Cooking"},
            "options",
        ),
    ]:
        refused = await authenticated_client.post(
            f"/public/web/{key}/submit",
            **_text({"session": session["session"], "input": answers}),
        )
        assert refused.status_code == 422, refused.text
        assert says in refused.json()["error"]
        assert refused.headers["access-control-allow-origin"] == SHOP
    assert await _rows(authenticated_client, pod_id, table) == []


async def test_a_form_must_ask_for_what_the_table_needs(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, name="needs")
    for form, says in [
        ({"table": table, "fields": [{"column": "seats"}]}, "needs full_name"),
        ({"table": table, "fields": [{"column": "id"}]}, "can't ask for id"),
        ({"table": "nowhere", "fields": [{"column": "x"}]}, "no table called"),
        (
            {"table": table, "fields": [{"column": "full_name", "input": "number"}]},
            "can't be asked for as number",
        ),
    ]:
        refused = await authenticated_client.post(
            f"/pods/{pod_id}/web-widgets",
            json={"name": f"Bad {says}", "kind": "form", "form": form},
        )
        assert refused.status_code == 422, refused.text
        assert says in refused.text


async def test_a_confirmed_visitors_row_names_them_on_a_contact_owned_table(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(
        authenticated_client, pod_id, name="requests", contact_owned=True
    )
    widget = await _widget(
        authenticated_client,
        pod_id,
        name="Requests",
        kind="form",
        form=_form(table),
        form_requires_code=True,
    )
    key = widget["public_key"]
    session = await _session(authenticated_client, key)
    answers = {"full_name": "Ana", "work_email": "ana@client.example"}

    anonymous = await authenticated_client.post(
        f"/public/web/{key}/submit",
        **_text({"session": session["session"], "input": answers}),
    )
    assert anonymous.status_code == 403

    email = f"form-{widget['id'][:8]}@client.example"
    await authenticated_client.post(
        f"/public/web/{key}/code",
        **_text({"session": session["session"], "email": email}),
    )
    verified = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        **_text(
            {"session": session["session"], "email": email, "code": _latest_code(email)}
        ),
    )
    assert verified.status_code == 200, verified.text

    sent = await authenticated_client.post(
        f"/public/web/{key}/submit",
        **_text(
            {
                "session": session["session"],
                "input": {
                    **answers,
                    "contact_id": "00000000-0000-0000-0000-000000000000",
                },
            }
        ),
    )
    assert sent.status_code == 200, sent.text
    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()
    contact_id = contacts["items"][0]["id"]
    rows = await _rows(authenticated_client, pod_id, table)
    assert [row.get("contact_id") for row in rows] == [contact_id]


async def test_the_hosted_page_carries_the_widget_and_nothing_else(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, name="hosted")
    widget = await _widget(
        authenticated_client,
        pod_id,
        name="<b>Sign-up</b>",
        kind="form",
        form=_form(table),
    )
    page = await authenticated_client.get(f"/public/web/{widget['public_key']}/page")
    assert page.status_code == 200
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "&lt;b&gt;Sign-up&lt;/b&gt;" in page.text
    assert "<b>Sign-up</b>" not in page.text
    assert "data-lemma-page" in page.text

    missing = await authenticated_client.get("/public/web/pk_nothing/page")
    assert missing.status_code == 404
