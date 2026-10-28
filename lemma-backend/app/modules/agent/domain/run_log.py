"""The run log: what a conversation's model was sent, kept byte for byte.

A provider caches the request prefix, so the next run of a conversation is only
cheap if it starts with exactly what the last run sent. Rebuilding that from the
people-facing message rows never quite managed it: JSONB re-sorted tool
arguments, a preamble and its call came back as two responses, thoughts were
dropped, notices were never stored. So each finished model step appends what it
added to the model's history, serialized as pydantic-ai serializes it, and the
next run replays it. ``agent_messages`` stays the projection for people,
surfaces, search and the Agent Host; nothing reads it to build model input
except to take in what was written outside any run's log (``pending_rows``).

Replay is the latest ``COMPACTION`` step and every step after it: a compaction
-- or any processor that rewrote what was already sent -- is written once, as a
checkpoint, and the conversation starts from it ever after.

The serialized messages are opaque here (the domain does not know pydantic-ai);
``open_tool_call_ids`` reads just enough of the JSON to say which calls the log
leaves unanswered, so their returns can be read from the projection.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.folded_turns import framed_folds
from app.modules.agent.domain.value_objects import MessageRole


class RunStepKind(StrEnum):
    """What a step added to the model's history."""

    #: A model step: the request it sent and the response it got.
    STEP = "step"
    #: The whole history, after a processor rewrote what was already sent.
    #: Replay starts at the latest one.
    COMPACTION = "compaction"
    #: Something Lemma told the model between steps.
    NOTICE = "notice"
    #: The returns for calls a run left open: a pause's answer, a result that
    #: committed after its step, a call interrupted with its run.
    RESUME = "resume"
    #: What somebody outside the pod said while a person had them.
    HELD = "held"
    #: What a contact merge copied in from another of their chats.
    FOLDED = "folded"


@dataclass(frozen=True, slots=True)
class RunStepDraft:
    """A step a harness wrote, before it has a place in the log.

    ``covers`` names message rows this step carries into the model's history
    that the harness knows by id -- rows it took in from the projection, or
    claimed mid-run. The rows the run itself wrote since its last step are
    known to whoever persisted them, and added there.
    """

    kind: RunStepKind
    messages: str
    covers: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """One stored step, as replay reads it."""

    seq: int
    kind: RunStepKind
    run_id: UUID | None
    messages: str


@dataclass(slots=True)
class RunLogReplay:
    """What a run starts from: the log, and what was written outside it since."""

    #: The latest checkpoint and every step after it, oldest first.
    steps: list[ReplayStep] = field(default_factory=list)
    #: Rows no step carries yet, in conversation order: the run's own prompt,
    #: what was held or folded in, notices, runs another harness answered.
    pending: list[Message] = field(default_factory=list)
    #: The stored return for each call the log leaves open, by call id.
    returns: dict[str, Message] = field(default_factory=dict)
    #: The last run the log holds was stopped by a member.
    stopped: bool = False
    #: Replays the log and writes nothing to it: a private note's run, whose
    #: words never reach the history a stranger's run replays.
    read_only: bool = False

    @property
    def empty(self) -> bool:
        return not self.steps

    def open_calls(self) -> list[str]:
        return open_tool_call_ids(step.messages for step in self.steps)

    @property
    def moved_on(self) -> bool:
        """Somebody said something new since the log ends: an open question
        the person did not answer was passed over, not still being asked."""
        return any(row.role is MessageRole.USER for row in self.pending)


#: ``part_kind`` values of pydantic-ai's serialized parts this reads.
_CALL_KINDS = frozenset({"tool-call"})
_ANSWER_KINDS = frozenset({"tool-return", "retry-prompt"})


def open_tool_call_ids(serialized_steps: Iterable[str]) -> list[str]:
    """The tool calls the log leaves unanswered, oldest first.

    Read from the serialized messages: a response's ``tool-call`` parts open a
    call, a request's ``tool-return`` or tool ``retry-prompt`` closes it.
    """
    open_calls: dict[str, None] = {}
    for serialized in serialized_steps:
        for message in json.loads(serialized):
            for part in message.get("parts", ()):
                call_id = part.get("tool_call_id")
                if not isinstance(call_id, str):
                    continue
                kind = part.get("part_kind")
                if message.get("kind") == "response" and kind in _CALL_KINDS:
                    open_calls[call_id] = None
                elif message.get("kind") == "request" and kind in _ANSWER_KINDS:
                    open_calls.pop(call_id, None)
    return list(open_calls)


def pending_rows(history: Sequence[Message], *, fallback: bool) -> list[Message]:
    """The rows of ``history`` no step carries yet, as the model reads them.

    ``history`` is the reconstruction the runner already assembles. On a
    conversation's first logged run (``fallback``) all of it is taken in, once:
    that is the reconstruction this log replaces, kept for one release so a
    conversation that predates the log carries its past into it.

    Otherwise a row is pending when no step covers it: every row a run writes
    is covered by the step that follows it, and every row taken in by the step
    that takes it in. Lemma's own reconstruction notices are left out -- the
    run that needs one writes it -- and the copies a merge folded in are framed
    again around what is new.
    """
    if fallback:
        return list(history)
    kept = [
        row
        for row in history
        if row.run_step_seq is None and not (row.metadata or {}).get("synthetic")
    ]
    return framed_folds(kept)


__all__ = [
    "ReplayStep",
    "RunLogReplay",
    "RunStepDraft",
    "RunStepKind",
    "open_tool_call_ids",
    "pending_rows",
]
