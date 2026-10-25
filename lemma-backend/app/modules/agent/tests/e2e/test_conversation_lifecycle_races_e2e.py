"""A turn's edges: a pause, its answer, Stop, and what is said in between.

Each test is a race between two things the product does in separate
transactions -- an approval decided and its tool still running, a question
answered before its run was written as paused, a message sent after Stop -- set
up one committed step at a time against real Postgres, because a stand-in
repository answers whatever the test tells it to and the races live in SQL.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.events.run_followup_work import start_queued_followup_for
from app.modules.agent.infrastructure.harness.lemma.history_replay import (
    history_and_prompt,
)
from app.modules.agent.infrastructure.db.approvals import ApprovalDecisions
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.services.approval_reconciliation import (
    stage_approval_execution,
)
from app.modules.test_support.e2e.agent_work import run_approval_work
from app.modules.agent.services.mcp_pausing_calls import record_pausing_tool_call
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    RECORD_DELETE_REFUSALS,
    Seeded,
    conversation,
    admit_message,
    resolve_pause,
    stop_conversation,
    finish,
    messages,
    owner_uow,
    pausing_call,
    queued_message,
    refused_before_asking,
    running_run,
    runs,
    seeded_conversation,
    uow_factory,
)
from app.modules.test_support.e2e.function_helpers import create_table
from app.modules.agent.domain.value_objects import QueueState

pytestmark = [pytest.mark.e2e]


async def _carry_out(seeded, approval_id: str) -> None:
    """The decision's ``agent.approval.execute`` item, run late -- as a worker
    that picked it up after everything above would."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await stage_approval_execution(
            uow,
            conversation_id=seeded.conversation_id,
            approval_id=approval_id,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
        )
        await uow.commit()
    await run_approval_work()


_TABLE = "chores"


async def _completed(seeded: Seeded, run_id: UUID) -> UUID | None:
    """Run the run's ``agent.followup``, as its finalize writes it."""
    return await start_queued_followup_for(
        seeded.conversation_id, run_id, uow_factory=uow_factory()
    )


async def _held_by_a_worker(seeded: Seeded, run_id: UUID) -> None:
    """The run's turn, claimed by a worker: a stop marks it, the worker ends it."""
    from sqlalchemy import text

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await uow.session.execute(
            text(
                "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
                "state, lease_owner, lease_until) VALUES (gen_random_uuid(), :ns, "
                "'interactive', 'agent.turn', '{}'::jsonb, :key, 'running', "
                "'elsewhere', now() + interval '1 minute')"
            ),
            {"ns": f"pod:{seeded.pod_id}", "key": f"agent-run:{run_id}"},
        )
        await uow.commit()


async def _run(seeded: Seeded, run_id: UUID):
    return next(run for run in await runs(seeded.conversation_id) if run.id == run_id)


def _returns_for(found, tool_call_id: str):
    return [
        message
        for message in found
        if message.kind is MessageKind.TOOL_RETURN
        and message.tool_call_id == tool_call_id
    ]


@pytest.mark.drives_work
async def test_a_message_sent_while_an_approved_tool_runs_rides_the_resume(
    scenario,
) -> None:
    """Approved, then the person types before the tool's result lands.

    The message used to start a run of its own, whose history showed the
    approval unanswered ("the user did not answer this"), and the result then
    landed behind it. Now the message waits for the resume, which carries both.
    """
    seeded = await seeded_conversation(scenario, toolsets=["POD", "USER_INTERACTION"])
    await create_table(scenario.owner_client, scenario.pod_id, _TABLE, enable_rls=False)
    rows: dict[str, str] = {}
    for title in ("first", "second"):
        created = await scenario.owner_client.post(
            f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records",
            json={"data": {"title": title}},
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text
        rows[title] = created.json()["id"]

    delete = {"action": "delete", "table_name": _TABLE, "record_id": rows["first"]}
    await refused_before_asking(
        seeded,
        tool_name="pod_write_record",
        args=delete,
        permission_ids=RECORD_DELETE_REFUSALS,
    )
    paused = await running_run(seeded)
    approval_id = await pausing_call(
        seeded,
        paused,
        tool_name="request_approval",
        tool_args={
            "tool_name": "pod_write_record",
            "args": delete,
            "title": "Delete the first chore",
        },
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)
    # Approved: the decision is committed and the tool handed to a worker,
    # which has not written its result yet.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ApprovalDecisions(uow).record_approval_decision(
            conversation_id=seeded.conversation_id,
            approval_id=approval_id,
            agent_run_id=paused,
            tool_name="pod_write_record",
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={},
            resolved_by_user_id=seeded.user_id,
        )
        await uow.commit()

    sent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json={"content": "And then tidy the second one."},
    )
    assert sent.status_code == status.HTTP_200_OK, sent.text
    assert sent.json()["started_new_run"] is False
    assert sent.json()["agent_run_id"] == str(paused)

    # The approval job arriving late, as the worker's retry would.
    await _carry_out(seeded, approval_id)

    listed = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/datastore/tables/{_TABLE}/records"
    )
    assert {row["title"] for row in listed.json()["items"]} == {"second"}
    found = await messages(seeded.conversation_id)
    assert len(_returns_for(found, approval_id)) == 1, "the approved tool ran twice"

    all_runs = await runs(seeded.conversation_id)
    assert [run.id for run in all_runs][:1] == [paused]
    (resume,) = all_runs[1:]
    assert resume.resumed_tool_call_id == approval_id
    typed = next(
        m for m in found if m.role is MessageRole.USER and "second" in (m.text or "")
    )
    assert typed.state.steered_into_run_id == resume.id

    # And the resumed run is shown the approved result, not "did not answer".
    history, _prompt = history_and_prompt(found)
    replayed = [
        part.content
        for message in history
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_call_id", None) == approval_id
        and hasattr(part, "content")
    ]
    assert replayed and all(
        "did not answer" not in str(content) for content in replayed
    ), replayed


