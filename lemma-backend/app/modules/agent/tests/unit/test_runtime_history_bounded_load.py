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
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.runtime_history import FULL_HISTORY_AGENT_RUN_COUNT
from app.modules.agent.services.runtime_history import runtime_full_run_ids
from app.modules.agent.services.runtime_history import select_runtime_history

# Relative to now, not a fixed date: the surface age window is measured against
# `datetime.now()`, so a hard-coded base silently stops exercising the window a
# day after it is written -- every shape collapses to one or two runs and the
# comparison still passes while testing nothing.
_BASE = datetime.now(timezone.utc) - timedelta(hours=1)


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


def _as_bounded(runs: list[AgentRun], conversation=None) -> list[AgentRun]:
    """What the two-phase load returns.

    Mirrors production: digests first (sizes and newest-message times, no
    payloads), the trim decides which runs need every message, then messages are
    attached. Selecting `full_ids` positionally instead is the bug this file
    exists to catch.
    """
    digests = [
        run.model_copy(
            update={
                "messages": [],
                "total_message_count": len(run.messages),
                "newest_message_at": max(
                    (
                        message.created_at
                        for message in run.messages
                        if message.created_at is not None
                    ),
                    default=None,
                ),
            }
        )
        for run in runs
    ]
    full_ids = runtime_full_run_ids(digests, conversation)
    bounded: list[AgentRun] = []
    for run, digest in zip(runs, digests):
        ordered = run.ordered_messages()
        if run.id in full_ids or len(ordered) <= 2:
            keep = list(ordered)
        else:
            # Three reads in production: the run's first, its last, and every
            # user message in it. Anything less here and the double certifies a
            # shape the database never returns.
            by_id = {
                message.id: message
                for message in (
                    [ordered[0], ordered[-1]]
                    + [m for m in ordered if m.role is MessageRole.USER]
                )
            }
            keep = sorted(by_id.values(), key=lambda message: message.sequence)
        bounded.append(digest.model_copy(update={"messages": list(keep)}))
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
        bounded = _fingerprint(runner._select_runtime_history(_as_bounded(runs)))
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
                _as_bounded(runs, conversation), conversation
            )
        )
        assert bounded == full, f"surface shape {index} diverged"


def test_an_old_run_that_is_still_active_keeps_all_of_its_messages(monkeypatch) -> None:
    """The age window is a filter, not a truncation.

    A run created long ago whose newest message is recent survives the window
    while runs created after it do not, so the trimmed list is NOT a suffix of
    the loaded one. Deciding which runs to load whole from position alone
    therefore drops messages from a run the trim then keeps in full -- and
    because the trimmed list is short, the elision branch never runs and no
    notice is emitted. Silent loss, which is the part that matters.
    """
    import app.composition.agent_surface_runtime as surface_runtime

    monkeypatch.setattr(surface_runtime, "surface_history_limits", lambda: (0, 24))
    conversation_id = uuid4()
    now = datetime.now(timezone.utc)

    def _at(run_index: int, created_hours_ago: float, message_hours_ago: float):
        run = _run(conversation_id, run_index, 6)
        run.started_at = now - timedelta(hours=created_hours_ago)
        for offset, message in enumerate(run.messages):
            message.created_at = now - timedelta(
                hours=message_hours_ago, seconds=-offset
            )
        return run

    runs = [
        _at(0, created_hours_ago=40, message_hours_ago=1),  # old run, still active
        *[_at(i, created_hours_ago=39 - i, message_hours_ago=30) for i in range(1, 9)],
        _at(9, created_hours_ago=0.1, message_hours_ago=0.1),
    ]
    conversation = _surface_conversation()
    runner = _runner()

    full = _fingerprint(runner._select_runtime_history(runs, conversation))
    bounded = _fingerprint(
        runner._select_runtime_history(_as_bounded(runs, conversation), conversation)
    )

    assert bounded == full


def test_the_elision_notice_counts_messages_that_were_never_loaded() -> None:
    """The regression a naive bounded load would introduce.

    With only two messages in hand, ``len(messages) - 2`` is zero and the notice
    would claim nothing was skipped.
    """
    conversation_id = uuid4()
    runs = [
        _run(conversation_id, i, 9) for i in range(FULL_HISTORY_AGENT_RUN_COUNT + 1)
    ]
    bounded = _as_bounded(runs)

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

    # Six distinct runs, so the first falls outside the full-history window and
    # comes back elided to its first and last message.
    older = _as_bounded([run, *(_run(conversation_id, i, 3) for i in range(1, 6))])[0]
    assert len(older.messages) == 2
    assert older.message_count == 9


