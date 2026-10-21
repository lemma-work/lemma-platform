"""Channel lines as people read them: who said what, when, and what became of it.

The generic table picked the columns it could find -- id, channel, time, seq,
deleted -- and left out the sender and the text, which are the whole point of a
timeline. These views name them. Every value goes in as a `Text`, never as
markup: a line is somebody else's words, and `[link=...]` in it must print as
typed. `--json` passes the payload through untouched.
"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.table import Table
from rich.text import Text

from .io import emit, to_plain
from .state import CliState, console

_TEXT_MAX = 100


def render_lines(state: CliState, payload: Any) -> None:
    """A page of a channel's timeline, or a thread (its first line, then replies)."""
    data = to_plain(payload)
    if state.output == "json" or not isinstance(data, dict):
        emit(state, data)
        return
    full = bool(getattr(state, "full", False))
    root = data.get("root")
    lines = [line for line in data.get("items") or [] if isinstance(line, dict)]
    if isinstance(root, dict):
        lines = [root, *lines]
    title = "Thread" if isinstance(root, dict) else "Lines"
    _print_table(title, [(None, line) for line in lines], full=full)
    if data.get("has_more"):
        _hint(_more_lines_hint(data))


def render_inbox(state: CliState, payload: Any) -> None:
    """What is new for you: the totals, then what waits on you and what names you."""
    data = to_plain(payload)
    if state.output == "json" or not isinstance(data, dict):
        emit(state, data)
        return
    full = bool(getattr(state, "full", False))
    totals = data.get("totals") or {}
    console.print(
        Text(
            f"{totals.get('channels', 0)} channel(s) with new lines · "
            f"{totals.get('mentions', 0)} naming you · "
            f"{totals.get('waiting', 0)} waiting on you"
        )
    )
    for key, title in (("waiting", "Waiting on you"), ("mentions", "Naming you")):
        items = [item for item in data.get(key) or [] if isinstance(item, dict)]
        if items:
            rows = [(item.get("channel"), item.get("message") or {}) for item in items]
            _print_table(title, rows, full=full)


def render_search(state: CliState, payload: Any) -> None:
    """Lines matching a search, newest first, each with its channel."""
    data = to_plain(payload)
    if state.output == "json" or not isinstance(data, dict):
        emit(state, data)
        return
    rows = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        line = dict(item.get("message") or {})
        if item.get("snippet"):
            line["text"] = item["snippet"]
        rows.append((item.get("channel"), line))
    _print_table("Matches", rows, full=bool(getattr(state, "full", False)))
    if data.get("next_cursor"):
        _hint(f"More matches: --cursor {data['next_cursor']}")


def _print_table(
    title: str, rows: list[tuple[Any, dict[str, Any]]], *, full: bool
) -> None:
    if not rows:
        console.print(Text("No lines.", style="dim"))
        return
    with_channel = any(channel is not None for channel, _ in rows)
    view = Table(title=title, box=box.SIMPLE_HEAVY)
    if with_channel:
        view.add_column("Channel", overflow="fold")
    for heading in ("Seq", "Time", "From"):
        view.add_column(heading, no_wrap=heading != "From")
    view.add_column("Line", overflow="fold", ratio=1)
    # Whole, so it can be copied into `messages thread` or `answer`.
    view.add_column("Id", no_wrap=True, min_width=36)
    for channel, line in rows:
        cells = [
            Text(str(line.get("seq", ""))),
            Text(_time(line.get("created_at"))),
            Text(sender_label(line)),
            line_text(line, full=full),
            Text(str(line.get("id") or ""), style="dim"),
        ]
        if with_channel:
            cells.insert(0, _channel_cell(channel))
        view.add_row(*cells)
    console.print(view)
    if not full and any(_was_cut(line) for _, line in rows):
        _hint("Some lines were shortened; pass --full for all of them.")


def sender_label(line: dict[str, Any]) -> str:
    """`Ada (member)`; an agent writing for somebody says for whom."""
    label = _person(line.get("sender"))
    on_behalf = line.get("on_behalf_of")
    if isinstance(on_behalf, dict):
        label += f" for {_person(on_behalf)}"
    return label


def line_text(line: dict[str, Any], *, full: bool = False) -> Text:
    """What the line says, on one line, then what is true of it, dimmed."""
    if line.get("deleted"):
        body = Text("(deleted)", style="dim italic")
    elif line.get("withheld"):
        body = Text("(withheld)", style="dim italic")
    else:
        body = Text(_one_line(_said(line), full=full))
    markers = _markers(line)
    if markers:
        body.append(f"  [{', '.join(markers)}]", style="dim")
    return body


def _said(line: dict[str, Any]) -> str:
    text = str(line.get("text") or "")
    files = [
        str(part.get("name") or part.get("path") or "file")
        for part in line.get("parts") or []
        if isinstance(part, dict) and part.get("type") == "file"
    ]
    if files:
        text = f"{text} (files: {', '.join(files)})".strip()
    return text


def _markers(line: dict[str, Any]) -> list[str]:
    markers: list[str] = []
    replies = line.get("reply_count") or 0
    if replies:
        markers.append(f"{replies} repl{'y' if replies == 1 else 'ies'}")
    if line.get("thread_root_id"):
        markers.append("in a thread")
    if line.get("edited_at") and not line.get("deleted"):
        markers.append("edited")
    if line.get("members_only"):
        markers.append("members only")
    question = line.get("question")
    if isinstance(question, dict):
        state = str(question.get("state") or "").lower()
        markers.append(f"question: {state}" if state else "question")
    return markers


def _person(ref: Any) -> str:
    if not isinstance(ref, dict):
        return "?"
    name = ref.get("name") or ref.get("id") or "?"
    kind = str(ref.get("kind") or "").lower()
    return f"{name} ({kind})" if kind else str(name)


def _channel_cell(channel: Any) -> Text:
    if not isinstance(channel, dict):
        return Text("")
    person = channel.get("person")
    name = channel.get("title") or (
        _person(person) if isinstance(person, dict) else str(channel.get("kind") or "")
    )
    cell = Text(str(name))
    if channel.get("id"):
        cell.append(f"\n{channel['id']}", style="dim")
    return cell


def _one_line(text: str, *, full: bool) -> str:
    flat = " ".join(text.split())
    if full or len(flat) <= _TEXT_MAX:
        return flat
    return flat[: _TEXT_MAX - 1].rstrip() + "…"


def _was_cut(line: dict[str, Any]) -> bool:
    return len(" ".join(_said(line).split())) > _TEXT_MAX


def _time(value: Any) -> str:
    """`2026-10-20 14:03`, in the zone the server gave."""
    text = str(value or "")
    return text[:16].replace("T", " ")


def _more_lines_hint(data: dict[str, Any]) -> str:
    if data.get("next_before_seq") is not None:
        return f"Older lines: --before-seq {data['next_before_seq']}"
    if data.get("next_after_seq") is not None:
        return f"Newer lines: --after-seq {data['next_after_seq']}"
    return "There is more."


def _hint(text: str) -> None:
    console.print(Text(text, style="dim"))
