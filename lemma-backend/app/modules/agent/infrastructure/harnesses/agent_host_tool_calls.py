"""Which tool calls a run has open, and when each becomes durable.

Split out of the normalizer because it is a state machine with its own rules,
and the normalizer's job is only to turn what it decides into events.

The rule that shapes all of it: a conversation message is appended, never
revised. A streaming adapter surfaces a tool call at ``content_block_start``,
before the model has finished writing its input, so the first report carries
``rawInput: {}`` and the real arguments follow moments later on a status-less
update. Announcing the call immediately therefore pinned ``{}`` as its
arguments for the life of the conversation, and any view built from them had
nothing to build from. So a call whose arguments are still in flight is *held*
until they arrive — which the adapter always does before the tool runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.agent.domain.value_objects import JsonObject, MessageDraft
from app.modules.agent.infrastructure.harnesses.agent_host_tool_payload import (
    raw_tool_args,
    tool_args,
    tool_metadata,
    tool_name_from_payload,
)


def _is_empty(value: object) -> bool:
    return isinstance(value, (dict, list, str)) and len(value) == 0


@dataclass(slots=True)
class _HeldToolCall:
    """A tool call announced before its arguments were written.

    Holds the best payload seen so far. Successive updates are folded in rather
    than replacing it, because an adapter refines a call in pieces — one update
    carries the arguments, the next only a title, a third nothing but its id —
    and a plain overwrite would let the emptiest of them win.
    """

    tool_name: str
    payload: JsonObject
    metadata: JsonObject
    sequence: int

    def arguments_settled(self) -> bool:
        """Whether this call is ready to go on the durable record.

        Reports *empty* arguments and reports *no* arguments are different
        claims. An adapter that names the keys and leaves them empty is saying
        "not written yet". An adapter that omits them is saying nothing about
        arguments at all, and waiting for a refinement it will never send would
        hold the call until the turn ended.

        A call that really was invoked with nothing is the one case this reads
        wrongly, and it costs only lateness: :meth:`ToolCallLedger.release`
        announces it when the call closes.
        """
        arguments = raw_tool_args(self.payload)
        if arguments is None:
            return True
        return not _is_empty(arguments)

    def absorb(self, payload: JsonObject, metadata: JsonObject) -> None:
        """Fold a later report of the same call into this one.

        Empty and null fields are skipped rather than written: an adapter sends
        several refinements per call, and the ones carrying nothing must not
        undo the one that carried the arguments.
        """
        for key, value in payload.items():
            if value is None:
                continue
            if key in {"arguments", "args", "rawInput"} and _is_empty(value):
                continue
            self.payload[key] = value
        self.metadata.update(metadata)
        refined = tool_name_from_payload(self.payload)
        if refined and refined != "tool":
            self.tool_name = refined

    def draft(self, object_id: str) -> MessageDraft:
        return MessageDraft.of_tool_call(
            tool_name=self.tool_name,
            tool_call_id=object_id,
            tool_args=tool_args(self.payload, self.tool_name),
            metadata=tool_metadata(self.metadata, self.payload),
        )


@dataclass(slots=True)
class ToolCallLedger:
    """Every tool call this run has opened, and how far each one has got."""

    #: Announced calls, by id, holding the name each was announced under.
    open_calls: dict[str, str] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    #: Calls whose arguments have not arrived yet. See the module docstring.
    held: dict[str, _HeldToolCall] = field(default_factory=dict)
    # ACP's ToolCall has no required id field, so an adapter is free to report a
    # call and its completion with nothing tying them together. See
    # :meth:`object_id`.
    anonymous_seen: int = 0
    anonymous_open: str | None = None

    def object_id(self, reported: str | None, *, opening: bool) -> str:
        """The id a tool call and its later update must agree on.

        ACP's ``ToolCall`` carries no required identifier, so an adapter can
        report a call and its completion with nothing linking them. The old
        fallback was the event sequence, which is different for the two events
        by definition: the call was therefore never closed — it was swept at
        the end as an abandoned call — and the update emitted a result for an
        id nobody held.

        With no id on the wire the only correlation available is order, so an
        untagged update is attributed to the untagged call still open. That is
        a heuristic, and it is exactly as good as the information the adapter
        gave us; ``acp.rs`` reaches for the same one when a permission request
        arrives without a tool-call id.
        """
        if reported:
            return reported
        if opening:
            self.anonymous_seen += 1
            self.anonymous_open = f"anonymous-tool-call-{self.anonymous_seen}"
            return self.anonymous_open
        return self.anonymous_open or f"anonymous-tool-call-{max(self.anonymous_seen, 1)}"

    def open(
        self,
        object_id: str,
        payload: JsonObject,
        metadata: JsonObject,
        *,
        sequence: int,
    ) -> tuple[MessageDraft, int] | None:
        """Take a reported tool call, announcing it if it is ready."""
        if object_id in self.open_calls:
            # A second opening for a call already announced. Adapters re-surface
            # a call rather than inventing a new id, so this is a refinement in
            # everything but name; treat it as one.
            return self.refine(object_id, payload, metadata)
        held = _HeldToolCall(
            tool_name=tool_name_from_payload(payload),
            payload=dict(payload),
            metadata=dict(metadata),
            sequence=sequence,
        )
        self.open_calls[object_id] = held.tool_name
        if held.arguments_settled():
            return self._announce(object_id, held)
        self.held[object_id] = held
        return None

    def refine(
        self, object_id: str, payload: JsonObject, metadata: JsonObject
    ) -> tuple[MessageDraft, int] | None:
        """Fold a status-less update into the call it refines."""
        held = self.held.get(object_id)
        if held is None:
            # Already announced; nothing left to correct, because the message is
            # on the durable record.
            return None
        held.absorb(payload, metadata)
        if not held.arguments_settled():
            return None
        return self._announce(object_id, held)

    def release(
        self,
        object_id: str,
        payload: JsonObject | None = None,
        metadata: JsonObject | None = None,
    ) -> tuple[MessageDraft, int] | None:
        """Announce a call whose arguments never arrived, so it is not lost.

        A call is held only while its arguments are still being written. If the
        turn ends first — the agent was cancelled, the adapter died, the tool
        genuinely takes no arguments — the call still happened and still owes
        the conversation a card.

        ``payload`` is the update doing the releasing, and is folded in first.
        The closing update is frequently the only one that ever named the tool
        or carried its input: releasing without reading it announced the call as
        an anonymous ``tool`` with ``{}`` for arguments while the answer to both
        sat in the very event that triggered the release. Only the fields a call
        reads are used; a result on the same payload is ignored here and handled
        by the caller.
        """
        held = self.held.get(object_id)
        if held is None:
            return None
        if payload is not None:
            held.absorb(payload, metadata or {})
        return self._announce(object_id, held)

    def release_all(self) -> list[tuple[MessageDraft, int]]:
        return [
            announced
            for object_id in list(self.held)
            if (announced := self.release(object_id)) is not None
        ]

    def close(self, object_id: str) -> None:
        self.closed.add(object_id)
        if object_id == self.anonymous_open:
            self.anonymous_open = None

    def outstanding(self) -> dict[str, str]:
        return {
            object_id: tool_name
            for object_id, tool_name in self.open_calls.items()
            if object_id not in self.closed
        }

    def _announce(
        self, object_id: str, held: _HeldToolCall
    ) -> tuple[MessageDraft, int]:
        self.held.pop(object_id, None)
        self.open_calls[object_id] = held.tool_name
        return held.draft(object_id), held.sequence