async def test_an_answer_that_lands_before_the_pause_is_written_still_resumes(
    scenario,
) -> None:
    """The card renders from the persisted call, before the run that
    asked is written as finished. An answer in that gap found the run still
    RUNNING, left the resume to it -- and nothing resumed, ever."""
    seeded = await seeded_conversation(scenario)
    asking = await running_run(seeded)
    question = await pausing_call(
        seeded, asking, tool_name="ask_user", tool_args={"questions": ["Which?"]}
    )

    async with owner_uow(seeded) as uow:
        await resolve_pause(uow).resolve(
            conversation=await conversation(seeded.conversation_id),
            approval_id=question,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"Which?": "the blue one"}},
        )
    assert [run.id for run in await runs(seeded.conversation_id)] == [asking]

    await finish(asking, conversation_status=ConversationStatus.WAITING)
    resumed = await _completed(seeded, asking)

    assert resumed is not None
    resume = await _run(seeded, resumed)
    assert resume.status == AgentRunStatus.RUNNING.value
    assert resume.resumed_tool_call_id == question
    assert (await conversation(seeded.conversation_id)).status == (
        ConversationStatus.RUNNING
    )


async def test_a_message_after_stop_is_answered_and_one_before_it_is_not(
    scenario,
) -> None:
    """ "Stop, do X instead" is the commonest message after Stop, and it
    joined the dying run as queued -- then the follow-up was skipped because
    the run had stopped, and the person heard nothing."""
    seeded = await seeded_conversation(scenario)
    working = await running_run(seeded)
    await _held_by_a_worker(seeded, working)
    before = await queued_message(seeded, working, "Also check the logs.")

    async with owner_uow(seeded) as uow:
        await stop_conversation(uow).stop(
            conversation_id=seeded.conversation_id,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
        )
    async with owner_uow(seeded) as uow:
        joined = await admit_message(uow).admit(
            await conversation(seeded.conversation_id),
            user_id=seeded.user_id,
            content="Stop that, summarise the README instead.",
            agent_name=None,
            message_metadata=None,
        )
    assert joined.agent_run_id == working and joined.started_new_run is False
    # The run being stopped does not take it in: nothing there would answer.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        steered = await QueuedMessages(uow).claim_queued_user_messages(
            working, as_run=True
        )
        await uow.rollback()
    assert steered == []

    await finish(working)
    assert (await _run(seeded, working)).status == AgentRunStatus.STOPPED.value
    followup = await _completed(seeded, working)

    assert followup is not None
    found = {message.id: message for message in await messages(seeded.conversation_id)}
    after = next(m for m in found.values() if "README" in (m.text or ""))
    assert after.state.steered_into_run_id == followup
    assert found[before.id].state.steered_into_run_id is None


async def test_a_remote_harness_question_is_answered_inside_its_own_run(
    scenario,
) -> None:
    """The other half of the same race: a question a remote harness asked over MCP is
    parked in a run that keeps going, and its answer is read by that run. That
    -- and only that -- is what keeps a resume from starting beside it, so the
    mark is Lemma's, written when it records the call."""
    seeded = await seeded_conversation(scenario)
    working = await running_run(seeded)
    question = await record_pausing_tool_call(
        uow_factory(),
        conversation_id=seeded.conversation_id,
        agent_run_id=working,
        tool_name="ask_user",
        arguments={"questions": ["Which branch?"]},
    )
    waiting = await record_pausing_tool_call(
        uow_factory(),
        conversation_id=seeded.conversation_id,
        agent_run_id=working,
        tool_name="wait_for",
        arguments={"seconds": 60},
    )
    marks = {
        m.tool_call_id: m.state.parked_in_run_id is not None or None
        for m in await messages(seeded.conversation_id)
        if m.kind is MessageKind.TOOL_CALL
    }
    assert marks == {question: True, waiting: None}

    async with owner_uow(seeded) as uow:
        await resolve_pause(uow).resolve(
            conversation=await conversation(seeded.conversation_id),
            approval_id=question,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"Which branch?": "main"}},
        )

    assert len(_returns_for(await messages(seeded.conversation_id), question)) == 1
    assert [run.id for run in await runs(seeded.conversation_id)] == [working]


