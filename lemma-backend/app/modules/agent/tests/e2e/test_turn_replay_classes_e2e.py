"""A resumed turn settles its previous attempt's open calls by replay class.

The rows are written exactly as a worker that died mid-step leaves them: the
run still RUNNING, its tool calls committed, no returns -- and, for the KEYED
calls, the effect the first attempt already had (the record it created, the
child it spawned). The turn is then run through the work runtime, against real
Postgres, with the deterministic model. What must hold:

* SAFE (``pod_tables``) runs again under the recorded id: one return;
* KEYED record create returns the record the first attempt wrote: one row;
* KEYED spawn returns the child the first attempt spawned: one child;
* UNSAFE (``exec_command``, a record update) and an unknown tool are never
  run: an "outcome unknown" return each, stored, so a reload does not
  resynthesise it, and one line in the conversation for the person;
* an open pause (``ask_user``) is not touched;
* the turn then carries on from there and completes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.contracts.turns import stage_agent_turn, turn_for
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.turn_resume import (
    INTERRUPTED_NOTICE,
    OUTCOME_UNKNOWN,
)
from app.modules.agent.tests.e2e.turn_work_support import (
    drain,
    rows,
    runtime_profile,
    scalar,
    turn_pod,
    turn_registry,
)
from app.modules.datastore.services.record_idempotency import keyed_id
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.scripted_model import script_text

pytestmark = [pytest.mark.e2e]


async def _grant(client, pod_id: str, agent_name: str, table: str) -> None:
    response = await client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_name,
                    "permission_ids": ["agent.read", "agent.execute"],
                },
                {
                    "resource_type": "datastore_table",
                    "resource_name": table,
                    "permission_ids": [
                        "datastore.table.read",
                        "datastore.record.read",
                        "datastore.record.write",
                    ],
                },
            ]
        },
    )
    assert response.status_code == 200, response.text


async def _interrupted_run(
    conversation_id: UUID,
    user_id: UUID,
    calls: list[tuple[str, str, dict]],
    *,
    profile_id: str,
) -> UUID:
    """A run that committed ``calls`` and died before any of them returned."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repository = ConversationRepository(uow)
        conversation = await repository.get_conversation(conversation_id)
        assert conversation is not None
        run = await repository.create_agent_run(
            conversation_id=conversation_id,
            agent_id=conversation.agent_id,
            agent_runtime=AgentRuntimeConfig(
                profile_id=profile_id, model_name="mock-safe-model"
            ),
            metadata={},
        )
        await repository.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text("do the things", role=MessageRole.USER),
        )
        for tool_name, call_id, args in calls:
            await repository.append_message(
                conversation_id=conversation_id,
                agent_run_id=run.id,
                draft=MessageDraft.of_tool_call(
                    tool_name=tool_name, tool_call_id=call_id, tool_args=args
                ),
            )
        await stage_agent_turn(
            uow, turn_for(conversation, run_id=run.id, user_id=user_id)
        )
        return run.id


