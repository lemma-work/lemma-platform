from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from rich import box
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from .io import emit
from .state import CliState, console

TERMINAL_EVENTS = {"completed", "stopped", "error"}


@dataclass(frozen=True)
class StreamEvent:
    type: str
    data: Any
    agent_run_id: str | None = None


def iter_sse_events(response: Any) -> Iterable[StreamEvent]:
    data_lines: list[str] = []

    def flush() -> StreamEvent | None:
        if not data_lines:
            return None
        raw_data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not raw_data:
            return None
        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            return StreamEvent(type="raw", data=raw_data)
        if not isinstance(payload, dict):
            return StreamEvent(type="raw", data=payload)
        return StreamEvent(
            type=str(payload.get("type") or payload.get("event") or "message"),
            data=payload.get("data", payload),
            agent_run_id=(
                str(payload["agent_run_id"]) if payload.get("agent_run_id") else None
            ),
        )

    try:
        line_iter = response.iter_lines(decode_unicode=True)
    except TypeError:
        line_iter = response.iter_lines()

    for raw_line in line_iter:
        line = raw_line.decode() if isinstance(raw_line, bytes) else str(raw_line)
        if line == "":
            event = flush()
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue
        if (
            line.startswith("event:")
            or line.startswith("id:")
            or line.startswith("retry:")
        ):
            continue
        data_lines.append(line)

    event = flush()
    if event is not None:
        yield event


def emit_stream_events(state: CliState, response: Any) -> None:
    try:
        for event in iter_sse_events(response):
            emit(
                state,
                {
                    "type": event.type,
                    "data": event.data,
                    **(
                        {"agent_run_id": event.agent_run_id}
                        if event.agent_run_id
                        else {}
                    ),
                },
            )
    finally:
        response.close()


def render_chat_stream(
    *,
    state: CliState,
    response: Any,
    agent: str | None,
    verbose: bool = False,
) -> None:
    if state.output == "json":
        emit_stream_events(state, response)
        return

    renderer = ChatRenderer(agent=agent, verbose=verbose)
    try:
        for event in iter_sse_events(response):
            renderer.handle(event)
    finally:
        response.close()
        renderer.finish()


