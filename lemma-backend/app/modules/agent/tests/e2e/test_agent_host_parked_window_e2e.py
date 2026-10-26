"""An Agent Host turn parked on a person is ended as WAITING, not held for hours.

A remote harness cannot pause inside a tool call, so a question it asks over
MCP, or a native permission request its host holds open, is parked inside a
run that stays RUNNING -- with its worker job -- until the person decides.
Pauses never expire, so that was the whole human wait: a worker slot held for
hours, every message typed meanwhile joined to a turn that could not read it,
and an "interrupted" run at the next deploy.

These drive the real harness over the real event intake, stream and lease, and
the real pump writing what it yields, against Postgres: a pause past the parked
window ends the turn with the host's own cancel and finishes the run WAITING
with the pause still listed; answering it then resumes a new run with the
decision; and a pause answered inside the window is answered inside the turn,
exactly as before.
"""

from __future__ import annotations

from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.db.pauses import Pauses

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select, update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import QueueState, RunTrigger
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    AgentRunApprovalDecision,
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.events.run_followup_work import start_queued_followup_for
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.harnesses.agent_host.harness import (
    AgentHostRunConfig,
    RemoteHarness,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.harnesses.agent_host.pre_approvals import (
    pre_approvals_for,
    supports_pre_approval,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    DispatchedRun,
)
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)
from app.modules.agent.services.mcp_pausing_calls import record_pausing_tool_call
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    conversation,
    resolve_pause,
    finish,
    messages,
    owner_uow,
    queued_message,
    running_run,
    runs,
    seeded_conversation,
    uow_factory,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

_QUESTION = {"questions": [{"question": "Which branch should I deploy?"}]}
_PERMISSION_ID = "agent-host-permission:native-shell"


class _Finalizer:
    """The run's terminal write, as ``RunFinalizer`` makes it.

    Only the write: the usage receipts and realtime frames around it are not
    what is under test, and need a usage recorder this suite has no use for.
    """

    async def finish(
        self,
        *,
        run: RunIdentity,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        **_: object,
    ) -> None:
        await finish(
            run.agent_run_id, status=status, conversation_status=conversation_status
        )


class _Observer:
    """Records the events a surface would be shown."""

    def __init__(self) -> None:
        self.events: list = []

    async def on_event(self, event, conversation, ctx) -> None:
        del conversation, ctx
        self.events.append(event)


async def _host_run(
    scenario, capabilities: dict | None = None
) -> tuple[Seeded, UUID, UUID]:
    """A conversation with an agent, mid-run on a paired machine's lease."""
    seeded = await seeded_conversation(scenario)
    machine = await paired_machine(
        scenario, display_name="parked window e2e", capabilities=capabilities
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
    return seeded, run_id, machine["host_id"]


async def _host_sends(host_id: UUID, run_id: UUID, *events: tuple) -> None:
    """Events as the host's link delivers them, through the real intake."""
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
                    for sequence, kind, payload, object_id in events
                ]
            ),
        )
        await uow.commit()


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


def _terminal(sequence: int, state: AgentHostRunState) -> tuple:
    return (sequence, AgentHostEventType.TERMINAL, {"state": state.value}, None)


def _worker(
    seeded: Seeded, run_id: UUID, observer: _Observer, **harness_options: float
) -> asyncio.Task[None]:
    """One worker job: the harness consuming the run, the pump writing it."""
    harness = RemoteHarness(
        uow_factory(),
        stream_block_ms=50,
        lease_check_seconds=0.2,
        parked_check_seconds=0.05,
        **harness_options,
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
            ctx=ToolContext(
                user_id=seeded.user_id,
                pod_id=seeded.pod_id,
                conversation_id=seeded.conversation_id,
                agent_run_id=run_id,
            ),
            conversation=loaded,
            options=HarnessOptions(model_name="gpt-5-codex"),
            run_config=AgentHostRunConfig(
                harness_id=uuid7(),
                runtime_profile_id=uuid7(),
                config_selections={},
                wait_timeout_seconds=300,
                model_name=None,
            ),
            dispatch=DispatchedRun(
                harness_key="codex",
                event_timeout_seconds=60.0,
                credential_bounded=False,
            ),
        )
        await RunEventPump(RunMessageWriter(uow_factory()), _Finalizer()).drive(
            events,
            run=RunIdentity(
                conversation_id=seeded.conversation_id, agent_run_id=run_id
            ),
            outcome=RunOutcome(),
            observer=observer,
            conversation=loaded,
            ctx=None,
        )

    return asyncio.ensure_future(drive())


