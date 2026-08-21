"""Chat message widgets: user/agent bubbles, collapsible tool calls, thinking, usage."""

from __future__ import annotations

import json
from typing import Any

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Collapsible, Markdown, Static


def _pretty(value: Any) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, default=str)
    except TypeError, ValueError:
        return str(value)


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(Text(text), classes="user-message")
        self.border_title = "you"


class AgentMessage(Markdown):
    """Streaming assistant message; re-renders markdown at ~10 Hz, not per token."""

    FLUSH_INTERVAL = 0.1

    def __init__(self, text: str = "") -> None:
        super().__init__(text, classes="agent-message")
        self.border_title = "agent"
        self._buffer = text
        self._dirty = False

    def on_mount(self) -> None:
        self.set_interval(self.FLUSH_INTERVAL, self._flush)

    def append_token(self, token: str) -> None:
        self._buffer += token
        self._dirty = True

    def set_text(self, text: str) -> None:
        self._buffer = text
        self._dirty = True

    @property
    def text(self) -> str:
        return self._buffer

    def _flush(self) -> None:
        if self._dirty:
            self._dirty = False
            self.update(self._buffer)

    def finalize(self) -> None:
        self._dirty = False
        self.update(self._buffer)


class ToolCallWidget(Collapsible):
    """Collapsed tool call showing name in the title; expands to input + output."""

    def __init__(self, *, tool_name: str, tool_input: Any) -> None:
        self._input_view = Static(_pretty(tool_input), classes="tool-input")
        self._output_view = Static("…running", classes="tool-output")
        super().__init__(
            Static("input", classes="tool-section-label"),
            self._input_view,
            Static("output", classes="tool-section-label"),
            self._output_view,
            title=f"⚒ {tool_name}",
            collapsed=True,
            classes="tool-call",
        )
        self.tool_name = tool_name

    def set_result(self, result: Any, *, error: bool = False) -> None:
        marker = "✗" if error else "✓"
        self.title = f"{marker} {self.tool_name}"
        self._output_view.update(_pretty(result))


class ApprovalPrompt(Vertical):
    """Inline request_approval card; Approve/Deny post a Decision the screen resolves."""

    class Decision(Message):
        def __init__(self, prompt: "ApprovalPrompt", decision: str) -> None:
            super().__init__()
            self.prompt = prompt
            self.decision = decision

        @property
        def approval_id(self) -> str:
            return self.prompt.approval_id

    def __init__(
        self,
        *,
        approval_id: str,
        title: str,
        description: str = "",
        kind: str = "approval",
    ) -> None:
        self._approve = Button("Approve", variant="success", classes="approval-approve")
        self._deny = Button("Deny", variant="error", classes="approval-deny")
        self._status_view = Static(
            Text("waiting for your decision", style="dim"), classes="approval-status"
        )
        children = [
            Static(
                Text(title or "Approval requested", style="bold"),
                classes="approval-title",
            )
        ]
        if description:
            children.append(Static(Text(description), classes="approval-description"))
        children.append(
            Horizontal(self._approve, self._deny, classes="approval-actions")
        )
        children.append(self._status_view)
        super().__init__(*children, classes="approval-prompt")
        self.border_title = f"approval · {kind}" if kind else "approval"
        self.approval_id = approval_id
        self.resolved_decision: str | None = None

    @on(Button.Pressed)
    def _decide(self, event: Button.Pressed) -> None:
        event.stop()
        decision = "DENY" if event.button.has_class("approval-deny") else "APPROVE_ONCE"
        self._set_buttons_disabled(True)
        self._status_view.update(Text(f"sending {decision}…", style="dim"))
        self.post_message(self.Decision(self, decision))

    def mark_resolved(self, decision: str | None) -> None:
        self.resolved_decision = decision or "resolved"
        self._set_buttons_disabled(True)
        self._status_view.update(
            Text(f"resolved: {self.resolved_decision}", style="dim")
        )

    def mark_failed(self, error: str) -> None:
        self._set_buttons_disabled(False)
        self._status_view.update(Text(f"failed: {error}", style="red"))

    def _set_buttons_disabled(self, disabled: bool) -> None:
        self._approve.disabled = disabled
        self._deny.disabled = disabled


class ThinkingBlock(Collapsible):
    def __init__(self, text: str) -> None:
        super().__init__(
            Static(text or "(no detail)", classes="thinking-body"),
            title="thinking…",
            collapsed=True,
            classes="thinking",
        )


class UsageBar(Static):
    """Docked footer line accumulating usage across the conversation."""

    def __init__(self) -> None:
        super().__init__("", id="usage-bar")
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0

    def add(
        self, *, input_tokens: int, output_tokens: int, tool_call_count: int
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.tool_calls += tool_call_count
        self.update(
            Text(
                f"tokens in {self.input_tokens}  out {self.output_tokens}  "
                f"tool calls {self.tool_calls}",
                style="dim",
            )
        )


class StatusLine(Static):
    def __init__(self, text: str, *, error: bool = False) -> None:
        style = "red" if error else "dim"
        super().__init__(Text(text, style=style), classes="status-line")
