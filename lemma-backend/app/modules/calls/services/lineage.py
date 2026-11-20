"""Whose work each sub-agent is.

A coordinator run that hands work to sub-agents must not be announced as
finished; one that called a spawning tool and started nothing must be. That
turns on knowing which coordinator run a sub-agent's run belongs to -- read
from the conversation's own ``parent_run_id``, never inferred from which run
happened to be going when the child started, since a child can start after
its parent ended and while another run is live.
"""

from __future__ import annotations

from uuid import UUID

#: Sub-agents of sub-agents are followed up this many levels.
_MAX_DEPTH = 8


class Lineage:
    def __init__(self, call_id: UUID) -> None:
        self._call_id = call_id
        #: Each conversation in the call, by the run that spawned it.
        self._spawned_by: dict[UUID, str] = {}
        #: Each run heard, by the conversation it ran in.
        self._ran_in: dict[str, UUID] = {}

    def spawned(
        self,
        conversation_id: UUID,
        by_run: str | None,
        by_conversation: UUID | None = None,
    ) -> None:
        """``conversation_id`` was spawned by ``by_run``, which ran in
        ``by_conversation`` when the store says so -- known without having
        heard that run's frames."""
        if by_run:
            self._spawned_by.setdefault(conversation_id, by_run)
            if by_conversation is not None:
                self._ran_in.setdefault(by_run, by_conversation)

    def learn(self, lineage: list[tuple[UUID, UUID | None, UUID | None]]) -> list[UUID]:
        """Take the store's ``(conversation, spawned by run, which ran in)``
        rows; returns the conversations, to watch."""
        for conversation_id, by_run, ran_in in lineage:
            self.spawned(conversation_id, str(by_run) if by_run else None, ran_in)
        return [conversation_id for conversation_id, _, _ in lineage]

    def ran(self, run_id: str, conversation_id: UUID) -> None:
        self._ran_in[run_id] = conversation_id

    def coordinator_run(self, conversation_id: UUID) -> str | None:
        """The run of the call's own conversation this conversation's work
        traces back to, or None when the chain is not known."""
        current = conversation_id
        for _ in range(_MAX_DEPTH):
            run = self._spawned_by.get(current)
            if run is None:
                return None
            parent = self._ran_in.get(run)
            if parent == self._call_id:
                return run
            if parent is None:
                return None
            current = parent
        return None


def children_started(result: dict[str, object]) -> list[str]:
    """The child conversations a spawning call's return names."""
    named = [result.get("conversation_id")]
    children = result.get("children")
    if isinstance(children, list):
        named.extend(
            child.get("conversation_id")
            for child in children
            if isinstance(child, dict)
        )
    return [child for child in named if isinstance(child, str)]
