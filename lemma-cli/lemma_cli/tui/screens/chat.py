"""Full-screen chat with streaming, tool-call inspection, and conversation resume."""

from __future__ import annotations

from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input
from textual.worker import get_current_worker

from ...cli_core.chat import iter_sse_events
from ...cli_core.io import list_items, to_plain
from ...cli_core.state import CliState, client_session
from ..events import (
    ChatEvent,
    ErrorEvent,
    Status,
    Terminal,
    TextMessage,
    Thinking,
    Token,
    ToolCall,
    ToolReturn,
    Usage,
    normalize_event,
    normalize_history_message,
)
from ..state import resolve_pod_id
from ..widgets.messages import (
    AgentMessage,
    ApprovalPrompt,
    StatusLine,
    ThinkingBlock,
    ToolCallWidget,
    UsageBar,
    UserMessage,
)

APPROVAL_TOOL_NAME = "request_approval"
APPROVAL_STATUS_HINTS = ("approval", "waiting")


def _approval_decision(result: Any) -> str | None:
    if isinstance(result, dict) and result.get("decision"):
        return str(result["decision"])
    return None


class ChatScreen(Screen[None]):
    BINDINGS = [
        ("escape", "stop_or_back", "Stop / Back"),
        ("ctrl+l", "clear_log", "Clear"),
    ]

    def __init__(
        self,
        *,
        state: CliState,
        agent: str | None,
        conversation_id: str | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.agent = agent
        self.conversation_id = conversation_id
        self.conversation_title = title
        self._streaming = False
        self._active_message: AgentMessage | None = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._pending_tools: list[ToolCallWidget] = []
        self._approval_widgets: dict[str, ApprovalPrompt] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat-log")
        yield UsageBar()
        yield Input(
            placeholder="Message the agent… (Esc stops a running turn)", id="chat-input"
        )
        yield Footer()

    def on_mount(self) -> None:
        agent = self.agent or "default agent"
        self.sub_title = f"{agent} · {self.conversation_id or 'new conversation'}"
        self.query_one("#chat-input", Input).focus()
        if self.conversation_id:
            self.load_history(self.conversation_id)

    # ------------------------------------------------------------- input

    @on(Input.Submitted, "#chat-input")
    def _submitted(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""
        self._mount_widget(UserMessage(message))
        if self._streaming:
            # A follow-up joins the turn already running rather than waiting for
            # it. `send_stream` would be wrong here: it opens a second SSE
            # subscription for the same run, and the stream this screen is
            # already reading is the one carrying the answer.
            self.append_turn(message)
            return
        self.stream_turn(message)

    def action_stop_or_back(self) -> None:
        if self._streaming:
            self.stop_run()
        else:
            self.app.pop_screen()

    def action_clear_log(self) -> None:
        self.query_one("#chat-log", VerticalScroll).remove_children()
        self._approval_widgets.clear()

    @on(ApprovalPrompt.Decision)
    def _approval_decided(self, message: ApprovalPrompt.Decision) -> None:
        self.resolve_approval(message.prompt, message.decision)

    # ------------------------------------------------------------ workers

    @work(thread=True, exclusive=True, group="chat")
    def stream_turn(self, message: str) -> None:
        worker = get_current_worker()
        app = self.app
        app.call_from_thread(self._set_streaming, True)
        try:
            with client_session(self.state) as client:
                pod_id = resolve_pod_id(self.state) or ""
                pod_sdk = client.pod(pod_id)
                if not self.conversation_id:
                    created = to_plain(
                        pod_sdk.conversations.create_for_agent(
                            self.agent or "",
                            title=self.conversation_title or "TUI chat",
                        )
                    )
                    self.conversation_id = str(created.get("id") or "")
                    app.call_from_thread(self._refresh_subtitle)
                response = pod_sdk.conversations.send_stream(
                    self.conversation_id, message
                )
                try:
                    for raw in iter_sse_events(response):
                        if worker.is_cancelled:
                            break
                        event = normalize_event(raw)
                        if event is not None:
                            app.call_from_thread(self.handle_event, event)
                finally:
                    response.close()
        except Exception as exc:  # surface, don't crash the UI thread
            app.call_from_thread(self.handle_event, ErrorEvent(str(exc)))
        finally:
            app.call_from_thread(self._set_streaming, False)

    @work(thread=True, group="chat-append")
    def append_turn(self, message: str) -> None:
        """Add a message to the run in flight, without a stream of its own.

        Its own worker group, not ``chat``: that group is exclusive and holds
        the stream reading the answer, so appending there would cancel the very
        stream this message is waiting on.
        """
        conversation_id = self.conversation_id
        app = self.app
        if not conversation_id:
            return
        try:
            with client_session(self.state) as client:
                pod_id = resolve_pod_id(self.state) or ""
                result = to_plain(
                    client.pod(pod_id).conversations.append(conversation_id, message)
                )
            if not result.get("started_new_run", True):
                app.call_from_thread(self.notify, "Added to the running turn.")
        except Exception as exc:
            app.call_from_thread(
                self.notify, f"Could not send that: {exc}", severity="error"
            )

    @work(thread=True, group="chat-stop")
    def stop_run(self) -> None:
        conversation_id = self.conversation_id
        app = self.app
        try:
            if conversation_id:
                with client_session(self.state) as client:
                    pod_id = resolve_pod_id(self.state) or ""
                    client.pod(pod_id).conversations.stop(conversation_id)
            app.call_from_thread(self.notify, "Stop requested.")
        except Exception as exc:
            app.call_from_thread(self.notify, f"Stop failed: {exc}", severity="error")
        app.workers.cancel_group(self, "chat")

    @work(thread=True, group="chat-approval-resolve")
    def resolve_approval(self, prompt: ApprovalPrompt, decision: str) -> None:
        conversation_id = self.conversation_id
        app = self.app
        if not conversation_id:
            return
        try:
            with client_session(self.state) as client:
                pod_id = resolve_pod_id(self.state) or ""
                client.pod(pod_id).conversations.resolve_approval(
                    conversation_id,
                    prompt.approval_id,
                    {"decision": decision},
                )
        except Exception as exc:
            app.call_from_thread(prompt.mark_failed, str(exc))
            return
        app.call_from_thread(prompt.mark_resolved, decision)

    @work(thread=True, exclusive=True, group="chat-approval-poll")
    def check_approvals(self) -> None:
        conversation_id = self.conversation_id
        app = self.app
        if not conversation_id:
            return
        try:
            with client_session(self.state) as client:
                pod_id = resolve_pod_id(self.state) or ""
                payload = client.pod(pod_id).conversations.approvals(conversation_id)
            items = [to_plain(item) for item in list_items(payload)]
        except Exception as exc:
            app.call_from_thread(
                self.notify, f"Approval check failed: {exc}", severity="error"
            )
            return
        app.call_from_thread(self._mount_pending_approvals, items)

    @work(thread=True, exclusive=True, group="chat-history")
    def load_history(self, conversation_id: str) -> None:
        app = self.app
        try:
            with client_session(self.state) as client:
                pod_id = resolve_pod_id(self.state) or ""
                payload = client.pod(pod_id).conversations.messages(
                    conversation_id, limit=100
                )
            messages = [to_plain(item) for item in list_items(payload)]
        except Exception as exc:
            app.call_from_thread(
                self.handle_event, ErrorEvent(f"History load failed: {exc}")
            )
            return
        app.call_from_thread(self._replay_history, messages)

    # ----------------------------------------------------------- rendering

    def _replay_history(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            role = str(message.get("role") or "").lower()
            event = normalize_history_message(message)
            if event is None:
                continue
            if isinstance(event, TextMessage) and role == "user":
                self._mount_widget(UserMessage(event.text))
                continue
            self._active_message = None  # each persisted message is complete
            self.handle_event(event)
        self._active_message = None

    def handle_event(self, event: ChatEvent) -> None:
        if isinstance(event, Token):
            self._ensure_agent_message().append_token(event.text)
        elif isinstance(event, TextMessage):
            if event.role == "user":
                if not self._streaming:
                    # During a live turn the input handler already rendered the
                    # user message; the stream echoes it back, so skip the dupe.
                    self._mount_widget(UserMessage(event.text))
            else:
                active = self._active_message
                if active is not None and active.text:
                    # Full message snapshot supersedes streamed tokens.
                    active.set_text(event.text)
                    active.finalize()
                    self._active_message = None
                else:
                    widget = AgentMessage(event.text)
                    self._mount_widget(widget)
                    widget.finalize()
        elif isinstance(event, Thinking):
            self._active_message = None
            self._mount_widget(ThinkingBlock(event.text))
        elif isinstance(event, ToolCall):
            self._active_message = None
            if event.tool_name == APPROVAL_TOOL_NAME:
                if event.tool_call_id:
                    tool_args = (
                        event.tool_input if isinstance(event.tool_input, dict) else {}
                    )
                    self._mount_approval(
                        approval_id=event.tool_call_id, tool_args=tool_args
                    )
                else:
                    # The durable approval id only lives on the approvals endpoint.
                    self.check_approvals()
                return
            widget = ToolCallWidget(
                tool_name=event.tool_name, tool_input=event.tool_input
            )
            if event.tool_call_id:
                self._tool_widgets[event.tool_call_id] = widget
            else:
                self._pending_tools.append(widget)
            self._mount_widget(widget)
        elif isinstance(event, ToolReturn):
            if event.tool_call_id and event.tool_call_id in self._approval_widgets:
                prompt = self._approval_widgets.pop(event.tool_call_id)
                prompt.mark_resolved(_approval_decision(event.result))
                return
            widget = None
            if event.tool_call_id:
                widget = self._tool_widgets.pop(event.tool_call_id, None)
            if widget is None and self._pending_tools:
                widget = self._pending_tools.pop(0)
            if widget is not None:
                widget.set_result(event.result)
            else:
                self._mount_widget(
                    StatusLine(f"{event.tool_name} returned (no matching call)")
                )
        elif isinstance(event, Usage):
            self.query_one(UsageBar).add(
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                tool_call_count=event.tool_call_count,
            )
        elif isinstance(event, Status):
            self._mount_widget(StatusLine(event.text))
            text = event.text.lower()
            if any(hint in text for hint in APPROVAL_STATUS_HINTS):
                self.check_approvals()
        elif isinstance(event, ErrorEvent):
            self._finalize_active()
            self._mount_widget(StatusLine(f"Error: {event.text}", error=True))
        elif isinstance(event, Terminal):
            self._finalize_active()
            detail = (
                f" — {event.detail}"
                if event.detail and event.detail != event.kind
                else ""
            )
            self._mount_widget(StatusLine(f"{event.kind}{detail}"))

    # ------------------------------------------------------------- helpers

    def _mount_approval(self, *, approval_id: str, tool_args: dict[str, Any]) -> None:
        if approval_id in self._approval_widgets:
            return
        # request_approval args: {tool_name, args, title, reason, payload?}.
        prompt = ApprovalPrompt(
            approval_id=approval_id,
            title=str(tool_args.get("title") or "Approval requested"),
            description=str(tool_args.get("reason") or ""),
            kind=str(tool_args.get("tool_name") or "approval"),
        )
        self._approval_widgets[approval_id] = prompt
        self._mount_widget(prompt)

    def _mount_pending_approvals(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            # Each item is a flat tool_call MessageResponse for request_approval.
            approval_id = str(item.get("tool_call_id") or "")
            if not approval_id:
                continue
            tool_args = item.get("tool_args")
            self._mount_approval(
                approval_id=approval_id,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
            )

    def _ensure_agent_message(self) -> AgentMessage:
        if self._active_message is None:
            self._active_message = AgentMessage()
            self._mount_widget(self._active_message)
        return self._active_message

    def _finalize_active(self) -> None:
        if self._active_message is not None:
            self._active_message.finalize()
            self._active_message = None

    def _mount_widget(self, widget) -> None:  # type: ignore[no-untyped-def]
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(widget)
        log.scroll_end(animate=False)

    def _set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming
        if not streaming:
            self._finalize_active()
            self._tool_widgets.clear()
            self._pending_tools.clear()

    def _refresh_subtitle(self) -> None:
        agent = self.agent or "default agent"
        self.sub_title = f"{agent} · {self.conversation_id or 'new conversation'}"
