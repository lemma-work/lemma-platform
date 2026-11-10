"""Machines: run commands on yours and your pods', copy files, share a port.

A machine is named by a ref rather than a bare name, because the same name can
be a pod's machine or one of your own:

  me                     your default machine
  pod:<pod-id>/<name>    a pod's machine (pod:<name> for the selected pod)
  mine:<name>            another machine of yours
  <machine-id>

`exec` and `shell` follow the process over a socket, so what it prints shows up
as it prints it, and `exec` exits with the command's own exit code.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from ..confirm import confirm_destructive
from ..io import emit, to_plain
from ..state import err_console, fail, run_with_client, state_from_ctx
from . import machine_connect

if TYPE_CHECKING:
    from lemma_sdk import Lemma

    from ..state import CliState

app = typer.Typer(
    help="Run commands, copy files and share ports on your machines and your pods'.",
    no_args_is_help=True,
)
preview_app = typer.Typer(
    help="Share a port a machine is serving on, as a URL.", no_args_is_help=True
)
logins_app = typer.Typer(
    help="Sites a machine's browser is signed in to.", no_args_is_help=True
)
app.add_typer(preview_app, name="preview")
app.add_typer(logins_app, name="logins")
# Joining this computer as a machine, and removing it (`machine_connect`).
app.command("connect")(machine_connect.connect)
app.command("disconnect")(machine_connect.disconnect)

_REF_HELP = "Which machine: me, pod:<pod-id>/<name>, pod:<name>, mine:<name> or an id."

#: Exit code for a command whose exit status the machine never reported (it
#: was cancelled, or timed out): the shell's convention for "killed".
_NO_EXIT_CODE = 137

_RESET_SCOPES = ("processes", "runs", "caches", "browser-session", "docker", "home")
_WORK_LOSING_SCOPES = frozenset({"docker", "home"})


def _ref_option(default: str = "me") -> Any:
    return typer.Argument(default, help=_REF_HELP)


# -- list ---------------------------------------------------------------------


@app.command("list")
def list_machines(
    ctx: typer.Context,
    pod: str | None = typer.Option(
        None, "--pod", help="Only this pod's machines (id or name)."
    ),
) -> None:
    """List the machines you can use."""
    state = state_from_ctx(ctx)

    def call(client: Lemma, cli_state: CliState) -> object:
        if pod is None:
            return client.machines.list()
        from .pods import resolve_pod_id

        return client.machines.list(pod_id=resolve_pod_id(client, cli_state, pod))

    result = run_with_client(ctx, call)
    if result is not None:
        emit(state, result)


# -- exec / shell -------------------------------------------------------------


def _command_line(command: list[str]) -> str:
    """One argument is a shell line as written; several are quoted words."""
    if len(command) == 1:
        return command[0]
    return shlex.join(command)


def _parse_env(pairs: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"{pair!r} is not KEY=VALUE", param_hint="--env")
        env[key] = value
    return env


def _exit_code_of(stream: Any) -> int:
    code = getattr(stream, "exit_code", None)
    if isinstance(code, int):
        return code
    return 0 if getattr(stream, "state", None) == "succeeded" else _NO_EXIT_CODE


@app.command(
    "exec",
    context_settings={"allow_interspersed_args": False},
    epilog=(
        "Examples:\n\n"
        "  lemma machine exec me ls -la\n"
        "  lemma machine exec pod:builder 'npm ci && npm test'\n"
        "  lemma machine exec --cwd /Users/me/src mine:mac make\n\n"
        "Options go before the ref: everything after it is the command."
    ),
)
def exec_command(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    command: list[str] = typer.Argument(
        ..., help="The command. One argument runs as a shell line."
    ),
    cwd: str | None = typer.Option(
        None, "--cwd", help="Directory to run in, inside the machine's home."
    ),
    env: list[str] | None = typer.Option(
        None, "--env", "-e", help="KEY=VALUE for the command's environment; repeatable."
    ),
    timeout: int | None = typer.Option(
        None, "--time-limit", help="Stop the command after this many seconds."
    ),
) -> None:
    """Run a command on a machine, stream its output, and exit with its code."""
    state = state_from_ctx(ctx)
    line = _command_line(command)
    environment = _parse_env(env)
    as_json = state.output == "json"

    def call(client: Lemma, _state: CliState) -> dict[str, object]:
        stream = client.machines.exec(
            ref, line, cwd=cwd, env=environment or None, timeout_seconds=timeout
        )
        stdout: list[str] = []
        stderr: list[str] = []
        try:
            for chunk in stream:
                into_err = chunk.channel == "stderr"
                if as_json:
                    (stderr if into_err else stdout).append(chunk.data)
                    continue
                target = sys.stderr if into_err else sys.stdout
                target.write(chunk.data)
                target.flush()
        except KeyboardInterrupt:
            stream.kill()
            raise typer.Exit(code=130) from None
        return {
            "process_id": getattr(stream, "process_id", None),
            "state": getattr(stream, "state", None),
            "exit_code": _exit_code_of(stream),
            "stdout": "".join(stdout),
            "stderr": "".join(stderr),
        }

    result = run_with_client(ctx, call)
    if result is None:
        return
    if as_json:
        emit(state, result)
    code = result["exit_code"]
    raise typer.Exit(code=code if isinstance(code, int) else 1)


@app.command("shell")
def shell(
    ctx: typer.Context,
    ref: str = _ref_option(),
    command: str = typer.Option(
        "bash -l", "--command", "-c", help="What to run in the terminal."
    ),
) -> None:
    """Open an interactive terminal on a machine."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        fail(
            "`lemma machine shell` needs a terminal. To run one command, use "
            f"`lemma machine exec {ref} <command>`."
        )
    try:
        import termios  # noqa: F401
    except ImportError:
        fail("`lemma machine shell` needs a POSIX terminal; use `lemma machine exec`.")

    def call(client: Lemma, _state: CliState) -> int:
        from ..machine_shell import run_shell

        return run_shell(client, ref, command)

    code = run_with_client(ctx, call)
    raise typer.Exit(code=code if isinstance(code, int) else 1)


