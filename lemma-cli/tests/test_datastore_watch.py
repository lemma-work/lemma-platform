"""Unit tests for `lemma datastore watch` helpers (URL, cursor, rendering)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from lemma_cli.cli_core.state import CliState
from lemma_cli.cli_core.watch import _compact_payload, _handle_message
from lemma_sdk.live import LiveSession, live_ws_url


def _state(output: str = "json") -> CliState:
    return CliState(
        config_path=Path("/tmp/lemma-test-config.json"),
        config={},
        base_url="https://api.example.test",
        auth_url=None,
        token="tok",
        timeout=5.0,
        no_verify_ssl=False,
        output=output,
    )


def test_live_url_scheme():
    assert live_ws_url("https://api.x.dev", "POD") == "wss://api.x.dev/pods/POD/live"
    assert (
        live_ws_url("http://localhost:8711/", "POD")
        == "ws://localhost:8711/pods/POD/live"
    )
    # Bare host (no scheme) assumes TLS, like a browser.
    assert live_ws_url("//host", "POD") == "wss://host/pods/POD/live"


def test_ready_then_subscribe_resumes_from_since():
    session = LiveSession()
    session.add(table="notes", since="c.7")
    assert _handle_message(_state(), session, json.dumps({"type": "ready"})) is True
    [frame] = [json.loads(f) for f in session.subscribe_frames()]
    assert frame == {
        "op": "subscribe",
        "id": "s1",
        "topic": "records",
        "table": "notes",
        "since": "c.7",
    }


def test_changes_advance_the_cursor_and_print_ndjson_in_the_old_shape(capsys):
    state = _state(output="json")
    session = LiveSession()
    session.add(table="notes")
    _handle_message(
        state,
        session,
        json.dumps(
            {
                "type": "changes",
                "id": "s1",
                "cursor": "c.12",
                "changes": [
                    {
                        "op": "insert",
                        "table": "notes",
                        "record_id": "abc",
                        "row": {"body": "hi"},
                    }
                ],
            }
        ),
    )
    assert session.cursor("s1") == "c.12"
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {
        "type": "datastore.record.insert",
        "table_name": "notes",
        "record_id": "abc",
        "operation": "insert",
        "payload": {"body": "hi"},
        "cursor": "c.12",
        "stream_id": "c.12",
    }


def test_resync_is_reported_and_moves_the_cursor(capsys):
    session = LiveSession()
    session.add(table="notes", since="c.1")
    _handle_message(
        _state(output="json"),
        session,
        json.dumps(
            {"type": "resync", "id": "s1", "cursor": "c.40", "reason": "too_many"}
        ),
    )
    assert session.cursor("s1") == "c.40"
    assert json.loads(capsys.readouterr().out) == {
        "type": "resync",
        "cursor": "c.40",
        "reason": "too_many",
    }


def test_invalid_or_unknown_messages_keep_cursor():
    state = _state()
    session = LiveSession()
    session.add(since="c.3")
    assert _handle_message(state, session, "not json") is False
    assert _handle_message(state, session, json.dumps([1, 2])) is False
    assert _handle_message(state, session, None) is False
    assert session.cursor("s1") == "c.3"


def test_compact_payload():
    assert _compact_payload({"body": "hi", "n": 3}, full=False) == "body=hi, n=3"
    assert _compact_payload({}, full=False) == ""
    long = _compact_payload({"k": "x" * 200}, full=False)
    assert long.endswith("…") and len(long) <= 120


# --- reconnect loop -----------------------------------------------------------


def _rotated_session_state(tmp_path):
    """A state whose on-disk tokens have already been rotated by someone else.

    This is the real branch that made the loop spin: `refresh_auth_session`
    finds a newer pair on disk, adopts it and returns True -- without this
    process ever proving the new access token is accepted. No patching, no
    network; the concurrent-rotation path is reached exactly as it is in
    production.
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": {"default": {}},
                "auth": {"access_token": "NEW", "refresh_token": "NEW-REFRESH"},
            }
        )
    )
    return CliState(
        config_path=config_path,
        config={"auth": {"access_token": "OLD", "refresh_token": "OLD-REFRESH"}},
        base_url="https://api.example.test",
        auth_url=None,
        token=None,  # a LEMMA_TOKEN would disable refresh entirely
        timeout=5.0,
        no_verify_ssl=False,
        output="json",
    )


