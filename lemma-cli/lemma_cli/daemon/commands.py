from __future__ import annotations

import os
import signal
import subprocess
import sys

import typer

from .catalog import discover_harness_catalog
from .config import (
    DAEMON_LOG_PATH,
    DAEMON_CONFIG_PATH,
    clear_daemon_status,
    ensure_config,
    process_is_running,
    read_daemon_status,
    read_pid,
    write_daemon_status,
)

# NOTE: asyncio and .runner are imported inside `start_daemon` — this module is
# loaded on every CLI startup to register the command group, and the runner
# (asyncio, http.server, harnesses) only matters once a daemon actually starts.

app = typer.Typer(help="Run the Lemma user daemon.", no_args_is_help=True)


def _console():
    from lemma_cli.cli_core.state import console  # noqa: PLC0415
    return console


def _fail(msg: str) -> None:
    from lemma_cli.cli_core.state import fail  # noqa: PLC0415
    fail(msg)


def _state_from_ctx(ctx: typer.Context):
    from lemma_cli.cli_core.state import state_from_ctx  # noqa: PLC0415
    return state_from_ctx(ctx)


def _refresh_auth_session(state) -> None:
    from lemma_cli.cli_core.state import refresh_auth_session  # noqa: PLC0415
    refresh_auth_session(state)


@app.command("discover")
def discover_daemon() -> None:
    _console().print_json(data=discover_harness_catalog())


@app.command("status")
def status_daemon() -> None:
    config = ensure_config()
    status = read_daemon_status() or {}
    pid = status.get("pid")
    running = pid is not None and process_is_running(pid)
    _console().print_json(
        data={
            "device_key": config["device_key"],
            "pid": pid,
            "running": running,
            "base_url": status.get("base_url"),
            "server": status.get("server"),
            "started_at": status.get("started_at"),
            "config_path": str(DAEMON_CONFIG_PATH),
            "log_path": str(DAEMON_LOG_PATH),
        }
    )


@app.command("start")
def start_daemon(
    ctx: typer.Context,
    background: bool = typer.Option(False, "--background", help="Run in background."),
    debug: bool = typer.Option(False, "--debug", help="Print verbose daemon event payloads."),
) -> None:
    if background:
        start_background(ctx, debug=debug)
        return
    state = _state_from_ctx(ctx)
    try:
        _refresh_auth_session(state)
    except ValueError as exc:
        _fail(f"Unable to refresh Lemma session: {exc}. Run `lemma auth login`.")
    import asyncio  # noqa: PLC0415
    import os  # noqa: PLC0415

    from lemma_sdk.config import resolve_base_url, resolve_token, resolve_verify_ssl  # noqa: PLC0415

    from .runner import run_daemon_with_graceful_shutdown  # noqa: PLC0415

    resolved_base_url = resolve_base_url(
        state.base_url, state.config, use_env=state.server_source == "env",
    )
    write_daemon_status(os.getpid(), base_url=resolved_base_url, server=state.server)
    try:
        asyncio.run(
            run_daemon_with_graceful_shutdown(
                base_url=resolved_base_url,
                token=resolve_token(
                    state.token,
                    state.config,
                    use_env=state.server_source == "env",
                ),
                verify_ssl=resolve_verify_ssl(state.no_verify_ssl),
                debug=debug,
            )
        )
    finally:
        clear_daemon_status()


@app.command("stop")
def stop_daemon() -> None:
    pid = read_pid()
    if pid is None:
        _console().print("Daemon is not running.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        _fail(f"Unable to stop daemon {pid}: {exc}")
    clear_daemon_status()
    _console().print(f"Stopped daemon {pid}.")


@app.command("logs")
def logs_daemon() -> None:
    if not DAEMON_LOG_PATH.exists():
        _console().print("No daemon log file found.")
        return
    _console().print(DAEMON_LOG_PATH.read_text())


def _detach_kwargs() -> dict:
    """Popen kwargs that detach the daemon from this CLI invocation.

    POSIX uses a new session (setsid). Windows has no setsid and silently ignores
    ``start_new_session``, so the equivalent is a detached, console-less new
    process group. The ``subprocess.*`` flag constants below only exist on
    Windows, so this branch is reached only where they are defined.
    """
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        }
    return {"start_new_session": True}


def start_background(ctx: typer.Context, *, debug: bool = False) -> None:
    state = _state_from_ctx(ctx)
    DAEMON_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if (pid := read_pid()) is not None and process_is_running(pid):
        _fail(f"Daemon is already running with pid {pid}")
    # Pass the RESOLVED base/auth URLs (active server's stored urls), not the raw
    # --base-url flag (usually None) — otherwise the child re-resolves and can
    # fall back to the cloud default, so its wss:// points at the wrong server.
    from lemma_sdk.config import resolve_auth_url, resolve_base_url  # noqa: PLC0415

    use_env = state.server_source == "env"
    resolved_base_url = resolve_base_url(state.base_url, state.config, use_env=use_env)
    resolved_auth_url = resolve_auth_url(state.auth_url, state.config, use_env=use_env)
    command = [sys.executable, "-m", "lemma_cli.cli_app.main"]
    command.extend(["--base-url", resolved_base_url])
    command.extend(["--auth-url", resolved_auth_url])
    if state.token:
        command.extend(["--token", state.token])
    if state.server:
        command.extend(["--server", state.server])
    if state.no_verify_ssl:
        command.append("--no-verify-ssl")
    command.extend(["--config-file", str(state.config_path), "daemon", "start"])
    if debug:
        command.append("--debug")
    # Detach the daemon so it outlives this CLI invocation (POSIX setsid /
    # Windows detached process group — see _detach_kwargs).
    with DAEMON_LOG_PATH.open("ab") as log_file:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            **_detach_kwargs(),
        )
    write_daemon_status(process.pid, base_url=resolved_base_url, server=state.server)
    _console().print(f"Started daemon in background with pid {process.pid}.")
    _console().print(f"Logs: {DAEMON_LOG_PATH}")
