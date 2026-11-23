"""A form is a page that adds a row to an open table, end to end.

A member opens a table to people outside -- anyone, or confirmed contacts --
for some of its columns. A visitor's page, holding only a widget's public key,
learns what to ask and adds one row, as the member who opened the table, with
only the open columns. And the refusals: a per-member table, a column the table
needs left closed, a stranger on a contacts-only table, a closed table. And the
chat beside the form: it fills the open columns, and only those, and writes
nothing.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts.visitor_stream import visitor_frame
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.test_web_widgets_e2e import (
    SHOP,
    _as,
    _handles_of,
    _latest_code,
    _page,
    _say,
    _session,
    _solved,
    _widget,
)
from app.modules.datastore.contracts.contact_rows import rows_for_contact
from app.modules.test_support.e2e.row_bound_records import row_bound_records

pytestmark = pytest.mark.e2e


async def _signups(client: AsyncClient, pod_id: str, name: str, **extra) -> str:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": extra.pop("enable_rls", False),
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


async def _open(client: AsyncClient, pod_id: str, table: str, **body):
    return await client.put(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows",
        json={
            "audience": "anyone",
            "columns": ["full_name", "work_email", "seats", "track"],
            **body,
        },
    )


async def _rows(client: AsyncClient, pod_id: str, table: str) -> list[dict]:
    listed = await client.get(f"/pods/{pod_id}/datastore/tables/{table}/records")
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


async def _confirm(client: AsyncClient, key: str, visitor: dict, email: str) -> dict:
    """The visitor, once they have proved ``email`` is theirs: now a contact."""
    await client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    verified = await client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": _latest_code(email)},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text
    return verified.json()


async def test_an_open_table_takes_one_row_per_answer_and_nothing_more(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "signups")

    before = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows"
    )
    assert before.status_code == 200, before.text
    assert before.json()["audience"] is None
    offered = [c["name"] for c in before.json()["offered"]]
    assert "id" not in offered and "internal_notes" in offered

    opened = await _open(authenticated_client, pod_id, table)
    assert opened.status_code == 200, opened.text
    assert opened.json()["columns"] == ["full_name", "work_email", "seats", "track"]
    listed = await authenticated_client.get(f"/pods/{pod_id}/datastore/public-rows")
    assert listed.json()["items"] == [{"table": table, "audience": "anyone"}]

    key = (await _widget(authenticated_client, pod_id, name="Door"))["public_key"]
    visitor = await _session(authenticated_client, key)

    described = await authenticated_client.get(
        f"/public/web/{key}/table", params={"table": table}, headers=_page()
    )
    assert described.status_code == 200, described.text
    assert described.headers["access-control-allow-origin"] == SHOP
    asks = described.json()
    assert asks["contacts_only"] is False
    assert [(c["name"], c["input"]) for c in asks["columns"]] == [
        ("full_name", "text"),
        ("work_email", "email"),
        ("seats", "number"),
        ("track", "select"),
    ]

    added = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={
            "table": table,
            "values": {
                "full_name": "Ana Ruiz",
                "work_email": "ana@client.example",
                "seats": "2",
                "track": "Code",
                "internal_notes": "VIP, comp her ticket",
            },
        },
        headers=_as(visitor),
    )
    assert added.status_code == 201, added.text
    assert added.json() == {"ok": True}

    rows = await _rows(authenticated_client, pod_id, table)
    assert len(rows) == 1
    row = rows[0]
    assert (row["full_name"], row["seats"], row["track"]) == ("Ana Ruiz", 2, "Code")
    assert row["internal_notes"] is None

    refused = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"work_email": "x@y.co"}},
        headers=_page(),
    )
    assert refused.status_code == 422
    assert "Full name is required" in refused.json()["message"]

    closed = await authenticated_client.delete(
        f"/pods/{pod_id}/datastore/tables/{table}/public-rows"
    )
    assert closed.status_code == 204
    after = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"full_name": "Late"}},
        headers=_page(),
    )
    assert after.status_code == 404
    assert len(await _rows(authenticated_client, pod_id, table)) == 1


async def test_a_table_opens_only_in_a_way_that_can_work(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "needs")
    for body, says in [
        ({"columns": ["seats"]}, "needs full_name"),
        ({"columns": ["full_name", "id"]}, "can't fill in id"),
    ]:
        refused = await _open(authenticated_client, pod_id, table, **body)
        assert refused.status_code == 422, refused.text
        assert says in refused.text

    mine = await _signups(authenticated_client, pod_id, "mine", enable_rls=True)
    per_member = await _open(authenticated_client, pod_id, mine)
    assert per_member.status_code == 422
    assert "their own rows" in per_member.text


async def test_a_contacts_only_table_asks_a_stranger_to_confirm_first(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(
        authenticated_client,
        pod_id,
        "requests",
        contact_owned=True,
        contact_columns=["full_name", "track"],
    )
    opened = await _open(authenticated_client, pod_id, table, audience="contacts")
    assert opened.status_code == 200, opened.text
    widget = await _widget(authenticated_client, pod_id, name="Requests door")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)

    described = await authenticated_client.get(
        f"/public/web/{key}/table", params={"table": table}, headers=_page()
    )
    assert described.json()["contacts_only"] is True
    answers = {"full_name": "Ana", "work_email": "ana@client.example"}
    stranger = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": answers},
        headers=_as(visitor),
    )
    assert stranger.status_code == 403
    assert stranger.json()["code"] == "needs_contact"

    email = f"rows-{widget['id'][:8]}@client.example"
    contact = await _confirm(authenticated_client, key, visitor, email)

    forged = {**answers, "contact_id": "00000000-0000-0000-0000-000000000000"}
    added = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": forged},
        headers=_as(contact),
    )
    assert added.status_code == 201, added.text
    contact_id = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ][0]["id"]
    rows = await _rows(authenticated_client, pod_id, table)
    assert [row.get("contact_id") for row in rows] == [contact_id]


async def test_a_contacts_row_lands_as_theirs_where_row_security_binds(
    authenticated_client: AsyncClient, test_pod, db_session: AsyncSession
):
    """A confirmed visitor's ``addRow`` names them, and they read back only it.

    Under a role the contact policy binds, as a deployment's is. The row is
    added as the member who opened the table, and once that add named nobody
    to the policy: the database refused it, and the page heard only that it
    did not save.
    """
    pod_id = test_pod["id"]
    table = await _signups(
        authenticated_client,
        pod_id,
        "bookings",
        contact_owned=True,
        contact_columns=["full_name"],
    )
    opened = await _open(authenticated_client, pod_id, table, audience="contacts")
    assert opened.status_code == 200, opened.text
    widget = await _widget(authenticated_client, pod_id, name="Bookings door")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)
    email = f"bound-{widget['id'][:8]}@client.example"
    contact = await _confirm(authenticated_client, key, visitor, email)
    someone_else = "00000000-0000-0000-0000-00000000000b"

    async with row_bound_records(UUID(pod_id)):
        added = await authenticated_client.post(
            f"/public/web/{key}/rows",
            json={"table": table, "values": {"full_name": "Ana"}},
            headers=_as(contact),
        )
        assert added.status_code == 201, added.text
        theirs = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/{table}/records",
            json={"data": {"full_name": "Bo", "contact_id": someone_else}},
        )
        assert theirs.status_code == 201, theirs.text

        contact_id = (
            await authenticated_client.get(f"/pods/{pod_id}/contacts")
        ).json()["items"][0]["id"]
        rows = await _rows(authenticated_client, pod_id, table)
        assert sorted((row["full_name"], row["contact_id"]) for row in rows) == [
            ("Ana", contact_id),
            ("Bo", someone_else),
        ]
        own = await rows_for_contact(
            SessionUnitOfWorkFactory(
                async_sessionmaker(db_session.bind, expire_on_commit=False)
            ),
            pod_id=UUID(pod_id),
            table_name=table,
            contact_id=UUID(contact_id),
        )
        assert own == [{"full_name": "Ana"}]


async def test_the_hosted_page_draws_an_open_tables_form_and_nothing_else(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "hosted")
    key = (await _widget(authenticated_client, pod_id, name="Hosted"))["public_key"]

    closed = await authenticated_client.get(f"/public/web/{key}/page?table={table}")
    assert closed.status_code == 404

    await _open(authenticated_client, pod_id, table)
    page = await authenticated_client.get(f"/public/web/{key}/page?table={table}")
    assert page.status_code == 200
    policy = page.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    # Only the widget's own site may frame it.
    assert f"frame-ancestors {SHOP}" in policy
    assert f'data-lemma-table="{table}"' in page.text
    assert "Don't share passwords" in page.text

    odd = await authenticated_client.get(f"/public/web/{key}/page?table=x%22%3E%3Cb")
    assert odd.status_code == 404
    chat = await authenticated_client.get(f"/public/web/{key}/page")
    assert chat.status_code == 200
    assert "data-lemma-table" not in chat.text


async def test_the_chat_fills_the_open_columns_and_writes_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "filled")
    await _open(authenticated_client, pod_id, table)
    key = (await _widget(authenticated_client, pod_id, name="Fill door"))["public_key"]
    visitor = await _session(authenticated_client, key)

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="I'm Jonas from Lumen Labs, 3 seats on Code please",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[
            script_tool_call(
                "fill_form",
                {
                    "table": table,
                    "values": {
                        "full_name": "Jonas Weber",
                        "seats": 3,
                        "track": "Code",
                        "internal_notes": "upsell",
                    },
                },
                tool_call_id="fill-1",
            ),
            script_text("I've filled that in. Check it and press Send."),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(conversation_id)
    )
    returned = next(
        m
        for m in messages
        if m.get("tool_name") == "fill_form" and m.get("tool_result")
    )
    assert returned["tool_result"]["filled"] == {
        "full_name": "Jonas Weber",
        "seats": "3",
        "track": "Code",
    }
    frame = visitor_frame({"type": "message", "data": returned})
    assert frame == {
        "type": "fill",
        "table": table,
        "values": {"full_name": "Jonas Weber", "seats": "3", "track": "Code"},
    }
    assert await _rows(authenticated_client, pod_id, table) == []


async def test_anonymous_row_submission_without_session_creates_contact_and_session(
    authenticated_client: AsyncClient, test_pod, db_session: AsyncSession
):
    pod_id = test_pod["id"]
    table = await _signups(
        authenticated_client,
        pod_id,
        "anonymous_signups",
        contact_owned=True,
        contact_columns=["full_name", "track"],
    )
    opened = await _open(authenticated_client, pod_id, table, audience="anyone")
    assert opened.status_code == 200, opened.text

    widget = await _widget(authenticated_client, pod_id, name="Public Door")
    key = widget["public_key"]

    contacts_before = (
        await authenticated_client.get(f"/pods/{pod_id}/contacts")
    ).json()["items"]
    assert len(contacts_before) == 0

    # 1. Successful row submission without an Authorization header
    added = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={
            "table": table,
            "values": {"full_name": "Ghost Visitor", "track": "Design"},
        },
        headers=_page(),
    )
    assert added.status_code == 201, added.text
    data = added.json()
    assert data["ok"] is True
    assert data["secret"] is not None
    assert data["session"] is not None
    assert data["session"]["is_contact"] is False
    access_token = data["session"]["access_token"]

    # 2. Exactly one anonymous visitor contact created
    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    assert len(contacts) == 1
    contact_id = contacts[0]["id"]

    # 3. The saved row's contact_id matching the created anonymous contact
    rows = await _rows(authenticated_client, pod_id, table)
    assert len(rows) == 1
    assert rows[0]["contact_id"] == contact_id
    assert rows[0]["full_name"] == "Ghost Visitor"

    # 4. The contact having the expected anonymous WEB/SESSION handle
    handles = await _handles_of(db_session, UUID(contact_id))
    assert handles == {("WEB", "SESSION")}

    # 5. Existing-session submissions reusing the same contact without duplication
    followup = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={
            "table": table,
            "values": {"full_name": "Ghost Visitor Again", "track": "Code"},
        },
        headers={**_page(), "Authorization": f"Bearer {access_token}"},
    )
    assert followup.status_code == 201, followup.text
    assert followup.json() == {"ok": True}

    contacts_after = (
        await authenticated_client.get(f"/pods/{pod_id}/contacts")
    ).json()["items"]
    assert len(contacts_after) == 1

    rows_after = await _rows(authenticated_client, pod_id, table)
    assert len(rows_after) == 2
    assert [r["contact_id"] for r in rows_after] == [contact_id, contact_id]


async def test_anonymous_row_submission_with_invalid_token_is_rejected(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "invalid_token_test")
    await _open(authenticated_client, pod_id, table, audience="anyone")
    widget = await _widget(authenticated_client, pod_id, name="Token Door")
    key = widget["public_key"]

    refused = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"full_name": "Hacker"}},
        headers={**_page(), "Authorization": "Bearer not_a_valid_token"},
    )
    assert refused.status_code == 401
    assert refused.json()["code"] == "bad_token"

    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    assert len(contacts) == 0


async def test_anonymous_row_submission_contacts_only_table_still_rejects_stranger(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(
        authenticated_client,
        pod_id,
        "contacts_only_guard",
        contact_owned=True,
        contact_columns=["full_name"],
    )
    await _open(authenticated_client, pod_id, table, audience="contacts")
    widget = await _widget(authenticated_client, pod_id, name="Guard Door")
    key = widget["public_key"]

    refused = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"full_name": "Stranger"}},
        headers=_page(),
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "needs_contact"

    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    assert len(contacts) == 0


async def test_anonymous_row_submission_with_files_creates_contact_and_session(
    authenticated_client: AsyncClient, test_pod, db_session: AsyncSession
):
    pod_id = test_pod["id"]
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "resumes",
            "primary_key_column": "id",
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["full_name"],
            "columns": [
                {"name": "id", "type": "UUID", "default": "gen_random_uuid()"},
                {"name": "full_name", "type": "TEXT", "required": True},
                {"name": "cv", "type": "FILE_PATH", "required": True},
            ],
        },
    )
    assert created.status_code == 201, created.text
    table = "resumes"
    await _open(authenticated_client, pod_id, table, columns=["full_name", "cv"])

    widget = await _widget(authenticated_client, pod_id, name="Files Door")
    key = widget["public_key"]

    added = await authenticated_client.post(
        f"/public/web/{key}/rows/with-files?table={table}",
        data={"values": '{"full_name": "File Candidate"}'},
        files={"cv": ("cv.pdf", b"%PDF-1.7 resume bytes", "application/pdf")},
        headers=_page(),
    )
    assert added.status_code == 201, added.text
    data = added.json()
    assert data["ok"] is True
    assert data["secret"] is not None
    assert data["session"] is not None

    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    assert len(contacts) == 1
    contact_id = contacts[0]["id"]

    rows = await _rows(authenticated_client, pod_id, table)
    assert len(rows) == 1
    assert rows[0]["contact_id"] == contact_id
    assert rows[0]["full_name"] == "File Candidate"

    handles = await _handles_of(db_session, UUID(contact_id))
    assert handles == {("WEB", "SESSION")}


async def test_anonymous_row_submission_with_bot_protection_requires_altcha(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    from app.core.config import settings
    from app.core.public_web import public_web_settings
    from app.modules.identity.config import identity_settings

    monkeypatch.setattr(public_web_settings, "public_web_altcha_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_enabled", False)
    monkeypatch.setattr(identity_settings, "auth_altcha_hmac_key", None)
    monkeypatch.setattr(identity_settings, "auth_altcha_max_number", 10_000)

    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "guarded_rows")
    await _open(authenticated_client, pod_id, table)

    widget = await _widget(authenticated_client, pod_id, name="Guarded Form")
    key = widget["public_key"]

    # Submission without session and without ALTCHA proof is refused
    bare = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"full_name": "Bot"}},
        headers=_page(),
    )
    assert bare.status_code == 400
    assert bare.json()["code"] == "altcha_failed"

    # Solve challenge
    challenge = (
        await authenticated_client.get(f"/public/web/{key}/challenge", headers=_page())
    ).json()
    assert challenge["enabled"] is True
    proof = _solved(challenge)

    # Submission with valid proof succeeds
    added = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"full_name": "Human"}, "altcha": proof},
        headers=_page(),
    )
    assert added.status_code == 201, added.text
    data = added.json()
    assert data["ok"] is True
    assert data["secret"] is not None


async def test_anonymous_row_submission_validation_failure_creates_no_contact(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "validation_fail")
    await _open(authenticated_client, pod_id, table)

    widget = await _widget(authenticated_client, pod_id, name="Val Door")
    key = widget["public_key"]

    # Missing required field full_name
    refused = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json={"table": table, "values": {"work_email": "fail@client.example"}},
        headers=_page(),
    )
    assert refused.status_code == 422
    assert "Full name is required" in refused.json()["message"]

    contacts = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ]
    assert len(contacts) == 0
