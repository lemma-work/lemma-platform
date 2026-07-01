from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from ._logging import log as daemon_log, preview as _preview, redact as _redact
from ._utils import bounded_error_detail
from .catalog import discover_harness_catalog
from .config import ensure_config, save_config, daemon_ws_url, device_info, max_concurrent_runs
from .harnesses.registry import get_harness
from .mcp import (
    payload_with_reachable_mcp_urls,
    provider_command,
    provider_cwd_for_run,
    provider_environment,
)

# Reconnect backoff: a dropped backend connection must not kill the daemon — it
# should wait and reconnect so it keeps serving runs. Full-jitter exponential
# backoff, capped, to avoid hammering the server on a sustained outage.
_RECONNECT_BASE_DELAY_SECONDS = 1.0
_RECONNECT_MAX_DELAY_SECONDS = 30.0

# App-level heartbeat: sent on its own asyncio task, independent of run-handling
# tasks, so a busy/slow run can never delay it. This is the primary liveness
# signal (the low-level websockets ping/pong stays at library defaults as a
# coarser secondary safety net) because it's the only one that can distinguish
# "connection is actually dead" from "the daemon process is just busy."
_PING_INTERVAL_SECONDS_ENV = "LEMMA_DAEMON_PING_INTERVAL_SECONDS"
_DEFAULT_PING_INTERVAL_SECONDS = 15.0
_PONG_MISS_LIMIT_ENV = "LEMMA_DAEMON_PONG_MISS_LIMIT"
_DEFAULT_PONG_MISS_LIMIT = 3


def ping_interval_seconds() -> float:
    raw = os.getenv(_PING_INTERVAL_SECONDS_ENV, str(_DEFAULT_PING_INTERVAL_SECONDS))
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_PING_INTERVAL_SECONDS


def pong_miss_limit() -> int:
    raw = os.getenv(_PONG_MISS_LIMIT_ENV, str(_DEFAULT_PONG_MISS_LIMIT))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_PONG_MISS_LIMIT


# Hold-not-kill: a transport drop must not kill an in-flight run's subprocess --
# it's an independent OS process that keeps running regardless of the
# websocket. Its events are buffered locally and flushed once a new connection
# reattaches; if nothing reattaches within the grace window, the daemon gives
# up and terminates the subprocess itself (bounds resource use on an abandoned
# laptop). Kept >= the backend's own reconnect grace (daemon_reconnect_grace_seconds,
# default 120s) so the daemon never kills a run the backend might still reattach to.
_HOLD_GRACE_SECONDS_ENV = "LEMMA_DAEMON_HOLD_GRACE_SECONDS"
_DEFAULT_HOLD_GRACE_SECONDS = 150.0
_MAX_BUFFERED_EVENTS_ENV = "LEMMA_DAEMON_MAX_BUFFERED_EVENTS"
_DEFAULT_MAX_BUFFERED_EVENTS = 2000


def hold_grace_seconds() -> float:
    raw = os.getenv(_HOLD_GRACE_SECONDS_ENV, str(_DEFAULT_HOLD_GRACE_SECONDS))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_HOLD_GRACE_SECONDS


def max_buffered_events_per_run() -> int:
    raw = os.getenv(_MAX_BUFFERED_EVENTS_ENV, str(_DEFAULT_MAX_BUFFERED_EVENTS))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_BUFFERED_EVENTS


class _RunEventSink:
    """Indirection for where a run's events go: a live websocket, or a capped
    local buffer while the connection is down.

    Letting this be redirected in place (instead of threading a fresh closure
    through a fresh task) is what lets a held run's subprocess keep running
    across a disconnect without being restarted -- only its event destination
    changes.
    """

    def __init__(self, websocket: Any, agent_run_id: str, send_lock: asyncio.Lock) -> None:
        self._websocket = websocket
        self._agent_run_id = agent_run_id
        self._send_lock = send_lock
        self._buffer: collections.deque[dict[str, Any]] | None = None
        self.overflowed = False

    def go_buffered(self, buffer: "collections.deque[dict[str, Any]]") -> None:
        self._buffer = buffer
        self.overflowed = False

    def go_live(self, websocket: Any, send_lock: asyncio.Lock) -> None:
        self._websocket = websocket
        self._send_lock = send_lock
        self._buffer = None

    async def __call__(self, event_type: str, data: Any) -> None:
        if self._buffer is not None:
            if len(self._buffer) == self._buffer.maxlen:
                self.overflowed = True
            self._buffer.append({"type": event_type, "data": data})
            return
        await send_run_event(
            self._websocket, self._agent_run_id, event_type, data, lock=self._send_lock
        )


