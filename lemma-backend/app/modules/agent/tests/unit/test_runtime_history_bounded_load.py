"""Loading only the history that gets sent must select what loading it all did.

The runtime prompt keeps every message of the five most recent runs and elides
each older run to its first and last. It used to get there by loading the whole
transcript and discarding most of it -- p90 271 messages at run time, p99 13688,
each carrying its text and its tool argument and result JSON -- so a long
conversation was slow to start for no benefit.

The loader now returns older runs already reduced to two messages, carrying
``total_message_count`` so the elision notice and the surface budget still know
how big the run really was. These tests pin the equivalence: for the same
conversation, the bounded shape and the full shape must select the same
messages, with the same counts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.modules.agent.domain.entities import (
    AgentRun,
    AgentRuntimeConfig,
    Message,
    MessageKind,
    MessageRole,
)
from app.modules.agent.services.agent_runner_service import (
    FULL_HISTORY_AGENT_RUN_COUNT,
    AgentRunnerService,
)

_BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _run(conversation_id: UUID, run_index: int, message_count: int) -> AgentRun:
    run_id = uuid4()
    return AgentRun(
        id=run_id,
        conversation_id=conversation_id,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        started_at=_BASE + timedelta(minutes=run_index),
        messages=[
            Message(
                conversation_id=conversation_id,
                sequence=(run_index * 1000) + index,
                agent_run_id=run_id,
                role=(
                    MessageRole.USER.value
                    if index == 0
                    else MessageRole.ASSISTANT.value
                ),
                kind=MessageKind.TEXT,
                text=f"run {run_index} message {index}",
                created_at=_BASE + timedelta(minutes=run_index, seconds=index),
            )
            for index in range(message_count)
        ],
    )


def _as_bounded(runs: list[AgentRun], full_run_count: int) -> list[AgentRun]:
    """What the repository returns: recent runs whole, older ones first+last."""
    full_ids = {run.id for run in runs[-full_run_count:]} if full_run_count else set()
    bounded: list[AgentRun] = []
    for run in runs:
        ordered = run.ordered_messages()
        keep = (
            ordered
            if run.id in full_ids or len(ordered) <= 2
            else [ordered[0], ordered[-1]]
        )
        bounded.append(
            run.model_copy(
                update={"messages": list(keep), "total_message_count": len(ordered)}
            )
        )
    return bounded


def _runner() -> AgentRunnerService:
    return AgentRunnerService(uow_factory=object(), harness_registry=object())


def _fingerprint(messages: list[Message]) -> list[tuple]:
    return [
        (
            message.agent_run_id,
            message.role,
            message.text,
            (message.metadata or {}).get("elided_message_count"),
        )
        for message in messages
    ]


def _surface_conversation():
    return SimpleNamespace(
        id=uuid4(), metadata={"surface_platform": "slack"}, is_pod_assistant=False
    )


def _shapes() -> list[list[AgentRun]]:
    conversation_id = uuid4()
    return [
        # Fewer runs than the full-history window: nothing is elided at all.
        [_run(conversation_id, i, 5) for i in range(3)],
        # Exactly the window.
        [_run(conversation_id, i, 5) for i in range(FULL_HISTORY_AGENT_RUN_COUNT)],
        # One over, which is where elision starts.
        [_run(conversation_id, i, 5) for i in range(FULL_HISTORY_AGENT_RUN_COUNT + 1)],
        # The shape that hurt in production: a long tail of old runs.
        [_run(conversation_id, i, 8) for i in range(40)],
        # Runs at the elision boundary -- one and two messages stay whole.
        [_run(conversation_id, i, (i % 3) + 1) for i in range(12)],
        # An empty run in the middle, which has no first or last message.
        [
            _run(conversation_id, 0, 4),
            _run(conversation_id, 1, 0),
            _run(conversation_id, 2, 6),
            *[_run(conversation_id, i, 3) for i in range(3, 9)],
        ],
    ]


def test_bounded_history_selects_what_the_full_load_selected() -> None:
    runner = _runner()
    for index, runs in enumerate(_shapes()):
        full = _fingerprint(runner._select_runtime_history(runs))
        bounded = _fingerprint(
            runner._select_runtime_history(
                _as_bounded(runs, FULL_HISTORY_AGENT_RUN_COUNT)
            )
        )
        assert bounded == full, f"shape {index} diverged"


def test_bounded_history_matches_on_surface_conversations(monkeypatch) -> None:
    """The surface budget counts messages, so it must count unloaded ones too."""
    import app.composition.agent_surface_runtime as surface_runtime

    monkeypatch.setattr(surface_runtime, "surface_history_limits", lambda: (40, 24))
    runner = _runner()
    conversation = _surface_conversation()
    for index, runs in enumerate(_shapes()):
        full = _fingerprint(runner._select_runtime_history(runs, conversation))
        bounded = _fingerprint(
            runner._select_runtime_history(
                _as_bounded(runs, FULL_HISTORY_AGENT_RUN_COUNT), conversation
            )
        )
        assert bounded == full, f"surface shape {index} diverged"


def test_the_elision_notice_counts_messages_that_were_never_loaded() -> None:
    """The regression a naive bounded load would introduce.

    With only two messages in hand, ``len(messages) - 2`` is zero and the notice
    would claim nothing was skipped.
    """
    conversation_id = uuid4()
    runs = [_run(conversation_id, i, 9) for i in range(FULL_HISTORY_AGENT_RUN_COUNT + 1)]
    bounded = _as_bounded(runs, FULL_HISTORY_AGENT_RUN_COUNT)

    selected = _runner()._select_runtime_history(bounded)

    notices = [
        message
        for message in selected
        if (message.metadata or {}).get("summary_kind") == "agent_run_middle_elision"
    ]
    assert len(notices) == 1
    assert notices[0].metadata["elided_message_count"] == 7


def test_a_run_reports_its_real_size_not_the_loaded_one() -> None:
    conversation_id = uuid4()
    run = _run(conversation_id, 0, 9)
    assert run.message_count == 9  # nothing elided: falls back to what is loaded

    bounded = _as_bounded([run], full_run_count=0)[0]
    assert len(bounded.messages) == 2
    assert bounded.message_count == 9