async def _commands(run_id: UUID) -> list[str]:
    async with async_session_maker() as session:
        found = await session.execute(
            select(AgentHostCommandModel.kind)
            .where(AgentHostCommandModel.run_id == run_id)
            .order_by(AgentHostCommandModel.created_at)
        )
        return list(found.scalars())


async def _cancel_requested(run_id: UUID) -> list[str]:
    return await eventually(
        label="the host is asked to end the parked turn",
        probe=lambda: _commands(run_id),
        done=lambda kinds: AgentHostCommandKind.CANCEL_RUN.value in kinds,
    )


async def _completed(seeded: Seeded, run_id: UUID) -> UUID | None:
    """Run the run's ``agent.followup``, as its finalize writes it."""
    return await start_queued_followup_for(
        seeded.conversation_id, run_id, uow_factory=uow_factory()
    )


async def _pending(scenario) -> list[tuple[str, str]]:
    listed = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/conversations/pending-interactions"
    )
    assert listed.status_code == 200, listed.text
    return [(i["tool_call_id"], i["kind"]) for i in listed.json()["items"]]


async def _decide(
    seeded: Seeded, approval_id: str, decision: AgentRunApprovalDecision, **response
) -> None:
    async with owner_uow(seeded) as uow:
        await resolve_pause(uow).resolve(
            conversation=await conversation(seeded.conversation_id),
            approval_id=approval_id,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=decision,
            response=response or None,
        )


def _returns(found, tool_call_id: str) -> list:
    return [
        m
        for m in found
        if m.kind is MessageKind.TOOL_RETURN and m.tool_call_id == tool_call_id
    ]


async def test_a_question_left_past_the_window_ends_the_run_waiting(scenario):
    """The parked question outlives its turn: the run finishes WAITING, the
    worker is free, the question is still listed, and answering it later
    resumes a new run carrying the answer and what was said meanwhile."""
    seeded, run_id, host_id = await _host_run(scenario)
    question = await record_pausing_tool_call(
        uow_factory(),
        conversation_id=seeded.conversation_id,
        agent_run_id=run_id,
        tool_name="ask_user",
        pauses=PauseKind.QUESTION,
        arguments=_QUESTION,
    )
    # Asked eleven minutes ago, against the shipped ten-minute window.
    async with async_session_maker() as session:
        await session.execute(
            update(MessageModel)
            .where(MessageModel.tool_call_id == question)
            .values(created_at=MessageModel.created_at - timedelta(minutes=11))
        )
        await session.commit()
    typed = await queued_message(seeded, run_id, "Also run the migrations first.")

    observer = _Observer()
    worker = _worker(seeded, run_id, observer)
    await _cancel_requested(run_id)
    # The host ends the turn and reports what it saw: a cancelled turn.
    await _host_sends(host_id, run_id, _terminal(1, AgentHostRunState.CANCELLED))
    await asyncio.wait_for(worker, timeout=30)

    waiting = [e for e in observer.events if e.type is AgentEventType.WAITING]
    assert [(e.data["tool_call_id"], e.data["kind"]) for e in waiting] == [
        (question, "ask_user")
    ]
    assert not any(e.type is AgentEventType.STOPPED for e in observer.events)
    (ended,) = await runs(seeded.conversation_id)
    assert ended.status == AgentRunStatus.COMPLETED.value
    assert ended.suspended_while_parked["tool_call_id"] == question
    assert (await conversation(seeded.conversation_id)).status is (
        ConversationStatus.WAITING
    )

    # What was typed during the window waits on the answer, not a turn of its own.
    assert await _completed(seeded, run_id) is None
    held = next(m for m in await messages(seeded.conversation_id) if m.id == typed.id)
    assert held.state.queue_state is QueueState.WAITING_ON_ANSWER
    assert await _pending(scenario) == [(question, "QUESTION")]

    await _decide(
        seeded,
        question,
        AgentRunApprovalDecision.APPROVE_ONCE,
        answers={"Which branch should I deploy?": "main"},
    )

    first, resumed = await runs(seeded.conversation_id)
    assert first.id == run_id
    assert resumed.status == AgentRunStatus.RUNNING.value
    assert resumed.trigger is RunTrigger.RESUME
    assert resumed.resumed_tool_call_id == question
    found = await messages(seeded.conversation_id)
    (answer,) = _returns(found, question)
    assert answer.tool_result["answers"] == {"Which branch should I deploy?": "main"}
    claimed = next(m for m in found if m.id == typed.id)
    assert claimed.agent_run_id == resumed.id or (
        claimed.state.steered_into_run_id == resumed.id
    )
    assert await _pending(scenario) == []