@dataclass
class _HeldRun:
    task: "asyncio.Task[None]"
    sink: _RunEventSink
    buffer: "collections.deque[dict[str, Any]]"
    disconnected_at: float


_HELD_RUN_REAP_POLL_INTERVAL_SECONDS = 5.0


async def _reap_expired_held_runs(held_runs: dict[str, _HeldRun]) -> None:
    """Terminate a held run's subprocess once nothing reattaches in time.

    Runs for the daemon's whole lifetime (a sibling to the reconnect loop, not
    scoped to one connection) since a held run must stay watched across
    however many failed reconnect attempts happen before the grace window
    elapses.
    """
    while True:
        await asyncio.sleep(_HELD_RUN_REAP_POLL_INTERVAL_SECONDS)
        grace = hold_grace_seconds()
        now = time.monotonic()
        for agent_run_id, held in list(held_runs.items()):
            if now - held.disconnected_at < grace:
                continue
            daemon_log(
                "hold grace period expired; terminating held run",
                {"agent_run_id": agent_run_id},
            )
            if not held.task.done():
                held.task.cancel()
            held_runs.pop(agent_run_id, None)


def reconnect_delay_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff delay (seconds) for a reconnect ``attempt``."""
    ceiling = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * (2 ** max(0, attempt)),
    )
    return random.uniform(0.0, ceiling)


def ssl_for_ws_url(ws_url: str, *, verify_ssl: bool) -> Any:
    """Build the ``ssl`` argument for ``websockets.connect``.

    The websockets client rejects ``ssl=None`` for a ``wss://`` URI (it can't tell
    "use the default TLS context" from "no TLS"), so a secure URL must get an
    explicit value: ``True`` for a verified handshake, or an unverified
    ``SSLContext`` when verification is disabled. Passing ``ssl=False`` is wrong for
    ``wss://`` — asyncio treats it as plaintext and would dial a TLS port without
    TLS. For a plain ``ws://`` URL there is no TLS, so ``None`` is the only value
    websockets accepts.
    """
    if ws_url.startswith("ws://"):
        return None
    if verify_ssl:
        return True
    import ssl as ssl_module  # noqa: PLC0415

    context = ssl_module.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl_module.CERT_NONE
    return context


async def run_daemon(
    *,
    base_url: str,
    token: str,
    verify_ssl: bool,
    debug: bool = False,
    token_provider: Callable[[], str] | None = None,
    connect_factory: Callable[[str], Any] | None = None,
    max_reconnect_attempts: int | None = None,
) -> None:
    """Run the daemon, reconnecting with backoff if the connection drops.

    ``token_provider`` (optional) is called before each (re)connect to obtain a
    fresh token. ``connect_factory`` / ``max_reconnect_attempts`` exist for tests;
    in production a real websocket connector is used and reconnects are unbounded.
    """
    from ._logging import set_debug
    set_debug(debug)
    try:
        import websockets
        from websockets.exceptions import InvalidStatus, WebSocketException
    except ImportError as exc:
        import click
        raise click.ClickException(
            "Missing dependency 'websockets'. Reinstall the CLI to enable daemon mode: "
            "pip install --upgrade lemma-terminal (or: pip install websockets)."
        ) from exc

    config = ensure_config()
    ws_url = daemon_ws_url(base_url)
    catalog = discover_harness_catalog()
    ssl_option = ssl_for_ws_url(ws_url, verify_ssl=verify_ssl)

    if connect_factory is None:
        def connect_factory(current_token: str) -> Any:
            return websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {current_token}"},
                ssl=ssl_option,
            )

    # `held_runs` survives across reconnects (unlike `_serve_connection`'s local
    # `active_runs`, which is fresh per connection) -- it's how a run's
    # subprocess keeps running, buffering its events, while the daemon is
    # between connections. The reaper is a sibling task for the daemon's whole
    # lifetime so a held run stays watched across however many reconnect
    # attempts happen before its grace window elapses.
    held_runs: dict[str, _HeldRun] = {}
    reaper_task = asyncio.create_task(_reap_expired_held_runs(held_runs))
    try:
        # `backoff_attempt` grows the delay while we can't stay connected and resets
        # once a connection is established; `reconnects` is a monotonic count of
        # reconnect cycles used only to bound the loop in tests.
        backoff_attempt = 0
        reconnects = 0
        while True:
            current_token = token_provider() if token_provider is not None else token
            daemon_log("connecting websocket", {"url": ws_url, "attempt": backoff_attempt})
            try:
                async with connect_factory(current_token) as websocket:
                    backoff_attempt = 0  # reset backoff once we're connected
                    await _serve_connection(
                        websocket,
                        config=config,
                        catalog=catalog,
                        base_url=base_url,
                        held_runs=held_runs,
                    )
                # _serve_connection returned: the server closed the socket cleanly.
                daemon_log("websocket closed; reconnecting", {})
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in {401, 403}:
                    import click
                    raise click.ClickException(
                        "Daemon websocket authentication failed. Run `lemma auth login` and try again."
                    ) from exc
                daemon_log("websocket rejected; will retry", {"status": status_code})
            except (OSError, WebSocketException) as exc:
                daemon_log("websocket connection error; will retry", {"error": str(exc)})

            reconnects += 1
            if max_reconnect_attempts is not None and reconnects > max_reconnect_attempts:
                daemon_log("giving up reconnect", {"reconnects": reconnects})
                return
            delay = reconnect_delay_seconds(backoff_attempt)
            backoff_attempt += 1
            daemon_log(
                "reconnecting after backoff",
                {"delay_seconds": round(delay, 2), "reconnects": reconnects},
            )
            await asyncio.sleep(delay)
    finally:
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper_task
        pending = [held.task for held in held_runs.values() if not held.task.done()]
        for task in pending:
            task.cancel()
        if pending:
            # Await, not just request, cancellation -- each task's own
            # CancelledError handler (harness-specific, e.g. claude_code.py's
            # terminate_gracefully) is what actually tears down its
            # subprocess. Returning before that finishes would let
            # run_daemon() exit (and the process die, if this is on the
            # graceful-shutdown path) while a provider subprocess is still
            # orphaned mid-teardown.
            await asyncio.gather(*pending, return_exceptions=True)


async def run_daemon_with_graceful_shutdown(**kwargs: Any) -> None:
    """Run ``run_daemon`` with SIGTERM/SIGINT cancelling it instead of killing
    the process outright.

    With no signal handler installed, the interpreter's default SIGTERM
    behavior tears the process down immediately -- none of ``run_daemon``'s
    own cleanup (terminating active/held provider subprocesses) gets a
    chance to run, orphaning them. `lemma daemon stop` and a plain `kill`
    both send SIGTERM; this makes both paths shut down the same way a
    reconnect-triggered hold/cancel already does.
    """
    import signal

    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(run_daemon(**kwargs))

    def _request_shutdown(sig_name: str) -> None:
        daemon_log("received shutdown signal; stopping gracefully", {"signal": sig_name})
        task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            # Not implemented on Windows -- falls back to default handling there.
            loop.add_signal_handler(sig, _request_shutdown, sig.name)

    with contextlib.suppress(asyncio.CancelledError):
        await task


def _capacity_payload(active_run_count: int) -> dict[str, int]:
    return {
        "max_concurrent_runs": max_concurrent_runs(),
        "active_run_count": active_run_count,
    }


async def _heartbeat_loop(
    websocket: Any,
    *,
    send_lock: asyncio.Lock,
    pong_seen: asyncio.Event,
    active_runs: dict[str, Any],
) -> None:
    """Send ``daemon.ping`` on its own task, independent of run-handling tasks.

    A busy/slow run task must never delay this — that decoupling is the actual
    fix for connections dying under load. Detects a dead peer by counting
    consecutive un-answered pings and closes the socket itself; that closure
    unblocks ``_serve_connection``'s ``async for`` message loop via the normal
    ``ConnectionClosed`` path, so no restructuring of that loop is needed.

    Also carries the daemon's live capacity (``active_run_count`` /
    ``max_concurrent_runs``) on every ping -- this is the most frequent signal
    the backend gets, so it's the natural carrier for a fact that can change
    every few seconds as runs start/finish.
    """
    interval = ping_interval_seconds()
    limit = pong_miss_limit()
    misses = 0
    while True:
        await asyncio.sleep(interval)
        pong_seen.clear()
        await _send_json(
            websocket,
            {"type": "daemon.ping", "payload": {"capacity": _capacity_payload(len(active_runs))}},
            lock=send_lock,
        )
        try:
            await asyncio.wait_for(pong_seen.wait(), timeout=interval)
            misses = 0
        except asyncio.TimeoutError:
            misses += 1
            daemon_log("daemon.pong missed", {"misses": misses, "limit": limit})
            if misses >= limit:
                daemon_log("heartbeat stale; closing connection", {"misses": misses})
                with contextlib.suppress(Exception):
                    await websocket.close()
                return


async def _serve_connection(
    websocket: Any,
    *,
    config: dict[str, Any],
    catalog: Any,
    base_url: str,
    held_runs: dict[str, _HeldRun],
) -> None:
    """Run the ready handshake + message loop until the connection closes.

    In-flight run tasks are NOT cancelled when the connection drops -- their
    subprocesses are independent OS processes that keep running regardless of
    the websocket. Each is moved into ``held_runs`` with its event sink
    redirected to a local buffer, then reattached (buffered events flushed,
    sink pointed live again) the next time a connection comes up. Only the
    daemon-wide reaper (``_reap_expired_held_runs``) or an explicit
    ``run.stop`` actually terminates a run's subprocess.
    """
    send_lock = asyncio.Lock()

    reattach_runs = [
        {
            "agent_run_id": agent_run_id,
            "buffered_event_count": len(held.buffer),
            "overflowed": held.sink.overflowed,
        }
        for agent_run_id, held in held_runs.items()
        if not held.task.done()
    ]
    await _send_json(
        websocket,
        {
            "type": "daemon.ready",
            "payload": {
                "device_key": config["device_key"],
                "display_name": config.get("display_name")
                or __import__("socket").gethostname(),
                "device_info": device_info(),
                "harness_catalog": catalog,
                "reattach_runs": reattach_runs,
                # Runs about to be reattached (below) already count against
                # capacity even though they haven't been re-added to
                # active_runs yet at this point in the handshake.
                "capacity": _capacity_payload(len(reattach_runs)),
            },
        },
        lock=send_lock,
    )
    daemon_log(
        "connected; waiting for runs",
        {"catalog": catalog, "reattached": len(reattach_runs)},
    )

    active_runs: dict[str, asyncio.Task[None]] = {}
    active_sinks: dict[str, _RunEventSink] = {}

    # Reattach: flush each held run's buffered events (oldest first) over the
    # new connection, THEN point its sink live -- in that order, so buffered
    # history can never race ahead of (or behind) events emitted after
    # reattachment.
    for agent_run_id, held in list(held_runs.items()):
        held_runs.pop(agent_run_id, None)
        if held.task.done():
            continue
        for buffered in list(held.buffer):
            await send_run_event(
                websocket, agent_run_id, buffered["type"], buffered["data"], lock=send_lock
            )
        held.buffer.clear()
        held.sink.go_live(websocket, send_lock)
        active_runs[agent_run_id] = held.task
        active_sinks[agent_run_id] = held.sink

        def _on_reattached_run_done(_task: Any, run_id: str = agent_run_id) -> None:
            active_runs.pop(run_id, None)
            active_sinks.pop(run_id, None)

        held.task.add_done_callback(_on_reattached_run_done)

    pong_seen = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            websocket, send_lock=send_lock, pong_seen=pong_seen, active_runs=active_runs
        )
    )
    try:
        async for raw_message in websocket:
            message = json.loads(raw_message)
            message_type = message.get("type")
            daemon_log("incoming websocket message", message)
            if message_type == "daemon.ready_ack":
                config["daemon_id"] = message.get("daemon_id")
                save_config(config)
                daemon_log("ready ack", {"daemon_id": config["daemon_id"]})
                continue
            if message_type == "daemon.pong":
                pong_seen.set()
                continue
            if message_type == "catalog.refresh":
                catalog = discover_harness_catalog()
                await _send_json(
                    websocket,
                    {
                        "type": "daemon.catalog",
                        "payload": catalog,
                        "capacity": _capacity_payload(len(active_runs)),
                    },
                    lock=send_lock,
                )
                daemon_log("catalog refreshed", catalog)
                continue
            if message_type == "run.start":
                agent_run_id = str(message.get("agent_run_id") or "")
                if agent_run_id in active_runs:
                    # Redelivered run.start for a run already in flight (e.g. a
                    # race during reconnect) -- ack, don't spawn a second
                    # subprocess for the same id.
                    daemon_log(
                        "duplicate run.start ignored", {"agent_run_id": agent_run_id}
                    )
                    continue
                cap = max_concurrent_runs()
                if len(active_runs) >= cap:
                    # Explicit, immediate rejection -- not a silent queue.
                    # Queuing here would look identical, from the backend's
                    # DaemonHarness.run() side, to a run that's legitimately
                    # just slow to emit its first event, and both would sit
                    # silently until event_timeout_seconds (2h) eventually
                    # fired. Rejecting up front also means this run never
                    # starts a timeout clock at all.
                    daemon_log(
                        "run.start rejected: daemon at capacity",
                        {"agent_run_id": agent_run_id, "active": len(active_runs), "cap": cap},
                    )
                    await send_run_event(
                        websocket,
                        agent_run_id,
                        "rejected",
                        {
                            "reason": "daemon_at_capacity",
                            "active_run_count": len(active_runs),
                            "max_concurrent_runs": cap,
                        },
                        lock=send_lock,
                    )
                    continue
                sink = _RunEventSink(websocket, agent_run_id, send_lock)
                task = asyncio.create_task(
                    handle_run_start(message, base_url=base_url, sink=sink)
                )
                active_runs[agent_run_id] = task
                active_sinks[agent_run_id] = sink

                def _on_run_done(_task: Any, run_id: str = agent_run_id) -> None:
                    active_runs.pop(run_id, None)
                    active_sinks.pop(run_id, None)

                task.add_done_callback(_on_run_done)
                continue
            if message_type == "run.stop":
                agent_run_id = str(message.get("agent_run_id") or "")
                await _stop_active_run(
                    websocket=websocket,
                    active_runs=active_runs,
                    agent_run_id=agent_run_id,
                    send_lock=send_lock,
                )
                continue
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        disconnected_at = time.monotonic()
        for agent_run_id, task in list(active_runs.items()):
            if task.done():
                continue
            sink = active_sinks.get(agent_run_id)
            if sink is None:
                # Should never happen -- sinks are added at the same time as
                # active_runs entries. Without a sink there's nowhere sane to
                # redirect this run's output, so fall back to the old
                # cancel-on-disconnect behavior rather than holding it blind.
                task.cancel()
                continue
            buffer: "collections.deque[dict[str, Any]]" = collections.deque(
                maxlen=max_buffered_events_per_run()
            )
            sink.go_buffered(buffer)
            held_runs[agent_run_id] = _HeldRun(
                task=task, sink=sink, buffer=buffer, disconnected_at=disconnected_at
            )


async def _stop_active_run(
    *,
    websocket: Any,
    active_runs: dict[str, asyncio.Task[None]],
    agent_run_id: str,
    send_lock: asyncio.Lock | None = None,
) -> None:
    task = active_runs.get(agent_run_id)
    if task is not None:
        task.cancel()
        return
    if agent_run_id:
        await send_run_event(websocket, agent_run_id, "stopped", {}, lock=send_lock)


async def handle_run_start(
    message: dict[str, Any],
    *,
    sink: _RunEventSink,
    base_url: str | None = None,
) -> None:
    """Run one provider turn, sending every event through ``sink``.

    ``sink`` is an indirection, not a fixed destination: it can be redirected
    between "send live" and "buffer locally" by ``_serve_connection`` across a
    disconnect/reconnect without this function (or the subprocess it drives)
    ever being restarted.
    """
    agent_run_id = str(message.get("agent_run_id") or "")
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    if base_url:
        payload = payload_with_reachable_mcp_urls(payload, base_url=base_url)
    harness_kind = str(payload.get("harness_kind") or "")
    daemon_log(
        "run requested",
        {
            "agent_run_id": agent_run_id,
            "harness_kind": harness_kind,
            "model_name": payload.get("model_name"),
            "mcp": payload.get("mcp"),
            "prompt_preview": _preview(_prompt_text_preview(payload)),
        },
    )
    await sink(
        "status", {"status": "daemon provider process starting", "harness_kind": harness_kind}
    )
    try:
        result = await run_provider_command(payload, event_sink=sink)
    except asyncio.CancelledError:
        daemon_log("run cancelled", {"agent_run_id": agent_run_id})
        await sink("stopped", {})
        raise
    except Exception as exc:
        error_detail = _exception_detail(exc)
        daemon_log("run failed", {"agent_run_id": agent_run_id, "error": error_detail})
        await sink("error", error_detail)
        return
    daemon_log(
        "provider result",
        {
            "agent_run_id": agent_run_id,
            "returncode": result.get("returncode"),
            "stdout_preview": _preview(result.get("stdout", "")),
            "stderr_preview": _preview(result.get("stderr", "")),
            "command": result.get("command"),
        },
    )
    result["stdout"] = _strip_prompt_echo_from_stdout(payload, str(result.get("stdout") or ""))
    if result["stdout"]:
        redacted_command = _redact(result["command"])
        if not result.get("streamed_tokens"):
            await sink("token", result["stdout"])
        if not result.get("streamed_messages"):
            await sink(
                "message",
                {
                    "role": "assistant",
                    "kind": "text",
                    "text": result["stdout"],
                    "metadata": {
                        "user_daemon": True,
                        "harness_kind": harness_kind,
                        "command": redacted_command,
                    },
                },
            )
    if result["returncode"] == 0:
        await sink("completed", {"returncode": result["returncode"], "stderr": result["stderr"]})
        return
    await sink(
        "error",
        result["stderr"] or f"Provider command failed with exit code {result['returncode']}",
    )


async def run_provider_command(
    payload: dict[str, Any],
    *,
    event_sink: Any = None,
) -> dict[str, Any]:
    harness_kind = str(payload.get("harness_kind") or "")
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    model_name = str(payload.get("model_name") or "default")
    mcp = payload.get("mcp") if isinstance(payload.get("mcp"), dict) else {}

    if harness_kind in {"CODEX", "CLAUDE_CODE", "OPENCODE", "CURSOR", "ANTIGRAVITY"}:
        harness = get_harness(harness_kind)
        allow_recovery = harness_kind == "CODEX"
        system_prompt, user_prompt, session_id = _prompt_parts(prompt, allow_recovery_system_prompt=allow_recovery)
        return await harness.run(
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=None,
        )

    # Generic one-shot provider via command template
    system_prompt, user_prompt, session_id = _prompt_parts(prompt)
    prompt_text = _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
    command = provider_command(
        harness_kind=harness_kind,
        model_name=model_name,
        prompt_text=prompt_text,
        mcp=mcp,
    )
    if not command:
        raise RuntimeError(f"No provider command configured for {harness_kind}")
    from .mcp import provider_command_template
    stdin_text = None if "{prompt}" in provider_command_template(harness_kind) else prompt_text
    cwd = provider_cwd_for_run(harness_kind, mcp)
    env = provider_environment(harness_kind=harness_kind, mcp=mcp)
    daemon_log("start one-shot provider", {"harness_kind": harness_kind, "command": command, "cwd": str(cwd)})
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await process.communicate(
            stdin_text.encode() if stdin_text is not None else None
        )
    except asyncio.CancelledError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        raise
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": int(process.returncode or 0),
        "stdout": stdout_bytes.decode(errors="replace").strip(),
        "stderr": stderr_bytes.decode(errors="replace").strip(),
    }


async def send_run_event(
    websocket: Any,
    agent_run_id: str,
    event_type: str,
    data: Any,
    *,
    lock: asyncio.Lock | None = None,
) -> None:
    daemon_log("send run event", {"agent_run_id": agent_run_id, "event_type": event_type, "data": data})
    await _send_json(
        websocket,
        {
            "type": "run.event",
            "agent_run_id": agent_run_id,
            "event": {"type": event_type, "data": data},
        },
        lock=lock,
    )


async def _send_json(
    websocket: Any,
    payload: dict[str, Any],
    *,
    lock: asyncio.Lock | None = None,
) -> None:
    """Send a JSON message, tolerating a dropped socket.

    A failed send (e.g. the connection dropped mid-run) must not crash the daemon —
    the reconnect loop re-establishes the connection; the dropped event is logged.

    ``lock`` serializes concurrent senders on one websocket (the heartbeat task
    and N run tasks can all be sending at once) — ``websockets``' ``send()`` is
    not safe to call concurrently from multiple tasks without one.
    """
    try:
        if lock is not None:
            async with lock:
                await websocket.send(json.dumps(payload))
        else:
            await websocket.send(json.dumps(payload))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport guard
        daemon_log(
            "websocket send failed; dropping message",
            {"type": payload.get("type"), "error": str(exc)},
        )


def _prompt_parts(
    prompt: dict[str, Any],
    *,
    allow_recovery_system_prompt: bool = False,
) -> tuple[str, str, str | None]:
    system_prompt = str(prompt.get("system_prompt") or "")
    if not system_prompt and allow_recovery_system_prompt:
        system_prompt = str(prompt.get("recovery_system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    session_id = str(prompt.get("session_id") or "").strip() or None
    if not user_prompt:
        raise RuntimeError("Daemon prompt payload is missing user_prompt")
    if session_id is None and not system_prompt:
        raise RuntimeError("Daemon prompt payload is missing system_prompt for new session")
    return system_prompt, user_prompt, session_id


def _provider_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return "\n\n".join(
        part
        for part in (system_prompt, _conversation_section(user_prompt))
        if part.strip()
    )


def _conversation_section(user_prompt: str) -> str:
    if not user_prompt:
        return ""
    if user_prompt.lstrip().startswith("#"):
        return user_prompt
    return "# Conversation\n" + user_prompt


def _prompt_text_preview(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    system_prompt = str(prompt.get("system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    if not system_prompt and not user_prompt:
        return ""
    return _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)


def _strip_prompt_echo_from_stdout(payload: dict[str, Any], stdout: str) -> str:
    if not stdout:
        return ""
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), dict) else {}
    system_prompt = str(prompt.get("system_prompt") or "")
    user_prompt = str(prompt.get("user_prompt") or "")
    candidates = [
        _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt),
        user_prompt,
    ]
    stripped_text = stdout.strip()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if stripped_text == candidate:
            return ""
        if stripped_text.startswith(candidate):
            return stripped_text[len(candidate):].lstrip()
    return stdout


def _exception_detail(exc: Exception) -> str:
    from .harnesses.codex import JsonRpcRequestError
    if isinstance(exc, JsonRpcRequestError):
        return str(exc)
    return bounded_error_detail(f"{type(exc).__name__}: {exc}")
