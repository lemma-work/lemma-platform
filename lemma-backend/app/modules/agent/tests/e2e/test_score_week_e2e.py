"""E2E: ``score_week`` counts a teammate's week from the real tables.

Every count here is SQL -- a visibility filter, a window bound, an "answered
by when" -- and a fake repository would hand back whatever list the test gave
it, so none of those rules could fail. The rows are seeded straight into the
tables, each with the timestamp that decides whether it counts, and the tool is
driven the way a pod default agent's run drives it.

What each seeded row is for is said beside it; the expected numbers at the end
are the sum of those comments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.delegation import is_pod_default_agent
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.pausing_tools import (
    SUPERSEDED_BY_NEW_MESSAGE,
    WAIT_TOOL_NAME,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    AgentConversationWaitModel,
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import (
    PodGetRecordsRequest,
    PodTablesRequest,
    PodWriteRecordRequest,
    QueryRequest,
    RecordFilter,
    ScoreWeekRequest,
)
from app.modules.agent.tools.pod.pydantic_adapter import (
    pod_get_records,
    pod_query,
    pod_tables,
    pod_write_record,
)
from app.modules.agent.tools.pod.scorecard import score_week
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_SCORECARD_COLUMNS = [
    {"name": "id", "type": "UUID", "required": True, "auto": True},
    {"name": "key", "type": "TEXT", "required": True, "unique": True},
    {"name": "measure", "type": "TEXT", "required": True},
    {"name": "kind", "type": "TEXT", "required": True},
    {"name": "counter", "type": "TEXT", "required": True},
    {"name": "query", "type": "TEXT", "required": False},
    {"name": "aim", "type": "TEXT", "required": True},
    {"name": "target", "type": "FLOAT", "required": True},
    {"name": "target_label", "type": "TEXT", "required": True},
    {"name": "counted_from", "type": "TEXT", "required": True},
    {"name": "is_on", "type": "BOOLEAN", "required": True},
    {"name": "note", "type": "TEXT", "required": False},
    {"name": "position", "type": "INTEGER", "required": True},
    {"name": "shape", "type": "TEXT", "required": False},
    {"name": "unit_table", "type": "TEXT", "required": False},
    {"name": "time_column", "type": "TEXT", "required": False},
    {"name": "test", "type": "TEXT", "required": False},
    {"name": "proposed_from", "type": "TEXT", "required": False},
]

_FOLLOWED_UP = (
    "SELECT count(*) FILTER (WHERE status = 'done') AS counted, count(*) AS total "
    "FROM commitments WHERE due >= '{start}'::date AND due < '{end}'::date"
)
_NOTHING_OVERDUE = (
    "SELECT count(*) AS counted, count(*) AS total FROM commitments "
    "WHERE coalesce(status, 'open') = 'open' AND due < ('{end}'::date - 7)"
)
_SMUGGLED = "SELECT 1 AS counted, 1 AS total; DROP TABLE commitments"


def _scorecard_row(key: str, counter: str, *, aim: str, target: float, **extra):
    row: dict[str, object] = {
        "key": key,
        "measure": key.replace("_", " "),
        "kind": "outcome",
        "counter": counter,
        "aim": aim,
        "target": target,
        "target_label": "a label",
        "counted_from": "e2e",
        "is_on": True,
        "position": 0,
    }
    row.update(extra)
    return row


_SCORECARD_ROWS = [
    _scorecard_row("approved", "approvals", aim="higher", target=0.9, position=1),
    _scorecard_row(
        "followed_up", "sql", aim="higher", target=0.9, position=2, query=_FOLLOWED_UP
    ),
    _scorecard_row(
        "nothing_overdue",
        "sql",
        aim="lower",
        target=0,
        position=3,
        query=_NOTHING_OVERDUE,
    ),
    _scorecard_row(
        "standing_work", "standing_work", aim="higher", target=1, position=4
    ),
    _scorecard_row(
        "open_questions", "open_questions", aim="lower", target=0, position=5
    ),
    _scorecard_row(
        "first_reply", "intercom_first_reply", aim="higher", target=0.9, position=6
    ),
    _scorecard_row(
        "switched_off", "approvals", aim="higher", target=0.9, position=7, is_on=False
    ),
    _scorecard_row(
        "smuggled", "sql", aim="higher", target=0.9, position=8, query=_SMUGGLED
    ),
    # The same week's commitments, counted from the rows themselves.
    _scorecard_row(
        "work_done",
        "work",
        aim="higher",
        target=1,
        position=9,
        shape="share",
        unit_table="commitments",
        time_column="due",
        test="status = 'done'",
    ),
    # Drafted by the teammate and not kept: never scored.
    _scorecard_row(
        "work_proposed",
        "work",
        aim="lower",
        target=0,
        position=10,
        shape="count",
        unit_table="commitments",
        time_column="due",
        is_on=False,
        proposed_from="tell me what is still open",
    ),
]


# --- Setup through the API and the pod tools ------------------------------------


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"scorecard-{uuid4().hex[:8]}",
            "description": "score_week e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(authenticated_client, pod_id: str, name: str, columns) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": columns,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


def _run_ctx(*, user_id: str, pod_id: str) -> SimpleNamespace:
    """The pod default agent's run context, as ``run_context_builder`` makes it."""
    pod = UUID(pod_id)
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(user_id),
            pod_id=pod,
            conversation_id=uuid4(),
            workload_type="agent",
            workload_id=pod,
            agent_name="pod_default",
            is_pod_default_agent=is_pod_default_agent(pod, pod_id=pod),
        )
    )


