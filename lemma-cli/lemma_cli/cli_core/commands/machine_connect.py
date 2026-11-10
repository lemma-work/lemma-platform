"""``lemma machine connect`` and ``disconnect``: join this laptop or VM to
Lemma, or take it away again. Registered on the ``machine`` group in
``commands/machine.py``.

``connect`` pairs the machine -- you approve a code in the browser -- and then
serves Lemma's workspace runtime on it, so agents, apps and the API reach it as
``mine:<name>`` the way they reach your cloud machine. By default it runs as
your own OS user, in a Python environment under ``--root`` (host mode); with
``--image`` it runs the same ``lemma-workspace`` image Lemma's own machines run,
under Docker, browser included.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..confirm import confirm_destructive
from ..io import emit
from ..state import err_console, fail, state_from_ctx
from .. import machine_setup as setup


def _api_url(ctx: typer.Context) -> str:
    from lemma_sdk.config import resolve_base_url

    state = state_from_ctx(ctx)
    url = resolve_base_url(
        state.base_url, state.config, use_env=state.server_source == "env"
    )
    if not url:
        fail("No Lemma server is configured. Run `lemma servers` to choose one.")
    return url.rstrip("/")


def _progress(message: str) -> None:
    err_console.print(f"[dim]{message}[/dim]")


def _announce(code: str, url: str | None) -> None:
    err_console.print()
    if url:
        err_console.print(
            f"Approve this machine in your browser:\n  [bold]{url}[/bold]"
        )
    err_console.print(
        f"The code it shows is [bold]{code}[/bold]. Waiting for approval…"
    )


def connect(
    ctx: typer.Context,
    root: Path = typer.Option(
        setup.DEFAULT_ROOT,
        "--root",
        help="Where Lemma's files and this machine's state live; the runtime's home.",
    ),
    image: bool = typer.Option(
        False,
        "--image",
        help="Run the lemma-workspace image under Docker instead of as your user.",
    ),
    image_ref: str | None = typer.Option(
        None, "--image-ref", help="The image to run; defaults to the server's own."
    ),
    docker: bool = typer.Option(
        False, "--docker", help="With --image: give it this machine's Docker daemon."
    ),
    pod: str | None = typer.Option(
        None, "--pod", help="Offer to share it into this pod when you approve it."
    ),
    install_service: bool = typer.Option(
        False,
        "--install-service",
        help="Keep it connected with a systemd user unit or a launchd agent.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the approval link without opening it."
    ),
) -> None:
    """Join this machine to Lemma and serve it until stopped."""
    state = state_from_ctx(ctx)
    api_url = _api_url(ctx)
    root = root.expanduser().resolve()
    if docker and not image:
        fail("--docker mounts this machine's Docker daemon into --image mode.")
    if image:
        consequence = (
            "Lemma agents you allow will run commands in a Docker container on this "
            "machine" + (", with this machine's Docker daemon" if docker else "") + "."
        )
    else:
        consequence = (
            f"Lemma agents you allow will run commands on this machine as "
            f"{setup.os_user()}, with their files under {root}. They can read "
            f"anything {setup.os_user()} can. Use --image to keep them in a container."
        )
    err_console.print(consequence)
    confirm_destructive("Connect this machine?", yes)

    try:
        connected = setup.connect(
            setup.ConnectOptions(
                api_url=api_url,
                root=root,
                image=image,
                image_ref=image_ref,
                mount_docker=docker,
                pod=pod,
                install_service=install_service,
                open_browser=not no_browser and sys.stdin.isatty(),
            ),
            announce=_announce,
            progress=_progress,
        )
    except setup.SetupError as error:
        fail(str(error))
    if connected.command is None:
        emit(
            state,
            {
                "name": connected.name,
                "mode": connected.mode,
                **({"service": str(connected.service)} if connected.service else {}),
                **({"container": connected.container} if connected.container else {}),
            },
        )
        return
    err_console.print(
        f"Serving {connected.name}. Press Ctrl-C to stop; "
        "`lemma machine connect --install-service` keeps it running."
    )
    os.execv(connected.command[0], connected.command)


def disconnect(
    ctx: typer.Context,
    root: Path = typer.Option(setup.DEFAULT_ROOT, "--root", help="Its --root."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Remove this machine from Lemma and stop serving it."""
    state = state_from_ctx(ctx)
    root = root.expanduser().resolve()
    confirm_destructive(f"Disconnect the machine joined from {root}?", yes)
    try:
        name = setup.disconnect(
            _api_url(ctx),
            root,
            warn=lambda message: err_console.print(f"[yellow]{message}[/yellow]"),
        )
    except setup.SetupError as error:
        fail(str(error))
    emit(state, {"name": name, "disconnected": True})
