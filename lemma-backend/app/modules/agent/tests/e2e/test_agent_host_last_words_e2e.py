"""A completed Agent Host run hands back the answer it ended on.

A run's output is what everything that waits on it reads as the result: a
workflow step, a schedule's ledger, a parent agent, a channel's reply, the CLI.
An Agent Host turn could finish COMPLETED with no output, its answer present in
the transcript as narration. The host announces a call its adapter opened but
never settled -- Claude Code reports a long tool call's progress under call ids
of its own, which never settle -- only once the turn is over, after the agent's
closing words; words followed by a call are sealed as a step on the way, so the
turn ended with nothing marked as its answer.

These drive the real harness over the real event intake, stream and lease, and
the real pump and finalizer writing what it yields, against Postgres: a run
that parked on an approval, was resumed by the decision and ended with such
calls open answers with its closing words, and no reader ever sees a run
COMPLETED before its output is its answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select, update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    UnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_tool_call_id,
)
from app.modules.agent.domain.entities import Agent, Message
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.harnesses.agent_host.harness import (
    AgentHostRunConfig,
    RemoteHarness,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    DispatchedRun,
)
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_finalizer import RunFinalizer
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    conversation,
    conversation_service,
    messages,
    owner_uow,
    running_run,
    runs,
    seeded_conversation,
    uow_factory,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

_ANSWER = "The desk is set up: every bill is checked, coded and queued for review."
_IMPORT = "toolu-import"
_WRITE = "toolu-write"


@asynccontextmanager
async def _units_watched(
    seen: list[tuple[str | None, object]], conversation_id: UUID
) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    """A unit of work that reads the conversation back after every commit."""
    async with uow_factory()() as uow:

        async def look() -> None:
            async with async_session_maker() as session:
                row = (
                    await session.execute(
                        select(
                            ConversationModel.status, ConversationModel.output_data
                        ).where(ConversationModel.id == conversation_id)
                    )
                ).one()
            seen.append((row.status, row.output_data))
            # ``commit`` takes the callbacks before running them; this unit
            # may commit again.
            uow.after_commit(look)

        uow.after_commit(look)
        yield uow


class _EveryCommit:
    """The run's units, and what the conversation read as after each commit.

    Each entry is what any reader -- ``conversations get``, a workflow step, a
    channel's reply -- could have seen between two of the run's transactions.
    """

    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        self.seen: list[tuple[str | None, object]] = []

    def __call__(self):
        return _units_watched(self.seen, self.conversation_id)


async def _on_a_host(scenario) -> tuple[Seeded, UUID, UUID, UUID]:
    """A conversation with an agent, mid-run on a paired machine's lease."""
    seeded = await seeded_conversation(scenario)
    machine = await paired_machine(
        scenario, display_name="last words e2e", harness_key="claude-code"
    )
    run_id = await running_run(seeded)
    await _lease(run_id, machine["host_id"], machine["harness_id"])
    async with async_session_maker() as session:
        # As `start` leaves it: the conversation is busy with this run.
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == seeded.conversation_id)
            .values(status=ConversationStatus.RUNNING.value)
        )
        await session.commit()
    return seeded, run_id, machine["host_id"], machine["harness_id"]


async def _lease(run_id: UUID, host_id: UUID, harness_id: UUID) -> None:
    """The run is RUNNING on that machine, as an accepted dispatch leaves it."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        session.add(
            AgentHostRunLeaseModel(
                run_id=run_id,
                host_id=host_id,
                harness_id=harness_id,
                lease_epoch=1,
                state=AgentHostRunState.RUNNING.value,
                accepted_at=now,
                lease_expires_at=now + timedelta(minutes=5),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def _host_sends(
    host_id: UUID, run_id: UUID, *events: tuple, after: int = 0
) -> None:
    """Events as the host's link delivers them, through the real intake,
    numbered on from ``after``, the last sequence already sent for the run."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await AgentHostDispatchRepository(uow).append_events(
            host_id=host_id,
            batch=AgentHostEventBatch(
                events=[
                    AgentHostEvent(
                        run_id=run_id,
                        lease_epoch=1,
                        sequence=sequence,
                        type=kind.value,
                        object_id=object_id,
                        payload=payload,
                        occurred_at=datetime.now(timezone.utc),
                    )
                    for sequence, (kind, payload, object_id) in enumerate(
                        events, start=after + 1
                    )
                ]
            ),
        )
        await uow.commit()