async def _write(ctx: SimpleNamespace, table: str, data: dict[str, object]) -> None:
    written = await pod_write_record(
        ctx, PodWriteRecordRequest(action="create", table_name=table, data=data)
    )
    assert written["success"] is True, written


async def _seed_pod_tables(authenticated_client, ctx, pod_id: str, end: date) -> None:
    await _create_table(authenticated_client, pod_id, "scorecard", _SCORECARD_COLUMNS)
    for row in _SCORECARD_ROWS:
        await _write(ctx, "scorecard", row)
    await _create_table(
        authenticated_client,
        pod_id,
        "commitments",
        [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "due", "type": "DATE", "required": True},
            {"name": "status", "type": "TEXT", "required": False},
        ],
    )
    for days_before_end, state in (
        (6, "done"),  # in the week, followed up
        (2, "done"),  # in the week, followed up
        (1, "open"),  # in the week, not yet
        (10, "done"),  # before the week: neither followed_up nor overdue
    ):
        due = (end - timedelta(days=days_before_end)).isoformat()
        await _write(ctx, "commitments", {"due": due, "status": state})


# --- Rows seeded straight into the agent and schedule tables --------------------


@dataclass
class _Thread:
    conversation_id: UUID
    run_id: UUID
    sequence: int = 0


async def _thread(session, *, pod_id: UUID, user_id: UUID, since: datetime) -> _Thread:
    conversation = ConversationModel(user_id=user_id, pod_id=pod_id, title="e2e")
    session.add(conversation)
    await session.flush()
    run = AgentRunModel(
        conversation_id=conversation.id,
        status=AgentRunStatus.COMPLETED.value,
        agent_runtime={"profile_id": "system:lemma"},
        started_at=since,
    )
    session.add(run)
    await session.flush()
    return _Thread(conversation_id=conversation.id, run_id=run.id)


def _ask(session, thread: _Thread, tool_name: str, *, at: datetime) -> str:
    """A pausing tool call, asked at ``at``."""
    thread.sequence += 1
    call_id = f"call-{uuid4().hex[:12]}"
    session.add(
        MessageModel(
            conversation_id=thread.conversation_id,
            agent_run_id=thread.run_id,
            sequence=thread.sequence,
            role=MessageRole.ASSISTANT.value,
            kind=MessageKind.TOOL_CALL.value,
            tool_name=tool_name,
            tool_call_id=call_id,
            tool_args={"question": "may I?"},
            created_at=at,
        )
    )
    return call_id


def _decide(
    session,
    thread: _Thread,
    call_id: str,
    decision: AgentRunApprovalDecision,
    *,
    at: datetime,
    superseded: bool = False,
) -> None:
    session.add(
        AgentApprovalDecisionModel(
            conversation_id=thread.conversation_id,
            agent_run_id=thread.run_id,
            approval_id=call_id,
            tool_name="pod_write_record",
            decision=decision.value,
            response={SUPERSEDED_BY_NEW_MESSAGE: True} if superseded else {},
            created_at=at,
        )
    )


