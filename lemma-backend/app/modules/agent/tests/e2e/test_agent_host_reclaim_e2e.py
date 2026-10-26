"""A run reclaimed by another worker picks up where the last one stopped.

A worker stopped mid-run -- a deploy, a SIGTERM -- leaves the run RUNNING, and
the next worker drives the same dispatch. It used to read the run's event
stream from its first entry with a fresh normalizer, and every narration, tool
call and tool return already in the conversation was appended a second time.

This drives the real harness over the real intake, stream and lease twice: a
first worker that persists what it is given and is then stopped while the agent
is mid-sentence, and a second that takes the run over to its end. Every message
the turn produced must be on the record exactly once, the sentence the first
worker was cut off in included.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select

from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.agent_host.domain.host import (
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    ConversationStatus,
    ConversationType,
    MessageKind,
)
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.event_stream import (
    agent_host_event_stream,
    run_events_stream_key,
)
from app.modules.agent.agent_host.harness.harness import (
    RemoteHarness,
)
from app.modules.agent.infrastructure.models import MessageModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e


def _events(run_id: UUID, *specs: tuple) -> AgentHostEventBatch:
    return AgentHostEventBatch(
        events=[
            AgentHostEvent(
                run_id=run_id,
                lease_epoch=1,
                sequence=sequence,
                type=kind,
                object_id=object_id,
                payload=payload,
            )
            for sequence, kind, payload, object_id in specs
        ]
    )


def _call(sequence: int, call_id: str, name: str) -> tuple:
    return (
        sequence,
        AgentHostEventType.TOOL_CALL,
        {"tool": {"name": name, "source": "native"}, "input": {"file_path": name}},
        call_id,
    )


def _result(sequence: int, call_id: str) -> tuple:
    return (
        sequence,
        AgentHostEventType.TOOL_CALL_RESULT,
        {"status": "completed", "output": {"content": "ok"}},
        call_id,
    )


def _text(sequence: int, kind: AgentHostEventType, text: str) -> tuple:
    return (sequence, kind, {"text": text}, None)


class _Worker:
    """One worker driving the run: the harness, and the runner's persistence."""

    def __init__(self, *, conversation: Conversation, run_id: UUID):
        # Its own sessions, as a worker has: it runs beside the test's probes.
        factory = SessionUnitOfWorkFactory(async_session_maker)
        self.harness = RemoteHarness(factory, stream_block_ms=50)
        self.writer = RunMessageWriter(factory)
        self.conversation = conversation
        self.run_id = run_id

    def events(self, harness_id: UUID):
        return self.harness.run(
            agent=Agent(
                id=uuid7(),
                pod_id=self.conversation.pod_id,
                user_id=self.conversation.user_id,
                name="helper",
                instruction="Help.",
            ),
            conversation=self.conversation,
            messages=[],
            ctx=ToolContext(
                user_id=self.conversation.user_id,
                pod_id=self.conversation.pod_id,
                conversation_id=self.conversation.id,
            ),
            options=HarnessOptions(
                model_name="gpt-5-codex",
                runtime_profile=RuntimeSnapshot(
                    profile_id=str(uuid7()),
                    profile_name="Laptop",
                    harness_id=str(harness_id),
                    scope=RuntimeProfileScope.PERSONAL,
                    protocol=RuntimeProfileProtocol.AGENT_HOST,
                    config={"harness_snapshot_revision": "rev-1"},
                ),
            ),
            agent_run_id=self.run_id,
        )

    async def drive(self, harness_id: UUID) -> None:
        async for event in self.events(harness_id):
            await self.persist(event)

    async def persist(self, event) -> None:
        """What the runner does with a message: put it on the record."""
        if event.type is AgentEventType.MESSAGE:
            await self.writer.persist(
                conversation_id=self.conversation.id,
                agent_run_id=self.run_id,
                data=event.data,
            )