def _says(text: str, message_id: str) -> list[tuple]:
    """Streamed words, sealed: the host seals before every non-chunk event."""
    return [
        (AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": text}, message_id),
        (AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": text}, message_id),
    ]


def _lemma_exec(call_id: str, cmd: str) -> list[tuple]:
    """A Lemma exec call that ran and returned."""
    tool = {"name": "exec_command", "source": "lemma"}
    return [
        (AgentHostEventType.TOOL_CALL, {"tool": tool, "input": {"cmd": cmd}}, call_id),
        (
            AgentHostEventType.TOOL_CALL_RESULT,
            {"status": "completed", "output": {"stdout": "ok\n", "exit_code": 0}},
            call_id,
        ),
    ]


def _never_settled(call_id: str) -> tuple:
    """A call the adapter opened and never settled, as the host releases it once
    the turn is over: no input, and no result to follow."""
    return (
        AgentHostEventType.TOOL_CALL,
        {"tool": {"name": "exec_command", "source": "lemma"}, "input": None},
        call_id,
    )


def _ends(state: AgentHostRunState) -> tuple:
    return (AgentHostEventType.TERMINAL, {"state": state.value}, None)


def _worker(
    seeded: Seeded,
    run_id: UUID,
    *,
    units: UnitOfWorkFactory | None = None,
    parked_window_seconds: float | None = None,
) -> asyncio.Task[None]:
    """One worker job: the harness consuming the run, the pump and the
    finalizer writing what it yields."""
    units = units or uow_factory()
    harness = RemoteHarness(
        units,
        stream_block_ms=50,
        lease_check_seconds=0.2,
        parked_check_seconds=0.05,
        parked_window_seconds=parked_window_seconds,
    )

    async def drive() -> None:
        loaded = await conversation(seeded.conversation_id)
        events = harness._consume(
            agent_run_id=run_id,
            agent=Agent(
                id=seeded.agent_id,
                pod_id=seeded.pod_id,
                user_id=seeded.user_id,
                name=seeded.agent_name,
                instruction="Help.",
            ),
            ctx=BaseAgentContext(
                user_id=seeded.user_id,
                pod_id=seeded.pod_id,
                conversation_id=seeded.conversation_id,
                agent_run_id=run_id,
            ),
            conversation=loaded,
            options=HarnessOptions(model_name="claude-opus"),
            run_config=AgentHostRunConfig(
                harness_id=uuid7(),
                runtime_profile_id=uuid7(),
                config_selections={},
                wait_timeout_seconds=300,
                model_name=None,
            ),
            dispatch=DispatchedRun(
                harness_key="claude-code",
                event_timeout_seconds=60.0,
                credential_bounded=False,
            ),
        )
        pump = RunEventPump(
            RunMessageWriter(units), RunFinalizer(units, RunUsageRecorder(units))
        )
        await pump.drive(
            events,
            run=RunIdentity(
                conversation_id=seeded.conversation_id,
                agent_run_id=run_id,
                pod_id=seeded.pod_id,
                user_id=seeded.user_id,
                agent_id=seeded.agent_id,
            ),
            outcome=RunOutcome(),
            observer=None,
            conversation=loaded,
            ctx=None,
        )

    return asyncio.ensure_future(drive())


async def _cancel_requested(run_id: UUID) -> None:
    async def kinds() -> list[str]:
        async with async_session_maker() as session:
            found = await session.execute(
                select(AgentHostCommandModel.kind).where(
                    AgentHostCommandModel.run_id == run_id
                )
            )
            return list(found.scalars())

    await eventually(
        label="the host is asked to end the parked turn",
        probe=kinds,
        done=lambda found: AgentHostCommandKind.CANCEL_RUN.value in found,
    )


def _said(found: list[Message], text: str) -> Message:
    (message,) = [
        m
        for m in found
        if m.role is MessageRole.ASSISTANT
        and m.kind is MessageKind.TEXT
        and m.text == text
    ]
    return message


async def test_a_resumed_run_ending_with_calls_it_never_ran_answers_with_its_last_words(
    scenario,
):
    """The shape that lost the answer: a turn parked on a native write past
    its window, the decision resumed a new run, and that run's closing words
    were followed by calls the host released only as the turn ended."""
    seeded, run_id, host_id, harness_id = await _on_a_host(scenario)
    tool = {"name": "write_file", "source": "native", "kind": "edit"}
    write = {"file_path": "check_bill.py", "content": "print('ok')\n"}
    await _host_sends(
        host_id,
        run_id,
        (AgentHostEventType.TOOL_CALL, {"tool": tool, "input": write}, _WRITE),
        (
            AgentHostEventType.PERMISSION_REQUEST,
            {
                "request_id": _WRITE,
                "tool_call_id": _WRITE,
                "tool": tool,
                "input": write,
                "options": [
                    {"option_id": "once", "name": "Allow", "kind": "allow_once"},
                    {"option_id": "no", "name": "No", "kind": "reject_once"},
                ],
            },
            _WRITE,
        ),
    )
    parked = _worker(seeded, run_id, parked_window_seconds=1.0)
    await _cancel_requested(run_id)
    await _host_sends(host_id, run_id, _ends(AgentHostRunState.CANCELLED), after=2)
    await asyncio.wait_for(parked, timeout=30)
    assert (await conversation(seeded.conversation_id)).status is (
        ConversationStatus.WAITING
    )

    async with owner_uow(seeded) as uow:
        await conversation_service(uow).resolve_user_approval_internal(
            conversation=await conversation(seeded.conversation_id),
            approval_id=permission_approval_tool_call_id(_WRITE),
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
        )
    first, resumed = await runs(seeded.conversation_id)
    assert first.id == run_id
    await _lease(resumed.id, host_id, harness_id)
    worker = _worker(seeded, resumed.id)
    await _host_sends(
        host_id,
        resumed.id,
        *_says("Approval came through. Importing the desk.", "msg-1"),
        *_lemma_exec(_IMPORT, "lemma pods import ."),
        *_says(_ANSWER, "msg-2"),
        _never_settled(f"{_IMPORT}-heartbeat-0"),
        _never_settled(f"{_IMPORT}-heartbeat-1"),
        _ends(AgentHostRunState.SUCCEEDED),
    )
    await asyncio.wait_for(worker, timeout=30)

    _, ended = await runs(seeded.conversation_id)
    assert ended.status == AgentRunStatus.COMPLETED.value
    assert ended.output_data == {"answer": _ANSWER}
    found = await messages(seeded.conversation_id)
    answer = _said(found, _ANSWER)
    assert answer.metadata["is_final_answer"] is True
    assert "is_intermediate_assistant_message" not in answer.metadata
    assert answer.metadata["answer_from_last_words"] is True
    # The words before the work stay a step on the way.
    step = _said(found, "Approval came through. Importing the desk.")
    assert step.metadata["is_final_answer"] is False
    # The calls are still closed, as every call a run leaves open is.
    for heartbeat in (f"{_IMPORT}-heartbeat-0", f"{_IMPORT}-heartbeat-1"):
        (closed,) = [
            m
            for m in found
            if m.kind is MessageKind.TOOL_RETURN and m.tool_call_id == heartbeat
        ]
        assert closed.metadata["synthetic_tool_return"] is True

    # What `conversations get` answers, and so what the CLI and a script read.
    read = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}"
    )
    assert read.status_code == 200, read.text
    assert read.json()["status"] == ConversationStatus.COMPLETED.value
    assert read.json()["output"] == {"answer": _ANSWER}