async def test_a_permission_left_past_the_window_resumes_with_the_decision(scenario):
    """A native permission request the host holds open is ended the same way.
    The card is not closed with the turn, and the decision -- which can no
    longer reach the turn that asked -- tells the resumed agent so."""
    seeded, run_id, host_id = await _host_run(scenario)
    await _host_sends(
        host_id,
        run_id,
        (
            1,
            AgentHostEventType.TOOL_CALL,
            {
                "tool": {"name": "exec_command", "source": "native", "kind": "execute"},
                "input": {"cmd": "rm -rf build"},
            },
            "native-shell",
        ),
        (
            2,
            AgentHostEventType.PERMISSION_REQUEST,
            {
                "request_id": "native-shell",
                "tool_call_id": "native-shell",
                "tool": {"name": "exec_command", "source": "native", "kind": "execute"},
                "input": {"cmd": "rm -rf build"},
                "options": [
                    {"option_id": "once", "name": "Allow", "kind": "allow_once"},
                    {"option_id": "no", "name": "No", "kind": "reject_once"},
                ],
            },
            "native-shell",
        ),
    )

    observer = _Observer()
    worker = _worker(seeded, run_id, observer, parked_window_seconds=1.0)
    await _cancel_requested(run_id)
    await _host_sends(host_id, run_id, _terminal(3, AgentHostRunState.CANCELLED))
    await asyncio.wait_for(worker, timeout=30)

    assert [
        (e.data["tool_call_id"], e.data["kind"])
        for e in observer.events
        if e.type is AgentEventType.WAITING
    ] == [(_PERMISSION_ID, "request_approval")]
    assert _returns(await messages(seeded.conversation_id), _PERMISSION_ID) == []
    assert (await conversation(seeded.conversation_id)).status is (
        ConversationStatus.WAITING
    )
    assert await _completed(seeded, run_id) is None
    assert await _pending(scenario) == [(_PERMISSION_ID, "PERMISSION")]

    await _decide(seeded, _PERMISSION_ID, AgentRunApprovalDecision.APPROVE_ONCE)

    # Nothing was sent to a host that dropped the request with its turn.
    assert await _commands(run_id) == [AgentHostCommandKind.CANCEL_RUN.value]
    (decided,) = _returns(await messages(seeded.conversation_id), _PERMISSION_ID)
    assert decided.tool_result["message"].startswith("Approved. Your earlier turn")
    first, resumed = await runs(seeded.conversation_id)
    assert first.id == run_id
    assert resumed.resumed_tool_call_id == _PERMISSION_ID