async def test_a_reclaimed_run_records_each_event_once(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Reclaim")
    machine = await paired_machine(scenario, display_name="reclaim e2e")
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        state=AgentHostRunState.RUNNING,
        title="reclaim",
    )
    await db_session.commit()
    conversation = Conversation(
        id=conversation_id,
        pod_id=UUID(scenario.pod_id),
        user_id=UUID(scenario.owner_user["id"]),
        title="reclaim",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
    )
    intake = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    chunk, upsert = (
        AgentHostEventType.AGENT_MESSAGE_CHUNK,
        AgentHostEventType.AGENT_MESSAGE_UPSERT,
    )
    await intake.append_events(
        host_id=machine["host_id"],
        batch=_events(
            run_id,
            _text(1, AgentHostEventType.AGENT_THOUGHT_CHUNK, "Look first."),
            _text(2, chunk, "Let me look. "),
            _text(3, upsert, "Let me look. "),
            _call(4, "call-1", "read_file"),
            _result(5, "call-1"),
            _text(6, chunk, "The first file is fine; "),
        ),
    )

    # The first worker persists everything it is handed, and is stopped -- as
    # a deploy stops it -- once it is waiting on the agent's next words.
    first = _Worker(conversation=conversation, run_id=run_id)
    worker = asyncio.ensure_future(first.drive(machine["harness_id"]))
    stream = agent_host_event_stream()._client()

    async def progress() -> tuple[str | None, list]:
        db_session.expire_all()
        lease = await db_session.get(AgentHostRunLeaseModel, run_id)
        entries = await stream.xrange(run_events_stream_key(run_id))
        return lease.consumed_stream_id, entries

    # The cursor is written after each entry that puts a message on the record,
    # so it passes through the tool call's entry on its way to the tool
    # result's. Waiting only for "trimmed to the cursor" caught it between the
    # two whenever the worker was slower than the probe. What the worker can
    # reach before it has to wait for more words is the tool result.
    last_persistable = "5"
    cursor, remaining = await eventually(
        label="the first worker records how far it persisted, and trims below it",
        probe=progress,
        done=lambda seen: (
            seen[0] is not None
            and bool(seen[1])
            and seen[1][0][0] == seen[0]
            and seen[1][0][1]["sequence"] == last_persistable
        ),
    )
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker
    # Trimmed below the cursor and never past it: the entry the cursor names
    # and the sentence nobody has persisted are both still there.
    assert [entry[0] for entry in remaining][0] == cursor
    assert len(remaining) == 2

    await intake.append_events(
        host_id=machine["host_id"],
        batch=_events(
            run_id,
            _text(7, chunk, "so is the second."),
            _text(8, upsert, "The first file is fine; so is the second."),
            _call(9, "call-2", "grep"),
            _result(10, "call-2"),
            _text(11, chunk, "Done."),
            _text(12, upsert, "Done."),
            (13, AgentHostEventType.TERMINAL, {"state": "SUCCEEDED"}, None),
        ),
    )
    second = _Worker(conversation=conversation, run_id=run_id)
    last = None
    async with asyncio.timeout(30):
        async for event in second.events(machine["harness_id"]):
            await second.persist(event)
            last = event
    assert last is not None and last.type is AgentEventType.COMPLETED

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(MessageModel)
            .where(MessageModel.agent_run_id == run_id)
            .order_by(MessageModel.sequence)
        )
    ).scalars()
    rows = list(rows)
    sequences = Counter(
        (row.kind, row.message_metadata.get("agent_host_sequence"))
        for row in rows
        if row.message_metadata and "agent_host_sequence" in row.message_metadata
    )
    assert sequences and all(count == 1 for count in sequences.values()), sequences
    assert Counter(
        (row.kind, row.tool_call_id) for row in rows if row.tool_call_id
    ) == {
        (MessageKind.TOOL_CALL.value, "call-1"): 1,
        (MessageKind.TOOL_RETURN.value, "call-1"): 1,
        (MessageKind.TOOL_CALL.value, "call-2"): 1,
        (MessageKind.TOOL_RETURN.value, "call-2"): 1,
    }
    texts = [
        row.text for row in rows if row.kind == MessageKind.TEXT.value and row.text
    ]
    assert texts == [
        "Let me look. ",
        "The first file is fine; so is the second.",
        "Done.",
    ]
    thoughts = [row.text for row in rows if row.kind == MessageKind.THINKING.value]
    assert thoughts == ["Look first."]
