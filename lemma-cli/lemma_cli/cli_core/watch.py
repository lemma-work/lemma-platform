"""Live datastore changes for `lemma datastore watch`.

Opens the pod's live socket (``/pods/{pod_id}/live``) with a bearer token,
subscribes to one table (or every readable one), renders each change, and
reconnects with backoff -- resuming from the last cursor seen, so a brief drop
replays missed changes. The protocol itself is ``lemma_sdk.live.LiveSession``,
shared with the Python SDK's ``Pod.watch_changes``.

Kept out of ``commands/data.py`` and imported lazily so the websocket/asyncio
machinery never loads on a plain ``lemma --help``.
"""

from __future__ import annotations

import json
import ssl

from rich.console import Console

from lemma_sdk.config import resolve_base_url, resolve_token, resolve_verify_ssl
from lemma_sdk.live import (
    LiveChange,
    LiveEnded,
    LiveEvent,
    LiveReady,
    LiveResync,
    LiveSession,
    live_ws_url,
    reconnect_delay,
)

from .state import CliState, console, fail, refresh_auth_session

# Diagnostics (connecting / reconnecting / refreshed) go to stderr so stdout
# stays pure change data -- clean NDJSON when piping with --output json.
_err = Console(stderr=True)

_OP_STYLE = {"insert": "green", "update": "yellow", "delete": "red"}

# The server accepts the handshake, then closes with this code when the session
# is missing, invalid or expired. Older servers refused the upgrade with an HTTP
# 401/403 instead; both mean "refresh once, then give up".
_CLOSE_UNAUTHENTICATED = 4401
# Terminal closes no retry can fix: the caller has no access to the pod, or
# the pod does not exist.
_TERMINAL_CLOSES = {
    4403: "No access to this pod's changes.",
    4404: "Pod or table not found.",
}


def _ssl_option(ws_url: str, *, verify_ssl: bool) -> ssl.SSLContext | None:
    """The ``ssl`` argument ``websockets.connect`` needs for ``ws_url``.

    ``websockets`` refuses ``None`` for a ``wss://`` URI and any value for a
    ``ws://`` one, and ``False`` is not "TLS without verification" but no TLS
    at all -- a plaintext handshake sent to a TLS port. So a ``wss://`` URI
    always gets a context; ``--no-verify-ssl`` only turns its checks off.
    """
    if not ws_url.startswith("wss://"):
        return None
    context = ssl.create_default_context()
    if not verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def watch_datastore_changes(
    state: CliState,
    pod_id: str,
    table: str | None,
    since: str | None,
) -> None:
    """Blocking entry point: stream changes until interrupted."""
    import asyncio

    try:
        asyncio.run(_run(state, pod_id, table, since))
    except KeyboardInterrupt:
        _err.print("[dim]Stopped.[/dim]")


