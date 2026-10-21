"""`lemma chat -m` returns once the run it started has ended.

Driven over real HTTP against a loopback stand-in for the API, because the
failure lived in how the CLI reads a live Server-Sent Events connection: it read
until the server closed the stream, and a stream whose terminal frame was lost --
or whose connection was never closed after it -- sat on keepalives for ever. A
one-shot chat whose run had long since completed never returned.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core import background_waits, sse
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import conversations as conversations_command

POD_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
_NOW = "2026-01-01T00:00:00Z"

#: A chat that names an agent and passes --conversation first checks the
#: conversation belongs to that agent, so the fake answers both lookups.
_LOOKUPS = {
    f"/conversations/{CONVERSATION_ID}": {
        "id": CONVERSATION_ID,
        "pod_id": POD_ID,
        "user_id": str(uuid.uuid4()),
        "agent_id": AGENT_ID,
        "created_at": _NOW,
        "updated_at": _NOW,
        "last_activity_at": _NOW,
        "pod_cwd": "/",
        "workspace_cwd": "/",
    },
    "/agents/coder": {
        "id": AGENT_ID,
        "pod_id": POD_ID,
        "user_id": str(uuid.uuid4()),
        "name": "coder",
        "instruction": "",
        "permissions": {"agent_id": AGENT_ID, "agent_name": "coder"},
        "created_at": _NOW,
        "updated_at": _NOW,
    },
}

#: Far past anything a passing run takes, and short enough that a regression
#: fails the suite rather than hanging it.
GIVE_UP_SECONDS = 20.0


def _frame(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _status() -> bytes:
    return _frame(
        {"type": "status", "agent_run_id": RUN_ID, "data": {"started_new_run": True}}
    )


def _token(text: str) -> bytes:
    return _frame(
        {"type": "token", "agent_run_id": RUN_ID, "data": text, "kind": "text"}
    )


def _completed(
    conversation_status: str | None = None, *, waiting_on_work: bool | None = None
) -> bytes:
    data: dict[str, object] = {
        "conversation_id": CONVERSATION_ID,
        "status": "COMPLETED",
    }
    if conversation_status:
        data["conversation_status"] = conversation_status
    if waiting_on_work is not None:
        data["waiting_on_work"] = waiting_on_work
    return _frame({"type": "completed", "agent_run_id": RUN_ID, "data": data})


def _message(
    sequence: int, role: str, text: str, run_id: str | None, kind: str = "TEXT"
) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "conversation_id": CONVERSATION_ID,
        "sequence": sequence,
        "role": role,
        "kind": kind,
        "text": text,
        "agent_run_id": run_id,
        "created_at": _NOW,
    }


def _failed(reason: str) -> bytes:
    return _frame({"type": "error", "agent_run_id": RUN_ID, "data": reason})


@dataclass
class Scenario:
    """What the stand-in API sends on the send's stream, then on a reattach."""

    sent: list[bytes]
    #: After `sent`: "keepalive" holds the stream open on comment frames, the
    #: way the API does through a quiet run; "silent" holds it open with no
    #: bytes at all, the way a dead connection looks.
    then: str
    reattach: list[bytes] = field(default_factory=list)
    reattached_for: list[str | None] = field(default_factory=list)
    #: Once the message is sent, each conversation GET answers the next of
    #: these (status, has_pending_interaction) and then repeats the last.
    polls: list[tuple[str, bool]] = field(default_factory=list)
    polled: int = 0
    #: What a stream attached with no run id -- the resumed run -- carries.
    resumed: list[bytes] = field(default_factory=list)
    #: The conversation's persisted messages, as the message list returns them.
    messages: list[dict[str, object]] = field(default_factory=list)
    #: Written by the resumed run: listed once a poll has seen the wait end.
    resumed_messages: list[dict[str, object]] = field(default_factory=list)


def _message_page(
    messages: list[dict[str, object]], query: dict[str, list[str]]
) -> dict[str, object]:
    """The message list's paging: newest first by page, oldest first within."""
    limit = int(query.get("limit", ["100"])[0])
    after = int(query.get("after_sequence", ["-1"])[0])
    before = int(query.get("page_token", [str(1 << 62)])[0])
    newest = sorted(
        (m for m in messages if after < int(m["sequence"]) < before),  # type: ignore[arg-type]
        key=lambda m: int(m["sequence"]),  # type: ignore[arg-type]
        reverse=True,
    )
    page = newest[:limit]
    token = str(page[-1]["sequence"]) if len(newest) > limit else None
    return {"items": list(reversed(page)), "limit": limit, "next_page_token": token}


