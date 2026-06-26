from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any


class StreamTextState:
    """Buffers incremental text snapshots and emits token/message events."""

    def __init__(
        self,
        *,
        harness_kind: str,
        event_sink: Callable[[str, Any], Awaitable[None]] | None,
    ) -> None:
        self.harness_kind = harness_kind
        self.event_sink = event_sink
        self.current_text = ""
        self.flushed_texts: list[str] = []
        self.streamed_tokens = False
        self.streamed_messages = False
        self.emitted_tool_call_ids: set[str] = set()
        self.emitted_tool_return_ids: set[str] = set()

    @property
    def full_text(self) -> str:
        parts = [*self.flushed_texts]
        if self.current_text.strip():
            parts.append(self.current_text.strip())
        return "\n".join(parts)

    async def update_text_snapshot(self, text: str) -> None:
        if not text:
            return
        if text == self.current_text:
            return
        if self.current_text and not text.startswith(self.current_text):
            await self.flush(is_final=False)
        delta = text[len(self.current_text):] if text.startswith(self.current_text) else text
        self.current_text = text
        if self.event_sink is not None and delta:
            self.streamed_tokens = True
            await self.event_sink("token", {"kind": "text", "data": delta})

    async def flush(self, *, is_final: bool) -> None:
        text = self.current_text.strip()
        self.current_text = ""
        if not text:
            return
        self.flushed_texts.append(text)
        if self.event_sink is None:
            return
        self.streamed_messages = True
        await emit_assistant_text_message(
            self.event_sink,
            text,
            harness_kind=self.harness_kind,
            is_final=is_final,
        )

    async def update_tool_parts(self, tool_parts: list[dict]) -> None:
        """Emit tool_call / tool_return events for structured tool parts.

        Harnesses that expose tool calls as message parts (opencode) call this on
        each poll with the current tool parts. Each part carries a ``callID``, a
        ``tool`` name, and a ``state`` (status + input/output). We emit a TOOL_CALL
        the first time a callID appears and a TOOL_RETURN once it reaches a terminal
        status, deduped across polls -- mirroring the codex harness so the bridge
        transmits tool activity, not just final text.
        """
        if self.event_sink is None:
            return
        for part in tool_parts:
            if not isinstance(part, dict):
                continue
            call_id = str(part.get("callID") or part.get("id") or "")
            tool_name = str(part.get("tool") or "")
            if not call_id or not tool_name:
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = str(state.get("status") or "")
            if call_id not in self.emitted_tool_call_ids:
                self.emitted_tool_call_ids.add(call_id)
                # Flush buffered text first so ordering reads text -> tool -> text.
                await self.flush(is_final=False)
                self.streamed_tokens = True
                self.streamed_messages = True
                await emit_tool_call(
                    self.event_sink,
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    tool_args=state.get("input"),
                    harness_kind=self.harness_kind,
                )
            if status in {"completed", "error"} and call_id not in self.emitted_tool_return_ids:
                self.emitted_tool_return_ids.add(call_id)
                self.streamed_messages = True
                result = state.get("output") if status == "completed" else state.get("error")
                await emit_tool_return(
                    self.event_sink,
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    tool_result=result,
                    harness_kind=self.harness_kind,
                )


async def emit_tool_call(
    event_sink: Callable[[str, Any], Awaitable[None]],
    *,
    tool_name: str,
    tool_call_id: str,
    tool_args: Any,
    harness_kind: str,
) -> None:
    """Emit a streamed tool token + a TOOL_CALL message (shape matches codex)."""
    await event_sink(
        "token",
        {
            "kind": "tool",
            "data": json.dumps(
                {"tool_name": tool_name, "args": tool_args or {}},
                separators=(",", ":"),
            ),
        },
    )
    await event_sink(
        "message",
        {
            "role": "assistant",
            "kind": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_args": tool_args,
            "metadata": {"tool_name": tool_name, "provider": harness_kind},
        },
    )


async def emit_tool_return(
    event_sink: Callable[[str, Any], Awaitable[None]],
    *,
    tool_name: str,
    tool_call_id: str,
    tool_result: Any,
    harness_kind: str,
) -> None:
    """Emit a TOOL_RETURN message (shape matches codex)."""
    await event_sink(
        "message",
        {
            "role": "tool",
            "kind": "tool_return",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_result": tool_result,
            "metadata": {"tool_name": tool_name, "provider": harness_kind},
        },
    )


async def emit_assistant_text_message(
    event_sink: Callable[[str, Any], Awaitable[None]],
    text: str,
    *,
    harness_kind: str,
    is_final: bool,
) -> None:
    metadata: dict[str, object] = {
        "user_daemon": True,
        "harness_kind": harness_kind,
    }
    if not is_final:
        metadata["is_final_answer"] = False
    await event_sink(
        "message",
        {
            "role": "assistant",
            "kind": "text",
            "text": text,
            "metadata": metadata,
        },
    )