def _refusing_server(monkeypatch, *, give_up_after: int):
    """Stub `websockets` so every connection is refused with a 401.

    Third-party, in front of the subject -- the watch loop's own collaborators
    are left alone so the real refresh logic runs.
    """
    import sys
    import types

    from websockets.exceptions import InvalidStatus

    class _Response:
        status_code = 401

    attempts = {"n": 0}

    def _connect(*_args, **_kwargs):
        attempts["n"] += 1
        # The spin this test exists to catch reaches no sleep at all, so the
        # bound has to live here or a regression hangs instead of failing.
        if attempts["n"] > give_up_after:
            raise KeyboardInterrupt
        raise InvalidStatus(_Response())

    stub = types.ModuleType("websockets")
    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "websockets", stub)
    return attempts


def test_a_refresh_that_does_not_help_stops_instead_of_spinning(
    monkeypatch, tmp_path
) -> None:
    """One immediate reconnect, then the ordinary failure path.

    The old code treated a successful refresh as licence to `continue`, past
    both the sleep and the backoff increment. When the refreshed token is
    refused as well -- which is the whole point of the concurrent-rotation
    branch, since it never checked -- that is an unbounded loop at zero delay.
    """
    import asyncio

    from lemma_cli.cli_core import watch as watch_module

    attempts = _refusing_server(monkeypatch, give_up_after=5)
    state = _rotated_session_state(tmp_path)

    with pytest.raises((typer.Exit, KeyboardInterrupt)):
        asyncio.run(watch_module._run(state, "POD", None, None))

    assert attempts["n"] == 2, (
        f"connected {attempts['n']} times; expected one refusal, one retry after "
        "the refresh, then a clean stop"
    )


def test_a_token_from_the_environment_never_refreshes(monkeypatch, tmp_path) -> None:
    """`LEMMA_TOKEN` disables refresh, so the first refusal is terminal."""
    import asyncio

    from lemma_cli.cli_core import watch as watch_module

    attempts = _refusing_server(monkeypatch, give_up_after=5)
    state = _rotated_session_state(tmp_path)
    state.token = "tok"

    with pytest.raises((typer.Exit, KeyboardInterrupt)):
        asyncio.run(watch_module._run(state, "POD", None, None))

    assert attempts["n"] == 1


def _closing_server(monkeypatch, *, code: int, give_up_after: int):
    """Stub `websockets` so every handshake is accepted, then closed with *code*.

    The shape the server now uses for a rejected session: an HTTP 401 never
    reaches a browser as anything but 1006, so it accepts and closes 4401.
    """
    import sys
    import types

    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    attempts = {"n": 0}

    class _Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionClosedError(Close(code, "rejected"), None)

    def _connect(*_args, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] > give_up_after:
            raise KeyboardInterrupt
        return _Socket()

    stub = types.ModuleType("websockets")
    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "websockets", stub)
    return attempts


def test_a_4401_close_refreshes_once_then_stops(monkeypatch, tmp_path) -> None:
    """Accept-then-4401 is an auth failure: one refresh, one retry, then stop.

    Treating it as a dropped connection would reconnect forever, and because
    the socket did open, the backoff would reset to zero on every attempt.
    """
    import asyncio

    from lemma_cli.cli_core import watch as watch_module

    attempts = _closing_server(monkeypatch, code=4401, give_up_after=5)
    state = _rotated_session_state(tmp_path)

    with pytest.raises((typer.Exit, KeyboardInterrupt)):
        asyncio.run(watch_module._run(state, "POD", None, None))

    assert attempts["n"] == 2


def test_a_4401_close_with_an_environment_token_stops_at_once(
    monkeypatch, tmp_path
) -> None:
    import asyncio

    from lemma_cli.cli_core import watch as watch_module

    attempts = _closing_server(monkeypatch, code=4401, give_up_after=5)
    state = _rotated_session_state(tmp_path)
    state.token = "tok"

    with pytest.raises((typer.Exit, KeyboardInterrupt)):
        asyncio.run(watch_module._run(state, "POD", None, None))

    assert attempts["n"] == 1


@pytest.mark.parametrize("code", [4403, 4404])
def test_a_forbidden_or_not_found_close_stops_without_retrying(
    monkeypatch, tmp_path, code
) -> None:
    """4403/4404 cannot be fixed by retrying; reconnecting would spin forever."""
    import asyncio

    from lemma_cli.cli_core import watch as watch_module

    attempts = _closing_server(monkeypatch, code=code, give_up_after=5)
    state = _rotated_session_state(tmp_path)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(watch_module._run(state, "POD", None, None))

    assert excinfo.value.exit_code != 0
    assert attempts["n"] == 1


# --- TLS -----------------------------------------------------------------------