def _return(session, thread: _Thread, call_id: str, *, at: datetime) -> None:
    """A call answered by its return with no decision: one that never paused."""
    thread.sequence += 1
    session.add(
        MessageModel(
            conversation_id=thread.conversation_id,
            agent_run_id=thread.run_id,
            sequence=thread.sequence,
            role=MessageRole.TOOL.value,
            kind=MessageKind.TOOL_RETURN.value,
            tool_name="ask_user",
            tool_call_id=call_id,
            tool_result={"answer": "go ahead"},
            created_at=at,
        )
    )


async def _seed_conversations(
    session, *, pod_id: UUID, owner_id: UUID, member_id: UUID, end_at: datetime
) -> None:
    def before(**delta: float) -> datetime:
        return end_at - timedelta(**delta)

    approve, for_session, deny = (
        AgentRunApprovalDecision.APPROVE_ONCE,
        AgentRunApprovalDecision.APPROVE_FOR_SESSION,
        AgentRunApprovalDecision.DENY,
    )
    mine = await _thread(
        session, pod_id=pod_id, user_id=owner_id, since=before(days=30)
    )

    # Approvals: approved 2 (a1, a2) of decided 3 (a1, a2, a3).
    a1 = _ask(session, mine, "request_approval", at=before(days=3))
    _decide(session, mine, a1, approve, at=before(days=3, hours=-1))
    a2 = _ask(session, mine, "request_approval", at=before(days=3))
    _decide(session, mine, a2, for_session, at=before(days=2))
    a3 = _ask(session, mine, "request_approval", at=before(days=2))
    _decide(session, mine, a3, deny, at=before(days=2, hours=-1))
    # Nobody chose this DENY: the person moved on. Not a verdict.
    a4 = _ask(session, mine, "request_approval", at=before(days=2))
    _decide(session, mine, a4, deny, at=before(days=1), superseded=True)
    # An answered question is a decision row too, but not an approval.
    q1 = _ask(session, mine, "ask_user", at=before(days=2))
    _decide(session, mine, q1, approve, at=before(days=2, hours=-1))
    # Decided before the week began.
    a5 = _ask(session, mine, "request_approval", at=before(days=10))
    _decide(session, mine, a5, approve, at=before(days=9))

    # Open questions: still waiting 3 (q2, q4, q6) of asked-in-week 9
    # (a1, a2, a3, a4, q1, q2, q3, q5, q6).
    _ask(session, mine, "ask_user", at=before(days=3))  # q2: never answered
    _ask(session, mine, "ask_user", at=before(hours=12))  # q3: not a day yet
    _ask(session, mine, "request_approval", at=before(days=20))  # q4: old, waiting
    q5 = _ask(session, mine, "ask_user", at=before(days=4))
    _return(session, mine, q5, at=before(days=4, minutes=-1))
    q6 = _ask(session, mine, "ask_user", at=before(days=3))
    # Answered, but after the week closed: as of its end it was still waiting.
    _decide(session, mine, q6, approve, at=end_at + timedelta(hours=1))
    # A timer is not a person. Neither the call nor its wait row counts.
    wait_call = _ask(session, mine, WAIT_TOOL_NAME, at=before(days=3))
    session.add(
        AgentConversationWaitModel(
            conversation_id=mine.conversation_id,
            agent_run_id=mine.run_id,
            pod_id=pod_id,
            tool_call_id=wait_call,
            wait_type="TIME",
            status="ACTIVE",
            scheduled_at=end_at + timedelta(days=1),
            spec={},
            created_at=before(days=3),
        )
    )

    # Another member's private conversation: none of it is the owner's to see.
    theirs = await _thread(
        session, pod_id=pod_id, user_id=member_id, since=before(days=30)
    )
    b1 = _ask(session, theirs, "request_approval", at=before(days=2))
    _decide(session, theirs, b1, approve, at=before(days=2, hours=-1))
    _ask(session, theirs, "ask_user", at=before(days=3))


