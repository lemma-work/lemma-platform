"""The events that pause or end an Agent Host run: a permission request, the
host's terminal event, and a lease that ended without one.

Split from the normalizer, which owns the accumulated text and the open tool
calls; these are the moments that flush the one and close the other. They read
the normalizer only through the narrow surface below, so neither half can reach
into the other's bookkeeping.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.agent.agent_host.domain.host import AgentHostRunState
from app.modules.agent.agent_host.domain.permissions import (
    permission_approval_events,
    permission_approval_tool_call_id,
    pre_approved_by,
    pre_approved_events,
)
from app.modules.agent.domain.value_objects import AgentEvent, JsonObject
from app.modules.agent.agent_host.harness.event_text import (
    error_event,
    no_terminal_message,
    terminal_event,
)
from app.modules.agent.agent_host.harness.tool_calls import (
    OpenToolCall,
    ToolCallLedger,
)


class EndingRun(Protocol):
    """What a pause or an ending needs of the normalizer."""

    agent_run_id: UUID
    tool_calls: ToolCallLedger

    def flush_messages(
        self, *, final: bool, discard_text: bool = False
    ) -> list[AgentEvent]: ...

    def close_outstanding(self, terminal: AgentEvent) -> list[AgentEvent]: ...


def permission_request_events(
    run: EndingRun,
    *,
    request_id: str,
    sequence: int,
    payload: JsonObject,
    metadata: JsonObject,
) -> list[AgentEvent]:
    """Turn a permission request into an ordinary Lemma approval.

    Rendering it as a ``request_approval`` call is what lets the web client,
    Slack, Teams and Telegram all show and resolve it with the machinery they
    already have; see ``domain.agent_host_permissions`` for the shape and for
    why this pause emits no WAITING event.

    The host releases the call this request gates before sending it, so the
    call is already on the record and the approval card follows it.
    """
    approval_id = permission_approval_tool_call_id(request_id)
    if run.tool_calls.known(approval_id):
        # Already on the record: a run resumed past this request reads it again.
        return []
    gated = payload.get("tool_call_id")
    gated_call = run.tool_calls.calls.get(gated) if isinstance(gated, str) else None
    approved_earlier = pre_approved_by(payload)
    if approved_earlier is not None:
        # Answered by the host already, from an approval carried into this
        # run: on the record as resolved, and nothing left open to park on.
        # Known to the ledger and closed, so a replay records it once and an
        # ending run synthesizes no return over the real one.
        run.tool_calls.open(approval_id, OpenToolCall(name="request_approval"))
        run.tool_calls.close(approval_id)
        return [
            *run.flush_messages(final=False),
            *pre_approved_events(
                agent_run_id=run.agent_run_id,
                request_id=request_id,
                approval_id=approved_earlier,
                sequence=sequence,
                payload=payload,
                metadata=metadata,
                tool_name=gated_call.name if gated_call is not None else None,
            ),
        ]
    # Tracked like any other open call, so a run that ends without an answer
    # closes it. An approval card outlives its run otherwise: the host is no
    # longer holding the request — its own timeout denied it — but the card
    # still offers buttons, and pressing one lands on a run that ended hours
    # ago. Answering it *does* write a return, and a synthesized return
    # never replaces a real one, so this closes only the abandoned ones.
    run.tool_calls.open(approval_id, OpenToolCall(name="request_approval"))
    return [
        *run.flush_messages(final=False),
        *permission_approval_events(
            agent_run_id=run.agent_run_id,
            request_id=request_id,
            sequence=sequence,
            payload=payload,
            metadata=metadata,
            # The name the gated call is showing under, so the card and the
            # call it interrupts say the same word.
            tool_name=gated_call.name if gated_call is not None else None,
        ),
    ]


def terminal_events(
    run: EndingRun, *, sequence: int, payload: JsonObject
) -> list[AgentEvent]:
    """The host's own end of the run: the final flush, then the terminal."""
    # The host says so when its terminal message is a rewrite of text the
    # adapter already streamed -- a signed-out agent reports itself once as
    # ordinary assistant text and again as the reason the turn ended. Both
    # reached the transcript, so the user saw the failure twice, in two
    # different voices.
    #
    # Dropping the streamed half is not only tidier. Retry is offered only
    # on a failed run whose messages are all the user's
    # (`AgentRun.is_safely_retryable`), so persisting the adapter's own
    # error as an assistant message is what took the button away from the
    # one failure a retry actually fixes.
    events = run.flush_messages(
        final=True,
        discard_text=bool(payload.get("supersedes_stream")),
    )
    terminal = terminal_event(
        agent_run_id=run.agent_run_id,
        state=str(payload.get("state") or payload.get("status") or "FAILED"),
        payload=payload,
        sequence=sequence,
    )
    events.extend(run.close_outstanding(terminal))
    events.append(terminal)
    return events


def events_without_terminal(
    run: EndingRun,
    *,
    state: AgentHostRunState,
    detail: str | None = None,
) -> list[AgentEvent]:
    """End a run whose lease terminalized without a terminal event.

    ``detail`` is what the lease recorded, and it wins: a run the Agent
    Host refused before it started journals nothing at all, so that one
    sentence is the whole of what reaches the person who pressed send.
    """
    events = run.flush_messages(final=True)
    terminal = error_event(run.agent_run_id, detail or no_terminal_message(state))
    events.extend(run.close_outstanding(terminal))
    events.append(terminal)
    return events


__all__ = [
    "EndingRun",
    "events_without_terminal",
    "permission_request_events",
    "terminal_events",
]