def test_a_plain_ws_url_passes_no_ssl_argument() -> None:
    from lemma_cli.cli_core.watch import _ssl_option

    assert _ssl_option("ws://localhost:8711/x", verify_ssl=True) is None
    assert _ssl_option("ws://localhost:8711/x", verify_ssl=False) is None


def test_a_wss_url_always_gets_a_tls_context() -> None:
    """``None`` makes websockets refuse a wss:// URI outright, and ``False``
    would mean no TLS at all rather than TLS without checks."""
    import ssl

    from lemma_cli.cli_core.watch import _ssl_option

    verified = _ssl_option("wss://api.example.test/x", verify_ssl=True)
    unverified = _ssl_option("wss://api.example.test/x", verify_ssl=False)

    assert isinstance(verified, ssl.SSLContext)
    assert verified.verify_mode is ssl.CERT_REQUIRED and verified.check_hostname
    assert isinstance(unverified, ssl.SSLContext)
    assert unverified.verify_mode is ssl.CERT_NONE
    assert not unverified.check_hostname


@pytest.fixture
def self_signed_tls_server(tmp_path):
    """A server-side TLS context behind a certificate nobody trusts."""
    import shutil
    import ssl
    import subprocess

    if shutil.which("openssl") is None:
        pytest.skip("openssl is needed to mint the test certificate")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


async def test_watch_connects_to_a_wss_server_with_verification_off(
    self_signed_tls_server,
) -> None:
    """Against a real TLS websocket server on loopback, not a stub."""
    import ssl

    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    from lemma_cli.cli_core.watch import _ssl_option

    async def _echo(websocket) -> None:
        async for message in websocket:
            await websocket.send(message)

    async with serve(_echo, "127.0.0.1", 0, ssl=self_signed_tls_server) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"wss://127.0.0.1:{port}/"

        async with connect(url, ssl=_ssl_option(url, verify_ssl=False)) as ws:
            await ws.send("ping")
            assert await ws.recv() == "ping"

        # Verification on is really on: the untrusted certificate is refused.
        with pytest.raises(ssl.SSLCertVerificationError):
            async with connect(url, ssl=_ssl_option(url, verify_ssl=True)):
                pass


def test_each_json_line_is_flushed_as_it_is_written(monkeypatch):
    """QA #7: piped stdout is block-buffered, and a watch never ends on its own.

    Unflushed, the NDJSON lines sat in the buffer, so a pipeline saw nothing
    until 8 KiB had piled up, and a watch that was stopped lost them all. Each
    line has to reach the file descriptor when it is printed.
    """
    import io
    import sys

    sink = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(sink, encoding="utf-8"))
    session = LiveSession()
    session.add(table="notes")

    _handle_message(
        _state(output="json"),
        session,
        json.dumps(
            {
                "type": "changes",
                "id": "s1",
                "cursor": "c.1",
                "changes": [{"op": "insert", "table": "notes", "record_id": "a"}],
            }
        ),
    )

    written = sink.getvalue().decode()
    assert written.endswith("\n"), f"the line is still in the buffer: {written!r}"
    assert json.loads(written)["record_id"] == "a"


def test_sigterm_stops_the_watch_and_flushes_what_it_printed():
    """`timeout 60 lemma datastore watch --output json > changes.ndjson`.

    Python dies on SIGTERM without flushing stdout, so the file came out empty.
    It stops the way Ctrl-C does: cleanly, with whatever was printed written.
    A subprocess, because the old behaviour this pins is the process dying.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import asyncio, os, signal
        from lemma_cli.cli_core import watch

        async def run(state, pod_id, table, since):
            print('{"type": "ready"}')  # buffered: stdout is a pipe
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(30)

        watch._run = run
        watch.watch_datastore_changes(None, "POD", None, None)
        """
    )

    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )

    assert finished.returncode == 0, (finished.returncode, finished.stderr)
    assert finished.stdout == '{"type": "ready"}\n'
    assert "Stopped." in finished.stderr


def test_a_reader_that_leaves_stops_the_watch_rather_than_reconnecting(monkeypatch):
    """Flushing per line means `| head -1` breaks the pipe on the next line.

    `BrokenPipeError` is an `OSError`, which the watch loop reads as a dropped
    socket and reconnects on -- forever, writing to nobody. It has to arrive as
    something the loop does not catch.
    """
    import sys

    from lemma_cli.cli_core import watch as watch_module

    class _ClosedPipe:
        def write(self, _text):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdout", _ClosedPipe())

    with pytest.raises(watch_module._StdoutClosed) as stopped:
        watch_module._emit({"type": "ready"})
    assert not isinstance(stopped.value, OSError)