# -- processes ----------------------------------------------------------------


@app.command("ps")
def list_processes(ctx: typer.Context, ref: str = _ref_option()) -> None:
    """Show what is running on a machine."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, _s: client.machines.processes.list(ref)
    )
    if result is not None:
        emit(state, result)


@app.command("kill")
def kill_process(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    process_id: str = typer.Argument(..., help="The process, from `lemma machine ps`."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Stop a process on a machine."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Stop process {process_id} on {ref}?", yes)
    result = run_with_client(
        ctx,
        lambda client, _s: client.machines.processes.kill(ref, process_id) or True,
    )
    if result is not None:
        emit(state, {"process_id": process_id, "stopped": True})


# -- cp -------------------------------------------------------------------------


def split_machine_path(spec: str) -> tuple[str, str] | None:
    """``(ref, path)`` for ``<ref>:<path>``, or None for a local path.

    A ref may itself hold colons (``pod:<pod>:<name>``), so the split is at the
    first ``:/`` after the ref's kind -- an absolute path -- and otherwise at
    the last colon, which leaves the path relative to the machine's ``~/lemma``.
    """
    if spec.startswith("me:"):
        return "me", spec[len("me:") :]
    if spec.startswith(("pod:", "mine:")):
        kind_end = spec.index(":") + 1
        anchor = spec.find(":/", kind_end)
        if anchor != -1:
            return spec[:anchor], spec[anchor + 1 :]
        ref, _, path = spec.rpartition(":")
        if len(ref) < kind_end:
            raise typer.BadParameter(
                f"{spec!r} names a machine but no path: write {spec}:<path>"
            )
        return ref, path
    head, sep, path = spec.partition(":")
    if sep and _is_uuid(head):
        return head, path
    return None


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@app.command(
    "cp",
    epilog=(
        "Examples:\n\n"
        "  lemma machine cp ./report.csv me:/home/user/lemma/report.csv\n"
        "  lemma machine cp pod:builder:/home/user/lemma/dist/app.zip ./app.zip"
    ),
)
def copy(
    ctx: typer.Context,
    source: str = typer.Argument(
        ..., help="A local path, or <ref>:<path> on a machine."
    ),
    destination: str = typer.Argument(
        ..., help="A local path, or <ref>:<path> on a machine."
    ),
) -> None:
    """Copy a file between this computer and a machine."""
    state = state_from_ctx(ctx)
    remote_source = split_machine_path(source)
    remote_destination = split_machine_path(destination)
    if (remote_source is None) == (remote_destination is None):
        fail(
            "Exactly one side must be on a machine, written <ref>:<path> "
            "(for example me:/home/user/lemma/notes.txt)."
        )

    if remote_destination is not None:
        local = Path(source).expanduser()
        if not local.is_file():
            fail(f"{source} is not a file. Copy one file at a time.")
        ref, path = remote_destination
        if not path or path.endswith("/"):
            path = f"{path}{local.name}"

        def upload(client: Lemma, _state: CliState) -> object:
            with local.open("rb") as handle:
                return client.machines.files.write(ref, path, handle)

        result = run_with_client(ctx, upload)
    else:
        assert remote_source is not None
        ref, path = remote_source
        target = Path(destination).expanduser()
        if target.is_dir() or destination.endswith(("/", os.sep)):
            target = target / (path.rstrip("/").rpartition("/")[2] or "download")

        def download(client: Lemma, _state: CliState) -> object:
            saved = client.machines.files.download_to(ref, path, target)
            return {"path": str(saved), "size_bytes": Path(saved).stat().st_size}

        result = run_with_client(ctx, download)
    if result is not None:
        emit(state, result)


# -- preview --------------------------------------------------------------------


@preview_app.command("create")
def create_preview(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    port: int = typer.Option(..., "--port", "-p", help="The port to share."),
    visibility: str = typer.Option(
        "private",
        "--visibility",
        help="private: people who may use the machine. link: anyone with the URL.",
    ),
    ttl: int | None = typer.Option(
        None, "--ttl", help="Seconds until the URL stops working."
    ),
) -> None:
    """Share a port a machine is serving on."""
    if visibility not in ("private", "link"):
        raise typer.BadParameter("use private or link", param_hint="--visibility")
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, _s: client.machines.previews.create(
            ref, port, visibility=visibility, ttl_seconds=ttl
        ),
    )
    if result is not None:
        emit(state, result)


@preview_app.command("list")
def list_previews(ctx: typer.Context, ref: str = _ref_option()) -> None:
    """List a machine's shared ports."""
    state = state_from_ctx(ctx)
    result = run_with_client(ctx, lambda client, _s: client.machines.previews.list(ref))
    if result is not None:
        emit(state, result)