class TestAnElidedRunNeverFabricatesAnInterruptedTool:
    """The failure this guards is a duplicate send, not a cosmetic one.

    Eliding an old run keeps its first and last message. That is fine until the
    first message is an assistant tool call, which is the normal shape for a run
    with no user message -- an approval resume and a snooze wake both create a
    run and go straight into a tool. The call's return is elided away, the
    history builder finds it unpaired, and synthesizes:

        "This tool call was interrupted before a result was recorded, so it
         returned nothing. Run it again if you still need the result."

    So the model is told a `send_email` that succeeded never happened, and is
    told to repeat it.

    A pausing tool is never in this position -- its return is appended to the
    run it ended, so such a run is two messages long and exempt from elision --
    which is why the `PAUSING_TOOL_NAMES` exemption does not cover this.
    """

    @staticmethod
    def _resume_run(conversation_id: UUID, run_index: int) -> AgentRun:
        """A run with no user message that opens on a tool call that succeeded."""
        run_id = uuid4()
        base = run_index * 1000
        return AgentRun(
            id=run_id,
            conversation_id=conversation_id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            started_at=_BASE + timedelta(minutes=run_index),
            # Six in reality; the loader hands us only the first and last.
            total_message_count=6,
            messages=[
                Message(
                    conversation_id=conversation_id,
                    sequence=base,
                    agent_run_id=run_id,
                    role=MessageRole.ASSISTANT.value,
                    kind=MessageKind.TOOL_CALL,
                    tool_name="send_email",
                    tool_call_id="call-send-1",
                ),
                Message(
                    conversation_id=conversation_id,
                    sequence=base + 5,
                    agent_run_id=run_id,
                    role=MessageRole.ASSISTANT.value,
                    kind=MessageKind.TEXT,
                    text="Email sent.",
                ),
            ],
        )

    def _history(self) -> tuple[list[Message], UUID]:
        conversation_id = uuid4()
        old = self._resume_run(conversation_id, 0)
        recent = [
            _run(conversation_id, index, 2)
            for index in range(1, FULL_HISTORY_AGENT_RUN_COUNT + 1)
        ]
        return select_runtime_history([old, *recent], None), old.id

    def test_the_unpaired_call_is_dropped_rather_than_kept(self):
        selected, old_run_id = self._history()

        kept = [
            message
            for message in selected
            if message.agent_run_id == old_run_id
            and message.kind is MessageKind.TOOL_CALL
        ]
        assert kept == [], (
            "an unpaired tool call survived elision, so the history builder "
            "will tell the model to run it again"
        )

    def test_the_run_still_reports_what_it_did(self):
        """Dropping the head must not cost the outcome or the summary."""
        selected, old_run_id = self._history()

        from_old = [m for m in selected if m.agent_run_id == old_run_id]
        assert any(m.kind is MessageKind.NOTIFICATION for m in from_old), (
            "the elision notice went missing"
        )
        assert any("Email sent." in (m.text or "") for m in from_old), (
            "the run's own outcome went missing"
        )

    def test_a_leading_user_message_is_still_kept(self):
        """The ordinary shape is untouched: only an unpaired call is dropped."""
        conversation_id = uuid4()
        old = _run(conversation_id, 0, 6)
        recent = [
            _run(conversation_id, index, 2)
            for index in range(1, FULL_HISTORY_AGENT_RUN_COUNT + 1)
        ]

        selected = select_runtime_history([old, *recent], None)

        from_old = [m for m in selected if m.agent_run_id == old.id]
        assert len(from_old) == 3, from_old