@pytest.mark.drives_work
async def test_stop_keeps_an_already_approved_pause_from_waking_the_agent(
    scenario,
) -> None:
    """Stop closed only pauses nobody had decided. An approval whose
    tool was already running finished it -- it cannot be taken back -- and then
    started the resume, so the agent carried on after Stop."""
    seeded = await seeded_conversation(scenario)
    paused = await running_run(seeded)
    question = await pausing_call(
        seeded, paused, tool_name="ask_user", tool_args={"questions": ["Go?"]}
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ApprovalDecisions(uow).record_approval_decision(
            conversation_id=seeded.conversation_id,
            approval_id=question,
            agent_run_id=paused,
            tool_name="ask_user",
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"Go?": "yes"}},
            resolved_by_user_id=seeded.user_id,
        )
        await uow.commit()

    async with owner_uow(seeded) as uow:
        await stop_conversation(uow).stop(
            conversation_id=seeded.conversation_id,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
        )
    await _carry_out(seeded, question)

    assert len(_returns_for(await messages(seeded.conversation_id), question)) == 1
    assert [run.id for run in await runs(seeded.conversation_id)] == [paused]
    assert (await conversation(seeded.conversation_id)).status == (
        ConversationStatus.STOPPED
    )


class _ExhaustedUsage:
    """An account with no headroom left on any window."""

    async def spent_windows(self, *, organization_id, user_id, outside_audience):
        del organization_id, user_id, outside_audience
        return ("org_month", "user_week", "user_month")


async def test_resuming_a_pause_is_held_to_the_usage_limit(scenario) -> None:
    """Every other way a run starts asks for headroom first; answering a
    question did not, so it was a way past an exhausted limit."""
    seeded = await seeded_conversation(scenario)
    paused = await running_run(seeded)
    question = await pausing_call(
        seeded, paused, tool_name="ask_user", tool_args={"questions": ["Which?"]}
    )
    await finish(paused, conversation_status=ConversationStatus.WAITING)

    async with owner_uow(seeded) as uow:
        await resolve_pause(uow, usage_service=_ExhaustedUsage()).resolve(
            conversation=await conversation(seeded.conversation_id),
            approval_id=question,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"Which?": "red"}},
        )

    # The answer is kept; no run starts; the conversation still waits.
    assert len(_returns_for(await messages(seeded.conversation_id), question)) == 1
    assert [run.id for run in await runs(seeded.conversation_id)] == [paused]
    assert (await conversation(seeded.conversation_id)).status == (
        ConversationStatus.WAITING
    )


async def test_messages_queued_behind_a_question_say_so_and_ride_its_answer(
    scenario,
) -> None:
    """Said while the run worked, then the run stopped to ask something:
    nothing answers those until the question is answered, and they used to be
    drawn as ordinary sent messages with no reply coming."""
    seeded = await seeded_conversation(scenario)
    asking = await running_run(seeded)
    waiting = await queued_message(seeded, asking, "And the totals, please.")
    question = await pausing_call(
        seeded, asking, tool_name="ask_user", tool_args={"questions": ["Which?"]}
    )
    await finish(asking, conversation_status=ConversationStatus.WAITING)

    assert await _completed(seeded, asking) is None
    held = next(m for m in await messages(seeded.conversation_id) if m.id == waiting.id)
    assert held.state.queue_state is QueueState.WAITING_ON_ANSWER

    async with owner_uow(seeded) as uow:
        await resolve_pause(uow).resolve(
            conversation=await conversation(seeded.conversation_id),
            approval_id=question,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"Which?": "March"}},
        )
    (_asking, resume) = await runs(seeded.conversation_id)
    answered = next(
        m for m in await messages(seeded.conversation_id) if m.id == waiting.id
    )
    assert answered.state.steered_into_run_id == resume.id


async def test_a_client_cannot_mark_its_own_message_delivered(scenario) -> None:
    """The delivery keys are the server's: a message that arrived
    claiming `steered_into_run` was never answered and could not be withdrawn."""
    seeded = await seeded_conversation(scenario)
    working = await running_run(seeded)

    sent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json={
            "content": "Look at this too.",
            "metadata": {
                "steered_into_run": str(working),
                "steer_dispatched_at": "2026-01-01T00:00:00Z",
                "waiting_on_answer": "x",
                "during_active_run": False,
                "keep_me": "yes",
            },
        },
    )

    assert sent.status_code == status.HTTP_200_OK, sent.text
    stored = next(
        m
        for m in await messages(seeded.conversation_id)
        if m.text == "Look at this too."
    )
    assert stored.state.queue_state is QueueState.QUEUED
    assert stored.state.steered_into_run_id is None
    assert stored.state.steer_dispatched_at is None
    assert stored.metadata == {"keep_me": "yes"}


@pytest.mark.parametrize("content", ["", "x" * 60_001])
async def test_an_empty_or_oversized_message_is_refused(scenario, content) -> None:
    """An empty message started a paid run with nothing to answer; an
    unbounded one is replayed into every later turn."""
    seeded = await seeded_conversation(scenario)

    sent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json={"content": content},
    )

    assert sent.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, sent.text
    assert await runs(seeded.conversation_id) == []
