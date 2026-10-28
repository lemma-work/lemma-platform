"""Replaying a conversation's run log, and writing each step back to it.

See ``domain/run_log`` for what the log is. This is the pydantic-ai half: the
stored steps decode with ``ModelMessagesTypeAdapter`` into the exact messages
the last run sent, and ``RunLogRecorder`` turns what each model step added into
the next steps to store.

What a run adds before its first request is decided once, here, and logged with
that request -- so it is never rebuilt again:

* **the returns for calls the log leaves open**, read from the rows: a pause's
  answer, a result that committed after its step, the return a run's ending
  made up for a call it left open -- or, where there is none, what the model
  should know about the call (``unanswered_call_text``);
* **the stop**, when the last logged run was stopped by a member;
* **the rows written outside any run's log** (``pending_rows``): the run's own
  prompt, what was held or folded in, notices, turns another harness answered.

The run-context part is history like any other: written once at the run's
turn, logged with the step that first sent it. When what the run now holds no
longer starts with what was logged -- a compaction, a clipped tool result, a detached
image -- the whole history is written as a ``COMPACTION`` checkpoint, once, and
replay starts there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.run_log import RunLogReplay, RunStepDraft, RunStepKind
from app.modules.agent.domain.runtime_history import STOPPED_BY_MEMBER
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    MessageDraft,
    MessageKind,
    MessageRole,
)

#: What an open question the person passed over is answered with: they were
#: asked, and said something else -- which is the turn being answered now.
NOT_ANSWERED = (
    "The user did not answer this. They sent a new message instead, which is the "
    "turn you are responding to now. Read it and carry on; ask again only if you "
    "still cannot proceed without an answer."
)
#: An open question nobody has answered or passed over yet.
STILL_OPEN = (
    "No answer has arrived yet. Carry on with what you can do without it, or "
    "end the turn and wait."
)
#: Deliberately not "run it again": the call may well have taken effect, and a
#: run interrupted mid-send that is told to retry sends twice.
INTERRUPTED = (
    "This tool call was interrupted before a result was recorded, so its outcome "
    "is unknown: it may have completed. Check the current state before repeating "
    "it, and only repeat it if it clearly did not take effect."
)


def unanswered_call_text(*, pauses: bool, moved_on: bool) -> str:
    """What a call with no stored return is answered with in the history."""
    if not pauses:
        return INTERRUPTED
    return NOT_ANSWERED if moved_on else STILL_OPEN


def encode(messages: Sequence[ModelMessage]) -> str:
    return ModelMessagesTypeAdapter.dump_json(list(messages)).decode()


def decode(serialized: Sequence[str]) -> list[ModelMessage]:
    """The stored steps as the messages they were: one list, oldest first."""
    messages: list[ModelMessage] = []
    for step in serialized:
        messages.extend(ModelMessagesTypeAdapter.validate_json(step))
    return messages


def open_calls(messages: Sequence[ModelMessage]) -> list[ToolCallPart]:
    """The calls ``messages`` make and never answer, oldest first."""
    pending: dict[str, ToolCallPart] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(message, ModelResponse) and isinstance(part, ToolCallPart):
                pending[part.tool_call_id] = part
            elif isinstance(part, ToolReturnPart | RetryPromptPart) and isinstance(
                getattr(part, "tool_call_id", None), str
            ):
                pending.pop(part.tool_call_id, None)
    return list(pending.values())


def resume_request(
    calls: Sequence[ToolCallPart],
    *,
    returns: dict[str, Message],
    pausing: frozenset[str],
    moved_on: bool,
) -> ModelRequest | None:
    """One request answering every open call, in the order they were made."""
    if not calls:
        return None
    parts: list[ToolReturnPart] = []
    for call in calls:
        stored = returns.get(call.tool_call_id)
        content = (
            stored.tool_result
            if stored is not None
            else {
                "success": False,
                "error": unanswered_call_text(
                    pauses=call.tool_name in pausing, moved_on=moved_on
                ),
            }
        )
        parts.append(
            ToolReturnPart(
                tool_name=call.tool_name,
                tool_call_id=call.tool_call_id,
                content=content,
            )
        )
    return ModelRequest(parts=parts)


def budget_pause_response(events: Sequence[AgentEvent]) -> list[ModelResponse]:
    """The approval a budget pause asks, as the call the log answers on resume.

    No model made it: the run stopped before its next request and asked
    instead. Logged as a response so the person's decision, stored as the
    call's return, is read back in its place.
    """
    calls = [
        ToolCallPart(
            tool_name=draft.tool_name or "",
            args=draft.tool_args if isinstance(draft.tool_args, dict) else {},
            tool_call_id=draft.tool_call_id or "",
        )
        for event in events
        for draft in (event.data,)
        if isinstance(draft, MessageDraft) and draft.kind is MessageKind.TOOL_CALL
    ]
    return [ModelResponse(parts=list(calls))] if calls else []


def stopped_notice(conversation_id: UUID) -> Message:
    """The line that says the last run was stopped, as a stored notice reads."""
    return Message(
        conversation_id=conversation_id,
        sequence=0,
        role=MessageRole.USER,
        kind=MessageKind.NOTIFICATION,
        text=STOPPED_BY_MEMBER,
        metadata={"synthetic": True, "summary_kind": "agent_run_stopped"},
    )


def row_kind(row: Message) -> RunStepKind:
    """Which kind of step a row taken in from the projection is logged as."""
    if row.state.held_for_a_person:
        return RunStepKind.HELD
    if row.state.folded_from is not None or str(
        (row.metadata or {}).get("summary_kind", "")
    ).startswith("folded_chat_"):
        return RunStepKind.FOLDED
    if row.kind is MessageKind.NOTIFICATION:
        return RunStepKind.NOTICE
    return RunStepKind.STEP


@dataclass(slots=True)
class RunLogRecorder:
    """Turns what a run holds into the steps it has not logged yet.

    ``logged`` is what the log holds, as this run replayed it plus what it has
    written since. ``labels`` names the kind of step the messages this run took
    in before its first request go in, by identity; everything else is a
    ``STEP``. ``covers`` are the rows those messages carry, written with the
    first step that holds any of them.
    """

    logged: list[ModelMessage]
    labels: dict[int, RunStepKind] = field(default_factory=dict)
    covers: list[UUID] = field(default_factory=list)
    _logged_bytes: list[bytes] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._logged_bytes = [_bytes(message) for message in self.logged]

    def claimed(self, row_ids: Sequence[UUID]) -> None:
        """Rows steered into the run: carried by the step that holds them."""
        self.covers.extend(row_ids)

    def take(self, messages: Sequence[ModelMessage]) -> list[RunStepDraft]:
        """The steps that bring the log up to ``messages``; none when it is."""
        current = list(messages)
        current_bytes = [
            known if message is self._logged_at(index) else _bytes(message)
            for index, message in enumerate(current)
            for known in (self._known_bytes(index),)
        ]
        prefix = len(self._logged_bytes)
        if current_bytes[:prefix] == self._logged_bytes:
            added = current[prefix:]
            drafts = self._drafts(added)
        else:
            drafts = [
                RunStepDraft(
                    kind=RunStepKind.COMPACTION,
                    messages=encode(current),
                    covers=tuple(self.covers),
                )
            ]
        if drafts:
            self.covers = []
        self.logged = list(current)
        self._logged_bytes = current_bytes
        return drafts

    def _drafts(self, added: Sequence[ModelMessage]) -> list[RunStepDraft]:
        """``added``, in runs of one kind; the first carries the covers."""
        groups: list[tuple[RunStepKind, list[ModelMessage]]] = []
        for message in added:
            kind = self.labels.get(id(message), RunStepKind.STEP)
            if groups and groups[-1][0] is kind:
                groups[-1][1].append(message)
            else:
                groups.append((kind, [message]))
        if not groups and self.covers:
            return []
        drafts = [
            RunStepDraft(kind=kind, messages=encode(group)) for kind, group in groups
        ]
        if drafts and self.covers:
            drafts[0] = replace(drafts[0], covers=tuple(self.covers))
        return drafts

    def _logged_at(self, index: int) -> ModelMessage | None:
        return self.logged[index] if index < len(self.logged) else None

    def _known_bytes(self, index: int) -> bytes:
        return self._logged_bytes[index] if index < len(self._logged_bytes) else b""


def _bytes(message: ModelMessage) -> bytes:
    return ModelMessagesTypeAdapter.dump_json([message])


@dataclass(slots=True)
class LogStart:
    """Where a run starts: its history, its prompt, and how it logs."""

    history: list[ModelMessage]
    user_prompt: str | None
    recorder: RunLogRecorder | None


def start_from_log(
    log: RunLogReplay,
    *,
    conversation_id: UUID,
    pausing: frozenset[str],
) -> LogStart:
    """The run's history from the log, and what it takes in before it starts."""
    from app.modules.agent.infrastructure.harness.lemma.history_replay import (
        prompt_and_rows,
        rows_to_messages,
    )

    replay = decode([step.messages for step in log.steps])
    labels: dict[int, RunStepKind] = {}
    covers: list[UUID] = []
    taken: list[ModelMessage] = []

    resume = resume_request(
        open_calls(replay),
        returns=log.returns,
        pausing=pausing,
        moved_on=log.moved_on,
    )
    if resume is not None:
        taken.append(resume)
        labels[id(resume)] = RunStepKind.RESUME
        covers.extend(row.id for row in log.returns.values())
    if log.stopped:
        for message in rows_to_messages([stopped_notice(conversation_id)]):
            taken.append(message)
            labels[id(message)] = RunStepKind.NOTICE

    rows, user_prompt = prompt_and_rows(log.pending)
    covers.extend(row.id for row in log.pending)
    for kind, group in _by_kind(rows):
        for message in rows_to_messages(group):
            taken.append(message)
            labels[id(message)] = kind

    recorder = (
        None
        if log.read_only
        else RunLogRecorder(logged=replay, labels=labels, covers=covers)
    )
    return LogStart(
        history=[*replay, *taken], user_prompt=user_prompt, recorder=recorder
    )


