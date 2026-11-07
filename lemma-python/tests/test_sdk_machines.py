from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from lemma_sdk.openapi_client.api.machine_processes import machine_process_start
from lemma_sdk.openapi_client.api.machines import machine_pod_create, machine_reset
from lemma_sdk.resources.machines import Machines, machine_ref, process_stream_url
from lemma_sdk.runtime import (
    FunctionContext,
    FunctionInvocationBinding,
    function_invocation_scope,
)
from lemma_sdk.transport import LemmaTransport

POD = uuid4()
PROCESS = str(uuid4())

Handler = Callable[[httpx.Request], httpx.Response]


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _wired(handler: Handler, base_url: str = "https://api.test") -> LemmaTransport:
    """A real transport whose HTTP goes to ``handler`` instead of a network."""
    transport = LemmaTransport(base_url=base_url, token="tok", max_retries=0)
    transport.generated.set_httpx_client(
        httpx.Client(
            base_url=base_url,
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer tok"},
        )
    )
    return transport


def _process(state: str = "running") -> dict[str, Any]:
    return {
        "id": PROCESS,
        "command": "echo hi",
        "cwd": "/home/user/lemma",
        "state": state,
    }


# -- refs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "pod", "expected"),
    [
        ("me", None, "me"),
        ("", None, "me"),
        ("mine:mac", POD, "mine:mac"),
        ("pod:abc/builder", None, "pod:abc:builder"),
        ("pod:abc:builder", None, "pod:abc:builder"),
        ("pod:builder", POD, f"pod:{POD}:builder"),
        ("pod:builder", None, "pod:builder"),
    ],
)
def test_refs_travel_in_the_colon_form(
    ref: str, pod: UUID | None, expected: str
) -> None:
    assert machine_ref(ref, pod_id=pod) == expected


def test_the_stream_url_is_on_the_api_host_with_the_ref_escaped() -> None:
    assert process_stream_url("https://api.test/", "pod:a:b", "p1") == (
        "wss://api.test/machines/pod%3Aa%3Ab/processes/p1/stream"
    )
    assert process_stream_url("http://localhost:8710", "me", "p1").startswith(
        "ws://localhost:8710/machines/me/"
    )


# -- bodies ------------------------------------------------------------------


def test_a_process_starts_with_its_terminal_and_the_bound_pod() -> None:
    transport = RecordingTransport()

    Machines(transport, pod_id=POD).processes.start(  # type: ignore[arg-type]
        "pod:builder", "bash -l", tty=(120, 40), env={"A": "1"}
    )

    endpoint, path_args, kwargs = transport.calls[0]
    assert endpoint is machine_process_start
    assert path_args == (f"pod:{POD}:builder",)
    assert kwargs["body"].to_dict() == {
        "command": "bash -l",
        "env": {"A": "1"},
        "tty": {"cols": 120, "rows": 40},
    }


def test_reset_sends_its_scopes_and_both_switches() -> None:
    transport = RecordingTransport()

    Machines(transport).reset(  # type: ignore[arg-type]
        "me", ["home", "caches"], confirm=True, forget_logins=True
    )

    endpoint, path_args, kwargs = transport.calls[0]
    assert endpoint is machine_reset
    assert path_args == ("me",)
    assert kwargs["body"].to_dict() == {
        "scopes": ["home", "caches"],
        "confirm": True,
        "forget_logins": True,
    }


def test_a_pod_machine_is_created_in_the_bound_pod() -> None:
    transport = RecordingTransport()

    Machines(transport, pod_id=POD).create(  # type: ignore[arg-type]
        "builder", template="workspace-docker", visibility="restricted", run_slots=2
    )

    endpoint, path_args, kwargs = transport.calls[0]
    assert endpoint is machine_pod_create
    assert path_args == (POD,)
    assert kwargs["body"].to_dict() == {
        "name": "builder",
        "template": "workspace-docker",
        "visibility": "restricted",
        "run_slots": 2,
    }


# -- files -----------------------------------------------------------------


def test_a_write_sends_raw_bytes_and_a_read_returns_them() -> None:
    stored: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.params["path"]
        if request.method == "PUT":
            assert request.url.path == "/machines/me/files/content"
            stored[path] = request.read()
            return httpx.Response(
                200,
                json={
                    "kind": "file",
                    "modified_at": "2026-10-22T00:00:00Z",
                    "name": "a.txt",
                    "path": path,
                    "size_bytes": len(stored[path]),
                },
            )
        assert request.url.path == "/machines/me/files:content"
        return httpx.Response(200, content=stored[path])

    files = Machines(_wired(handler)).files

    entry = files.write("me", "/home/user/lemma/a.txt", "héllo")

    assert entry.size_bytes == len("héllo".encode())
    assert files.read("me", "/home/user/lemma/a.txt") == "héllo".encode()


