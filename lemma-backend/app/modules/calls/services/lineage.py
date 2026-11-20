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

    def spawned(self, conversation_id: UUID, by_run: str | None) -> None:
        if by_run:
            self._spawned_by.setdefault(conversation_id, by_run)

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
