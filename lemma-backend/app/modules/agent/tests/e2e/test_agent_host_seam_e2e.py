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
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

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
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.harnesses.agent_host import (
    RemoteHarness,
    _AgentHostRunConfig,
)
from app.modules.agent.infrastructure.harnesses.agent_host_run_window import (
    DispatchedRun,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostCommandModel
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.agent.tools.context import BaseAgentContext

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
    persisted = "".join(m.text for m in messages if m.text and m.tool_call_id is None)

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
                at(2, AgentHostEventType.AGENT_MESSAGE_UPSERT, {"text": "One moment. "}),
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
    approvals = [m for m in messages if m.tool_name == "request_approval"]
    assert len(approvals) == 1, "the pause must surface as an ordinary approval"
    assert approvals[0].tool_call_id == "agent-host-permission:native-shell"

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
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        found = (
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
        if found is not None:
            return found
        await db_session.rollback()
        await asyncio.sleep(0.1)
    return None