def test_a_failed_write_raises_the_typed_error() -> None:
    from lemma_sdk.errors import LemmaAPIError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"detail": "Write at most 64 MB per request."})

    with pytest.raises(LemmaAPIError, match="64 MB"):
        Machines(_wired(handler)).files.write("me", "/home/user/a", b"x")


def test_opening_a_preview_reads_the_redirect_rather_than_following_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/machine-previews/abc:open"
        return httpx.Response(
            302, headers={"location": "https://abc.preview.test/?t=1"}
        )

    assert (
        Machines(_wired(handler)).previews.open_url("abc")
        == "https://abc.preview.test/?t=1"
    )


# -- exec / run ----------------------------------------------------------------


def test_run_polls_output_until_the_process_stops() -> None:
    pages = iter(
        [
            {
                "chunks": [{"sequence": 0, "channel": "stdout", "data": "hi\n"}],
                "next_sequence": 1,
                "state": "running",
            },
            {
                "chunks": [{"sequence": 1, "channel": "stderr", "data": "warn\n"}],
                "next_sequence": 2,
                "state": "failed",
                "exit_code": 3,
            },
        ]
    )
    afters: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert json.loads(request.read())["command"] == "echo hi"
            return httpx.Response(201, json=_process())
        afters.append(request.url.params["after"])
        return httpx.Response(200, json=next(pages))

    result = Machines(_wired(handler)).run("me", "echo hi", via="poll")

    assert result.exit_code == 3
    assert result.state == "failed"
    assert result.stdout == "hi\n"
    assert result.stderr == "warn\n"
    assert result.output == "hi\nwarn\n"
    assert afters == ["0", "1"]


@contextmanager
def _socket_server(frames: list[dict[str, Any]], received: list[str]) -> Iterator[str]:
    from websockets.sync.server import serve

    def handler(connection: Any) -> None:
        received.append(connection.request.headers.get("Authorization", ""))
        received.append(connection.request.path)
        connection.send(frames[0] and json.dumps(frames[0]))
        received.append(connection.recv(timeout=5))
        for frame in frames[1:]:
            connection.send(json.dumps(frame))

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.socket.getsockname()[:2]
            yield f"http://{host}:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_exec_streams_over_the_socket_and_types_into_it() -> None:
    received: list[str] = []
    frames = [
        {"type": "output", "sequence": 0, "channel": "pty", "data": "$ "},
        {"type": "output", "sequence": 1, "channel": "pty", "data": "ok\r\n"},
        {"type": "exit", "state": "succeeded", "exit_code": 0},
    ]
    with _socket_server(frames, received) as base_url:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json=_process())

        stream = Machines(_wired(handler, base_url)).exec(
            "mine:mac", "bash", tty=(80, 24), via="socket"
        )
        assert stream.live
        chunks = iter(stream)
        first = next(chunks)
        stream.send_input("echo ok\n")
        rest = list(chunks)

    assert [c.data for c in [first, *rest]] == ["$ ", "ok\r\n"]
    assert stream.exit_code == 0
    assert stream.state == "succeeded"
    assert received[0] == "Bearer tok"
    assert received[1] == f"/machines/mine%3Amac/processes/{PROCESS}/stream"
    assert json.loads(received[2]) == {"type": "input", "data": "echo ok\n"}


# -- the function context ------------------------------------------------------


def test_ctx_machines_acts_as_the_run_and_closes_with_it() -> None:
    binding = FunctionInvocationBinding(
        base_url="https://api.test", token="run-token", pod_id=POD
    )
    ctx = FunctionContext(pod_id=POD, function_id="fn")

    with function_invocation_scope(binding):
        machines = ctx.machines
        assert machines is ctx.machines
        assert machines.pod_id == str(POD)
        assert machines._ref("pod:builder") == f"pod:{POD}:builder"
        assert machines._transport.generated.token == "run-token"
        assert binding._resources

    assert not binding._resources


def test_ctx_machines_outside_a_run_says_so() -> None:
    with pytest.raises(RuntimeError, match="ctx.machines"):
        _ = FunctionContext(pod_id=POD, function_id="fn").machines


def test_a_large_read_is_fetched_a_page_at_a_time(monkeypatch, tmp_path) -> None:
    from lemma_sdk.resources import machines as machines_mod

    monkeypatch.setattr(machines_mod, "MAX_READ_BYTES", 4)
    content = b"0123456789"
    asked: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset, length = request.url.params["offset"], request.url.params["length"]
        asked.append((offset, length))
        start = int(offset)
        return httpx.Response(200, content=content[start : start + int(length)])

    target = Machines(_wired(handler)).files.download_to(
        "me", "big.bin", tmp_path / "big.bin"
    )

    assert target.read_bytes() == content
    assert asked == [("0", "4"), ("4", "4"), ("8", "4")]
