"""Reading a run's Server-Sent Events stream: frames, and following one run
to its end."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol


class SseResponse(Protocol):
    """The part of an ``httpx.Response`` a stream is read through."""

    def iter_lines(self) -> Iterator[str]: ...

    def close(self) -> None: ...


TERMINAL_EVENTS = {"completed", "stopped", "error"}


@dataclass(frozen=True)
class StreamEvent:
    type: str
    data: Any
    agent_run_id: str | None = None
    #: Which channel a token delta belongs to -- "text", "thinking" or "tool".
    #: A sibling of ``data`` on the wire, not nested inside it: the server sends
    #: ``{"type":"token","data":"...","kind":"tool"}`` and ``data`` is always a
    #: bare string. Reading it from the wrong place is what let a tool call
    #: reach users as answer text.
    kind: str | None = None


#: How long a run's stream may carry nothing but keepalives before the CLI asks
#: whether the run has ended. The server sends one every 15 seconds through a
#: quiet run, so this is a few of them: a model thinking for this long is
#: ordinary, and asking costs one request.
QUIET_RECHECK_SECONDS = 45.0

#: The longest a run's stream may go with no bytes at all, keepalives included.
#: Past this the connection is dead rather than the run quiet.
STREAM_READ_TIMEOUT_SECONDS = 60.0

#: How many times in a row the CLI reattaches to a run and learns nothing --
#: the stream ends or breaks without a frame -- before it gives up on hearing
#: the outcome.
MAX_FRUITLESS_REATTACHES = 3

#: How long to wait before reattaching to a stream that ended or broke without
#: telling us anything.
REATTACH_PAUSE_SECONDS = 1.0

#: Frames that say nothing about how the run is going: the opening status, and
#: the server reporting that its own subscription gave out.
_NO_PROGRESS = {"status", "stream_error"}


class StreamWentQuiet(Exception):
    """A run's stream carried only keepalives for ``quiet_after`` seconds."""


def iter_sse_events(
    response: Any,
    *,
    quiet_after: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterable[StreamEvent]:
    """Parse a Server-Sent Events response into frames.

    With ``quiet_after``, raise ``StreamWentQuiet`` once that long has passed
    with nothing but keepalive comments. Checked when a keepalive arrives, so
    it needs the server to be sending them; a stream with no bytes at all is
    the read timeout's to catch.
    """
    data_lines: list[str] = []
    last_frame = clock()

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
            kind=str(payload["kind"]) if payload.get("kind") else None,
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
                last_frame = clock()
                yield event
            continue
        if line.startswith(":"):
            _raise_if_quiet(quiet_after, clock() - last_frame)
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue
        if line.startswith(("event:", "id:", "retry:")):
            continue
        data_lines.append(line)

    event = flush()
    if event is not None:
        yield event


def _raise_if_quiet(quiet_after: float | None, silent_for: float) -> None:
    if quiet_after is not None and silent_for >= quiet_after:
        raise StreamWentQuiet()


#: Reattach to a run by id: a live stream of it, or its terminal event replayed
#: when it has already ended.
Reattach = Callable[[str], SseResponse]


def follow_run(
    response: SseResponse,
    reattach: Reattach | None,
    *,
    quiet_after: float | None = None,
) -> Iterator[StreamEvent]:
    """The frames of one run, ending with its terminal event.

    A send's stream is a live subscription to the run's realtime channel, and
    that channel is best-effort: a terminal frame lost in transit left the
    stream on keepalives for ever -- the server only closes it once it relays
    the ending -- and the one-shot `lemma chat -m` waiting on it never returned,
    even for a run that had long since completed. So the stream is not trusted
    to say when to stop:

    - the first terminal frame ends it, whatever the connection does next;
    - a stream that goes quiet, ends, or breaks before one arrives is
      reattached by run id, which replays the ending of a run that is over and
      resumes a run that is not. Nothing is replayed except that ending, so a
      quiet stream loses nothing by it.

    Without ``reattach`` -- or before any frame has named the run -- this is
    the stream as it comes.
    """
    # Going quiet means something only to a caller that can ask about it.
    recheck_after = (
        (QUIET_RECHECK_SECONDS if quiet_after is None else quiet_after)
        if reattach is not None
        else None
    )
    run_id: str | None = None
    fruitless = 0
    while True:
        segment = yield from _read_segment(
            response, run_id, recheck_after, may_reattach=reattach is not None
        )
        run_id = segment.run_id
        if segment.ended or reattach is None or run_id is None:
            return
        if not segment.quiet:
            fruitless = 0 if segment.progressed else fruitless + 1
            if fruitless >= MAX_FRUITLESS_REATTACHES:
                return
            # The stream ended or broke rather than going quiet; do not turn a
            # server that is closing every stream into a tight loop.
            if not segment.progressed:
                time.sleep(REATTACH_PAUSE_SECONDS)
        response = reattach(run_id)


@dataclass(frozen=True)
class _Segment:
    """How one connection to a run's stream ended."""

    run_id: str | None
    #: It carried the run's terminal frame.
    ended: bool = False
    #: It carried a frame about the run, not just the opening status.
    progressed: bool = False
    #: It went quiet on keepalives rather than ending or breaking.
    quiet: bool = False


def _read_segment(
    response: SseResponse,
    run_id: str | None,
    recheck_after: float | None,
    *,
    may_reattach: bool,
) -> Generator[StreamEvent, None, _Segment]:
    """Yield one connection's frames up to and including a terminal one."""
    import httpx

    progressed = False
    try:
        for event in iter_sse_events(response, quiet_after=recheck_after):
            run_id = run_id or event.agent_run_id
            progressed = progressed or event.type.lower() not in _NO_PROGRESS
            yield event
            if event.type.lower() in TERMINAL_EVENTS:
                return _Segment(run_id, ended=True, progressed=True)
    except StreamWentQuiet:
        # Keepalives were arriving: the server is there and simply has not said
        # the run ended. Asking is not a failure to count.
        return _Segment(run_id, progressed=progressed, quiet=True)
    except httpx.TransportError:
        # Includes the read timeout: no bytes at all, not even keepalives.
        if not may_reattach or run_id is None:
            raise
    finally:
        response.close()
    return _Segment(run_id, progressed=progressed)
