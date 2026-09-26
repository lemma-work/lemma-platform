"""Live datastore change stream for `lemma datastore watch`.

Connects to the backend ``/pods/{pod_id}/datastore/changes`` websocket with a
bearer token, renders each record change, and reconnects with backoff — resuming
from the last seen stream id so a brief drop replays missed changes.

Kept out of ``commands/data.py`` and imported lazily so the websocket/asyncio
machinery never loads on a plain ``lemma --help``.
"""

from __future__ import annotations

import json
import random
from urllib.parse import urlencode

from rich.console import Console

from lemma_sdk.config import resolve_base_url, resolve_token, resolve_verify_ssl

from .state import CliState, console, fail, refresh_auth_session

# Diagnostics (connecting / reconnecting / refreshed) go to stderr so stdout
# stays pure change data — clean NDJSON when piping with --output json.
_err = Console(stderr=True)

_OP_STYLE = {"insert": "green", "update": "yellow", "delete": "red"}

# Full-jitter exponential backoff, capped — mirrors the Agent Host reconnect
# policy.
_RECONNECT_BASE_DELAY_SECONDS = 0.5
_RECONNECT_MAX_DELAY_SECONDS = 30.0

# The server accepts the handshake, then closes with this code when the session
# is missing, invalid or expired. Older servers refused the upgrade with an HTTP
# 401/403 instead; both mean "refresh once, then give up".
_CLOSE_UNAUTHENTICATED = 4401
# Terminal closes no retry can fix: the caller has no access to the pod, or
# the pod/table does not exist.
_TERMINAL_CLOSES = {
    4403: "No access to this pod's changes.",
    4404: "Pod or table not found.",
}


def _reconnect_delay(attempt: int) -> float:
    ceiling = min(
        _RECONNECT_MAX_DELAY_SECONDS,
        _RECONNECT_BASE_DELAY_SECONDS * (2 ** max(0, attempt)),
    )
    return random.uniform(0.0, ceiling)


def _changes_ws_url(
    base_url: str, pod_id: str, table: str | None, since: str | None
) -> str:
    root = base_url.rstrip("/")
    if root.startswith("https://"):
        root = "wss://" + root.removeprefix("https://")
    elif root.startswith("http://"):
        root = "ws://" + root.removeprefix("http://")
    else:
        # No scheme (bare host) — assume TLS, like a browser.
        root = "wss://" + root.removeprefix("//")
    params: dict[str, str] = {}
    if table:
        params["table"] = table
    if since:
        params["since"] = since
    url = f"{root}/pods/{pod_id}/datastore/changes"
    query = urlencode(params)
    return f"{url}?{query}" if query else url


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

    cursor = since
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

        ws_url = _changes_ws_url(base_url, pod_id, table, cursor)
        ssl_option = None if ws_url.startswith("ws://") or verify_ssl else False
        auth_rejected = False
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                ssl=ssl_option,
            ) as websocket:
                async for raw in websocket:
                    # The server accepts before it authenticates, so an open
                    # socket proves nothing; the first frame (`ready`) does.
                    attempt = 0
                    refreshed_since_connect = False
                    cursor = _handle_message(state, raw, cursor)
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

        await asyncio.sleep(_reconnect_delay(attempt))
        attempt += 1


def _handle_message(state: CliState, raw: object, cursor: str | None) -> str | None:
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError, TypeError, ValueError:
        return cursor
    if not isinstance(frame, dict):
        return cursor

    if frame.get("type") == "ready":
        cursor = frame.get("since") or cursor
        if state.output == "json":
            print(json.dumps(frame, default=str))  # noqa: T201 — parseable stdout
        else:
            _err.print(f"[dim]● streaming (since={cursor})[/dim]")
        return cursor

    cursor = frame.get("stream_id") or cursor
    _render_frame(state, frame)
    return cursor


def _render_frame(state: CliState, frame: dict) -> None:
    if state.output == "json":
        print(json.dumps(frame, default=str))  # noqa: T201 — parseable stdout
        return
    operation = str(frame.get("operation") or "")
    style = _OP_STYLE.get(operation, "white")
    table_name = frame.get("table_name") or "?"
    record_id = frame.get("record_id") or ""
    when = _short_time(frame.get("occurred_at"))
    if frame.get("payload_truncated"):
        # An empty payload here means "too large to send", not "empty row".
        # Rendering nothing would read as the latter.
        payload = "[dim](body too large — read the record)[/dim]"
    else:
        payload = _compact_payload(
            frame.get("payload"), full=getattr(state, "full", False)
        )
    prefix = f"[dim]{when}[/dim] " if when else ""
    console.print(
        f"{prefix}[{style}]{operation:<6}[/{style}] [bold]{table_name}[/bold]"
        f" [dim]{record_id}[/dim] {payload}",
        highlight=False,
    )


def _short_time(occurred_at: object) -> str:
    if not isinstance(occurred_at, str) or "T" not in occurred_at:
        return ""
    return occurred_at.split("T", 1)[1][:8]


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
