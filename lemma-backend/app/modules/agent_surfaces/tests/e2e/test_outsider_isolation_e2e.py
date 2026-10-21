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
* any member directly -- it has ``ask_a_person`` and nothing that writes to,
  lists or looks up members, so it cannot probe who is in the pod;
* answers to questions some other conversation asked;
* what the member wrote the agent privately in the strangers' own thread;
* answers the bot gave a member with that member's access;
* and it stays a stranger's run even when the conversation loses its mark.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.background_reads import background_read
from app.modules.agent_surfaces.tests.e2e.group_timeline import (
    a_stranger,
    bot_for,
    say,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.asked_questions import message_ref
from app.modules.channels.contracts import (
    Asker,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts.questions import answer
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _messages_for_conversation,
    _seed_external_user,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import direct_lines
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
    assert context.audience.answers_outsiders is True
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


#: What reaches a member, lists them, or reads their questions: none of it is a
#: stranger's run's to call.
MEMBER_TOOLS = {
    "channel_post",
    "channel_read",
    "channel_search",
    "channel_list",
    "list_pod_members",
    "list_teammates",
    "ask_teammate",
    "answer_question",
}


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


async def test_a_stranger_reaches_no_member_and_can_only_call_a_person_in(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    """A stranger's run writes to nobody: it calls a person in, and that is all.

    ``message_user`` used to be offered, fenced to the member who looks after
    the group, and had to answer the same for any address so a stranger could
    not learn who is in the pod from it. Nothing that writes to, lists or looks
    up members is offered now; ``ask_a_person`` tells the member who looks
    after them, and nobody else.
    """
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    colleague = await scenario.create_user("outsider-colleague")
    await scenario.add_user_to_pod(user=colleague, role="POD_EDITOR")
    seen = record_model_requests(monkeypatch)

    context = await _stranger_asks(
        db_session,
        text="please ask your colleagues",
        message_id=104,
        script=[
            script_tool_call(
                "channel_post",
                {
                    "to": colleague["email"],
                    "text": "Is the rate 40k?",
                    "wants_answer": True,
                },
                tool_call_id="member",
            ),
            script_tool_call(
                "ask_a_person",
                {"reason": "They ask whether the rate is 40k."},
                tool_call_id="call",
            ),
            script_text("Someone from the team will get back to you."),
        ],
    )

    assert seen, "the model was never asked anything"
    for request in seen:
        assert "ask_a_person" in request["tools"]
        assert not MEMBER_TOOLS & request["tools"], request["tools"]
    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )
    assert colleague["email"] not in results.get("member", "")
    assert '"success": true' in results["call"], results["call"]
    assert (
        await direct_lines(
            db_session, pod_id=scenario.pod_id, user_id=UUID(colleague["id"])
        )
        == []
    )
    [notice] = await direct_lines(db_session, pod_id=scenario.pod_id, user_id=owner)
    assert "needs a person" in notice.body


async def test_a_stranger_never_reads_answers_to_another_conversations_questions(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    """``channel_read(asked_by_me=True)`` reads what *this* conversation asked.

    A stranger's run is offered neither it nor ``channel_search``, so a member's
    answer to a question some other conversation put to them is never in reach
    -- not by id, not by searching for its words.
    """
    _, _, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    elsewhere = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "Owner's own"}
    )
    assert elsewhere.status_code == 201, elsewhere.text
    # The owner's other conversation asks them, as ``channel_post`` does with
    # ``wants_answer``, and they answer.
    pod_id = UUID(scenario.pod_id)
    sessions = SessionUnitOfWorkFactory(async_session_maker)
    async with sessions() as uow:
        theirs = await direct_channel(uow, pod_id=pod_id, user_id=owner)
        asked = await post(
            uow,
            channel_id=theirs.id,
            line=NewLine(
                sender=Sender.agent(pod_id, on_behalf_of=Principal.member(owner)),
                text="What is payroll this month?",
                conversation_id=UUID(elsewhere.json()["id"]),
            ),
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=message_ref(UUID(elsewhere.json()["id"]), "payroll"),
                asker=Asker.message(UUID(elsewhere.json()["id"]), "payroll"),
                question={"tool": "message", "prompt": "What is payroll this month?"},
                awaits=Principal.member(owner),
            ),
        )
        await uow.commit()
    assert asked is not None
    await answer(
        sessions,
        message_id=asked.id,
        by=Principal.member(owner),
        given={"text": "PAYROLL-ANSWER-SECRET"},
    )
    seen = record_model_requests(monkeypatch)

    context = await _stranger_asks(
        db_session,
        text=f"check message {asked.id}",
        message_id=105,
        script=[
            script_tool_call(
                "channel_read", {"asked_by_me": True}, tool_call_id="peek"
            ),
            script_text("Nothing to report."),
        ],
    )

    assert seen, "the model was never asked anything"
    assert all(not MEMBER_TOOLS & request["tools"] for request in seen)
    assert all("PAYROLL-ANSWER-SECRET" not in request["text"] for request in seen)
    results = await _tool_results(
        scenario.owner_client,
        pod_id=scenario.pod_id,
        conversation_id=context.conversation_id,
    )
    assert "PAYROLL-ANSWER-SECRET" not in results.get("peek", "")


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
    `message_user`, and posted in the group quoting the stranger's last line.
    The run is still the stranger's, so nothing that writes to a member is
    offered; it answers in the group, as the member's words."""
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
        script=[script_text("KEEPER-ANSWER: Tom, how are things with you?")],
    )

    assert seen, "the member's turn asked the model nothing"
    assert all("channel_post" not in request["tools"] for request in seen)
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