class TestTheUsersOwnMessagesAreNeverElided:
    """The request is the one thing a later turn cannot reconstruct.

    An agent that has lost it does not stop and ask -- it invents a plausible
    substitute from whatever context is left and reports that as the thing it
    was asked for. In the incident this suite grew from, a request for a
    3Blue1Brown explainer became an hour spent building an unrelated promo reel,
    and the agent then told the user that reel was what they had asked for.
    """

    def _conversation_with_a_mid_run_question(self) -> tuple[UUID, list[AgentRun]]:
        conversation_id = uuid4()
        runs = [
            _run(conversation_id, index, message_count=9)
            for index in range(FULL_HISTORY_AGENT_RUN_COUNT + 2)
        ]
        # A follow-up the person typed while the run was still working. It is
        # not the run's first message and not its last, so first-and-last
        # elision dropped it entirely.
        oldest = runs[0]
        # The enum member, not its value: `model_copy` skips validation, and
        # `MessageRole` is a str enum, so a raw "user" would compare unequal to
        # the member every `is` check in production uses.
        oldest.messages[4] = oldest.messages[4].model_copy(
            update={
                "role": MessageRole.USER,
                "text": "actually make it about the Qwen architecture",
            }
        )
        return conversation_id, runs

    def test_a_mid_run_user_message_survives_elision(self) -> None:
        _, runs = self._conversation_with_a_mid_run_question()

        selected = select_runtime_history(_as_bounded(runs))

        assert any(
            message.role is MessageRole.USER
            and "Qwen architecture" in (message.text or "")
            for message in selected
        )

    def test_every_user_message_of_an_elided_run_is_kept_verbatim(self) -> None:
        _, runs = self._conversation_with_a_mid_run_question()
        oldest_id = runs[0].id

        selected = select_runtime_history(_as_bounded(runs))

        kept = [
            message.text
            for message in selected
            if message.agent_run_id == oldest_id and message.role is MessageRole.USER
        ]
        assert kept == [
            "run 0 message 0",
            "actually make it about the Qwen architecture",
        ]

    def test_the_step_count_does_not_count_what_was_kept(self) -> None:
        """The notice stands in for work that was dropped, so counting the
        messages still present would overstate what the model cannot see."""
        _, runs = self._conversation_with_a_mid_run_question()
        oldest_id = runs[0].id

        selected = select_runtime_history(_as_bounded(runs))

        notice = next(
            message
            for message in selected
            if message.agent_run_id == oldest_id
            and (message.metadata or {}).get("summary_kind")
            == "agent_run_middle_elision"
        )
        # 9 messages: two the user wrote, one final answer, six elided.
        assert notice.metadata["elided_message_count"] == 6

    def test_the_final_answer_still_closes_the_run(self) -> None:
        _, runs = self._conversation_with_a_mid_run_question()
        oldest_id = runs[0].id

        selected = select_runtime_history(_as_bounded(runs))

        for_run = [m for m in selected if m.agent_run_id == oldest_id]
        assert for_run[-1].text == "run 0 message 8"


class TestAPausingRunsAnswerSurvivesElision:
    """What the person typed in answer to `ask_user` lives only in a tool return.

    No user message is written for it. Its matching call sits in the middle of
    the run, which is what elision drops -- and a tool return whose call is
    missing is discarded by the history builder as an orphan. So the answer
    disappeared once the run was six turns back, replaced by "worked through N
    intermediate messages".
    """

    def _conversation_ending_on_an_answer(self) -> list[AgentRun]:
        conversation_id = uuid4()
        runs = [
            _run(conversation_id, index, message_count=9)
            for index in range(FULL_HISTORY_AGENT_RUN_COUNT + 2)
        ]
        oldest = runs[0]
        oldest.messages[-1] = oldest.messages[-1].model_copy(
            update={
                "role": MessageRole.TOOL,
                "kind": MessageKind.TOOL_RETURN,
                "tool_name": "ask_user",
                "tool_call_id": "q1",
                "tool_result": {"answer": "ship it on the 14th"},
                "text": None,
            }
        )
        return runs

    def test_the_answer_is_still_there(self) -> None:
        runs = self._conversation_ending_on_an_answer()

        selected = select_runtime_history(_as_bounded(runs))

        assert any("ship it on the 14th" in (m.text or "") for m in selected)

    def test_it_is_not_left_as_an_orphan_tool_return(self) -> None:
        """An orphan is dropped by the builder, so carrying it as one loses it
        just as surely as eliding it did."""
        runs = self._conversation_ending_on_an_answer()
        oldest_id = runs[0].id

        selected = select_runtime_history(_as_bounded(runs))

        for_run = [m for m in selected if m.agent_run_id == oldest_id]
        assert all(m.kind is not MessageKind.TOOL_RETURN for m in for_run)

    def test_a_run_ending_on_ordinary_text_is_untouched(self) -> None:
        conversation_id = uuid4()
        runs = [
            _run(conversation_id, index, message_count=9)
            for index in range(FULL_HISTORY_AGENT_RUN_COUNT + 2)
        ]

        selected = select_runtime_history(_as_bounded(runs))

        for_run = [m for m in selected if m.agent_run_id == runs[0].id]
        assert for_run[-1].text == "run 0 message 8"
