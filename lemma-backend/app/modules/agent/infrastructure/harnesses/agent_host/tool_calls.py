"""Which tool calls a run has open, so the run can close them when it ends.

This used to be a state machine. A streaming adapter reports a tool call before
the model has finished writing its input, and a conversation message is
appended, never revised, so the backend *held* each call until its arguments
stopped growing, folding successive ACP updates together and guessing which one
meant "done". The host does that now, with adapter-specific knowledge the
backend never had (docs/architecture/agent-host-events.md#tool-calls): it emits
``tool_call`` exactly once, with final arguments, and a ``tool_call_result``
always follows one. What is left here is bookkeeping:

* the name each call was announced under, because its return must carry the
  same one and the result event does not repeat it;
* which calls were deliberately *not* announced (Lemma's own pausing tools, see
  ``AgentHostEventNormalizer._tool_call``), so their results are dropped with
  them rather than reported as unpaired;
* which calls are still open, so a run that ends mid-call closes them with a
  synthesized return instead of leaving a spinner that never stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.agent.domain.value_objects import JsonObject


@dataclass(frozen=True, slots=True)
class OpenToolCall:
    """What a result needs to know about the call it closes.

    ``input`` is kept raw -- unbounded and untouched -- because a structured
    final answer can ride in the arguments rather than the output, and bounding
    would turn it into something that looks structured without being so.
    """

    name: str
    input: object = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ToolCallLedger:
    """Every tool call this run has announced, and whether each has closed."""

    calls: dict[str, OpenToolCall] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    #: Calls left to Lemma to record (its pausing tools). Their results are
    #: dropped with them.
    dropped: set[str] = field(default_factory=set)

    def known(self, call_id: str) -> bool:
        return call_id in self.calls or call_id in self.dropped

    def open(self, call_id: str, call: OpenToolCall) -> None:
        self.calls[call_id] = call

    def drop(self, call_id: str) -> None:
        self.dropped.add(call_id)

    def close(self, call_id: str) -> OpenToolCall | None:
        """Close a call, returning it, or ``None`` if it is not open.

        ``None`` covers both a result for a call never announced and a second
        result for one already closed; neither may put a return on the record.
        """
        call = self.calls.get(call_id)
        if call is None or call_id in self.closed:
            return None
        self.closed.add(call_id)
        return call

    def outstanding(self) -> dict[str, str]:
        return {
            call_id: call.name
            for call_id, call in self.calls.items()
            if call_id not in self.closed
        }
