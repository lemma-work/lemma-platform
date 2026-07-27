"""`lemma agent-host` facade for the native, durable Agent Host binary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer


app = typer.Typer(
    help="Manage the durable local Agent Host.",
    no_args_is_help=True,
)


def _binary() -> str:
    configured = os.getenv("LEMMA_AGENT_HOST_BIN")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        raise RuntimeError(
            f"LEMMA_AGENT_HOST_BIN points to a missing file: {path}"
        )

    installed = shutil.which("lemma-agent-host")
    if installed:
        return installed

    executable_dir = Path(sys.executable).resolve().parent
    packaged_name = (
        "lemma-agent-host.exe" if sys.platform == "win32" else "lemma-agent-host"
    )
    packaged = executable_dir / packaged_name
    if packaged.is_file():
        return str(packaged)

    repository = Path(__file__).resolve().parents[3]
    for profile in ("release", "debug"):
        development = repository / "agent-host" / "target" / profile / packaged_name
        if development.is_file():
            return str(development)

    raise RuntimeError(
        "lemma-agent-host is not installed. Install the Lemma Desktop app or "
        "the Agent Host release binary, then place it on PATH (or set "
        "LEMMA_AGENT_HOST_BIN)."
    )


def _run(*arguments: str) -> None:
    try:
        result = subprocess.run([_binary(), *arguments], check=False)
    except OSError as exc:
        raise typer.BadParameter(f"could not start Agent Host: {exc}") from exc
    if result.returncode:
        raise typer.Exit(result.returncode)


def _locald_binary() -> str | None:
    configured = os.getenv("LEMMA_LOCALD_BIN")
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    installed = shutil.which("lemma-locald")
    if installed:
        return installed
    name = "lemma-locald.exe" if sys.platform == "win32" else "lemma-locald"
    sibling = Path(sys.executable).resolve().parent / name
    if sibling.is_file():
        return str(sibling)
    repository = Path(__file__).resolve().parents[3]
    for profile in ("release", "debug"):
        candidate = repository / "locald" / "target" / profile / name
        if candidate.is_file():
            return str(candidate)
    return None


def _locald_token_path() -> Path:
    configured = os.getenv("LEMMA_LOCALD_ROOT")
    if configured:
        return Path(configured).expanduser() / "control.token"
    if sys.platform == "darwin":
        root = Path.home() / "Library/Application Support/Lemma/locald"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "Lemma/locald"
    else:
        root = Path(
            os.environ.get("XDG_STATE_HOME", "~/.local/state")
        ).expanduser() / "lemma/locald"
    return root / "control.token"


def _run_lifecycle(command: str) -> None:
    locald = _locald_binary()
    if locald and _locald_token_path().is_file():
        request = json.dumps(
            {"cmd": f"agent-host.{command}", "id": f"lemma-cli-{command}"},
            separators=(",", ":"),
        )
        result = subprocess.run([locald, "send", request], check=False)
        if result.returncode == 0:
            return
    _run(command)


@app.command("connect")
def connect(
    ctx: typer.Context,
    pairing_code: str = typer.Option(..., "--pairing-code"),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Lemma API URL. Defaults to the active CLI server.",
    ),
    name: str = typer.Option("My computer", "--name"),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http"),
) -> None:
    if url is None:
        from lemma_cli.cli_core.state import state_from_ctx  # noqa: PLC0415
        from lemma_sdk.config import resolve_base_url  # noqa: PLC0415

        state = state_from_ctx(ctx)
        url = resolve_base_url(
            state.base_url,
            state.config,
            use_env=state.server_source == "env",
        )
    arguments = [
        "connect",
        "--url",
        url,
        "--pairing-code",
        pairing_code,
        "--name",
        name,
    ]
    if allow_insecure_http:
        arguments.append("--allow-insecure-http")
    _run(*arguments)


@app.command("disconnect")
def disconnect(
    target: str | None = typer.Option(None, "--target"),
    force_local: bool = typer.Option(False, "--force-local"),
) -> None:
    arguments = ["disconnect"]
    if target:
        arguments.extend(["--target", target])
    if force_local:
        arguments.append("--force-local")
    _run(*arguments)


@app.command("list")
def list_targets() -> None:
    _run("status", "--json")


@app.command("status")
def status() -> None:
    _run("status", "--json")


@app.command("start")
def start() -> None:
    _run_lifecycle("start")


@app.command("stop")
def stop() -> None:
    _run_lifecycle("stop")


@app.command("restart")
def restart() -> None:
    _run_lifecycle("restart")


@app.command("drain")
def drain(target: str | None = typer.Option(None, "--target")) -> None:
    _target_command("drain", target)


@app.command("resume")
def resume(target: str | None = typer.Option(None, "--target")) -> None:
    _target_command("resume", target)


@app.command("refresh")
def refresh(target: str | None = typer.Option(None, "--target")) -> None:
    _target_command("refresh", target)


def _target_command(command: str, target: str | None) -> None:
    arguments = [command]
    if target:
        arguments.extend(["--target", target])
    _run(*arguments)


@app.command("doctor")
def doctor() -> None:
    _run("doctor", "--json")


@app.command("discover")
def discover(
    probe: bool = typer.Option(False, "--probe"),
) -> None:
    arguments = ["discover", "--json"]
    if probe:
        arguments.append("--probe")
    _run(*arguments)


@app.command("logs")
def logs(
    lines: int = typer.Option(200, "--lines", min=1),
    follow: bool = typer.Option(False, "--follow", "-f"),
) -> None:
    arguments = ["logs", "--lines", str(lines)]
    if follow:
        arguments.append("--follow")
    _run(*arguments)


@app.command("install-service")
def install_service() -> None:
    _run("install-service")


@app.command("uninstall-service")
def uninstall_service() -> None:
    _run("uninstall-service")


@app.command("serve")
def serve() -> None:
    _run("serve")


@app.command("run", hidden=True)
def run_local(
    agent: str = typer.Option(..., "--agent"),
    prompt: str = typer.Option(..., "--prompt"),
) -> None:
    _run("run", "--agent", agent, "--prompt", prompt)