def starting_history(
    messages: Sequence[Message],
    *,
    options: HarnessOptions,
    conversation_id: UUID,
    agent_run_id: UUID,
) -> tuple[list[ModelMessage], str | None, RunLogRecorder | None]:
    """The run's history and prompt, and how it logs its steps.

    From the run log when the run was given one. Otherwise rebuilt from
    ``messages`` -- the reconstruction, for a caller with no log (a test, a
    caller with no database) -- logging nothing.
    """
    from app.modules.agent.infrastructure.harness.lemma.history_replay import (
        history_and_prompt,
    )
    from app.modules.agent.infrastructure.harness.lemma.thinking import (
        replays_prior_run_reasoning,
    )

    if options.run_log is not None:
        start = start_from_log(
            options.run_log,
            conversation_id=conversation_id,
            pausing=frozenset(
                spec.name for spec in options.tools if spec.policy.pauses
            ),
        )
        return start.history, start.user_prompt, start.recorder
    snapshot = options.runtime_profile
    history, user_prompt = history_and_prompt(
        messages,
        # Whether a stored thought can be replayed depends on the provider
        # family: each wants different reasoning credentials.
        protocol=snapshot.protocol if snapshot else None,
        current_run_id=agent_run_id,
        replay_prior_run_reasoning=replays_prior_run_reasoning(snapshot),
    )
    return history, user_prompt, None


def _by_kind(rows: Sequence[Message]) -> list[tuple[RunStepKind, list[Message]]]:
    """``rows`` in runs of one step kind, in order."""
    groups: list[tuple[RunStepKind, list[Message]]] = []
    for row in rows:
        kind = row_kind(row)
        if groups and groups[-1][0] is kind:
            groups[-1][1].append(row)
        else:
            groups.append((kind, [row]))
    return groups


__all__ = [
    "INTERRUPTED",
    "LogStart",
    "NOT_ANSWERED",
    "RunLogRecorder",
    "STILL_OPEN",
    "budget_pause_response",
    "decode",
    "encode",
    "open_calls",
    "resume_request",
    "start_from_log",
    "starting_history",
    "unanswered_call_text",
]