class _FakeApi:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.stopping = threading.Event()
        self.sent = False
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _open_stream(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

            def _json(self, body: object) -> None:
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _hold(self, how: str) -> None:
                while not api.stopping.is_set():
                    if how == "keepalive":
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    time.sleep(0.05)

            def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if not self.path.endswith(f"/conversations/{CONVERSATION_ID}/messages"):
                    self.send_error(404)
                    return
                api.sent = True
                self._open_stream()
                try:
                    for chunk in api.scenario.sent:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    self._hold(api.scenario.then)
                except BrokenPipeError, ConnectionResetError:
                    # The CLI hung up, which is what it is meant to do.
                    return

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                if url.path.endswith(f"/conversations/{CONVERSATION_ID}/messages"):
                    self._json(
                        _message_page(api.scenario.messages, parse_qs(url.query))
                    )
                    return
                lookup = next(
                    (
                        body
                        for tail, body in _LOOKUPS.items()
                        if url.path.endswith(tail)
                    ),
                    None,
                )
                polls = api.scenario.polls
                if (
                    lookup is not None
                    and api.sent
                    and polls
                    and "agents" not in url.path
                ):
                    status, pending = polls[min(api.scenario.polled, len(polls) - 1)]
                    api.scenario.polled += 1
                    if status != "WAITING" and api.scenario.resumed_messages:
                        api.scenario.messages.extend(api.scenario.resumed_messages)
                        api.scenario.resumed_messages = []
                    lookup = {
                        **lookup,
                        "status": status,
                        "has_pending_interaction": pending,
                    }
                if lookup is not None:
                    self._json(lookup)
                    return
                if not url.path.endswith(f"/conversations/{CONVERSATION_ID}/stream"):
                    self.send_error(404)
                    return
                run_id = parse_qs(url.query).get("agent_run_id", [None])[0]
                api.scenario.reattached_for.append(run_id)
                self._open_stream()
                for chunk in api.scenario.reattach if run_id else api.scenario.resumed:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, *_args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fast_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arrange quiet and dead streams to be noticed in a test's time."""
    monkeypatch.setattr(sse, "QUIET_RECHECK_SECONDS", 0.3)
    monkeypatch.setattr(conversations_command, "STREAM_READ_TIMEOUT_SECONDS", 0.5)


@pytest.fixture
def api(request: pytest.FixtureRequest) -> Iterator[_FakeApi]:
    fake = _FakeApi(request.param)
    yield fake
    fake.close()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll a waiting conversation without pausing between polls."""
    monkeypatch.setattr(background_waits, "FIRST_POLL_SECONDS", 0.0)


def _one_shot(base_url: str, *extra: str) -> tuple[int | None, str]:
    """Run `lemma chat -m` against `base_url`; (None, "") if it never returns."""
    outcome: dict[str, object] = {}

    def invoke() -> None:
        result = CliRunner().invoke(
            app,
            [
                "--base-url",
                base_url,
                "--token",
                "test-token",
                "--pod",
                POD_ID,
                "chat",
                "-a",
                "coder",
                "--conversation",
                CONVERSATION_ID,
                "-m",
                "Reply with exactly: OK",
                *extra,
            ],
        )
        outcome["exit_code"] = result.exit_code
        outcome["output"] = result.output

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(GIVE_UP_SECONDS)
    if worker.is_alive():
        return None, ""
    return outcome["exit_code"], str(outcome["output"])  # type: ignore[return-value]


@pytest.mark.parametrize(
    "api",
    [Scenario(sent=[_status(), _token("OK"), _completed()], then="keepalive")],
    indirect=True,
)
def test_a_completed_run_returns_even_if_the_stream_stays_open(
    api: _FakeApi, fast_rechecks: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "OK" in output
    assert api.scenario.reattached_for == [], "the ending was heard; nothing to ask"


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("partial"), _failed("Codex did not start in time")],
            then="keepalive",
        )
    ],
    indirect=True,
)
def test_a_failed_run_returns_non_zero_even_if_the_stream_stays_open(
    api: _FakeApi, fast_rechecks: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 1, output
    assert "Codex did not start in time" in output


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("OK")],
            then="keepalive",
            reattach=[_completed()],
        )
    ],
    indirect=True,
)
def test_a_run_whose_ending_never_arrived_is_asked_about(
    api: _FakeApi, fast_rechecks: None
) -> None:
    """The terminal frame was lost and the stream carries only keepalives."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "OK" in output
    assert api.scenario.reattached_for == [RUN_ID]


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("half")],
            then="silent",
            reattach=[_failed("Agent Host run deadline elapsed")],
        )
    ],
    indirect=True,
)
def test_a_dead_connection_to_a_failed_run_is_noticed_and_reported(
    api: _FakeApi, fast_rechecks: None
) -> None:
    """No bytes at all, not even keepalives: the read deadline notices."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 1, output
    assert "Agent Host run deadline elapsed" in output
    assert api.scenario.reattached_for == [RUN_ID]


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False), ("WAITING", False), ("RUNNING", False)],
            resumed=[_status(), _token("SUB: 391"), _completed("COMPLETED")],
        )
    ],
    indirect=True,
)
def test_a_run_waiting_on_a_sub_agent_is_followed_to_the_answer(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    """The run ends WAITING; the answer arrives in the run the wait resumes."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "SUB: 391" in output
    assert "waiting on the agent's background work" in output
    assert api.scenario.reattached_for == [None], "the resumed run, by no run id"
    assert api.scenario.polled == 3, "two polls see WAITING, the third RUNNING"


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Approve?"), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", True)],
            resumed=[_token("never streamed")],
        )
    ],
    indirect=True,
)
def test_a_run_waiting_on_a_person_returns_without_polling(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "Approve?" in output
    assert "resume:" in output
    assert api.scenario.polled == 1, "one look, no polling"
    assert api.scenario.reattached_for == []


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            # A task agent that ended WAITING for the person's reply holds no
            # pause, so only the frame can say nobody else will answer.
            sent=[
                _status(),
                _token("Send me the code."),
                _completed("WAITING", waiting_on_work=False),
            ],
            then="keepalive",
            polls=[("WAITING", False)],
            resumed=[_token("never streamed")],
        )
    ],
    indirect=True,
)
def test_a_server_saying_the_wait_is_the_persons_is_not_polled_at_all(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "resume:" in output
    assert api.scenario.polled == 0
    assert api.scenario.reattached_for == []


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Started a timer."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False)],
        )
    ],
    indirect=True,
)
def test_a_wait_past_the_timeout_leaves_the_resume_hint(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url, "--wait-timeout", "0")

    assert exit_code == 0, output
    assert "resume:" in output
    assert api.scenario.reattached_for == []