async def test_words_a_tool_result_followed_were_a_step_and_are_no_answer(scenario):
    """The other side of the rule: words the agent followed with work that
    returned a result led up to that work, so a run that ends on the work has
    no answer to give -- even with a call it never ran after it."""
    seeded, run_id, host_id, _ = await _on_a_host(scenario)
    worker = _worker(seeded, run_id)
    await _host_sends(
        host_id,
        run_id,
        *_says("Importing the desk.", "msg-1"),
        *_lemma_exec(_IMPORT, "lemma pods import ."),
        _never_settled(f"{_IMPORT}-heartbeat-0"),
        _ends(AgentHostRunState.SUCCEEDED),
    )
    await asyncio.wait_for(worker, timeout=30)

    (ended,) = await runs(seeded.conversation_id)
    assert ended.status == AgentRunStatus.COMPLETED.value
    assert ended.output_data is None
    step = _said(await messages(seeded.conversation_id), "Importing the desk.")
    assert step.metadata["is_final_answer"] is False
    assert step.metadata["is_intermediate_assistant_message"] is True


@pytest.mark.parametrize(
    "ending",
    [
        pytest.param([], id="ends-on-its-words"),
        pytest.param(
            [_never_settled("toolu-long-heartbeat-0")],
            id="ends-after-a-call-it-never-ran",
        ),
    ],
)
async def test_a_run_never_reads_completed_before_its_answer_is_its_output(
    scenario, ending
):
    """Status and output move in one commit: whatever read the conversation
    between any two of the run's transactions saw it RUNNING, or COMPLETED
    with its answer -- never COMPLETED and waiting for one."""
    seeded, run_id, host_id, _ = await _on_a_host(scenario)
    watched = _EveryCommit(seeded.conversation_id)
    worker = _worker(seeded, run_id, units=watched)
    await _host_sends(
        host_id,
        run_id,
        *_lemma_exec("toolu-long", "lemma pods import ."),
        *_says(_ANSWER, "msg-1"),
        *ending,
        _ends(AgentHostRunState.SUCCEEDED),
    )
    await asyncio.wait_for(worker, timeout=30)

    completed = [
        output
        for status, output in watched.seen
        if status == ConversationStatus.COMPLETED.value
    ]
    assert completed, watched.seen
    assert completed[0] == {"answer": _ANSWER}
    (ended,) = await runs(seeded.conversation_id)
    assert ended.output_data == {"answer": _ANSWER}
