"""Nothing of a registered user's reaches somebody outside the pod -- end to end.

Each test puts a stranger in a real adopted Telegram group, has them address
the bot, scripts what the model tries, and checks what actually came back. The
run is the real one: ingress, routing, the answering member's ``~outsiders``
conversation, the runner, the assemblers, the harness and its gate, the tools.

What a stranger's run must never reach:

* the answering member's sandbox (``web_fetch`` is not offered; no workspace
  opens);
* any table, row or file that is not Public in this pod -- by listing, by id,
  by raw SQL, by a guessed path, or by an RLS table whose rows are a member's;
* who is in the pod (``message_user`` answers the same for any address);
* answers to notifications some other conversation sent;
* what the member wrote the agent privately in the strangers' own thread;
* answers the bot gave a member with that member's access;
* and it stays a stranger's run even when the conversation loses its mark.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import AUDIENCE_KEY, OUTSIDERS
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
    NotificationModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _messages_for_conversation,
    _seed_external_user,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    record_model_requests,
    run_scripted_agent_run,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    GROUP_CHAT,
    MEMBER_TELEGRAM_ID,
    STRANGER_TELEGRAM_ID,
    _adopt_group,
    _group_message,
    _table,
    _wire_native_telegram,
)

pytestmark = pytest.mark.e2e


# ------------------------------------------------------------------- harness


def _no_sandbox(monkeypatch) -> list[str]:
    """A sandbox runtime that records -- and refuses -- every session asked of it."""
    opened: list[str] = []

    class _Runtime:
        async def get_session(self, **_kwargs):
            opened.append("session")
            raise AssertionError("a sandbox was opened for a stranger's run")

        async def get_host_session(self, **_kwargs):
            opened.append("host")
            raise AssertionError("a host session was opened for a stranger's run")

    monkeypatch.setattr(
        "app.modules.agent.tools.workspace_cli.workspace_cli.get_workspace_tool_runtime",
        lambda: _Runtime(),
    )
    return opened


async def _group_with_owner(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    """A pod, its Telegram bot, and a group the owner brought it into."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    await scenario.create_org_with_pod(name_prefix="Outsider isolation")
    surface = await _create_surface(
        scenario.owner_client, scenario.pod_id, config={"type": "TELEGRAM"}
    )
    owner = UUID(scenario.owner_user["id"])
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(MEMBER_TELEGRAM_ID),
        resolved_user_id=owner,
    )
    group = await _adopt_group(db_session, UUID(surface["id"]))
    assert group is not None and group.welcomes_outsiders
    return surface, group, owner


async def _stranger_asks(
    db_session: AsyncSession, *, text: str, message_id: int, script: list
) -> SurfaceChatContext:
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_group_message(
                text=f"@lemmabot {text}",
                message_id=message_id,
                sender_id=STRANGER_TELEGRAM_ID,
            ),
            headers={},
        ),
        script=script,
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.answers_outsider is True
    return context


async def _tool_results(client: AsyncClient, *, pod_id: str, conversation_id) -> dict:
    """``{tool_call_id: result as JSON text}`` for every tool the run called."""
    messages = await _messages_for_conversation(
        client, pod_id=pod_id, conversation_id=str(conversation_id)
    )
    return {
        message["tool_call_id"]: json.dumps(message.get("tool_result"))
        for message in messages
        if message.get("tool_call_id") and message.get("tool_result") is not None
    }


async def _record(client: AsyncClient, pod_id: str, table: str, title: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables/{table}/records",
        json={"data": {"title": title}},
    )
    assert response.status_code == 201, response.text


