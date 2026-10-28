"""What kind of turn a run is, and what that decides about it.

A run's kind is set once, by the code that starts it, and stored on the run
(``agent_runs.kind``). It used to be worked out again from the conversation row
wherever it was needed, which made every turn of a conversation the kind the
conversation was opened as: a person writing into a schedule's conversation was
counted, queued and remembered as the schedule.

The kinds are the turns product-needs lists by *who the turn is for*. Why a run
started -- a resume, a wake, a retry, a follow-up, replies, a delegated result
-- is its ``RunTrigger``; a run started for one of those continues the run
before it and takes its kind (``continuing``).

``RunPolicy`` is what a kind decides, as a table in code. A field is here only
because something reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunKind(str, Enum):
    """Who a turn is for."""

    #: A member in Lemma: the web app, the CLI, an SDK, a project's chat.
    CHAT = "chat"
    #: A member on a chat platform.
    SURFACE = "surface"
    #: A line in one of Lemma's own channels.
    CHANNEL = "channel"
    #: Somebody outside the pod: a contact, a group's outsiders, a web visitor.
    CONTACT = "contact"
    WORKFLOW = "workflow"
    SCHEDULE = "schedule"
    #: A turn another agent delegated.
    SUBAGENT = "subagent"
    #: Another pod asking this one.
    POD_ASK = "pod_ask"
    #: A live voice call's coordinator.
    CALL = "call"

    def continuing(self, *, by_a_person: bool = False) -> RunKind:
        """The kind of a run that continues one of this kind.

        ``by_a_person``: what it continues with is something a person said --
        messages queued while the run worked. Said into an automation's
        conversation, that is a person's turn, not the automation's.
        """
        if by_a_person and self in _AUTOMATION:
            return RunKind.CHAT
        return self

    def for_audience(self, *, answers_outsiders: bool) -> RunKind:
        """A chat platform's message is a contact's when its thread answers
        people outside the pod."""
        if self is RunKind.SURFACE and answers_outsiders:
            return RunKind.CONTACT
        return self

    @property
    def policy(self) -> RunPolicy:
        return POLICIES[self]


_AUTOMATION = frozenset({RunKind.WORKFLOW, RunKind.SCHEDULE})


class Lane(str, Enum):
    """Which pool of turn slots a run waits in."""

    #: Somebody is waiting on the answer: never queued behind automation.
    ATTENDED = "attended"
    #: Nobody is watching it live: a burst of these never queues a person.
    BACKGROUND = "background"


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """What a run of one kind gets."""

    lane: Lane
    #: The memory pass after a completed turn (``application/background``):
    #: only for a member's own turns, where something worth keeping was said.
    memory_pass: bool


POLICIES: dict[RunKind, RunPolicy] = {
    RunKind.CHAT: RunPolicy(Lane.ATTENDED, memory_pass=True),
    RunKind.SURFACE: RunPolicy(Lane.ATTENDED, memory_pass=True),
    RunKind.CHANNEL: RunPolicy(Lane.ATTENDED, memory_pass=True),
    RunKind.CONTACT: RunPolicy(Lane.ATTENDED, memory_pass=False),
    RunKind.WORKFLOW: RunPolicy(Lane.BACKGROUND, memory_pass=False),
    RunKind.SCHEDULE: RunPolicy(Lane.BACKGROUND, memory_pass=False),
    # Its own pool from people's chats: a parent fanning out never queues them.
    RunKind.SUBAGENT: RunPolicy(Lane.BACKGROUND, memory_pass=False),
    RunKind.POD_ASK: RunPolicy(Lane.ATTENDED, memory_pass=False),
    # Every voice turn is a run; a memory pass after each is noise.
    RunKind.CALL: RunPolicy(Lane.ATTENDED, memory_pass=False),
}


__all__ = ["POLICIES", "Lane", "RunKind", "RunPolicy"]
