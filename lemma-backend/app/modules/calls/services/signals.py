"""What the call hears from its agents, as the session reads it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class Relay:
    text: str
    urgency: str


@dataclass(frozen=True, slots=True)
class Shown:
    """Something put on the canvas: the tool call that showed it."""

    conversation_id: UUID
    tool_call_id: str
    tool_name: str
    args: JsonValue
    result: JsonValue

    def as_frame(self) -> dict[str, JsonValue]:
        return {
            "conversation_id": str(self.conversation_id),
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class Working:
    """Whether anyone in the call -- the coordinator or a sub-agent -- has a
    run going."""

    running: bool


@dataclass(frozen=True, slots=True)
class Failed:
    """The coordinator's run failed; ``reason`` is what to tell the person."""

    reason: str


@dataclass(frozen=True, slots=True)
class Finished:
    """The coordinator's run ended without relaying or showing anything.

    ``answer`` is its last written words, if any: the voice would otherwise
    keep saying the work is in hand."""

    answer: str | None


@dataclass(frozen=True, slots=True)
class RunStarted:
    """A new run on the call's own conversation: the chat beside the call
    re-reads and follows it. Said for every run, not only when the call goes
    from idle to working -- a second run that starts while a sub-agent is
    still busy changes nothing about "working", but is a run to follow."""

    agent_run_id: str


type AgentSignal = Relay | Shown | Working | Failed | Finished | RunStarted