@pytest.mark.drives_work
async def test_a_resumed_turn_settles_each_open_call_by_its_class(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    client = authenticated_client
    profile = await runtime_profile(client, fixed_test_org["id"])
    pod = await turn_pod(
        client,
        fixed_test_org["id"],
        profile_id=profile,
        toolsets=["POD", "SUBAGENTS", "WORKSPACE_CLI", "USER_INTERACTION"],
    )
    table = f"replayed_{uuid4().hex[:8]}"
    datastore = DatastoreApi(client, pod.pod_id)
    await datastore.create_table(
        {
            "name": table,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        }
    )
    await _grant(client, pod.pod_id, pod.agent_name, table)
    # Turn 0 is the interrupted attempt's (already in the history); turn 1 is
    # what the resumed model says once every call has a return.
    conversation = UUID(
        await pod.conversation([script_text("unused"), script_text("All settled.")])
    )
    calls = [
        ("pod_tables", "safe-1", {}),
        (
            "pod_write_record",
            "keyed-record-1",
            {
                "request": {
                    "action": "create",
                    "table_name": table,
                    "data": {"title": "once"},
                }
            },
        ),
        ("spawn_subagent", "keyed-spawn-1", {"request": {"input": "look into it"}}),
        ("exec_command", "unsafe-1", {"cmd": "rm -rf /tmp/never", "comment": "no"}),
        (
            "pod_write_record",
            "unsafe-update-1",
            {
                "request": {
                    "action": "update",
                    "table_name": table,
                    "record_id": str(uuid4()),
                    "data": {"title": "stale"},
                }
            },
        ),
        ("mystery_mcp_tool", "unknown-1", {"x": 1}),
        ("ask_user", "pause-1", {"questions": [{"question": "Which?"}]}),
    ]
    run_id = await _interrupted_run(
        conversation, UUID(fixed_test_user["id"]), calls, profile_id=profile
    )
    # The first attempt's KEYED effects, as they committed before it died.
    record_key = f"run:{run_id}:keyed-record-1"
    await datastore.create_record(table, {"id": keyed_id(record_key), "title": "once"})
    child_id = UUID(await pod.conversation([]))
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repository = ConversationRepository(uow)
        await scalar_update_parent(uow, child_id, conversation)
        await repository.set_spawn_call(child_id, f"{run_id}:keyed-spawn-1")

    await drain(turn_registry())

    returns = {
        call_id: (result, metadata)
        for call_id, result, metadata in await rows(
            "SELECT tool_call_id, tool_result, message_metadata FROM agent_messages "
            "WHERE agent_run_id = :run AND kind = 'TOOL_RETURN'",
            run=run_id,
        )
    }
    # SAFE: run again, one return.
    assert returns["safe-1"][1]["replay_class"] == "SAFE"
    assert "error" not in returns["safe-1"][0] or not returns["safe-1"][0]["error"]
    # KEYED record: the row the first attempt wrote, not a second one.
    assert returns["keyed-record-1"][1]["replay_class"] == "KEYED"
    assert returns["keyed-record-1"][0]["record"]["id"] == keyed_id(record_key)
    listed = await datastore.list_records(table)
    assert len(listed["items"]) == 1
    # UNSAFE update: never replayed over whatever the row holds now.
    assert listed["items"][0]["title"] == "once"
    assert returns["unsafe-update-1"][1]["replay_class"] == "UNSAFE"
    # KEYED spawn: the child the first attempt spawned.
    assert returns["keyed-spawn-1"][1]["replay_class"] == "KEYED"
    assert returns["keyed-spawn-1"][0]["conversation_id"] == str(child_id)
    assert (
        await scalar(
            "SELECT count(*) FROM agent_conversations WHERE parent_id = :p",
            p=conversation,
        )
        == 1
    )
    # UNSAFE and unknown: never run, outcome unknown, stored.
    for call_id in ("unsafe-1", "unknown-1", "unsafe-update-1"):
        result, metadata = returns[call_id]
        assert metadata["replay_class"] == "UNSAFE"
        assert result == {"success": False, "error": OUTCOME_UNKNOWN}
    # The person reading the conversation is told, once, outside the run (so
    # the resumed model is not handed it as their words).
    notices = await rows(
        "SELECT text, agent_run_id FROM agent_messages WHERE conversation_id = :c "
        "AND kind = 'NOTIFICATION'",
        c=conversation,
    )
    assert notices == [
        (
            INTERRUPTED_NOTICE.format(
                tools="exec_command, mystery_mcp_tool, pod_write_record"
            ),
            None,
        )
    ]
    # The open pause is the person's, not the replay's.
    assert "pause-1" not in returns
    # Exactly one return per settled call, and the turn carried on.
    counts = dict(
        await rows(
            "SELECT tool_call_id, count(*) FROM agent_messages WHERE agent_run_id = :r "
            "AND kind = 'TOOL_RETURN' GROUP BY tool_call_id",
            r=run_id,
        )
    )
    assert all(n == 1 for n in counts.values()), counts
    assert await pod.run_status(run_id) in {"COMPLETED"}


async def scalar_update_parent(uow, child_id: UUID, parent_id: UUID) -> None:
    from sqlalchemy import text

    await uow.session.execute(
        text(
            "UPDATE agent_conversations SET parent_id = :p, conversation_metadata = "
            "coalesce(conversation_metadata, '{}'::jsonb) || "
            '\'{"is_sub_agent": true, "reports_to_parent": true}\'::jsonb '
            "WHERE id = :c"
        ),
        {"p": parent_id, "c": child_id},
    )
