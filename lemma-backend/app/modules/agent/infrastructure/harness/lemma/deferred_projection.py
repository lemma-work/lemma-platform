"""What a ``call_tool`` call projects as: the tool it ran.

The run log keeps the call as the model made it. Everything read off the
projection -- the stored call and its return, the card a pause shows, a
surface's progress line, the pausing-call queries -- names the tool that ran,
with its own arguments, exactly as if the model had called it directly. See
``infrastructure/tools/deferred``.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent.domain.pause_opening import PauseOpening
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.tools.deferred.tools import as_called


class DeferredCallProjection:
    """Rewrites the run's events for ``call_tool`` calls to name the tool run."""

    def __init__(self, pauses: Mapping[str, PauseKind]) -> None:
        self._pauses = pauses
        self._called: dict[str, str] = {}

    def project(self, event: AgentEvent) -> AgentEvent:
        draft = event.data
        if event.type is not AgentEventType.MESSAGE or not isinstance(
            draft, MessageDraft
        ):
            return event
        if draft.kind is MessageKind.TOOL_CALL:
            called = as_called(draft.tool_name, draft.tool_args)
            if called is None or draft.tool_call_id is None:
                return event
            name, args = called
            self._called[draft.tool_call_id] = name
            pause = self._pauses.get(name)
            return event.model_copy(
                update={
                    "data": draft.model_copy(
                        update={
                            "tool_name": name,
                            "tool_args": args,
                            "metadata": {**(draft.metadata or {}), "tool_name": name},
                            "pause": PauseOpening(kind=pause) if pause else None,
                        }
                    )
                }
            )
        if draft.kind is MessageKind.TOOL_RETURN and draft.tool_call_id in self._called:
            name = self._called[draft.tool_call_id]
            return event.model_copy(
                update={
                    "data": draft.model_copy(
                        update={
                            "tool_name": name,
                            "metadata": {**(draft.metadata or {}), "tool_name": name},
                        }
                    )
                }
            )
        return event


__all__ = ["DeferredCallProjection"]