RESUMED_RUN_ID = str(uuid.uuid4())


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False), ("COMPLETED", False)],
            messages=[
                _message(1, "user", "Reply with exactly: OK", None),
                _message(2, "assistant", "Asking the sub-agent.", RUN_ID),
                _message(3, "assistant", "wait_for", RUN_ID, kind="TOOL_CALL"),
            ],
            resumed_messages=[_message(4, "assistant", "SUB: 391", RESUMED_RUN_ID)],
        )
    ],
    indirect=True,
)
def test_a_resumed_run_that_ended_between_polls_is_read_from_its_messages(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    """Attaching would replay only the ending; the answer is in the messages."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "SUB: 391" in output
    assert output.count("Asking the sub-agent.") == 1, "the paused run, once"
    assert "wait_for" not in output
    assert api.scenario.reattached_for == [], "nothing live to attach to"


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False), ("RUNNING", False)],
            # It finished between the poll and the attach: the stream replays
            # only its ending.
            resumed=[_completed("COMPLETED")],
            messages=[
                _message(1, "user", "Reply with exactly: OK", None),
                _message(2, "assistant", "Asking the sub-agent.", RUN_ID),
            ],
            resumed_messages=[_message(3, "assistant", "SUB: 391", RESUMED_RUN_ID)],
        )
    ],
    indirect=True,
)
def test_an_attach_that_replays_only_the_ending_reads_the_answer(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "SUB: 391" in output
    assert api.scenario.reattached_for == [None]


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False), ("COMPLETED", False)],
            messages=[
                _message(1, "user", "Reply with exactly: OK", None),
                _message(2, "assistant", "Asking the sub-agent.", RUN_ID),
            ],
            # More than one page of the message list.
            resumed_messages=[
                _message(3 + n, "assistant", f"line {n}", RESUMED_RUN_ID)
                for n in range(150)
            ],
        )
    ],
    indirect=True,
)
def test_an_answer_longer_than_one_page_is_read_in_full(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "line 0" in output and "line 149" in output


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            # An older server: no waiting_on_work, and nothing will come.
            sent=[_status(), _token("Send me the code."), _completed("WAITING")],
            then="keepalive",
            polls=[("WAITING", False)],
        )
    ],
    indirect=True,
)
def test_an_unmarked_wait_is_looked_at_briefly_not_for_the_full_timeout(
    api: _FakeApi,
    fast_rechecks: None,
    no_sleep: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background_waits, "UNMARKED_WAIT_SECONDS", 0.3)
    started = time.monotonic()
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "resume:" in output
    assert time.monotonic() - started < 10, "held for the full wait timeout"


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("COMPLETED", False)],
            # A quick sub-agent: the resumed run wrote its answer before the
            # CLI's first look, so the newest message is already the answer.
            messages=[
                _message(1, "user", "Reply with exactly: OK", None),
                _message(2, "assistant", "Asking the sub-agent.", RUN_ID),
                _message(3, "assistant", "SUB: 391", RESUMED_RUN_ID),
            ],
        )
    ],
    indirect=True,
)
def test_an_answer_written_before_the_first_look_is_still_shown(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "SUB: 391" in output
    assert output.count("Asking the sub-agent.") == 1


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("Asking the sub-agent."), _completed("WAITING")],
            then="keepalive",
            polls=[("COMPLETED", False)],
            # The resumed run wrote more than a page before the first look:
            # the paused run's own messages are on an older page.
            messages=[
                _message(1, "user", "Reply with exactly: OK", None),
                _message(2, "assistant", "Asking the sub-agent.", RUN_ID),
                *(
                    _message(3 + n, "assistant", f"line {n}", RESUMED_RUN_ID)
                    for n in range(150)
                ),
            ],
        )
    ],
    indirect=True,
)
def test_a_paused_run_pages_back_still_bounds_the_replay(
    api: _FakeApi, fast_rechecks: None, no_sleep: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "line 0" in output and "line 149" in output
    assert output.count("Asking the sub-agent.") == 1, "not replayed"