def _json_object_at(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    """Parse the JSON object beginning at ``text[start]``; returns (obj, end)."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                import json as _json

                try:
                    return _json.loads(text[start : index + 1]), index + 1
                except ValueError:
                    return None, index + 1
    return None, len(text)


def extract_final_result(text: str) -> Any:
    """Pull an agent's answer out of a raw token transcript.

    Some runtimes stream their whole interior as plain tokens — reasoning
    paragraphs with literal ``{"tool_name": ...}`` blobs inline — and deliver the
    answer as the last ``final_result`` call. Structured event suppression can't
    help there, because none of it arrives as events. Find that call and return
    its output; return None when the transcript has no such marker, so the caller
    can fall back to printing what it got.
    """
    marker = '"final_result"'
    cursor = text.rfind(marker)
    while cursor != -1:
        brace = text.rfind("{", 0, cursor)
        while brace != -1:
            payload, _end = _json_object_at(text, brace)
            if isinstance(payload, dict) and payload.get("tool_name") == "final_result":
                args = payload.get("args") or payload.get("arguments") or {}
                if isinstance(args, dict):
                    return args.get("output", args)
                return args
            brace = text.rfind("{", 0, brace)
        cursor = text.rfind(marker, 0, cursor)
    return None


class ChatRenderer:
    """Renders an agent's stream.

    By default only the ANSWER reaches the terminal. A run used to print its
    whole interior — reasoning, every tool call, usage — around 4KB of transcript
    for a three-sentence reply, with the real answer buried in the last
    `final_result`. That is a token bill and a parsing problem for anything
    driving the CLI. `--verbose` restores the full stream for watching a run.
    """

    def __init__(self, *, agent: str | None, verbose: bool = False) -> None:
        self.agent = agent or "pod agent"
        self.verbose = verbose
        self.started_answer = False
        self.printed_tokens = False
        self.answer_line_open = False
        self.seen_terminal = False
        # Some runtimes deliver the answer as a `final_result` tool call rather
        # than as tokens. Hold it so `finish` can print it when nothing else did.
        self.final_result: Any = None
        self.buffered: list[str] = []

    def handle(self, event: StreamEvent) -> None:
        event_type = event.type.lower()
        if event_type == "token":
            self._token_event(event.data)
            return
        if event_type == "message":
            self._message(event.data)
            return
        if event_type == "usage":
            self._usage(event.data)
            return
        if event_type == "status":
            self._status(event.data)
            return
        if event_type == "error":
            self._end_answer_line()
            console.print(f"[red]Error:[/red] {event.data}")
            self.seen_terminal = True
            return
        if event_type in {"completed", "stopped"}:
            self.seen_terminal = True
            # A plain "COMPLETED" adds nothing next to the answer itself, and in
            # quiet mode it arrives BEFORE the answer (which can only be
            # extracted once the stream ends), reading as though the run ended
            # with no output. Keep it for --verbose and for a run that stopped.
            if not self.verbose and event_type == "completed":
                return
            self._end_answer_line()
            status = _status_text(event.data) or event_type
            style = "dim green" if event_type == "completed" else "dim yellow"
            console.print(f"[{style}]{status}[/{style}]")
            return
        self._status(event.data)

    def finish(self) -> None:
        if not self.printed_tokens:
            payload = self.final_result
            if payload is None and self.buffered:
                payload = extract_final_result("".join(self.buffered))
            if payload is not None:
                self._render_final_result(payload)
            elif self.buffered:
                # No final_result marker — print what the agent said rather than
                # swallowing it.
                self._start_answer()
                console.print(Text("".join(self.buffered).strip()))
                self.printed_tokens = True
        self._end_answer_line()
        self.finished = True

    def _render_final_result(self, payload: Any) -> None:
        """Print a structured final result as the answer, not as a tool blob."""
        self._start_answer()
        if isinstance(payload, dict):
            # Unwrap the common {"output": ...} envelope.
            value = payload.get("output", payload)
        else:
            value = payload
        if isinstance(value, (dict, list)):
            import json as _json

            console.print_json(_json.dumps(value, default=str))
        else:
            console.print(Text(str(value)))
        self.printed_tokens = True

    def _token_event(self, data: Any) -> None:
        """Render only the answer channel of a token stream.

        Every delta the harness emits is tagged: ``text`` is the answer,
        ``thinking`` is model reasoning, and ``tool`` is the literal serialized
        call — ``{"tool_name": "pod_query", "args": {…}}`` — streamed so a UI can
        show a tool running. The tag was never read here, so all three were
        stringified into the answer, and a reply came back as::

            I'll check the items table count.
            {"tool_name":"pod_query","args":{"sql":"SELECT COUNT(*) …"}}1

        `_message` below has always kinded its payloads correctly, and the
        surfaces token stream filters the same way
        (``agent_surfaces/services/token_stream.py``). This path was the one that
        did not.

        A bare string with no tag is passed through: not every runtime sends the
        envelope, and dropping their output would trade a cosmetic bug for a
        silent one.
        """
        if isinstance(data, dict) and "kind" in data:
            if str(data.get("kind") or "") != "text":
                return
            self._token(str(data.get("data") or ""))
            return
        self._token(str(data or ""))

    def _token(self, token: str) -> None:
        if not token:
            return
        if not self.verbose:
            # Hold it: the answer can only be separated from the reasoning once
            # the stream has ended (see extract_final_result).
            self.buffered.append(token)
            return
        self._start_answer()
        self.printed_tokens = True
        self.answer_line_open = True
        console.print(Text(token), end="")

    def _message(self, data: Any) -> None:
        if not isinstance(data, dict):
            if not self.printed_tokens:
                self._token(str(data))
            return

        role = str(data.get("role") or "").lower()
        if role == "user":
            return

        metadata = data.get("metadata") or {}
        raw_kind = data.get("kind")
        if raw_kind is None:
            body = data.get("text")
            if body is None:
                body = data.get("content")
            if body is not None and not self.printed_tokens:
                self._token(str(body))
            return

        kind = str(raw_kind)
        if kind == "text":
            text = str(data.get("text") or "")
            if text and not self.printed_tokens:
                self._token(text)
            return
        if kind == "thinking":
            self._status("thinking")
            return
        if kind == "tool_call":
            self._tool_call(data)
            return
        if kind == "tool_return":
            self._tool_return(data)
            return
        if kind == "notification":
            text = str(data.get("text") or "")
            if text:
                self._status(text)
            return
        if metadata:
            self._status(metadata)

    def _usage(self, data: Any) -> None:
        if not self.verbose or not isinstance(data, dict):
            return
        bits = []
        for key in ("input_tokens", "output_tokens", "tool_call_count"):
            value = data.get(key)
            if value:
                bits.append(f"{key.replace('_', ' ')} {value}")
        if bits:
            self._status("usage: " + ", ".join(bits))

    def _status(self, data: Any) -> None:
        if not self.verbose:
            return
        text = _status_text(data)
        if not text:
            return
        self._end_answer_line()
        console.print(f"[dim]{text}[/dim]")

    def _tool_call(self, message: dict[str, Any]) -> None:
        name = str(message.get("tool_name") or "tool")
        if name == "final_result":
            # The answer, arriving as a tool call. Keep it for `finish`.
            self.final_result = message.get("args") or message.get("arguments")
            return
        if not self.verbose:
            return
        self._end_answer_line()
        console.print(f"[dim]> {name}[/dim]")

    def _tool_return(self, message: dict[str, Any]) -> None:
        if not self.verbose:
            return
        self._end_answer_line()
        name = str(message.get("tool_name") or "tool")
        console.print(f"[dim]< {name} returned[/dim]")

    def _start_answer(self) -> None:
        if self.started_answer:
            return
        console.print(f"\n[bold green]{self.agent}[/bold green]")
        self.started_answer = True
        self.answer_line_open = True

    def _end_answer_line(self) -> None:
        if self.answer_line_open:
            console.print()
            self.answer_line_open = False


def render_chat_header(
    *,
    pod: str | None,
    agent: str | None,
    conversation_id: str,
    interactive: bool,
) -> None:
    console.print(Rule("[bold cyan]Lemma chat[/bold cyan]"))
    target = agent or "default pod agent"
    mode = "interactive" if interactive else "one-shot"
    details = Text()
    if pod:
        details.append("pod ", style="dim")
        details.append(pod)
        details.append("  ")
    details.append("agent ", style="dim")
    details.append(target)
    details.append("  ")
    details.append("mode ", style="dim")
    details.append(mode)
    details.append("  ")
    details.append("conversation ", style="dim")
    details.append(conversation_id)
    console.print(details)
    if interactive:
        console.print("[dim]Commands: /help, /id, /stop, /clear, /quit[/dim]")


def render_user_message(message: str) -> None:
    console.print()
    console.print("[bold cyan]You[/bold cyan]")
    console.print(message)


def read_chat_prompt() -> str:
    return Prompt.ask("[bold cyan]You[/bold cyan]")


def render_chat_help() -> None:
    console.print(
        Panel(
            "\n".join(
                [
                    "/help   show chat commands",
                    "/id     show the conversation id",
                    "/stop   stop the active agent run",
                    "/clear  clear the terminal",
                    "/quit   leave chat",
                ]
            ),
            title="Chat commands",
            box=box.SIMPLE,
        )
    )


def _status_text(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if data.get("status"):
            return str(data["status"])
        if data.get("error"):
            return str(data["error"])
        if data.get("message"):
            return str(data["message"])
    return None