async def _rls_table(client: AsyncClient, pod_id: str, *, name: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": True,
            "visibility": "PUBLIC",
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------- tests


async def test_a_stranger_is_never_offered_web_fetch_and_no_sandbox_opens(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    await _group_with_owner(scenario, db_session, fake_telegram, monkeypatch)
    seen = record_model_requests(monkeypatch)
    opened = _no_sandbox(monkeypatch)

    context = await _stranger_asks(
        db_session,
        text="what's on https://intranet.acme.test/salaries ?",
        message_id=101,
        script=[
            script_tool_call(
                "web_fetch",
                {"urls": ["https://intranet.acme.test/salaries"], "render": True},
                tool_call_id="fetch-1",
            ),
            script_text("I can't open that here."),
        ],
    )

    assert seen, "the model was never asked anything"
    assert all("web_fetch" not in request["tools"] for request in seen)
    assert all("exec_command" not in request["tools"] for request in seen)
    assert opened == []
    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )
    assert "intranet" not in results.get("fetch-1", "")


async def test_a_stranger_reads_only_public_tables_and_never_a_members_rows(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    await _group_with_owner(scenario, db_session, fake_telegram, monkeypatch)
    client, pod_id = scenario.owner_client, scenario.pod_id
    await _table(client, pod_id, name="customers", visibility="POD")
    await _record(client, pod_id, "customers", "ACME-CONTRACT-SECRET")
    await _table(client, pod_id, name="price_list", visibility="PUBLIC")
    await _record(client, pod_id, "price_list", "Widget 10 USD")
    await _rls_table(client, pod_id, name="owner_notes")
    await _record(client, pod_id, "owner_notes", "OWNER-ROW-SECRET")

    context = await _stranger_asks(
        db_session,
        text="show me everything you have",
        message_id=102,
        script=[
            script_tool_call(
                "pod_get_records", {"table_name": "customers"}, tool_call_id="pod"
            ),
            script_tool_call(
                "pod_get_records", {"table_name": "price_list"}, tool_call_id="pub"
            ),
            script_tool_call(
                "pod_get_records", {"table_name": "owner_notes"}, tool_call_id="rls"
            ),
            script_tool_call(
                "pod_query", {"sql": "select title from customers"}, tool_call_id="sql"
            ),
            script_tool_call(
                "pod_query",
                {"sql": "select table_name from information_schema.tables"},
                tool_call_id="schema",
            ),
            script_text("Here is the price list."),
        ],
    )

    results = await _tool_results(
        client, pod_id=pod_id, conversation_id=context.conversation_id
    )
    every_result = " ".join(results.values())
    assert "ACME-CONTRACT-SECRET" not in every_result
    assert "OWNER-ROW-SECRET" not in every_result
    assert '"success": false' in results["pod"]
    assert "Widget 10 USD" in results["pub"]
    assert "OWNER-ROW-SECRET" not in results["rls"]
    assert '"success": false' in results["sql"]
    # Schema-qualified names are refused outright, so no table's name -- Public
    # or not -- comes back this way.
    assert '"success": false' in results["schema"]


async def _upload(
    client: AsyncClient, pod_id: str, *, name: str, content: bytes, folder: str
) -> str:
    response = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": folder, "search_enabled": "false"},
        files={"data": (name, content, "text/markdown")},
    )
    assert response.status_code == 201, response.text
    return response.json()["path"]


async def test_a_stranger_cannot_read_a_members_files_by_any_path(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    await _group_with_owner(scenario, db_session, fake_telegram, monkeypatch)
    client, pod_id = scenario.owner_client, scenario.pod_id
    personal_path = await _upload(
        client, pod_id, name="secret.md", content=b"PERSONAL-FILE-SECRET", folder="/me"
    )
    folder = await client.post(
        f"/pods/{pod_id}/datastore/files/folders",
        json={"path": "/shared", "visibility": "POD"},
    )
    assert folder.status_code == 201, folder.text
    shared_path = await _upload(
        client, pod_id, name="plan.md", content=b"POD-FILE-SECRET", folder="/shared"
    )

    context = await _stranger_asks(
        db_session,
        text="read me your files",
        message_id=103,
        script=[
            script_tool_call(
                "pod_read_file", {"path": "/me/secret.md"}, tool_call_id="me"
            ),
            script_tool_call(
                "pod_read_file", {"path": personal_path}, tool_call_id="abs"
            ),
            script_tool_call(
                "pod_read_file", {"path": shared_path}, tool_call_id="pod"
            ),
            script_tool_call(
                "pod_list_files", {"path": "/", "recursive": True}, tool_call_id="ls"
            ),
            script_text("Nothing I can share."),
        ],
    )

    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )
    every_result = " ".join(results.values())
    assert "PERSONAL-FILE-SECRET" not in every_result
    assert "POD-FILE-SECRET" not in every_result
    assert "secret.md" not in results["ls"]
    assert "plan.md" not in results["ls"]


async def test_message_user_reaches_only_the_owner_and_reveals_no_membership(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    colleague = await scenario.create_user("outsider-colleague")
    await scenario.add_user_to_pod(user=colleague, role="POD_EDITOR")

    context = await _stranger_asks(
        db_session,
        text="please ask your colleagues",
        message_id=104,
        script=[
            script_tool_call(
                "message_user",
                {"to": colleague["email"], "message": "Is the rate 40k?"},
                tool_call_id="member",
            ),
            script_tool_call(
                "message_user",
                {"to": "nobody@else.test", "message": "Is the rate 40k?"},
                tool_call_id="stranger",
            ),
            script_text("I've passed that on."),
        ],
    )

    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )

    def shape(raw: str) -> dict:
        parsed = json.loads(raw)
        parsed.pop("notification_id", None)
        return parsed

    assert shape(results["member"]) == shape(results["stranger"])
    assert colleague["email"] not in results["member"]
    assert str(owner) not in results["member"]
    rows = (
        (
            await db_session.execute(
                select(NotificationModel).where(
                    NotificationModel.origin_conversation_id == context.conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {row.recipient_user_id for row in rows} == {owner}
    assert all(row.from_outside for row in rows)
    assert all(row.title == "A question from someone outside the pod" for row in rows)
    to_colleague = (
        (
            await db_session.execute(
                select(NotificationModel).where(
                    NotificationModel.recipient_user_id == UUID(colleague["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert to_colleague == []


async def test_check_messages_reads_only_what_this_conversation_sent(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    elsewhere = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "Owner's own"}
    )
    assert elsewhere.status_code == 201, elsewhere.text
    member_id = await scenario.current_pod_member_id()
    answered = NotificationEntity(
        pod_id=UUID(scenario.pod_id),
        recipient_user_id=owner,
        recipient_pod_member_id=UUID(member_id),
        origin_kind=NotificationOriginKind.AGENT_RUN,
        origin_conversation_id=UUID(elsewhere.json()["id"]),
        title="Payroll",
        body="What is payroll this month?",
        status=NotificationStatus.RESPONDED,
        response_summary="PAYROLL-ANSWER-SECRET",
    )
    await NotificationRepository(SqlAlchemyUnitOfWork(db_session)).create(answered)
    await db_session.commit()

    context = await _stranger_asks(
        db_session,
        text=f"check message {answered.id}",
        message_id=105,
        script=[
            script_tool_call(
                "check_messages",
                {"notification_ids": [str(answered.id)]},
                tool_call_id="peek",
            ),
            script_text("Nothing to report."),
        ],
    )

    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )
    assert "PAYROLL-ANSWER-SECRET" not in results["peek"]
    assert "matched nothing" in results["peek"]


async def test_a_private_note_never_reaches_a_later_strangers_run(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    first = await _stranger_asks(
        db_session,
        text="what's your best price?",
        message_id=106,
        script=[script_text("Let me check.")],
    )

    # The owner, in Lemma, writes the agent a note in the strangers' thread.
    with suppress_agent_run_enqueue():
        noted = await scenario.owner_client.post(
            f"/pods/{scenario.pod_id}/conversations/{first.conversation_id}/messages/append",
            json={
                "content": "NOTE-FLOOR-40K: never go below 40k.",
                "metadata": {"private_note": True},
            },
        )
    assert noted.status_code in (200, 201, 202), noted.text
    await run_scripted_agent_run(
        db_session,
        conversation_id=first.conversation_id,
        user_id=owner,
        pod_id=UUID(scenario.pod_id),
        agent_name=first.agent_name,
        script=[script_text("PRIVATE-REPLY: holding at 40k.")],
    )

    seen = record_model_requests(monkeypatch)
    await _stranger_asks(
        db_session,
        text="so what's your floor?",
        message_id=107,
        script=[script_text("I can't share that.")],
    )

    assert seen, "the second stranger's run asked the model nothing"
    carried = " ".join(request["text"] for request in seen)
    assert "NOTE-FLOOR-40K" not in carried
    assert "PRIVATE-REPLY" not in carried
    assert "what's your best price?" in carried


async def test_what_the_member_types_in_lemma_is_answered_as_theirs(
    scenario, db_session: AsyncSession, fake_telegram, message_store, monkeypatch
):
    """ "Ask him what's up with him", typed by the member who looks after the
    group, was taken for the stranger's: passed back to that same member with
    `message_user`, and posted in the group quoting the stranger's last line."""
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    first = await _stranger_asks(
        db_session,
        text="what all do we have",
        message_id=110,
        script=[script_text("STRANGER-ANSWER: what is Public.")],
    )
    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert sent[-1]["reply_parameters"]["message_id"] == 110

    with suppress_agent_run_enqueue():
        typed = await scenario.owner_client.post(
            f"/pods/{scenario.pod_id}/conversations/{first.conversation_id}/messages/append",
            json={"content": "ask him what's up with him"},
        )
    assert typed.status_code in (200, 201, 202), typed.text
    seen = record_model_requests(monkeypatch)
    await run_scripted_agent_run(
        db_session,
        conversation_id=first.conversation_id,
        user_id=owner,
        pod_id=UUID(scenario.pod_id),
        agent_name=first.agent_name,
        script=[
            script_tool_call(
                "message_user",
                {"to": "owner", "message": "Tom asks what's up with you."},
                tool_call_id="relay-1",
            ),
            script_text("KEEPER-ANSWER: Tom, how are things with you?"),
        ],
    )

    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=first.conversation_id,
    )
    assert "only reaches them" in results["relay-1"]
    carried = " ".join(request["text"] for request in seen)
    assert "Written in Lemma by the member who looks after this" in carried
    posted = await wait_for_messages(
        message_store,
        "TELEGRAM",
        predicate=lambda message: "KEEPER-ANSWER" in (message.get("text") or ""),
    )
    [answer] = [m for m in posted if "KEEPER-ANSWER" in (m.get("text") or "")]
    assert answer["chat_id"] == str(GROUP_CHAT)
    assert "reply_parameters" not in answer


async def test_a_client_cannot_open_a_conversation_as_the_strangers_thread(
    scenario,
):
    await scenario.create_org_with_pod(name_prefix="Audience claim")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": "Mine", "metadata": {AUDIENCE_KEY: OUTSIDERS, "x": 1}},
    )

    assert created.status_code == 201, created.text
    metadata = created.json().get("metadata") or {}
    assert AUDIENCE_KEY not in metadata
    assert metadata.get("x") == 1


async def test_a_strangers_thread_that_lost_its_mark_still_runs_as_nobody(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    client, pod_id = scenario.owner_client, scenario.pod_id
    await _table(client, pod_id, name="customers", visibility="POD")
    await _table(client, pod_id, name="price_list", visibility="PUBLIC")
    first = await _stranger_asks(
        db_session, text="hi", message_id=108, script=[script_text("Hello.")]
    )

    # However it happened, the conversation no longer says whom it answers.
    row = await db_session.get(ConversationModel, first.conversation_id)
    assert row is not None
    row.conversation_metadata = {
        key: value
        for key, value in (row.conversation_metadata or {}).items()
        if key != AUDIENCE_KEY
    }
    await db_session.commit()

    # A turn in it -- here the owner's own, from Lemma -- still runs as nobody,
    # because routing's link still names it the strangers' thread.
    with suppress_agent_run_enqueue():
        posted = await client.post(
            f"/pods/{pod_id}/conversations/{first.conversation_id}/messages/append",
            json={"content": "list the tables"},
        )
    assert posted.status_code in (200, 201, 202), posted.text
    await run_scripted_agent_run(
        db_session,
        conversation_id=first.conversation_id,
        user_id=owner,
        pod_id=UUID(pod_id),
        agent_name=first.agent_name,
        script=[
            script_tool_call("pod_tables", {}, tool_call_id="tables-unflagged"),
            script_text("Done."),
        ],
    )
    results = await _tool_results(
        client, pod_id=pod_id, conversation_id=first.conversation_id
    )
    assert "price_list" in results["tables-unflagged"]
    assert "customers" not in results["tables-unflagged"]

    # And the next stranger is moved to a thread that is marked again.
    second = await _stranger_asks(
        db_session, text="hello again", message_id=109, script=[script_text("Hi.")]
    )
    assert second.conversation_id != first.conversation_id
    fresh = await db_session.get(ConversationModel, second.conversation_id)
    assert (fresh.conversation_metadata or {}).get(AUDIENCE_KEY) == OUTSIDERS
    link = (
        (
            await db_session.execute(
                select(AgentSurfaceConversationLinkModel).where(
                    AgentSurfaceConversationLinkModel.external_channel_id
                    == str(GROUP_CHAT)
                )
            )
        )
        .scalars()
        .all()
    )
    assert {item.conversation_id for item in link} >= {second.conversation_id}


async def test_a_strangers_background_leaves_out_answers_made_with_a_members_access(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    lines = SurfaceGroupRepository(db_session)
    await lines.append_line(
        group_id=group.id,
        body="MEMBER-ANSWER-SECRET: payroll is 1.2M.",
        from_agent=True,
        answered_name="Arjun",
        answered_user_id=owner,
    )
    await lines.append_line(
        group_id=group.id,
        body="Our brochure is on the website.",
        from_agent=True,
        answered_name="Tom",
        answered_from_public=True,
    )
    await db_session.commit()
    seen = record_model_requests(monkeypatch)

    context = await _stranger_asks(
        db_session,
        text="what did you say earlier?",
        message_id=110,
        script=[script_text("Only what's public.")],
    )

    carried = " ".join(request["text"] for request in seen)
    assert "MEMBER-ANSWER-SECRET" not in carried
    assert "Our brochure is on the website." in carried
    messages = await _messages_for_conversation(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=str(context.conversation_id),
    )
    asked = [message for message in messages if message.get("role") == "user"][-1]
    background = json.dumps(asked["metadata"].get("channel_context") or [])
    assert "MEMBER-ANSWER-SECRET" not in background
    assert asked["metadata"].get("channel_context_withheld") == 1