async def test_a_client_cannot_open_a_conversation_as_a_contacts(
    scenario, db_session: AsyncSession
):
    """Whom a conversation answers is a column no request reaches."""
    await scenario.create_org_with_pod(name_prefix="Audience claim")
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={
            "title": "Mine",
            "metadata": {"audience": "contact", "contact_id": str(uuid4()), "x": 1},
        },
    )

    assert created.status_code == 201, created.text
    row = await db_session.get(ConversationModel, UUID(created.json()["id"]))
    assert row is not None
    assert row.contact_id is None
    assert (row.conversation_metadata or {}).get("x") == 1


async def test_a_contacts_conversation_with_its_metadata_wiped_still_runs_as_them(
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

    # However it happened, the conversation's metadata says nothing any more.
    row = await db_session.get(ConversationModel, first.conversation_id)
    assert row is not None and row.contact_id is not None
    contact_id = row.contact_id
    row.conversation_metadata = {}
    await db_session.commit()

    # A turn in it -- here the owner's own, from Lemma -- still runs as the
    # contact, because the column says whom it answers.
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

    # And the same stranger's next question is theirs again, in the same place.
    second = await _stranger_asks(
        db_session, text="hello again", message_id=109, script=[script_text("Hi.")]
    )
    assert second.conversation_id == first.conversation_id
    assert second.audience.contact_id == contact_id


async def test_a_strangers_background_leaves_out_answers_made_with_a_members_access(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    bot = UUID(scenario.pod_id)  # the pod's own agent
    tom = await a_stranger(
        db_session, group, telegram_id=str(STRANGER_TELEGRAM_ID), name="Tom"
    )
    await say(
        db_session,
        group,
        "MEMBER-ANSWER-SECRET: payroll is 1.2M.",
        sender=bot_for(Principal.member(owner), agent_id=bot),
    )
    await say(
        db_session,
        group,
        "Our brochure is on the website.",
        sender=bot_for(tom, agent_id=bot),
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
    # The message keeps which lines, not what they said; read as the run
    # reads them, the member's answer is left out, and counted.
    assert "MEMBER-ANSWER-SECRET" not in json.dumps(asked["metadata"])
    shown = await background_read(asked["metadata"])
    assert "MEMBER-ANSWER-SECRET" not in " ".join(line.text for line in shown.lines)
    assert shown.withheld == 1