def _run(schedule_id: UUID, due: datetime, **fields: object) -> ScheduleRun:
    values: dict[str, object] = {
        "schedule_id": schedule_id,
        "source_event_id": f"e2e-{uuid4().hex}",
        "status": ScheduleRunStatus.DISPATCHED.value,
        "attempts": 1,
        "target_kind": "AGENT",
        "payload": {},
        "fire_metadata": {},
        "llm_output": {},
        "source_occurred_at": due,
        "started_at": due + timedelta(minutes=1),
    }
    values.update(fields)
    return ScheduleRun(**values)


async def _seed_schedule_runs(
    session, *, schedule_id: UUID, pod_id: UUID, owner_id: UUID, end_at: datetime
) -> None:
    """On time 1 (r1) of due 3 (r1, r2, r3)."""
    completed = ScheduleRunStatus.COMPLETED.value

    def due(days: int) -> datetime:
        return end_at - timedelta(days=days) + timedelta(hours=9)

    r1 = _run(schedule_id, due(6), target_outcome=completed)
    r2 = _run(  # completed, but forty minutes late
        schedule_id,
        due(5),
        target_outcome=completed,
        started_at=due(5) + timedelta(minutes=40),
    )
    r3 = _run(schedule_id, due(4), target_outcome=ScheduleRunStatus.TARGET_FAILED.value)
    session.add_all([r1, r2, r3])
    await session.flush()
    session.add_all(
        [
            # A filter deciding not to act is the schedule working.
            _run(schedule_id, due(3), status=ScheduleRunStatus.FILTERED.value),
            # A person re-running r3 does not make its occurrence due twice.
            _run(
                schedule_id, due(4), target_outcome=completed, redrive_of_run_id=r3.id
            ),
            # Before the week.
            _run(schedule_id, due(9), target_outcome=completed),
        ]
    )
    internal = Schedule(
        user_id=owner_id,
        pod_id=pod_id,
        schedule_type=ScheduleType.TIME,
        config={},
        is_internal=True,
        visibility="POD",
    )
    session.add(internal)
    await session.flush()
    # Workflow machinery, not standing work.
    session.add(_run(internal.id, due(2), target_outcome=completed))


async def _seed_everything(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
) -> tuple[str, SimpleNamespace, date]:
    end = datetime.now(timezone.utc).date()
    end_at = datetime.combine(end, time.min, tzinfo=timezone.utc)
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    ctx = _run_ctx(user_id=fixed_test_user["id"], pod_id=pod_id)
    await _seed_pod_tables(authenticated_client, ctx, pod_id, end)

    member = await signup_user(async_client, "scorecard-member")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=member
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_USER",
    )

    schedule = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "TIME",
            "agent_name": "POD_DEFAULT",
            "instruction": "Review the week against the scorecard.",
            "config": {"cron": "0 9 * * 1"},
        },
    )
    assert schedule.status_code == status.HTTP_201_CREATED, schedule.text

    async with async_session_maker() as session, session.begin():
        await _seed_conversations(
            session,
            pod_id=UUID(pod_id),
            owner_id=UUID(fixed_test_user["id"]),
            member_id=UUID(member["id"]),
            end_at=end_at,
        )
        await _seed_schedule_runs(
            session,
            schedule_id=UUID(schedule.json()["id"]),
            pod_id=UUID(pod_id),
            owner_id=UUID(fixed_test_user["id"]),
            end_at=end_at,
        )
    return pod_id, ctx, end


async def _week_rows(ctx: SimpleNamespace, end: date) -> dict[str, dict[str, object]]:
    listed = await pod_get_records(
        ctx,
        PodGetRecordsRequest(
            table_name="scorecard_weeks",
            limit=200,
            filters=[RecordFilter(column="week", op="eq", value=end.isoformat())],
        ),
    )
    assert listed["success"] is True, listed
    rows = listed["records"]
    keys = [row["key"] for row in rows]
    assert len(keys) == len(set(keys)), f"a week holds one row per measure: {keys}"
    return {row["key"]: row for row in rows}


