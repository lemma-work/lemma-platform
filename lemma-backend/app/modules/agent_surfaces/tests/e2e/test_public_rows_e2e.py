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

from uuid import UUID, uuid4

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


async def test_with_bot_protection_on_each_unconfirmed_answer_takes_its_own_proof(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    """The key is on the page for anybody to copy, so a script could post rows
    with nothing else. With protection on -- the default -- every answer from
    somebody the pod has not confirmed carries a proof solved for an answer:
    none, a reused one or a chat's one is refused and adds nothing, and the
    proof is asked before the send is counted. A confirmed contact sends none."""
    from app.core.config import settings
    from app.core.public_web import public_web_settings
    from app.modules.identity.config import identity_settings

    pod_id = test_pod["id"]
    table = await _signups(authenticated_client, pod_id, "guarded_signups")
    opened = await _open(authenticated_client, pod_id, table)
    assert opened.status_code == 200, opened.text
    widget = await _widget(authenticated_client, pod_id, name="Guarded form")
    key = widget["public_key"]
    # Confirmed while protection is off: asking for a code is its own proof's
    # business, covered where codes are.
    contact = await _confirm(
        authenticated_client,
        key,
        await _session(authenticated_client, key),
        f"guarded-{widget['id'][:8]}@client.example",
    )
    anonymous = await _session(authenticated_client, key)

    monkeypatch.setattr(public_web_settings, "public_web_altcha_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_enabled", False)
    monkeypatch.setattr(identity_settings, "auth_altcha_hmac_key", None)
    monkeypatch.setattr(identity_settings, "auth_altcha_max_number", 10_000)

    async def proof(purpose: str) -> str:
        challenge = await authenticated_client.get(
            f"/public/web/{key}/challenge", params={"purpose": purpose}
        )
        assert challenge.status_code == 200, challenge.text
        return _solved(challenge.json())

    def answer(name: str, altcha: str | None = None) -> dict[str, object]:
        body: dict[str, object] = {"table": table, "values": {"full_name": name}}
        if altcha is not None:
            body["altcha"] = altcha
        return body

    for headers in (_page(), _as(anonymous)):
        bare = await authenticated_client.post(
            f"/public/web/{key}/rows", json=answer("Script"), headers=headers
        )
        assert bare.status_code == 400, bare.text
        assert bare.json()["code"] == "altcha_failed"
    wrong_purpose = await authenticated_client.post(
        f"/public/web/{key}/rows",
        json=answer("Script", await proof("session")),
        headers=_as(anonymous),
    )
    assert wrong_purpose.status_code == 400
    assert await _rows(authenticated_client, pod_id, table) == []

    solved = await proof("row")
    added = await authenticated_client.post(
        f"/public/web/{key}/rows", json=answer("Ana", solved), headers=_page()
    )
    assert added.status_code == 201, added.text
    reused = await authenticated_client.post(
        f"/public/web/{key}/rows", json=answer("Ana again", solved), headers=_page()
    )
    assert reused.status_code == 400

    confirmed = await authenticated_client.post(
        f"/public/web/{key}/rows", json=answer("Bo"), headers=_as(contact)
    )
    assert confirmed.status_code == 201, confirmed.text
    names = sorted(
        row["full_name"] for row in await _rows(authenticated_client, pod_id, table)
    )
    assert names == ["Ana", "Bo"]


async def test_each_form_takes_its_own_answers_a_day(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    """A form's day is its own: counted per open table, whichever widget's page
    an answer comes through, at what the member set -- up to the deployment's
    ceiling -- else the default. One form filling up refuses nobody else's."""
    from app.core.public_web import public_web_settings

    monkeypatch.setattr(
        public_web_settings, "public_web_answers_per_form_per_day_max", 50
    )
    pod_id = test_pod["id"]
    small = await _signups(authenticated_client, pod_id, f"launch_{uuid4().hex[:8]}")
    other = await _signups(authenticated_client, pod_id, f"waitlist_{uuid4().hex[:8]}")

    too_many = await _open(authenticated_client, pod_id, small, answers_per_day=51)
    assert too_many.status_code == 422, too_many.text
    assert "between 1 and 50" in too_many.text

    opened = await _open(authenticated_client, pod_id, small, answers_per_day=2)
    assert opened.status_code == 200, opened.text
    assert (opened.json()["answers_per_day"], opened.json()["answers_per_day_max"]) == (
        2,
        50,
    )
    default = await _open(authenticated_client, pod_id, other)
    assert default.json()["answers_per_day"] is None

    first = (await _widget(authenticated_client, pod_id, name="Launch page"))[
        "public_key"
    ]
    second = (await _widget(authenticated_client, pod_id, name="Docs page"))[
        "public_key"
    ]

    async def answer(key: str, table: str, name: str) -> int:
        sent = await authenticated_client.post(
            f"/public/web/{key}/rows",
            json={"table": table, "values": {"full_name": name}},
            headers=_page(),
        )
        return sent.status_code

    assert await answer(first, small, "One") == 201
    assert await answer(second, small, "Two") == 201
    assert await answer(first, small, "Three") == 429
    assert await answer(first, other, "Elsewhere") == 201
    assert len(await _rows(authenticated_client, pod_id, small)) == 2

    reread = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{small}/public-rows"
    )
    assert reread.json()["answers_per_day"] == 2


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