async def _run(
    state: CliState,
    pod_id: str,
    table: str | None,
    since: str | None,
) -> None:
    import asyncio

    try:
        import websockets
        from websockets.exceptions import (
            ConnectionClosed,
            InvalidStatus,
            WebSocketException,
        )
    except ImportError:
        fail(
            "Missing dependency 'websockets'. Reinstall the CLI to enable watch: "
            "pip install --upgrade lemma-terminal (or: pip install websockets)."
        )
        return

    use_env = state.server_source == "env"
    base_url = resolve_base_url(state.base_url, state.config, use_env=use_env)
    verify_ssl = resolve_verify_ssl(state.no_verify_ssl)
    ws_url = live_ws_url(base_url, pod_id)
    ssl_option = _ssl_option(ws_url, verify_ssl=verify_ssl)

    session = LiveSession()
    session.add(table=table, since=since)
    attempt = 0
    # One free immediate reconnect per successful connection. `refresh_auth_session`
    # returns True when *another process* already rotated the tokens, without this
    # one ever proving the new token is accepted -- so an unconditional `continue`
    # below skipped both the sleep and the backoff increment and span at zero delay.
    refreshed_since_connect = False
    _err.print(
        f"[dim]Watching {table or 'all tables'} in pod {pod_id}… Ctrl-C to stop.[/dim]"
    )

    while True:
        try:
            token = resolve_token(state.token, state.config, use_env=use_env)
        except ValueError as exc:
            fail(str(exc))
            return

        auth_rejected = False
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                ssl=ssl_option,
            ) as websocket:
                async for raw in websocket:
                    # The server accepts before it authenticates, so an open
                    # socket proves nothing; `ready` does.
                    if _handle_message(state, session, raw):
                        attempt = 0
                        refreshed_since_connect = False
                        for frame in session.subscribe_frames():
                            await websocket.send(frame)
                    if not session.active:
                        fail("The server ended the subscription.")
                        return
        except asyncio.CancelledError:
            raise
        except InvalidStatus as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {401, 403}:
                auth_rejected = True
            else:
                _err.print(
                    f"[dim]Server rejected the stream (status {status_code}); "
                    "retrying…[/dim]"
                )
        except ConnectionClosed as exc:
            close_code = exc.rcvd.code if exc.rcvd is not None else None
            if close_code in _TERMINAL_CLOSES:
                fail(_TERMINAL_CLOSES[close_code])
                return
            if close_code == _CLOSE_UNAUTHENTICATED:
                auth_rejected = True
            else:
                _err.print(f"[dim]Connection lost ({exc}); reconnecting…[/dim]")
        except (OSError, WebSocketException) as exc:
            _err.print(f"[dim]Connection lost ({exc}); reconnecting…[/dim]")

        if auth_rejected:
            if (
                not use_env
                and not refreshed_since_connect
                and refresh_auth_session(state)
            ):
                refreshed_since_connect = True
                _err.print("[dim]Session refreshed; reconnecting…[/dim]")
                continue
            fail("Authentication failed. Run `lemma auth login` and try again.")
            return

        await asyncio.sleep(reconnect_delay(attempt))
        attempt += 1


def _handle_message(state: CliState, session: LiveSession, raw: object) -> bool:
    """Render what one server frame carries; True when it is ``ready``."""
    if not isinstance(raw, (str, bytes)):
        return False
    ready, events = session.handle(raw)
    for event in events:
        _render_event(state, event)
    return ready


def _render_event(state: CliState, event: LiveEvent) -> None:
    if isinstance(event, LiveChange):
        _render_change(state, event)
    elif isinstance(event, LiveReady):
        if state.output == "json":
            _emit({"type": "ready", "cursor": event.cursor, "since": event.cursor})
        else:
            _err.print(f"[dim]● streaming (since={event.cursor})[/dim]")
    elif isinstance(event, LiveResync):
        if state.output == "json":
            _emit({"type": "resync", "cursor": event.cursor, "reason": event.reason})
        else:
            _err.print(
                f"[yellow]resync[/yellow] [dim]({event.reason}): changes before "
                f"{event.cursor} were not replayed; re-read the table if you need them.[/dim]"
            )
    elif isinstance(event, LiveEnded):
        _err.print(f"[red]Subscription ended: {event.reason}[/red]")


def _emit(frame: dict[str, object]) -> None:
    print(json.dumps(frame, default=str))  # noqa: T201 — parseable stdout


def _render_change(state: CliState, change: LiveChange) -> None:
    if state.output == "json":
        # The shape the previous change socket streamed, so pipelines built on
        # it keep parsing; `cursor` is new and `stream_id` is its alias.
        frame = {
            "type": f"datastore.record.{change.op}",
            "table_name": change.table,
            "record_id": change.record_id,
            "operation": change.op,
            "payload": change.row or {},
            "cursor": change.cursor,
            "stream_id": change.cursor,
        }
        _emit(frame)
        return
    style = _OP_STYLE.get(change.op, "white")
    payload = _compact_payload(change.row, full=getattr(state, "full", False))
    console.print(
        f"[{style}]{change.op:<6}[/{style}] [bold]{change.table or '?'}[/bold]"
        f" [dim]{change.record_id}[/dim] {payload}",
        highlight=False,
    )


def _compact_payload(payload: object, *, full: bool) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    parts = [f"{key}={_short_value(value)}" for key, value in payload.items()]
    text = ", ".join(parts)
    if not full and len(text) > 120:
        text = text[:119].rstrip() + "…"
    return text


def _short_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)