@preview_app.command("revoke")
def revoke_preview(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    preview_id: str = typer.Argument(
        ..., help="The preview, from `lemma machine preview list`."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Stop sharing a port; its URL stops working."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Revoke preview {preview_id} on {ref}?", yes)
    result = run_with_client(
        ctx,
        lambda client, _s: client.machines.previews.revoke(ref, preview_id) or True,
    )
    if result is not None:
        emit(state, {"preview_id": preview_id, "revoked": True})


# -- reset ----------------------------------------------------------------------


@app.command("reset")
def reset(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    scope: list[str] = typer.Option(
        ...,
        "--scope",
        "-s",
        help=f"What to clean; repeatable: {', '.join(_RESET_SCOPES)}.",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Allow the scopes that discard work (home, docker).",
    ),
    forget_logins: bool = typer.Option(
        False, "--forget-logins", help="Also sign the browser out of every site."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Clean a machine: stop processes, clear caches, or wipe its home."""
    unknown = sorted(set(scope) - set(_RESET_SCOPES))
    if unknown:
        raise typer.BadParameter(
            f"{', '.join(unknown)}: use {', '.join(_RESET_SCOPES)}",
            param_hint="--scope",
        )
    losing = sorted(set(scope) & _WORK_LOSING_SCOPES)
    if losing and not confirm:
        fail(f"Resetting {', '.join(losing)} discards work; pass --confirm to do it.")
    state = state_from_ctx(ctx)
    confirm_destructive(f"Reset {', '.join(scope)} on {ref}?", yes)
    result = run_with_client(
        ctx,
        lambda client, _s: client.machines.reset(
            ref, scope, confirm=confirm, forget_logins=forget_logins
        ),
    )
    if result is not None:
        emit(state, result)


# -- logins ---------------------------------------------------------------------


@logins_app.command("list")
def list_logins(
    ctx: typer.Context,
    ref: str = _ref_option(),
    wake: bool = typer.Option(
        False, "--wake", help="Start the machine if it is asleep."
    ),
) -> None:
    """List the sites a machine's browser holds cookies for."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, _s: client.machines.browser.logins(ref, wake=wake)
    )
    if result is not None:
        emit(state, result)


@logins_app.command("forget")
def forget_login(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help=_REF_HELP),
    site: str = typer.Argument(..., help="The site, such as app.example.com."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Sign a machine's browser out of a site."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Sign {ref}'s browser out of {site}?", yes)
    result = run_with_client(
        ctx, lambda client, _s: client.machines.browser.forget_login(ref, site)
    )
    if result is not None:
        emit(state, to_plain(result) or {"site": site, "forgotten": True})


__all__ = ["app", "err_console", "split_machine_path"]