# --- Tests ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_week_counts_each_measure_from_what_the_caller_may_see(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    pod_id, ctx, end = await _seed_everything(
        authenticated_client, async_client, fixed_test_org, fixed_test_user
    )

    result = await score_week(ctx, ScoreWeekRequest(end=end))

    assert result["success"] is True, result
    assert result["week"] == end.isoformat()
    assert result["start"] == (end - timedelta(days=7)).isoformat()
    scores = {score["key"]: score for score in result["measures"]}
    # Off rows -- a proposal nobody kept among them -- are not scored at all;
    # everything else is, in position order.
    assert [score["key"] for score in result["measures"]] == [
        "approved",
        "followed_up",
        "nothing_overdue",
        "standing_work",
        "open_questions",
        "first_reply",
        "smuggled",
        "work_done",
    ]

    approved = scores["approved"]
    assert (approved["counted"], approved["total"]) == (2, 3)
    # Three decisions against "9 in 10": counted, and too few to judge.
    assert approved["status"] == "too_few"
    assert approved["shown"] == "too few to judge (3)"
    assert approved["met"] is None

    assert scores["followed_up"]["shown"] == "too few to judge (3)"
    assert scores["nothing_overdue"]["shown"] == "none"
    assert scores["nothing_overdue"]["met"] is True

    standing = scores["standing_work"]
    assert (standing["counted"], standing["total"]) == (1, 3)
    assert standing["shown"] == "1 of 3"

    questions = scores["open_questions"]
    assert (questions["counted"], questions["total"]) == (3, 9)
    assert questions["shown"] == "3"
    assert questions["met"] is False

    assert scores["first_reply"]["status"] == "not_counted"
    assert scores["first_reply"]["counted"] is None

    smuggled = scores["smuggled"]
    assert smuggled["status"] == "failed"
    assert "single statement" in smuggled["reason"]
    # Refused before it ran: the table its second statement named is intact.
    still_there = await pod_query(
        ctx, QueryRequest(sql="SELECT count(*) AS n FROM commitments")
    )
    assert still_there["success"] is True, still_there
    assert still_there["rows"][0]["n"] == 4

    # "Every one" is judged however few: one of the three is still open.
    work_done = scores["work_done"]
    assert (work_done["counted"], work_done["total"]) == (2, 3)
    assert (work_done["shown"], work_done["met"]) == ("2 of 3", False)

    rows = await _week_rows(ctx, end)
    assert set(rows) == set(scores)
    assert rows["approved"]["shown"] == "too few to judge (3)"
    assert rows["approved"]["met"] is None
    assert rows["work_done"]["status"] == "counted"
    assert rows["standing_work"]["value"] == pytest.approx(1 / 3)
    assert rows["first_reply"]["status"] == "not_counted"
    assert result["rows_written"] == len(scores)

    described = await pod_tables(ctx, PodTablesRequest(table_name="scorecard_weeks"))
    assert described["table"]["rls_enabled"] is False


@pytest.mark.asyncio
async def test_score_week_replaces_the_weeks_rows_when_counted_again(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    pod_id, ctx, end = await _seed_everything(
        authenticated_client, async_client, fixed_test_org, fixed_test_user
    )
    first = await score_week(ctx, ScoreWeekRequest(end=end))
    assert first["success"] is True, first

    # A measure switched off since the last count loses its row for the week.
    scorecard = await pod_get_records(
        ctx,
        PodGetRecordsRequest(
            table_name="scorecard",
            filters=[RecordFilter(column="key", op="eq", value="first_reply")],
        ),
    )
    turned_off = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="update",
            table_name="scorecard",
            record_id=str(scorecard["records"][0]["id"]),
            data={"is_on": False},
        ),
    )
    assert turned_off["success"] is True, turned_off

    second = await score_week(ctx, ScoreWeekRequest(end=end))

    assert second["success"] is True, second
    rows = await _week_rows(ctx, end)
    assert "first_reply" not in rows
    assert set(rows) == {score["key"] for score in second["measures"]}
    assert rows["approved"]["shown"] == "too few to judge (3)"


@pytest.mark.asyncio
async def test_score_week_without_a_scorecard_says_so_and_writes_nothing(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    ctx = _run_ctx(user_id=fixed_test_user["id"], pod_id=pod_id)

    result = await score_week(ctx, ScoreWeekRequest())

    assert result["success"] is False
    assert "no `scorecard` table" in result["error"]
    tables = await pod_tables(ctx, PodTablesRequest())
    assert "scorecard_weeks" not in {table["name"] for table in tables["tables"]}
