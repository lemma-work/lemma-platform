"""Following a one-shot chat through the agent's background waits.

An agent that delegates to a sub-agent, or waits on a process or a timer, ends
its run there: the run completes with the conversation WAITING, and the answer
arrives in a later run the server starts when the wait resolves. `lemma chat -m`
stopped at the first run's ending and printed only the conversation id, so the
reply landed a minute later in a run nobody was watching.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

from .io import to_plain
from .sse import SseResponse, StreamEvent
from .state import CliState, err_console

#: How long `lemma chat` follows a conversation through background waits.
DEFAULT_WAIT_TIMEOUT_SECONDS = 900.0

#: Polls of a waiting conversation start this far apart and double up to the
#: cap: a sub-agent often answers in seconds, a timer may take minutes.
FIRST_POLL_SECONDS = 0.5
MAX_POLL_SECONDS = 5.0
#: How long to look when the server does not say what the wait is on (an
#: older server): long enough for a quick sub-agent, short enough that a wait
#: on a person -- with no run to come -- does not hold the command.
UNMARKED_WAIT_SECONDS = 30.0


def waits_in_background(terminal: StreamEvent | None) -> bool:
    """Whether a run ended leaving its conversation WAITING on the agent's own
    work -- a sub-agent, a process, a timer -- with a run to come.

    The server says which in ``waiting_on_work``. Without it (an older server)
    any WAITING ending is a candidate, and the conversation read decides.
    """
    if terminal is None or terminal.type.lower() != "completed":
        return False
    data = terminal.data if isinstance(terminal.data, dict) else {}
    if str(data.get("conversation_status") or "").upper() != "WAITING":
        return False
    return data.get("waiting_on_work") is not False


def follow_background_waits(
    state: CliState,
    conversations: Any,
    conversation_id: str,
    terminal: StreamEvent | None,
    *,
    render: Callable[[SseResponse], StreamEvent | None],
    open_stream: Callable[[], SseResponse],
    wait_timeout: float,
) -> None:
    """Render each run the conversation resumes into, until one ends it.

    Stops, leaving the caller to print its resume hint, when the conversation
    waits on a person -- a question or an approval nobody here will answer --
    or when ``wait_timeout`` passes with it still waiting.
    """
    overall = time.monotonic() + wait_timeout
    announced = False
    while waits_in_background(terminal):
        marked = (
            terminal is not None
            and isinstance(terminal.data, dict)
            and (terminal.data.get("waiting_on_work") is True)
        )
        deadline = min(
            overall,
            time.monotonic() + (wait_timeout if marked else UNMARKED_WAIT_SECONDS),
        )
        # The paused run's last message: whatever a resumed run writes comes
        # after it, even if it is already written by the time this reads.
        after = _paused_run_end(conversations, conversation_id, terminal)
        delay = FIRST_POLL_SECONDS
        while True:
            conversation = to_plain(conversations.get(conversation_id)) or {}
            if conversation.get("has_pending_interaction"):
                return
            if str(conversation.get("status") or "").upper() != "WAITING":
                break
            if not announced and state.output != "json":
                err_console.print("[dim]waiting on the agent's background work…[/dim]")
                announced = True
            if time.monotonic() + delay > deadline:
                return
            time.sleep(delay)
            delay = min(delay * 2, MAX_POLL_SECONDS)
        status = str(conversation.get("status") or "").upper()
        if status in {"RUNNING", "STOP_REQUESTED"}:
            # No run id: the server attaches to whichever run is active now.
            live = _Watched(open_stream())
            terminal = render(live)
            if live.saw_text:
                continue
            # It finished between the poll and the attach, so the stream
            # replayed only its ending: the answer is in the messages.
            status = _ending_status(terminal) or status
        # The resumed run finished unwatched. Attaching would replay only its
        # ending, so read what it said instead.
        terminal = render(
            _resumed_messages(
                conversations,
                conversation_id,
                after,
                status,
                output=conversation.get("output"),
            )
        )


def _paused_run_end(
    conversations: Any, conversation_id: str, paused: StreamEvent | None
) -> int:
    """The sequence of the paused run's last message, or of the last message
    before any run's if it wrote none. Not simply the newest message: a quick
    resumed run may already have written its answer, which must stay after --
    and may have written pages of it, so this reads back until it finds the
    paused run's own messages."""
    run_id = paused.agent_run_id if paused else None
    token: str | None = None
    while True:
        listed = (
            to_plain(conversations.messages(conversation_id, page_token=token)) or {}
        )
        items = listed.get("items") or []
        own = [
            m.get("sequence") or 0
            for m in items
            if run_id and str(m.get("agent_run_id")) == run_id
        ]
        if own:
            return max(own)
        # A message no run wrote (the person's) sits before the paused run.
        before_runs = [
            m.get("sequence") or 0 for m in items if not m.get("agent_run_id")
        ]
        if before_runs:
            return max(before_runs)
        token = listed.get("next_page_token")
        if not token:
            return 0