async def test_a_question_answered_inside_the_window_is_answered_in_its_turn(
    scenario,
):
    """Today's behaviour, kept: an answer within the window reaches the turn
    that asked, which carries on and finishes; nothing is cancelled and no
    second run starts."""
    seeded, run_id, host_id = await _host_run(scenario)
    question = await record_pausing_tool_call(
        uow_factory(),
        conversation_id=seeded.conversation_id,
        agent_run_id=run_id,
        tool_name="ask_user",
        pauses=PauseKind.QUESTION,
        arguments=_QUESTION,
    )

    observer = _Observer()
    worker = _worker(seeded, run_id, observer)
    await _decide(
        seeded,
        question,
        AgentRunApprovalDecision.APPROVE_ONCE,
        answers={"Which branch should I deploy?": "main"},
    )
    await _host_sends(
        host_id,
        run_id,
        (1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Deploying main."}, None),
        (2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Deploying main."}, None),
        _terminal(3, AgentHostRunState.SUCCEEDED),
    )
    await asyncio.wait_for(worker, timeout=30)

    assert await _commands(run_id) == []
    assert not any(e.type is AgentEventType.WAITING for e in observer.events)
    (only,) = await runs(seeded.conversation_id)
    assert only.status == AgentRunStatus.COMPLETED.value
    assert only.suspended_while_parked is None
    assert len(_returns(await messages(seeded.conversation_id), question)) == 1
    assert await _completed(seeded, run_id) is None
    assert [r.id for r in await runs(seeded.conversation_id)] == [run_id]


def _shell_request(sequence: int, call_id: str, **extra: object) -> list[tuple]:
    """The host's gated call and its permission request, as it sends them."""
    tool = {"name": "exec_command", "source": "native", "kind": "execute"}
    command = {"cmd": "rm -rf build"}
    return [
        (
            sequence,
            AgentHostEventType.TOOL_CALL,
            {"tool": tool, "input": command},
            call_id,
        ),
        (
            sequence + 1,
            AgentHostEventType.PERMISSION_REQUEST,
            {
                "request_id": call_id,
                "tool_call_id": call_id,
                "tool": tool,
                "input": command,
                "options": [
                    {"option_id": "once", "name": "Allow", "kind": "allow_once"},
                    {"option_id": "no", "name": "No", "kind": "reject_once"},
                ],
                **extra,
            },
            call_id,
        ),
    ]


async def test_a_late_approval_is_carried_to_the_resumed_turn_once(scenario):
    """Approving after the turn ended no longer means approving twice: a host
    that honours pre-approvals is sent the decision with the resume run, says
    so to the agent, and the request the resumed turn raises again is recorded
    as approved earlier -- resolved, with nothing left waiting on anyone."""
    seeded, run_id, host_id = await _host_run(
        scenario, capabilities={"load_session": True, "pre_approval": True}
    )
    await _host_sends(host_id, run_id, *_shell_request(1, "native-shell"))
    worker = _worker(seeded, run_id, _Observer(), parked_window_seconds=1.0)
    await _cancel_requested(run_id)
    await _host_sends(host_id, run_id, _terminal(3, AgentHostRunState.CANCELLED))
    await asyncio.wait_for(worker, timeout=30)

    await _decide(seeded, _PERMISSION_ID, AgentRunApprovalDecision.APPROVE_ONCE)

    (decided,) = _returns(await messages(seeded.conversation_id), _PERMISSION_ID)
    assert (
        "allowed once without asking the person again"
        in (decided.tool_result["message"])
    )
    first, resumed = await runs(seeded.conversation_id)
    # What the resume run's START_RUN carries, from what is on the record.
    async with uow_factory()() as uow:
        resumed_pause = await Pauses(uow).get(
            conversation_id=seeded.conversation_id,
            tool_call_id=resumed.resumed_tool_call_id,
        )
    carried = pre_approvals_for(resumed_pause)
    assert [approval.model_dump(mode="json") for approval in carried] == [
        {
            "approval_id": _PERMISSION_ID,
            "tool_name": "exec_command",
            "input": {"cmd": "rm -rf build"},
            "decision": "APPROVE_ONCE",
        }
    ]

    async with async_session_maker() as session:
        lease = await session.get(AgentHostRunLeaseModel, first.id)
        assert lease is not None
        harness_id = lease.harness_id
    await _lease(resumed.id, host_id, harness_id)
    observer = _Observer()
    worker = _worker(seeded, resumed.id, observer)
    await _host_sends(
        host_id,
        resumed.id,
        *_shell_request(
            1, "native-shell-2", pre_approved={"approval_id": _PERMISSION_ID}
        ),
        _terminal(3, AgentHostRunState.SUCCEEDED),
    )
    await asyncio.wait_for(worker, timeout=30)

    again = "agent-host-permission:native-shell-2"
    (allowed,) = _returns(await messages(seeded.conversation_id), again)
    assert allowed.tool_result["message"].startswith("Allowed without asking again")
    assert allowed.tool_result["response"] == {"approved_earlier": _PERMISSION_ID}
    assert not any(
        e.type is AgentEventType.WAITING
        or (e.type is AgentEventType.STATUS and e.data.get("tool_call_id") == again)
        for e in observer.events
    )
    assert await _pending(scenario) == []


async def test_a_late_approval_is_not_carried_to_a_host_that_cannot_honour_it(
    scenario,
):
    """An older host is sent nothing it would ignore, and the agent is told
    the truth for it: it will be asked again."""
    seeded, run_id, host_id = await _host_run(scenario)
    await _host_sends(host_id, run_id, *_shell_request(1, "native-shell"))
    worker = _worker(seeded, run_id, _Observer(), parked_window_seconds=1.0)
    await _cancel_requested(run_id)
    await _host_sends(host_id, run_id, _terminal(3, AgentHostRunState.CANCELLED))
    await asyncio.wait_for(worker, timeout=30)

    await _decide(seeded, _PERMISSION_ID, AgentRunApprovalDecision.APPROVE_ONCE)

    (decided,) = _returns(await messages(seeded.conversation_id), _PERMISSION_ID)
    assert "will be asked to allow it once more" in decided.tool_result["message"]
    async with async_session_maker() as session:
        lease = await session.get(AgentHostRunLeaseModel, run_id)
        assert lease is not None
        harness_id = lease.harness_id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        harness = await AgentHostRepository(uow).get_harness(harness_id=harness_id)
    assert harness is not None
    assert not supports_pre_approval(harness.capabilities)
