"""A link ask, answered by the real runtime: worker, harness, tools, authority.

Support asks Billing over a link, from a task nobody started. The ask opens a
thread in Billing the way production does (``PodAskService``); the worker then
runs it, so ``run_context_builder`` reads the thread's ``ask`` into a POD
audience and every tool call is authorized as ``POD:Support``. Only the model is
scripted. What it reaches is what Billing shared -- ``invoices`` and
``/shared-docs`` -- and not ``payroll`` or ``/kept-docs``; and a write tool is
not even offered to it.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from fastapi import status

from app.modules.test_support.e2e.scripted_model import tag_conversation
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.services.turn_staging import stage_agent_turn, turn_for
from app.modules.agent.domain.pod_asks import ANSWER_SOURCE, AskMode
from app.modules.agent.domain.value_objects import ConversationStatus, MessageKind
from app.modules.agent.infrastructure.harness.lemma.mock_model import (
    MOCK_SCRIPT_METADATA_KEY,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.pod_link_helpers import (
    add_rows,
    ask_service,
    asking_deps,
    connect,
    folder_share,
    messages,
    table_share,
    task_conversation,
    teammate,
    thread,
)
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.test_support.e2e.function_helpers import create_folder, create_table
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_SHARED_DIR = "/shared-docs"
_KEPT_DIR = "/kept-docs"


async def _upload(client, pod_id: str, directory: str, name: str, text: str) -> None:
    made = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": directory, "search_enabled": "false"},
        files={"data": (name, text.encode(), "text/markdown")},
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text


def _script() -> list[dict[str, object]]:
    return [
        script_tool_call(
            "pod_query", {"sql": "SELECT title FROM payroll"}, tool_call_id="payroll"
        ),
        script_tool_call(
            "pod_query",
            {"sql": "SELECT title FROM invoices ORDER BY title"},
            tool_call_id="invoices",
        ),
        script_tool_call(
            "pod_read_file", {"path": f"{_KEPT_DIR}/salaries.md"}, tool_call_id="kept"
        ),
        script_tool_call(
            "pod_read_file",
            {"path": f"{_SHARED_DIR}/terms.md"},
            tool_call_id="shared",
        ),
        script_tool_call(
            "pod_list_files", {"path": _KEPT_DIR}, tool_call_id="kept-list"
        ),
        script_tool_call(
            "pod_list_files", {"path": _SHARED_DIR}, tool_call_id="shared-list"
        ),
        script_tool_call(
            "pod_search_files",
            {"query": "salaries", "method": "TEXT", "scope_path": _KEPT_DIR},
            tool_call_id="kept-search",
        ),
        script_tool_call(
            "pod_search_files",
            {"query": "terms", "method": "TEXT", "scope_path": _SHARED_DIR},
            tool_call_id="shared-search",
        ),
        script_tool_call(
            "pod_write_record",
            {"table_name": "invoices", "action": "create", "data": {"title": "x"}},
            tool_call_id="write",
        ),
        script_text("Two invoices: inv-1 and inv-2."),
    ]


async def _start_scripted(conversation_id: UUID, script: list[dict[str, object]]):
    """Script the thread's model and hand its pending run to the worker.

    The ask opened the run with the enqueue suppressed, so the script is on the
    thread before the worker first reads it. The event is the one the ask would
    have written.
    """
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = Conversations(uow)
        await tag_conversation(uow, conversation_id, MOCK_SCRIPT_METADATA_KEY, script)
        found = await conversations.get_conversation(conversation_id, include_runs=True)
        assert found is not None
        run = found.agent_runs[-1]
        await stage_agent_turn(
            uow, turn_for(found, run_id=run.id, user_id=found.user_id)
        )
        await uow.commit()


def _returns(found) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for message in found:
        if message.kind is not MessageKind.TOOL_RETURN or not message.tool_call_id:
            continue
        raw = message.tool_result
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            # A call refused before any tool ran comes back as plain text.
            parsed = raw
        results[message.tool_call_id] = (
            parsed if isinstance(parsed, dict) else {"raw": parsed}
        )
    return results


@pytest.mark.asyncio
async def test_a_link_run_reads_what_was_shared_and_nothing_else(
    authenticated_client, fixed_test_org, fixed_test_user, worker
):
    del worker
    client = authenticated_client
    support = await _create_test_pod(client, fixed_test_org)
    billing = await _create_test_pod(client, fixed_test_org)
    for table in ("invoices", "payroll"):
        await create_table(client, billing, table, enable_rls=False)
    await add_rows(client, billing, "invoices", "inv-1", "inv-2")
    await add_rows(client, billing, "payroll", "the CFO's salary")
    for directory in (_SHARED_DIR, _KEPT_DIR):
        await create_folder(client, billing, directory)
    await _upload(client, billing, _SHARED_DIR, "terms.md", "Net 30 payment terms.")
    await _upload(client, billing, _KEPT_DIR, "salaries.md", "The CFO earns a lot.")
    linked = await connect(
        client,
        answering=billing,
        asking=support,
        shares=[table_share("invoices"), folder_share(_SHARED_DIR)],
    )
    assert linked.status_code == status.HTTP_204_NO_CONTENT, linked.text

    asking = await task_conversation(client, support)
    deps = asking_deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=asking,
    )
    with suppress_agent_run_enqueue():
        outcome = await ask_service().ask(
            deps,
            teammate=await teammate(deps, billing),
            request="Which invoices are open?",
            wait_seconds=0,
        )
    assert outcome.mode is AskMode.LINK
    await _start_scripted(outcome.conversation_id, _script())

    settled = await eventually(
        label="the link run finished",
        probe=lambda: thread(outcome.conversation_id),
        done=lambda found: (
            found.status
            not in (ConversationStatus.RUNNING, ConversationStatus.STOP_REQUESTED)
        ),
        timeout_seconds=60,
    )
    assert settled.status is ConversationStatus.COMPLETED, settled.status
    returns = _returns(await messages(outcome.conversation_id))

    # Tables: the one shared, not the one kept.
    assert returns["payroll"].get("code") == "MISSING_POD_GRANT", returns["payroll"]
    assert "salary" not in json.dumps(returns["payroll"])
    assert returns["invoices"]["success"] is True, returns["invoices"]
    assert [row["title"] for row in returns["invoices"]["rows"]] == ["inv-1", "inv-2"]

    # Files: the folder shared, not the one kept -- read, listed or searched.
    assert returns["kept"].get("code") == "MISSING_POD_GRANT", returns["kept"]
    assert "CFO" not in json.dumps(returns["kept"])
    assert returns["shared"]["success"] is True, returns["shared"]
    assert "Net 30" in json.dumps(returns["shared"])
    assert returns["kept-list"].get("code") == "MISSING_POD_GRANT"
    assert "salaries.md" not in json.dumps(returns["kept-list"])
    assert "terms.md" in json.dumps(returns["shared-list"]), returns["shared-list"]
    # Searching inside the kept folder is refused outright; inside the shared
    # one it is allowed (nothing is indexed here, so it finds nothing).
    assert returns["kept-search"].get("code") == "MISSING_POD_GRANT"
    assert returns["shared-search"].get("success") is True, returns["shared-search"]

    # A write is not on offer: the harness has no such tool for this run, and
    # names what it does have -- reads and the steward's message line only.
    refusal = str(returns["write"].get("raw"))
    assert refusal.startswith("Unknown tool name: 'pod_write_record'"), refusal
    for withheld in (
        "pod_write_file",
        "pod_edit_file",
        "ask_teammate",
        "list_teammates",
        "exec_command",
    ):
        assert f"'{withheld}'" not in refusal, refusal
    rows = await client.get(f"/pods/{billing}/datastore/tables/invoices/records")
    assert sorted(row["title"] for row in rows.json()["items"]) == ["inv-1", "inv-2"]

    # And the answer goes back to Support, framed as Billing's words.
    async def _delivered():
        return [
            m
            for m in await messages(asking)
            if (m.metadata or {}).get("source") == ANSWER_SOURCE
        ]

    [answer] = await eventually(
        label="the answer delivered to Support",
        probe=_delivered,
        done=bool,
        timeout_seconds=60,
    )
    assert answer.metadata["ask_mode"] == "LINK"
    assert answer.metadata["ask_outcome"] == "ANSWERED"
    assert answer.text[answer.metadata["answer_offset"] :] == (
        "Two invoices: inv-1 and inv-2."
    )
