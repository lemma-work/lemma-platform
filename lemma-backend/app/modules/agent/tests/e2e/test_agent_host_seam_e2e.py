"""One agent turn, from host events to the events Lemma acts on.

Every half of this path was tested and the seam between them was not. The host
crate proves an ACP agent reaches a stub control plane; the normalizer proves an
envelope becomes the right ``AgentEvent``; ``append_events`` proves a batch is
accepted. Nothing drove the real intake path into the real Redis stream and out
through the real consume loop, so a disagreement between what the host emits and
what the normalizer accumulates could not be caught — which is exactly how a
message could lose the text preceding a tool call while every test stayed green.

This feeds the event sequence a real Codex turn produces, through the shipped
intake and the shipped reader, and asserts on what a conversation ends up with.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostEvent,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    ConversationStatus,
    ConversationType,
    HarnessOptions,
    MessageKind,
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.harnesses.agent_host import (
    RemoteHarness,
    _AgentHostRunConfig,
)
from app.modules.agent.infrastructure.harnesses.agent_host_dispatch import (
    _resumed_tool_call_id,
)
from app.modules.agent.infrastructure.harnesses.agent_host_run_window import (
    DispatchedRun,
)
from app.modules.agent.domain.pausing_tools import SNOOZE_TOOL_NAME
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.mcp_pausing_calls import (
    close_pausing_tool_call,
    record_pausing_tool_call,
)
from app.modules.agent.services.snooze_wake_service import SnoozeWakeService
from app.modules.agent.tools.snooze.models import SnoozeRequest
from app.modules.agent.tools.snooze.pydantic_adapter import snooze
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

# A 1x1 PNG, so the artifact path is exercised with bytes that really are one.
_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
        "00000049454e44ae426082"
    )
).decode()


def _event(sequence: int, kind: AgentHostEventType, payload: dict, **extra):
    return AgentHostEvent(
        run_id=extra["run_id"],
        lease_epoch=1,
        sequence=sequence,
        type=kind,
        object_id=extra.get("object_id"),
        payload=payload,
    )


def _turn(run_id: UUID) -> list[AgentHostEvent]:
    """What the host emits for: think, speak, call a tool, speak, finish.

    The upserts are what the host actually sends — each carries only the text
    since the previous one, because it seals and clears its buffer before every
    non-chunk event.
    """
    at = lambda n, k, p, **kw: _event(n, k, p, run_id=run_id, **kw)  # noqa: E731
    return [
        at(1, AgentHostEventType.AGENT_THOUGHT_CHUNK, {"text": "Checking the file."}),
        at(2, AgentHostEventType.AGENT_THOUGHT_UPSERT, {"text": "Checking the file."}),
        at(3, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Let me look. "}),
        at(4, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Let me look. "}),
        at(
            5,
            AgentHostEventType.TOOL_CALL_UPSERT,
            {"title": "read_file", "rawInput": {"path": "README.md"}},
            object_id="call-1",
        ),
        at(
            6,
            AgentHostEventType.TOOL_CALL_UPDATE,
            {"status": "COMPLETED", "result": "# Lemma"},
            object_id="call-1",
        ),
        at(7, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "It is the readme."}),
        at(8, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "It is the readme."}),
        at(
            9,
            AgentHostEventType.USAGE_UPDATE,
            {"usage": {"input_tokens": 120, "output_tokens": 34}},
        ),
        at(
            10,
            AgentHostEventType.TERMINAL,
            {"state": AgentHostRunState.SUCCEEDED.value, "stop_reason": "end_turn"},
        ),
    ]


async def _seed_run(db_session, scenario, pod_id: UUID) -> tuple[UUID, UUID, UUID]:
    """A conversation with one accepted, leased run, as dispatch leaves it."""
    machine = await paired_machine(scenario, display_name="e2e seam")
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        state=AgentHostRunState.RUNNING,
        title="seam",
    )
    return conversation_id, run_id, machine["host_id"]


def _conversation(conversation_id: UUID, pod_id: UUID) -> Conversation:
    return Conversation(
        id=conversation_id,
        pod_id=pod_id,
        user_id=uuid7(),
        agent_id=uuid7(),
        title="seam",
        type=ConversationType.CHAT,
        status=ConversationStatus.RUNNING,
    )


def _agent(pod_id: UUID) -> Agent:
    return Agent(
        id=uuid7(), pod_id=pod_id, user_id=uuid7(), name="helper", instruction="Help."
    )


async def _drive(harness, *, run_id, agent, conversation, ctx) -> list:
    """Run the real consume loop to its terminal event."""
    collected = []
    async for event in harness._consume(
        agent_run_id=run_id,
        agent=agent,
        ctx=ctx,
        conversation=conversation,
        options=HarnessOptions(model_name="gpt-5-codex"),
        run_config=_AgentHostRunConfig(
            harness_id=uuid7(),
            runtime_profile_id=uuid7(),
            config_selections={},
            wait_timeout_seconds=300,
            model_name=None,
        ),
        dispatch=DispatchedRun(
            harness_key="codex",
            event_timeout_seconds=30.0,
            credential_bounded=False,
        ),
    ):
        collected.append(event)
    return collected


@pytest.mark.asyncio
async def test_a_whole_turn_survives_the_trip_from_host_to_conversation(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="Seam")
    pod_id = scenario.pod_id
    conversation_id, run_id, host_id = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    ack = await repository.append_events(
        host_id=host_id,
        batch=AgentHostEventBatch(events=_turn(run_id)),
    )
    assert ack.acked_through == 10

    events = await _drive(
        RemoteHarness(lambda: SqlAlchemyUnitOfWork(db_session)),
        run_id=run_id,
        agent=_agent(pod_id),
        conversation=_conversation(conversation_id, pod_id),
        ctx=BaseAgentContext(
            user_id=uuid7(), pod_id=pod_id, conversation_id=conversation_id
        ),
    )

    streamed = "".join(
        event.data["data"]
        for event in events
        if event.type is AgentEventType.TOKEN and event.data["kind"] == "text"
    )
    messages = [event.data for event in events if event.type is AgentEventType.MESSAGE]
    # kind == TEXT: the agent's thinking ("Checking the file.") is correctly
    # persisted too, as its own MessageKind.THINKING draft (_flush_messages) --
    # a real, separate record, not part of the answer. streamed already
    # excludes it via its own kind == "text" filter above; persisted must
    # match, or a thought landing between the two message chunks looks like a
    # lost/reordered answer instead of the working-as-designed split it is.
    persisted = "".join(
        m.text
        for m in messages
        if m.text and m.tool_call_id is None and m.kind == MessageKind.TEXT
    )

    # The whole answer, not just the part after the tool call.
    assert streamed == "Let me look. It is the readme."
    assert "Let me look. It is the readme." in persisted

    # The tool use is one call with one matching result.
    calls = [m for m in messages if m.tool_call_id and m.tool_args is not None]
    returns = [m for m in messages if m.tool_call_id and m.tool_result is not None]
    assert [c.tool_call_id for c in calls] == ["call-1"]
    assert [r.tool_call_id for r in returns] == ["call-1"]

    assert any(event.type is AgentEventType.USAGE for event in events)
    assert events[-1].type is AgentEventType.COMPLETED


@pytest.mark.asyncio
async def test_the_stream_carries_a_permission_request_without_ending_the_run(
    db_session, scenario
):
    """A permission pause happens *inside* a live run, so it must not terminate
    it — and the text before it must survive into the finished message."""
    await scenario.create_org_with_pod(name_prefix="SeamPermission")
    pod_id = scenario.pod_id
    conversation_id, run_id, host_id = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    at = lambda n, k, p, **kw: _event(n, k, p, run_id=run_id, **kw)  # noqa: E731
    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    await repository.append_events(
        host_id=host_id,
        batch=AgentHostEventBatch(
            events=[
                at(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "One moment. "}),
                at(
                    2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "One moment. "}
                ),
                at(
                    3,
                    AgentHostEventType.PERMISSION_REQUEST,
                    {
                        "toolCall": {"title": "Run rm -rf build", "kind": "execute"},
                        "options": [
                            {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "no", "name": "No", "kind": "reject_once"},
                        ],
                    },
                    object_id="native-shell",
                ),
                at(4, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Done."}),
                at(5, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "Done."}),
                at(
                    6,
                    AgentHostEventType.TERMINAL,
                    {"state": AgentHostRunState.SUCCEEDED.value},
                ),
            ]
        ),
    )

    events = await _drive(
        RemoteHarness(lambda: SqlAlchemyUnitOfWork(db_session)),
        run_id=run_id,
        agent=_agent(pod_id),
        conversation=_conversation(conversation_id, pod_id),
        ctx=BaseAgentContext(
            user_id=uuid7(), pod_id=pod_id, conversation_id=conversation_id
        ),
    )

    messages = [event.data for event in events if event.type is AgentEventType.MESSAGE]
    approvals = [
        m
        for m in messages
        if m.tool_name == "request_approval" and m.kind is MessageKind.TOOL_CALL
    ]
    assert len(approvals) == 1, "the pause must surface as an ordinary approval"
    assert approvals[0].tool_call_id == "agent-host-permission:native-shell"

    # The run here ends while the permission is still outstanding, so the call
    # is closed with a synthetic return rather than left dangling — a tool call
    # with no return renders as a spinner that never stops.
    returns = [
        m
        for m in messages
        if m.tool_name == "request_approval" and m.kind is MessageKind.TOOL_RETURN
    ]
    assert len(returns) == 1, "the unanswered approval must be closed out"
    assert returns[0].tool_call_id == approvals[0].tool_call_id
    assert returns[0].tool_result["interaction_fallback"] is True

    # A permission pause is not a WAITING run: it continues once answered.
    assert not any(event.type is AgentEventType.WAITING for event in events)
    assert events[-1].type is AgentEventType.COMPLETED

    text = "".join(m.text for m in messages if m.text and m.tool_call_id is None)
    assert "One moment." in text and "Done." in text


class _RecordingArtifactWriter:
    """Stands in for the pod-file writer, recording what it was handed.

    Deliberately not the real one: persisting a pod file builds a directory
    tree, which cannot run nested inside the transaction this test holds open.
    What is under test here is the *seam* — that an image content block reaches
    a writer at all and that its markdown is folded back into the message. The
    writer's own decoding and validation are covered by its unit tests, and
    that it is actually constructed in production by
    ``test_agent_host_registry_wiring``.
    """

    def __init__(self) -> None:
        self.mime_types: list[str] = []

    async def materialize_event(self, *, payload, **_kwargs):
        from app.modules.agent.infrastructure.harnesses.agent_host_artifacts import (
            AgentHostArtifactMaterialization,
            _inline_images,
        )

        images = _inline_images(payload)
        self.mime_types.extend(image.mime_type for image in images)
        return AgentHostArtifactMaterialization(
            markdown="\n\n".join(
                f"![Generated image](agent-output/chart-{index}.png)"
                for index, _ in enumerate(images, start=1)
            )
        )


@pytest.mark.asyncio
async def test_an_image_the_agent_produced_reaches_the_conversation(
    db_session, scenario
):
    """The host publishes generated images as content blocks, and a content
    block with no text renders to nothing. Until the writer was wired, every
    image an agent produced arrived here and was dropped in silence."""
    await scenario.create_org_with_pod(name_prefix="SeamImage")
    pod_id = scenario.pod_id
    conversation_id, run_id, host_id = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    at = lambda n, k, p, **kw: _event(n, k, p, run_id=run_id, **kw)  # noqa: E731
    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    await repository.append_events(
        host_id=host_id,
        batch=AgentHostEventBatch(
            events=[
                at(
                    1,
                    AgentHostEventType.AGENT_MESSAGE_CHUNK,
                    {
                        "content": {
                            "type": "image",
                            "data": _PNG,
                            "mimeType": "image/png",
                        },
                        "filename": "chart.png",
                    },
                    object_id="generated-image-1",
                ),
                at(
                    2,
                    AgentHostEventType.TERMINAL,
                    {"state": AgentHostRunState.SUCCEEDED.value},
                ),
            ]
        ),
    )

    writer = _RecordingArtifactWriter()
    events = await _drive(
        RemoteHarness(
            lambda: SqlAlchemyUnitOfWork(db_session),
            artifact_writer=writer,
        ),
        run_id=run_id,
        agent=_agent(pod_id),
        conversation=_conversation(conversation_id, pod_id),
        ctx=BaseAgentContext(
            user_id=uuid7(), pod_id=pod_id, conversation_id=conversation_id
        ),
    )

    assert writer.mime_types == ["image/png"], (
        "the image content block never reached the artifact writer"
    )
    text = "".join(
        event.data.text
        for event in events
        if event.type is AgentEventType.MESSAGE and event.data.text
    )
    assert ".png" in text, (
        f"the generated image never reached the conversation: {text!r}"
    )


class _Clock:
    """A wall clock the test moves, so an hour-wide window fits in a test."""

    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.mark.asyncio
async def test_a_run_outlives_the_credential_it_was_dispatched_with(
    db_session, scenario
):
    """The Lemma token a run is dispatched with is valid for an hour and used
    verbatim by a process on someone's laptop. Nothing renewed it, so a long
    turn either had to be cut short at that expiry or carry on with every
    ``lemma_*`` call returning 401 — the agent's tools quietly vanishing
    part-way through the task.

    The clock is injected rather than waited out: the window under test is an
    hour wide and only the credential is measured against it. The run is left
    genuinely in flight while that happens, because renewal is something that
    has to occur *during* a turn.
    """
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

    await scenario.create_org_with_pod(name_prefix="SeamCredential")
    pod_id = scenario.pod_id
    conversation_id, run_id, host_id = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    # Mid-turn output, and deliberately no terminal event yet.
    at = lambda n, k, p, **kw: _event(n, k, p, run_id=run_id, **kw)  # noqa: E731
    await AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session)).append_events(
        host_id=host_id,
        batch=AgentHostEventBatch(
            events=[
                at(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "Working. "}),
            ]
        ),
    )
    await db_session.commit()

    clock = _Clock()
    # The harness gets its own sessions, as it does in production, so its lease
    # checks cannot race the assertions this test makes on `db_session`.
    harness = RemoteHarness(
        SessionUnitOfWorkFactory(async_session_maker),
        clock=clock,
        lease_check_seconds=0.0,
        stream_block_ms=50,
    )
    dispatch = DispatchedRun(
        harness_key="codex",
        event_timeout_seconds=60.0,
        credential_bounded=False,
        # Already inside the renewal margin; time then moves past this entirely.
        credential_expires_at=clock.now + timedelta(minutes=5),
    )
    clock.advance(minutes=4)

    events: list = []

    async def drive() -> None:
        async for event in harness._consume(
            agent_run_id=run_id,
            agent=_agent(pod_id),
            ctx=BaseAgentContext(
                user_id=UUID(scenario.owner_user["id"]),
                pod_id=pod_id,
                conversation_id=conversation_id,
            ),
            conversation=_conversation(conversation_id, pod_id),
            options=HarnessOptions(model_name="gpt-5-codex"),
            run_config=_AgentHostRunConfig(
                harness_id=uuid7(),
                runtime_profile_id=uuid7(),
                config_selections={},
                wait_timeout_seconds=300,
                model_name=None,
            ),
            dispatch=dispatch,
        ):
            events.append(event)

    running = asyncio.create_task(drive())
    try:
        queued = await _await_refresh_command(db_session, run_id)
    finally:
        # However that went, let the run finish rather than leaking the task.
        await harness.events.append(
            run_id=run_id,
            events=[
                {
                    "sequence": 2,
                    "type": AgentHostEventType.TERMINAL.value,
                    "object_id": None,
                    "payload": {"state": AgentHostRunState.SUCCEEDED.value},
                }
            ],
        )
        await asyncio.wait_for(running, timeout=30)

    assert queued is not None, (
        "no replacement credential was issued, so the run would have been cut "
        "short at the expiry it was dispatched with"
    )
    assert "encrypted_mcp" in queued.payload, (
        "a credential must never be queued in the clear"
    )
    # The run ended on its own terms rather than on its credential.
    assert events[-1].type is AgentEventType.COMPLETED, (
        f"the run was ended by its credential, not its work: {events[-1].data!r}"
    )


async def _await_refresh_command(db_session, run_id: UUID, *, timeout: float = 20.0):
    """The REFRESH_CREDENTIAL row, once the running turn has queued it."""

    async def probe():
        # The rollback is load-bearing, not cleanup: it releases this session's
        # snapshot so the *next* query can see the row committed by the
        # harness's own (different) session -- without it, this session keeps
        # reading its original snapshot and never observes that commit.
        # Running it before the first query too (a no-op there) keeps the
        # ordering simple: every read in this loop is preceded by one.
        await db_session.rollback()
        return (
            (
                await db_session.execute(
                    select(AgentHostCommandModel).where(
                        AgentHostCommandModel.run_id == run_id,
                        AgentHostCommandModel.kind
                        == AgentHostCommandKind.REFRESH_CREDENTIAL.value,
                    )
                )
            )
            .scalars()
            .first()
        )

    try:
        return await eventually(
            label=f"REFRESH_CREDENTIAL command for run {run_id}",
            probe=probe,
            done=lambda found: found is not None,
            timeout_seconds=timeout,
            interval_seconds=0.1,
        )
    except pytest.fail.Exception:
        return None


@pytest.mark.asyncio
async def test_a_snoozing_agent_ends_its_turn_waiting_and_wakes_where_it_left_off(
    db_session, scenario
):
    """The whole sleep, on the real seam: tool, turn, wake.

    `snooze` is the one tool an agent uses to say "not now, later". In-process it
    raises and the run loop catches it. A remote harness cannot be interrupted
    from inside its own MCP tool call, so this is the path where every step is
    different: Lemma has to end the turn, read a stopped run as a sleeping one,
    and start the woken run itself.

    The failure this guards is silent in every piece and only visible whole — a
    turn that never stops leaves a run active, and the wake finds one and does
    nothing, so the agent sleeps forever and the conversation looks busy.
    """
    await scenario.create_org_with_pod(name_prefix="Snooze")
    pod_id = scenario.pod_id
    conversation_id, run_id, host_id = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    ctx = BaseAgentContext(
        user_id=UUID(scenario.owner_user["id"]),
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        # False for every Agent Host run: nothing here catches a raised pause.
        supports_pause_signal=False,
    )
    tool_call_id = await record_pausing_tool_call(
        lambda: SqlAlchemyUnitOfWork(db_session),
        conversation_id=conversation_id,
        agent_run_id=run_id,
        tool_name=SNOOZE_TOOL_NAME,
        arguments={"reason": "waiting for the nightly build", "seconds": 600},
    )
    await db_session.commit()

    answer = await snooze(
        SimpleNamespace(deps=ctx, tool_call_id=tool_call_id),
        SnoozeRequest(
            reason="waiting for the nightly build",
            seconds=600,
            note_to_self="check whether it went green",
        ),
    )
    assert answer.success is True

    # The host was really told to stop, on the real lease.
    commands = (
        (
            await db_session.execute(
                select(AgentHostCommandModel).where(
                    AgentHostCommandModel.run_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [command.kind for command in commands] == [
        AgentHostCommandKind.CANCEL_RUN.value
    ]

    # ... and the host stops, reporting what it saw. Which is not what happened.
    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))
    ack = await repository.append_events(
        host_id=host_id,
        batch=AgentHostEventBatch(
            events=[
                AgentHostEvent(
                    run_id=run_id,
                    lease_epoch=1,
                    sequence=1,
                    type=AgentHostEventType.TERMINAL.value,
                    payload={
                        "state": AgentHostRunState.CANCELLED.value,
                        "stop_reason": "cancelled",
                    },
                    occurred_at=datetime.now(timezone.utc),
                )
            ]
        ),
    )
    assert ack.acked_through == 1

    events = await _drive(
        RemoteHarness(lambda: SqlAlchemyUnitOfWork(db_session)),
        run_id=run_id,
        agent=_agent(pod_id),
        conversation=_conversation(conversation_id, pod_id),
        ctx=ctx,
    )
    # Not STOPPED, which reads as "the user pressed Stop" and leaves the
    # conversation looking finished while a timer counts down to wake it.
    assert events[-1].type is AgentEventType.WAITING
    assert events[-1].data["tool_call_id"] == tool_call_id

    # The run has to be over before the timer fires, or the wake sees a live run
    # and quietly declines to start a second one.
    await ConversationRepository(SqlAlchemyUnitOfWork(db_session)).finish_agent_run(
        agent_run_id=run_id, status=AgentRunStatus.COMPLETED
    )
    await db_session.commit()

    uow = SqlAlchemyUnitOfWork(db_session)
    waits = AgentConversationWaitRepository(uow)
    wait = await waits.find_active_for_run(run_id)
    assert wait is not None and wait.tool_call_id == tool_call_id

    woke = await SnoozeWakeService(uow).wake(wait=wait)
    await db_session.commit()
    assert woke is True

    runs = (
        (
            await db_session.execute(
                select(AgentRunModel)
                .where(AgentRunModel.conversation_id == conversation_id)
                .order_by(AgentRunModel.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 2, "the timer did not start the run that carries on"
    assert runs[-1].run_metadata["source"] == "snooze_resume"
    # Named so the dispatch can prompt the woken agent with what it woke to,
    # rather than re-sending a request its provider session already holds.
    assert runs[-1].run_metadata["resumed_tool_call_id"] == tool_call_id
    assert _resumed_tool_call_id(runs[-1].to_entity()) == tool_call_id

    messages, _ = await ConversationRepository(uow).list_messages(
        conversation_id=conversation_id, limit=50
    )
    returned = [
        message
        for message in messages
        if message.kind is MessageKind.TOOL_RETURN
        and message.tool_call_id == tool_call_id
    ]
    assert len(returned) == 1, "the wake wrote no return, or wrote two"
    assert returned[0].tool_result["woke_because"] == "TIMER"
    assert returned[0].tool_result["note_to_self"] == "check whether it went green"


@pytest.mark.asyncio
async def test_a_rejected_snooze_does_not_strand_the_one_that_follows(
    db_session, scenario
):
    """A pausing call that answers at once must not stay open.

    `snooze(5)` is rejected on purpose — waking replays the conversation, so a
    poll loop costs more than it saves. But the call is on the record by then,
    and `start_resume_run_if_ready` refuses to resume a run while any pausing
    call in it is outstanding. So the rejected one would sit there and the real
    snooze after it would wake to a resume that quietly declines to start: an
    agent asleep forever, with nothing reporting a failure anywhere.
    """
    await scenario.create_org_with_pod(name_prefix="Snooze")
    pod_id = scenario.pod_id
    conversation_id, run_id, _ = await _seed_run(db_session, scenario, pod_id)
    await db_session.commit()

    uow_factory = lambda: SqlAlchemyUnitOfWork(db_session)  # noqa: E731
    ctx = BaseAgentContext(
        user_id=UUID(scenario.owner_user["id"]),
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        supports_pause_signal=False,
    )

    rejected_id = await record_pausing_tool_call(
        uow_factory,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        tool_name=SNOOZE_TOOL_NAME,
        arguments={"reason": "polling", "seconds": 5},
    )
    refusal = await snooze(
        SimpleNamespace(deps=ctx, tool_call_id=rejected_id),
        SnoozeRequest(reason="polling", seconds=5),
    )
    assert refusal.success is False
    await close_pausing_tool_call(
        uow_factory,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        tool_call_id=rejected_id,
        tool_name=SNOOZE_TOOL_NAME,
        result=refusal,
    )
    await db_session.commit()

    real_id = await record_pausing_tool_call(
        uow_factory,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        tool_name=SNOOZE_TOOL_NAME,
        arguments={"reason": "the nightly build", "seconds": 600},
    )
    slept = await snooze(
        SimpleNamespace(deps=ctx, tool_call_id=real_id),
        SnoozeRequest(reason="the nightly build", seconds=600),
    )
    assert slept.success is True
    await close_pausing_tool_call(
        uow_factory,
        conversation_id=conversation_id,
        agent_run_id=run_id,
        tool_call_id=real_id,
        tool_name=SNOOZE_TOOL_NAME,
        result=slept,
    )
    await ConversationRepository(uow_factory()).finish_agent_run(
        agent_run_id=run_id, status=AgentRunStatus.COMPLETED
    )
    await db_session.commit()

    uow = uow_factory()
    wait = await AgentConversationWaitRepository(uow).find_active_for_run(run_id)
    assert wait is not None and wait.tool_call_id == real_id
    assert await SnoozeWakeService(uow).wake(wait=wait) is True
    await db_session.commit()

    runs = (
        (
            await db_session.execute(
                select(AgentRunModel).where(
                    AgentRunModel.conversation_id == conversation_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(runs) == 2, "the rejected snooze held the real one's wake shut"

    # And closing the rejected one did not also close the real one: the return
    # under the sleeping call has to be the wake, not the acknowledgement the
    # model already read. `append_pause_tool_return` is idempotent, so a return
    # written early is the wake never being written at all.
    messages, _ = await ConversationRepository(uow).list_messages(
        conversation_id=conversation_id, limit=50
    )
    returns = {
        message.tool_call_id: message.tool_result
        for message in messages
        if message.kind is MessageKind.TOOL_RETURN
    }
    assert returns[real_id]["woke_because"] == "TIMER"
    assert returns[rejected_id]["success"] is False


@pytest.mark.asyncio
async def test_full_dispatch_admits_polls_and_completes_a_run(
    db_session, scenario
):
    """The whole dispatch seam: admit -> poll START_RUN -> append -> consume.

    Earlier seam tests drive ``_consume`` directly and seed the lease by hand,
    so ``agent_host_admission`` (admit exactly once, refuse conflicts) and the
    repository's ``poll_commands`` handout were never exercised together. This
    goes through the real admission, hands the START_RUN out the way a host's
    poll does, appends a real terminal event batch, and then consumes to
    completion — all in-process and deterministic.
    """
    from app.modules.agent.domain.agent_host import AgentHostRunSpec

    await scenario.create_org_with_pod(name_prefix="Dispatch")
    pod_id = scenario.pod_id
    machine = await paired_machine(scenario, display_name="dispatch e2e")

    # A fresh run with no lease yet; admission creates the lease and the
    # START_RUN command together.
    created = await scenario.owner_client.post(
        f"/pods/{pod_id}/conversations", json={"title": "dispatch"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.commit()
    run_id = run.id

    # A real harness-bound runtime profile row the lease can reference.
    profile = AgentRuntimeProfileModel(
        organization_id=UUID(scenario.org_id),
        runtime_type="HARNESS",
        harness_id=machine["harness_id"],
        user_id=UUID(scenario.owner_user["id"]),
        scope="PERSONAL",
        kind="HARNESS",
        protocol="AGENT_HOST",
        name=f"Dispatch profile {uuid4().hex[:8]}",
        default_model_name="gpt-5-codex",
        model_catalog=[],
        config={"harness_id": str(machine["harness_id"]), "config_options": []},
    )
    db_session.add(profile)
    await db_session.flush()
    await db_session.commit()
    profile_id = profile.id

    uow = SqlAlchemyUnitOfWork(db_session)
    repository = AgentHostDispatchRepository(uow)
    now = datetime.now(timezone.utc)
    run_spec = AgentHostRunSpec(
        agent_run_id=run_id,
        conversation_id=conversation_id,
        harness_id=machine["harness_id"],
        profile_revision="rev-1",
        model_name="gpt-5-codex",
        config_selections={},
        system_prompt="Dispatch test system prompt.",
        prompt=[{"role": "user", "content": "Say the dispatch secret."}],
        run_deadline=now + timedelta(minutes=30),
    )
    admitted = await repository.enqueue_run(
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        runtime_profile_id=profile_id,
        run_spec=run_spec,
        encrypted_mcp_payload={"encrypted": True},
        now=now,
    )
    assert admitted.kind == AgentHostCommandKind.START_RUN
    # Admission is exactly-once: re-admitting the same run returns the same command.
    again = await repository.enqueue_run(
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        runtime_profile_id=profile_id,
        run_spec=run_spec,
        encrypted_mcp_payload={},
        now=now,
    )
    assert again.id == admitted.id
    await db_session.commit()

    # The host polls and is handed the START_RUN, and its lease is advanced.
    polled = await repository.poll_commands(
        host_id=machine["host_id"],
        limit=10,
        acknowledged_command_ids=[],
        checkpoints=[],
        rejections=[],
        available_run_slots=1,
        now=now,
    )
    started = [c for c in polled if c.kind == AgentHostCommandKind.START_RUN]
    assert len(started) == 1, list(polled)
    assert "encrypted_mcp" in started[0].payload
    await db_session.commit()

    # The host appends a real terminal turn, then the harness consumes it.
    ack = await repository.append_events(
        host_id=machine["host_id"],
        batch=AgentHostEventBatch(
            events=[
                _event(1, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "The "}, run_id=run_id),
                _event(2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "The "}, run_id=run_id),
                _event(3, AgentHostEventType.AGENT_MESSAGE_CHUNK, {"text": "secret."}, run_id=run_id),
                _event(4, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "secret."}, run_id=run_id),
                _event(
                    5,
                    AgentHostEventType.TERMINAL,
                    {"state": AgentHostRunState.SUCCEEDED.value, "stop_reason": "end_turn"},
                    run_id=run_id,
                ),
            ]
        ),
    )
    assert ack.acked_through == 5
    await db_session.commit()

    events = await _drive(
        RemoteHarness(lambda: SqlAlchemyUnitOfWork(db_session)),
        run_id=run_id,
        agent=_agent(pod_id),
        conversation=_conversation(conversation_id, pod_id),
        ctx=BaseAgentContext(
            user_id=UUID(scenario.owner_user["id"]),
            pod_id=pod_id,
            conversation_id=conversation_id,
        ),
    )
    streamed = "".join(
        event.data["data"]
        for event in events
        if event.type is AgentEventType.TOKEN and event.data["kind"] == "text"
    )
    assert "The secret." in streamed
    assert events[-1].type is AgentEventType.COMPLETED