def _messages_after(conversations: Any, conversation_id: str, after: int) -> list:
    """Every message after ``after``, oldest first, across all pages."""
    items: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        listed = (
            to_plain(
                conversations.messages(
                    conversation_id, after_sequence=after, page_token=token
                )
            )
            or {}
        )
        items.extend(listed.get("items") or [])
        token = listed.get("next_page_token")
        if not token:
            return sorted(items, key=lambda m: m.get("sequence") or 0)


def _ending_status(terminal: StreamEvent | None) -> str | None:
    if terminal is None:
        return None
    data = terminal.data if isinstance(terminal.data, dict) else {}
    found = data.get("conversation_status") or data.get("status")
    return str(found).upper() if found else None


def _resumed_messages(
    conversations: Any,
    conversation_id: str,
    after: int,
    status: str,
    *,
    output: Any = None,
) -> SseResponse:
    """The answer of runs that ended unwatched, as the frames a stream sends.

    Everything after ``after`` -- the newest message when the wait began -- is
    theirs. Played through the ordinary renderer, so text and `--output json`
    read it as they would have read it live -- the ending too, which carries
    the conversation and the run's output as the server's own ``completed``
    does, the output read off the conversation.
    """
    frames: list[dict[str, Any]] = []
    run_id: str | None = None
    for message in _messages_after(conversations, conversation_id, after):
        if message.get("role") != "assistant":
            continue
        run_id = str(message.get("agent_run_id") or run_id)
        if message.get("kind") == "TEXT" and message.get("text"):
            frames.append({"type": "token", "data": message["text"], "kind": "text"})
    if status == "FAILED":
        frames.append({"type": "error", "data": "The resumed run failed."})
    elif status == "STOPPED":
        frames.append({"type": "stopped", "data": {"status": status}})
    else:
        ending: dict[str, Any] = {
            "conversation_id": conversation_id,
            "status": status,
            "conversation_status": status,
        }
        if output is not None:
            ending["output_data"] = output
        frames.append({"type": "completed", "data": ending})
    return _Replayed([{**frame, "agent_run_id": run_id} for frame in frames])


class _Watched:
    """A live stream, noting whether it carried any of the run's text."""

    def __init__(self, stream: SseResponse) -> None:
        self._stream = stream
        self.saw_text = False

    def iter_lines(self) -> Iterator[str]:
        for line in self._stream.iter_lines():
            if line.startswith("data:") and (
                '"type": "token"' in line
                or '"type":"token"' in line
                or '"type": "message"' in line
                or '"type":"message"' in line
            ):
                self.saw_text = True
            yield line

    def close(self) -> None:
        self._stream.close()


class _Replayed:
    """Frames already in hand, read the way a live stream is."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames

    def iter_lines(self) -> Iterator[str]:
        for frame in self._frames:
            yield f"data: {json.dumps(frame)}"
            yield ""

    def close(self) -> None:
        pass
