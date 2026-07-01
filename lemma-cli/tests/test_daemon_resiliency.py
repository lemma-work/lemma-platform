"""Tests for daemon transport resiliency: reconnect + send guard."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import os
import signal
import time

import pytest

from lemma_cli.daemon import runner


def test_reconnect_delay_is_bounded():
    for attempt in range(8):
        delay = runner.reconnect_delay_seconds(attempt)
        assert 0.0 <= delay <= runner._RECONNECT_MAX_DELAY_SECONDS
    # First attempt is capped at the base delay.
    assert runner.reconnect_delay_seconds(0) <= runner._RECONNECT_BASE_DELAY_SECONDS


def test_ssl_for_plain_ws_is_none():
    # ws:// has no TLS; websockets only accepts ssl=None for it.
    assert runner.ssl_for_ws_url("ws://example/daemon", verify_ssl=True) is None
    assert runner.ssl_for_ws_url("ws://example/daemon", verify_ssl=False) is None


def test_ssl_for_wss_with_verify_is_true_not_none():
    # Regression: a verified wss:// connection must NOT pass ssl=None, which
    # websockets rejects with "ssl=None is incompatible with a wss:// URI".
    result = runner.ssl_for_ws_url("wss://api.lemma.work/daemon", verify_ssl=True)
    assert result is True


def test_ssl_for_wss_without_verify_is_unverified_context():
    import ssl as ssl_module

    context = runner.ssl_for_ws_url("wss://api.lemma.work/daemon", verify_ssl=False)
    # Must be an SSLContext (not False, which asyncio treats as plaintext on a
    # TLS port) with verification disabled.
    assert isinstance(context, ssl_module.SSLContext)
    assert context.check_hostname is False
    assert context.verify_mode == ssl_module.CERT_NONE


class _FakeWS:
    """Fake websocket.

    Default mode (``hang_when_empty=False``) preserves the original behavior:
    once ``incoming`` is exhausted, iteration ends with ``StopAsyncIteration``
    (simulates a connection the peer already closed).

    ``hang_when_empty=True`` instead backs iteration with an ``asyncio.Queue``,
    so the "connection" stays open indefinitely and a test can push more
    messages later via ``push_incoming()`` and have a pending ``__anext__()``
    wake up for them -- needed to test behavior that only shows up while a
    connection is genuinely still live (e.g. a heartbeat ticking alongside a
    busy run task).
    """

    def __init__(self, incoming=None, *, hang_when_empty: bool = False):
        self.sent: list[str] = []
        self.send_should_fail = False
        self.closed = False
        if hang_when_empty:
            self._queue: asyncio.Queue[str] | None = asyncio.Queue()
            for item in list(incoming or []):
                self._queue.put_nowait(item)
            self._incoming = None
        else:
            self._queue = None
            self._incoming = list(incoming or [])

    async def send(self, data):
        if self.send_should_fail:
            raise ConnectionError("socket closed")
        self.sent.append(data)

    async def close(self, *_args, **_kwargs):
        self.closed = True

    def push_incoming(self, data: str) -> None:
        assert self._queue is not None, "push_incoming requires hang_when_empty=True"
        self._queue.put_nowait(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._queue is not None:
            return await self._queue.get()
        if self._incoming:
            return self._incoming.pop(0)
        raise StopAsyncIteration


class _FakeConn:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *_a):
        return False


@pytest.mark.asyncio
async def test_send_json_swallows_send_failure():
    ws = _FakeWS()
    ws.send_should_fail = True
    # Must not raise even though the underlying socket send fails.
    await runner._send_json(ws, {"type": "run.event"})


@pytest.mark.asyncio
async def test_send_run_event_is_guarded():
    ws = _FakeWS()
    ws.send_should_fail = True
    await runner.send_run_event(ws, "run-1", "token", "hi")  # must not raise


@pytest.mark.asyncio
async def test_send_json_reraises_cancellation():
    class _CancelWS:
        async def send(self, _data):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await runner._send_json(_CancelWS(), {"type": "x"})


@pytest.mark.asyncio
async def test_run_daemon_reconnects_after_drop(monkeypatch):
    # Avoid real config/discovery/sleep.
    monkeypatch.setattr(
        runner, "ensure_config", lambda: {"device_key": "k", "display_name": "t"}
    )
    monkeypatch.setattr(runner, "discover_harness_catalog", lambda: {})
    monkeypatch.setattr(runner, "save_config", lambda _c: None)
    monkeypatch.setattr(runner, "device_info", lambda: {})
    monkeypatch.setattr(runner, "daemon_ws_url", lambda _b: "ws://example/daemon")
    monkeypatch.setattr(runner, "reconnect_delay_seconds", lambda _a: 0.0)

    connections: list[_FakeWS] = []

    def connect_factory(_token):
        # Each connection immediately receives a ready-ack then closes (iter ends),
        # which makes run_daemon reconnect.
        ws = _FakeWS(
            incoming=[json.dumps({"type": "daemon.ready_ack", "daemon_id": "d1"})]
        )
        connections.append(ws)
        return _FakeConn(ws)

    await runner.run_daemon(
        base_url="http://example",
        token="tok",
        verify_ssl=True,
        connect_factory=connect_factory,
        max_reconnect_attempts=1,
    )

    # Initial connect + exactly one reconnect, then it gives up (bounded by test).
    assert len(connections) == 2
    # The ready handshake was sent on every connection.
    for ws in connections:
        assert any('"daemon.ready"' in message for message in ws.sent)


@pytest.mark.asyncio
async def test_run_daemon_uses_token_provider_on_each_connect(monkeypatch):
    monkeypatch.setattr(
        runner, "ensure_config", lambda: {"device_key": "k", "display_name": "t"}
    )
    monkeypatch.setattr(runner, "discover_harness_catalog", lambda: {})
    monkeypatch.setattr(runner, "save_config", lambda _c: None)
    monkeypatch.setattr(runner, "device_info", lambda: {})
    monkeypatch.setattr(runner, "daemon_ws_url", lambda _b: "ws://example/daemon")
    monkeypatch.setattr(runner, "reconnect_delay_seconds", lambda _a: 0.0)

    tokens_used: list[str] = []
    counter = {"n": 0}

    def token_provider() -> str:
        counter["n"] += 1
        return f"token-{counter['n']}"

    def connect_factory(token):
        tokens_used.append(token)
        return _FakeConn(_FakeWS(incoming=[]))

    await runner.run_daemon(
        base_url="http://example",
        token="unused",
        verify_ssl=True,
        token_provider=token_provider,
        connect_factory=connect_factory,
        max_reconnect_attempts=1,
    )

    assert tokens_used == ["token-1", "token-2"]


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text) if self.text else None


@pytest.mark.asyncio
async def test_opencode_get_retries_on_5xx(monkeypatch):
    from lemma_cli.daemon.harnesses import opencode

    monkeypatch.setattr(opencode, "_OPENCODE_GET_RETRY_BASE_DELAY", 0.0)

    calls = {"n": 0}

    class _Client:
        async def request(self, method, url, json=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(503, "busy")
            return _Resp(200, '{"ok": true}')

    result = await opencode._opencode_request(
        _Client(), "GET", "http://x", "/status", params={}
    )
    assert calls["n"] == 2  # retried once after the 5xx
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_opencode_post_does_not_retry(monkeypatch):
    from lemma_cli.daemon.harnesses import opencode

    monkeypatch.setattr(opencode, "_OPENCODE_GET_RETRY_BASE_DELAY", 0.0)

    calls = {"n": 0}

    class _Client:
        async def request(self, method, url, json=None):
            calls["n"] += 1
            return _Resp(503, "busy")

    with pytest.raises(RuntimeError):
        await opencode._opencode_request(
            _Client(), "POST", "http://x", "/session", params={}
        )
    assert calls["n"] == 1  # POSTs are never auto-retried


def _pings_sent(ws: _FakeWS) -> list[dict]:
    return [json.loads(m) for m in ws.sent if json.loads(m).get("type") == "daemon.ping"]


def test_ping_interval_seconds_defaults(monkeypatch):
    monkeypatch.delenv(runner._PING_INTERVAL_SECONDS_ENV, raising=False)
    assert runner.ping_interval_seconds() == 15.0


def test_ping_interval_seconds_env_override(monkeypatch):
    monkeypatch.setenv(runner._PING_INTERVAL_SECONDS_ENV, "5")
    assert runner.ping_interval_seconds() == 5.0


def test_ping_interval_seconds_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(runner._PING_INTERVAL_SECONDS_ENV, "not-a-number")
    assert runner.ping_interval_seconds() == 15.0


def test_pong_miss_limit_defaults(monkeypatch):
    monkeypatch.delenv(runner._PONG_MISS_LIMIT_ENV, raising=False)
    assert runner.pong_miss_limit() == 3


def test_pong_miss_limit_env_override(monkeypatch):
    monkeypatch.setenv(runner._PONG_MISS_LIMIT_ENV, "5")
    assert runner.pong_miss_limit() == 5


def test_pong_miss_limit_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(runner._PONG_MISS_LIMIT_ENV, "not-a-number")
    assert runner.pong_miss_limit() == 3


@pytest.mark.asyncio
async def test_heartbeat_closes_connection_after_missed_pongs(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(runner, "pong_miss_limit", lambda: 2)
    ws = _FakeWS()
    pong_seen = asyncio.Event()  # never set -- every ping counts as a miss

    await asyncio.wait_for(
        runner._heartbeat_loop(ws, send_lock=asyncio.Lock(), pong_seen=pong_seen, active_runs={}),
        timeout=1.0,
    )

    assert len(_pings_sent(ws)) == 2  # closes right after the 2nd consecutive miss
    assert ws.closed is True


@pytest.mark.asyncio
async def test_heartbeat_keeps_running_while_pongs_answered(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(runner, "pong_miss_limit", lambda: 2)
    ws = _FakeWS()
    pong_seen = asyncio.Event()

    async def _answer_every_ping():
        while True:
            await asyncio.sleep(0.001)
            if ws.sent:
                pong_seen.set()

    responder = asyncio.create_task(_answer_every_ping())
    heartbeat = asyncio.create_task(
        runner._heartbeat_loop(ws, send_lock=asyncio.Lock(), pong_seen=pong_seen, active_runs={})
    )
    await asyncio.sleep(0.08)
    heartbeat.cancel()
    responder.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat
    with contextlib.suppress(asyncio.CancelledError):
        await responder

    assert len(_pings_sent(ws)) >= 3
    assert ws.closed is False


@pytest.mark.asyncio
async def test_heartbeat_independent_of_busy_run_task(monkeypatch):
    """Regression guard: a slow/long run task must never delay the heartbeat.

    This is the actual fix for the reported bug -- the heartbeat runs on its
    own asyncio task, decoupled from run-handling tasks, so it can detect and
    react to a dead connection even while a run is in flight.
    """
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(runner, "pong_miss_limit", lambda: 1000)  # never self-declare dead here

    busy_started = asyncio.Event()

    async def _busy_handle_run_start(message, *, sink, base_url=None):
        busy_started.set()
        await asyncio.sleep(1.0)

    monkeypatch.setattr(runner, "handle_run_start", _busy_handle_run_start)

    ws = _FakeWS(
        incoming=[json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}})],
        hang_when_empty=True,
    )

    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    await asyncio.wait_for(busy_started.wait(), timeout=1.0)
    await asyncio.sleep(0.1)  # several heartbeat intervals while the run task sleeps
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    assert len(_pings_sent(ws)) >= 3


@pytest.mark.asyncio
async def test_serve_connection_routes_daemon_pong_to_heartbeat(monkeypatch):
    """Regression guard for the message-loop <-> heartbeat wiring itself."""
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 0.01)
    monkeypatch.setattr(runner, "pong_miss_limit", lambda: 2)

    ws = _FakeWS(hang_when_empty=True)
    original_send = ws.send

    async def _send_with_auto_pong(data):
        await original_send(data)
        if json.loads(data).get("type") == "daemon.ping":
            ws.push_incoming(json.dumps({"type": "daemon.pong"}))

    ws.send = _send_with_auto_pong

    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    await asyncio.sleep(0.15)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    assert len(_pings_sent(ws)) >= 3  # never self-declared dead: every ping got its pong
    assert ws.closed is False


def _run_events(ws: _FakeWS) -> list[dict]:
    return [json.loads(m) for m in ws.sent if json.loads(m).get("type") == "run.event"]


@pytest.mark.asyncio
async def test_duplicate_run_start_for_active_run_is_ignored(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)

    calls = {"n": 0}

    async def _fake_handle_run_start(message, *, sink, base_url=None):
        calls["n"] += 1
        await asyncio.sleep(10)  # stays "active" for the duration of the test

    monkeypatch.setattr(runner, "handle_run_start", _fake_handle_run_start)

    ws = _FakeWS(
        incoming=[
            json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}}),
            json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}}),
        ],
        hang_when_empty=True,
    )
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(100):
        if calls["n"] >= 1:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # give the (would-be) second dispatch a chance to fire
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    assert calls["n"] == 1  # the redelivered run.start never spawned a second task


@pytest.mark.asyncio
async def test_held_run_reaper_terminates_after_grace_expires(monkeypatch):
    monkeypatch.setattr(runner, "hold_grace_seconds", lambda: 0.02)
    monkeypatch.setattr(runner, "_HELD_RUN_REAP_POLL_INTERVAL_SECONDS", 0.01)

    async def _never_finishes():
        await asyncio.sleep(10)

    task = asyncio.create_task(_never_finishes())
    sink = runner._RunEventSink(_FakeWS(), "run-held", asyncio.Lock())
    buffer: "collections.deque" = collections.deque(maxlen=10)
    held_runs = {
        "run-held": runner._HeldRun(
            task=task, sink=sink, buffer=buffer, disconnected_at=time.monotonic()
        )
    }

    reaper = asyncio.create_task(runner._reap_expired_held_runs(held_runs))
    for _ in range(200):
        if "run-held" not in held_runs:
            break
        await asyncio.sleep(0.01)
    reaper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reaper

    assert "run-held" not in held_runs
    assert task.cancelled() or task.cancelling()


@pytest.mark.asyncio
async def test_held_run_not_reaped_before_grace_expires(monkeypatch):
    monkeypatch.setattr(runner, "hold_grace_seconds", lambda: 10.0)
    monkeypatch.setattr(runner, "_HELD_RUN_REAP_POLL_INTERVAL_SECONDS", 0.01)

    async def _never_finishes():
        await asyncio.sleep(10)

    task = asyncio.create_task(_never_finishes())
    sink = runner._RunEventSink(_FakeWS(), "run-held", asyncio.Lock())
    buffer: "collections.deque" = collections.deque(maxlen=10)
    held_runs = {
        "run-held": runner._HeldRun(
            task=task, sink=sink, buffer=buffer, disconnected_at=time.monotonic()
        )
    }

    reaper = asyncio.create_task(runner._reap_expired_held_runs(held_runs))
    await asyncio.sleep(0.1)
    reaper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reaper

    assert "run-held" in held_runs
    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_survives_disconnect_and_reattaches_with_buffered_events(monkeypatch):
    """End-to-end CLI-side hold-not-kill + reattach: a run's subprocess is not
    cancelled when its connection drops, events emitted while disconnected are
    buffered, and reconnecting flushes them + resumes live streaming -- all
    without the run task itself ever being restarted.
    """
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)

    emit_more = asyncio.Event()
    emitted_second_event = asyncio.Event()

    async def _fake_handle_run_start(message, *, sink, base_url=None):
        await sink("status", {"status": "starting"})
        await emit_more.wait()
        await sink("token", "emitted-while-disconnected")
        emitted_second_event.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "handle_run_start", _fake_handle_run_start)

    held_runs: dict[str, runner._HeldRun] = {}
    ws1 = _FakeWS(
        incoming=[json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}})],
        hang_when_empty=True,
    )
    conn1 = asyncio.create_task(
        runner._serve_connection(
            ws1, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs=held_runs
        )
    )
    for _ in range(100):
        if _run_events(ws1):
            break
        await asyncio.sleep(0.01)
    assert _run_events(ws1)[0]["event"]["type"] == "status"

    # Simulate the connection dropping (same code path a real ConnectionClosed
    # would hit: _serve_connection's finally block runs regardless of why the
    # `async for` loop stopped).
    conn1.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await conn1

    assert "run-1" in held_runs
    assert not held_runs["run-1"].task.done()

    # While "disconnected," the run keeps going and emits another event -- it
    # must land in the buffer, not be lost or crash anything.
    emit_more.set()
    await asyncio.wait_for(emitted_second_event.wait(), timeout=1.0)
    for _ in range(100):
        if held_runs["run-1"].buffer:
            break
        await asyncio.sleep(0.01)
    assert len(held_runs["run-1"].buffer) == 1
    assert held_runs["run-1"].buffer[0] == {"type": "token", "data": "emitted-while-disconnected"}

    # Reconnect: a fresh connection sharing the SAME held_runs dict.
    ws2 = _FakeWS(hang_when_empty=True)
    conn2 = asyncio.create_task(
        runner._serve_connection(
            ws2, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs=held_runs
        )
    )
    try:
        for _ in range(200):
            if _run_events(ws2):
                break
            await asyncio.sleep(0.01)

        ready_payload = json.loads(ws2.sent[0])["payload"]
        assert ready_payload["reattach_runs"] == [
            {"agent_run_id": "run-1", "buffered_event_count": 1, "overflowed": False}
        ]
        flushed = _run_events(ws2)
        assert flushed[0]["agent_run_id"] == "run-1"
        assert flushed[0]["event"] == {"type": "token", "data": "emitted-while-disconnected"}
        # Reattachment reclaims the run -- it's no longer "held" once live again.
        assert held_runs == {}
    finally:
        conn2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn2


def test_hold_grace_seconds_defaults(monkeypatch):
    monkeypatch.delenv(runner._HOLD_GRACE_SECONDS_ENV, raising=False)
    assert runner.hold_grace_seconds() == 150.0


def test_hold_grace_seconds_env_override(monkeypatch):
    monkeypatch.setenv(runner._HOLD_GRACE_SECONDS_ENV, "30")
    assert runner.hold_grace_seconds() == 30.0


def test_hold_grace_seconds_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(runner._HOLD_GRACE_SECONDS_ENV, "not-a-number")
    assert runner.hold_grace_seconds() == 150.0


def test_max_buffered_events_per_run_defaults(monkeypatch):
    monkeypatch.delenv(runner._MAX_BUFFERED_EVENTS_ENV, raising=False)
    assert runner.max_buffered_events_per_run() == 2000


def test_max_buffered_events_per_run_env_override(monkeypatch):
    monkeypatch.setenv(runner._MAX_BUFFERED_EVENTS_ENV, "50")
    assert runner.max_buffered_events_per_run() == 50


def test_max_buffered_events_per_run_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(runner._MAX_BUFFERED_EVENTS_ENV, "not-a-number")
    assert runner.max_buffered_events_per_run() == 2000


@pytest.mark.asyncio
async def test_run_event_sink_buffer_overflow_sets_flag_and_drops_oldest():
    buffer: "collections.deque" = collections.deque(maxlen=2)
    sink = runner._RunEventSink(_FakeWS(), "run-1", asyncio.Lock())
    sink.go_buffered(buffer)

    await sink("token", "one")
    await sink("token", "two")
    assert sink.overflowed is False
    await sink("token", "three")

    assert sink.overflowed is True
    assert list(buffer) == [
        {"type": "token", "data": "two"},
        {"type": "token", "data": "three"},
    ]


def test_max_concurrent_runs_defaults(monkeypatch):
    from lemma_cli.daemon import config as daemon_config

    monkeypatch.delenv(daemon_config.MAX_CONCURRENT_RUNS_ENV, raising=False)
    assert daemon_config.max_concurrent_runs() == 4


def test_max_concurrent_runs_env_override(monkeypatch):
    from lemma_cli.daemon import config as daemon_config

    monkeypatch.setenv(daemon_config.MAX_CONCURRENT_RUNS_ENV, "8")
    assert daemon_config.max_concurrent_runs() == 8


def test_max_concurrent_runs_invalid_env_falls_back_to_default(monkeypatch):
    from lemma_cli.daemon import config as daemon_config

    monkeypatch.setenv(daemon_config.MAX_CONCURRENT_RUNS_ENV, "not-a-number")
    assert daemon_config.max_concurrent_runs() == 4


def test_ensure_config_persists_max_concurrent_runs(tmp_path, monkeypatch):
    from lemma_cli.daemon import config as daemon_config

    monkeypatch.setattr(daemon_config, "DAEMON_DIR", tmp_path)
    monkeypatch.setattr(daemon_config, "DAEMON_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.delenv(daemon_config.MAX_CONCURRENT_RUNS_ENV, raising=False)

    config = daemon_config.ensure_config()
    assert config["max_concurrent_runs"] == 4

    # A pre-existing value is preserved across calls (matching device_key's
    # idempotency), even if the env default would differ now.
    config["max_concurrent_runs"] = 99
    daemon_config.save_config(config)
    reloaded = daemon_config.ensure_config()
    assert reloaded["max_concurrent_runs"] == 99


@pytest.mark.asyncio
async def test_run_start_rejected_when_at_capacity(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)
    monkeypatch.setattr(runner, "max_concurrent_runs", lambda: 2)

    async def _never_finishes(message, *, sink, base_url=None):
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "handle_run_start", _never_finishes)

    ws = _FakeWS(
        incoming=[
            json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}}),
            json.dumps({"type": "run.start", "agent_run_id": "run-2", "payload": {}}),
            json.dumps({"type": "run.start", "agent_run_id": "run-3", "payload": {}}),
        ],
        hang_when_empty=True,
    )
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(200):
        if len(_run_events(ws)) >= 1:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    events = _run_events(ws)
    rejected = [e for e in events if e["event"]["type"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["agent_run_id"] == "run-3"
    assert rejected[0]["event"]["data"] == {
        "reason": "daemon_at_capacity",
        "active_run_count": 2,
        "max_concurrent_runs": 2,
    }


@pytest.mark.asyncio
async def test_rejected_run_never_starts_a_task(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)
    monkeypatch.setattr(runner, "max_concurrent_runs", lambda: 1)

    calls: list[str] = []

    async def _record_and_hang(message, *, sink, base_url=None):
        calls.append(str(message.get("agent_run_id")))
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "handle_run_start", _record_and_hang)

    ws = _FakeWS(
        incoming=[
            json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}}),
            json.dumps({"type": "run.start", "agent_run_id": "run-2", "payload": {}}),
        ],
        hang_when_empty=True,
    )
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(200):
        if calls:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    # Only the first (admitted) run ever reached handle_run_start -- the
    # rejected one never started a task, so it can never accidentally start a
    # timeout clock for work that never ran.
    assert calls == ["run-1"]


@pytest.mark.asyncio
async def test_daemon_ready_includes_capacity_payload(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)
    monkeypatch.setattr(runner, "max_concurrent_runs", lambda: 4)

    ws = _FakeWS(hang_when_empty=True)
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(200):
        if ws.sent:
            break
        await asyncio.sleep(0.01)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    ready = json.loads(ws.sent[0])
    assert ready["payload"]["capacity"] == {"max_concurrent_runs": 4, "active_run_count": 0}


@pytest.mark.asyncio
async def test_daemon_catalog_refresh_includes_live_active_run_count(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 1000.0)
    monkeypatch.setattr(runner, "max_concurrent_runs", lambda: 4)

    async def _never_finishes(message, *, sink, base_url=None):
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "handle_run_start", _never_finishes)

    ws = _FakeWS(
        incoming=[
            json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}}),
            json.dumps({"type": "catalog.refresh"}),
        ],
        hang_when_empty=True,
    )
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(200):
        if any(json.loads(m).get("type") == "daemon.catalog" for m in ws.sent):
            break
        await asyncio.sleep(0.01)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    catalog_msg = next(json.loads(m) for m in ws.sent if json.loads(m).get("type") == "daemon.catalog")
    assert catalog_msg["capacity"] == {"max_concurrent_runs": 4, "active_run_count": 1}


@pytest.mark.asyncio
async def test_heartbeat_ping_includes_capacity_payload(monkeypatch):
    monkeypatch.setattr(runner, "ping_interval_seconds", lambda: 0.02)
    monkeypatch.setattr(runner, "pong_miss_limit", lambda: 1000)
    monkeypatch.setattr(runner, "max_concurrent_runs", lambda: 4)

    async def _never_finishes(message, *, sink, base_url=None):
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "handle_run_start", _never_finishes)

    ws = _FakeWS(
        incoming=[json.dumps({"type": "run.start", "agent_run_id": "run-1", "payload": {}})],
        hang_when_empty=True,
    )
    serve_task = asyncio.create_task(
        runner._serve_connection(
            ws, config={"device_key": "k"}, catalog={}, base_url="http://x", held_runs={}
        )
    )
    for _ in range(200):
        if _pings_sent(ws):
            break
        await asyncio.sleep(0.01)
    serve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task

    ping = _pings_sent(ws)[0]
    assert ping["payload"]["capacity"] == {"max_concurrent_runs": 4, "active_run_count": 1}


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="add_signal_handler is POSIX-only")
async def test_graceful_shutdown_cancels_run_daemon_on_sigterm(monkeypatch):
    """`lemma daemon stop` / a plain `kill` send SIGTERM. With no handler at
    all, the interpreter's default behavior tears the process down before
    run_daemon()'s own subprocess-teardown cleanup gets a chance to run,
    orphaning any active/held provider subprocess. The wrapper must turn
    that into a cancellation of the daemon task instead.
    """
    cleanup_ran = asyncio.Event()

    async def _fake_run_daemon(**kwargs):
        del kwargs
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cleanup_ran.set()
            raise

    monkeypatch.setattr(runner, "run_daemon", _fake_run_daemon)

    task = asyncio.create_task(runner.run_daemon_with_graceful_shutdown())
    # Handler registration is synchronous, before the wrapper's first
    # `await` -- yielding back to the loop a few times is enough for that
    # setup to have run.
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(task, timeout=2)
    assert cleanup_ran.is_set()
    assert task.done() and not task.cancelled()  # CancelledError is suppressed, not propagated


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="add_signal_handler is POSIX-only")
async def test_graceful_shutdown_awaits_held_run_cancellation_before_returning(monkeypatch):
    """run_daemon()'s own finally block must AWAIT cancelled held-run tasks,
    not just call .cancel() and move on -- otherwise the process can still
    exit (via the graceful-shutdown wrapper returning) before a held
    subprocess's own CancelledError-triggered teardown actually finishes.
    """
    torn_down = asyncio.Event()

    async def _held_task_body():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)  # simulate terminate-then-wait teardown work
            torn_down.set()
            raise

    # run_daemon() owns `held_runs` internally and only ever hands it to
    # _serve_connection() by reference -- inject the pre-populated entry from
    # inside the fake so it lands in the SAME dict run_daemon()'s own
    # `finally` later inspects.
    async def _fake_serve_connection(*args, held_runs, **kwargs):
        del args, kwargs
        held_runs["run-1"] = runner._HeldRun(
            task=asyncio.create_task(_held_task_body()),
            sink=runner._RunEventSink(_FakeWS(), "run-1", asyncio.Lock()),
            buffer=collections.deque(),
            disconnected_at=time.monotonic(),
        )
        await asyncio.sleep(10)

    monkeypatch.setattr(runner, "_serve_connection", _fake_serve_connection)

    daemon_task = asyncio.create_task(
        runner.run_daemon(
            base_url="http://x",
            token="t",
            verify_ssl=False,
            connect_factory=lambda _token: _FakeConn(_FakeWS()),
        )
    )
    await asyncio.sleep(0.05)
    daemon_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(daemon_task, timeout=2)

    assert torn_down.is_set()
