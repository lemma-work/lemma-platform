"""Unit tests for `lemma datastore watch` helpers (URL, cursor, rendering)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from lemma_cli.cli_core.state import CliState
from lemma_cli.cli_core.watch import (
    _changes_ws_url,
    _compact_payload,
    _handle_message,
    _short_time,
)


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


def test_changes_ws_url_scheme_and_query():
    assert (
        _changes_ws_url("https://api.x.dev", "POD", "notes", "5-0")
        == "wss://api.x.dev/pods/POD/datastore/changes?table=notes&since=5-0"
    )
    assert (
        _changes_ws_url("http://localhost:8711/", "POD", None, None)
        == "ws://localhost:8711/pods/POD/datastore/changes"
    )
    # Bare host (no scheme) assumes TLS, like a browser.
    assert (
        _changes_ws_url("//host", "POD", None, "9-0")
        == "wss://host/pods/POD/datastore/changes?since=9-0"
    )


def test_ready_frame_advances_cursor_from_since():
    state = _state()
    cursor = _handle_message(state, json.dumps({"type": "ready", "since": "7-0"}), None)
    assert cursor == "7-0"


def test_record_frame_advances_cursor_from_stream_id_and_prints_ndjson(capsys):
    state = _state(output="json")
    frame = {
        "type": "datastore.record.insert",
        "table_name": "notes",
        "record_id": "abc",
        "operation": "insert",
        "payload": {"body": "hi"},
        "stream_id": "12-0",
    }
    cursor = _handle_message(state, json.dumps(frame), "7-0")
    assert cursor == "12-0"
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == frame


def test_invalid_or_unknown_messages_keep_cursor():
    state = _state()
    assert _handle_message(state, "not json", "3-0") == "3-0"
    assert _handle_message(state, json.dumps([1, 2]), "3-0") == "3-0"


def test_compact_payload_and_short_time():
    assert _compact_payload({"body": "hi", "n": 3}, full=False) == "body=hi, n=3"
    assert _compact_payload({}, full=False) == ""
    long = _compact_payload({"k": "x" * 200}, full=False)
    assert long.endswith("…") and len(long) <= 120
    assert _short_time("2026-06-21T18:49:01.309492Z") == "18:49:01"
    assert _short_time(None) == ""


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
