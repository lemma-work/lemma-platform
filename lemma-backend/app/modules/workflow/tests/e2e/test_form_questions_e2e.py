"""A workflow's form, as a question waiting on its assignee, end to end.

Assigning a FORM puts a question in the assignee's own channel, waiting on
them until the wait's ceiling. Answered on its line, the engine validates it
-- a bad answer is refused and the question stays open -- and the run moves
on; submitted in the workflow's own screen, the question closes all the same;
a cancelled run leaves nothing waiting, and a question that expired ends the
run as a form nobody answered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.contracts import (
    ChannelKind,
    Principal,
    QuestionKind,
    QuestionState,
    channel_by_id,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.lines import viewer_of
from app.modules.channels.contracts.questions import AnswerRefused
from app.modules.channels.events.sweeps import questions_expire
from app.modules.channels.infrastructure.models import ChannelQuestionModel
from app.modules.workflow.events.run_work import register_run_work
from app.modules.workflow.events.step_jobs import register_step_jobs

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _run_workflow_work() -> None:
    registry = WorkRegistry()
    register_run_work(registry)
    register_step_jobs(registry)
    await run_until_idle(
        async_session_maker,
        registry=registry,
        classes=[WorkClass.EVENTS, WorkClass.AUTOMATION, WorkClass.DELIVERY],
    )


async def _form_run(scenario) -> str:
    await scenario.create_org_with_pod(name_prefix="Forms")
    client, pod_id = scenario.owner_client, scenario.pod_id
    member_id = await scenario.current_pod_member_id()
    created = await client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": "expense-approval",
            "start": {"type": "MANUAL"},
            "mode": "GLOBAL",
        },
    )
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "approve",
                    "type": "FORM",
                    "label": "Approve the expense",
                    "config": {
                        "assignee_pod_member_id": str(member_id),
                        "input_schema": {
                            "type": "object",
                            "properties": {"approved": {"type": "boolean"}},
                            "required": ["approved"],
                        },
                    },
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "approve", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text
    run = await client.post(f"/pods/{pod_id}/workflows/{name}/runs")
    assert run.status_code == 201, run.text
    await _run_workflow_work()
    return run.json()["id"]


async def _waiting(scenario):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        viewer = await viewer_of(
            uow,
            pod_id=UUID(scenario.pod_id),
            user_id=UUID(scenario.owner_user["id"]),
        )
        waiting = await channel_questions.waiting_on(uow, viewer)
        channels = [await channel_by_id(uow, q.channel_id) for q in waiting]
    return list(zip(waiting, channels, strict=True))


async def test_a_form_waits_on_its_assignee_and_is_answered_on_its_line(scenario):
    run_id = await _form_run(scenario)
    [(asked, channel)] = await _waiting(scenario)
    assert channel is not None and channel.kind is ChannelKind.DIRECT
    assert asked.kind is QuestionKind.FORM
    assert (asked.question["run_id"], asked.question["node_id"]) == (run_id, "approve")
    assert asked.question["schema"]["properties"]["approved"]
    assert asked.ref.startswith(f"wf:{run_id}:approve:")
    # Until the wait gives up on the person.
    assert asked.expires_at is not None
    by = Principal.member(UUID(scenario.owner_user["id"]))
    answers = SessionUnitOfWorkFactory(async_session_maker)

    with pytest.raises(AnswerRefused) as refused:
        await channel_questions.answer(
            answers, message_id=asked.message_id, by=by, given={"approved": "maybe"}
        )
    assert refused.value.status_code == 422
    assert len(await _waiting(scenario)) == 1

    answered = await channel_questions.answer(
        answers, message_id=asked.message_id, by=by, given={"approved": True}
    )
    assert answered.state is QuestionState.ANSWERED
    assert answered.answer == {"approved": True}
    assert answered.answered_by == by
    assert await _waiting(scenario) == []
    await _run_workflow_work()
    finished = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/workflow-runs/{run_id}"
    )
    assert finished.json()["status"] == "COMPLETED", finished.text


async def test_a_form_submitted_in_the_workflow_closes_its_question(scenario):
    run_id = await _form_run(scenario)
    [(asked, _)] = await _waiting(scenario)
    submitted = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/workflow-runs/{run_id}/form",
        json={"node_id": "approve", "inputs": {"approved": False}},
    )
    assert submitted.status_code == 200, submitted.text
    assert await _waiting(scenario) == []
    async with create_uow_from_session_maker(async_session_maker) as uow:
        closed = await channel_questions.question_of(uow, asked.message_id)
    assert closed is not None and closed.state is QuestionState.ANSWERED
    assert closed.answer == {"approved": False}


async def test_a_cancelled_run_leaves_nothing_waiting(scenario):
    run_id = await _form_run(scenario)
    [(asked, _)] = await _waiting(scenario)
    cancelled = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/workflow-runs/{run_id}/cancel"
    )
    assert cancelled.status_code in (200, 202), cancelled.text
    assert await _waiting(scenario) == []
    async with create_uow_from_session_maker(async_session_maker) as uow:
        closed = await channel_questions.question_of(uow, asked.message_id)
    assert closed is not None and closed.state is QuestionState.CANCELLED


async def test_a_form_whose_question_expired_ends_its_run_unanswered(scenario):
    """The question in the inbox ran out of time: the run waiting on it is
    told now, as a form nobody answered -- not left parked behind a question
    nobody can answer any more."""
    run_id = await _form_run(scenario)
    [(asked, _)] = await _waiting(scenario)
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelQuestionModel)
            .where(ChannelQuestionModel.message_id == asked.message_id)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    await questions_expire()
    await _run_workflow_work()

    ended = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/workflow-runs/{run_id}"
    )
    assert ended.json()["status"] == "FAILED", ended.text
    assert "Nobody answered this form" in (ended.json().get("error") or ""), ended.text
