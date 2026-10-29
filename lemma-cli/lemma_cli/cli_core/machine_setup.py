"""What ``lemma machine connect`` does on the machine being joined.

The machine's own agent -- the program that holds its link to Lemma and serves
the workspace runtime -- is ``sandbox_runtime.machine_link.agent``, which ships
in the runtime bundle the server installs everywhere. This module only gets it
running:

* **pairing** -- ask the server for a code, show it, and wait on the link for
  the person to approve it in the browser; the machine's secret comes back on
  the same socket, once;
* **host mode** -- a uv-managed Python 3.14 environment under
  ``<root>/.lemma/env`` holding the image's locked third-party closure, and the
  server's runtime overlay installed into it; the agent then runs as this OS
  user, with ``<root>`` as the runtime's home;
* **image mode** -- the ``lemma-workspace`` image the server runs its own
  sandboxes from, under Docker, with the agent inside it;
* **services** -- a systemd user unit or a launchd agent that keeps host mode
  running; image mode is kept running by Docker's restart policy.

Nothing here imports the server's code: everything the agent needs comes from
the server, so the machine runs the server's version.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import io
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

#: The link protocol the compute role arrived in.
PROTOCOL_VERSION = 4
DEFAULT_ROOT = Path("~/lemma-machine")
STATE = ".lemma"
CONFIG_NAME = "machine.json"
SECRET_NAME = "machine-secret"
PYTHON_VERSION = "3.14"
AGENT_MODULE = "sandbox_runtime.machine_link.agent"

#: Inside the image: where the agent's two files go, its home, its interpreter.
IMAGE_STATE = "/run/lemma-bootstrap"
IMAGE_HOME = "/home/user"
IMAGE_PYTHON = "/opt/lemma-python/bin/python"
IMAGE_OVERLAY = "/opt/lemma-runtime"
IMAGE_UID = 10001
IMAGE_RUNTIME_PORT = 8080
IMAGE_RELAY_PORT = 4850


class SetupError(RuntimeError):
    """Something a person must fix; the message says what."""


@dataclass(frozen=True)
class Paired:
    connection_id: str
    user_id: str
    name: str
    machine_secret: str


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def state_directory(root: Path) -> Path:
    return root / STATE


def link_url(api_url: str) -> str:
    parts = urlsplit(api_url.rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, f"{parts.path}/agent-host/link", "", ""))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ------------------------------------------------------------------ pairing


def request_code(
    api_url: str, *, claim_key: str, display_name: str, mode: str, verify: bool = True
) -> dict[str, str]:
    response = httpx.post(
        f"{api_url.rstrip('/')}/agent-host/compute/pairings",
        json={
            "claim_key_sha256": hashlib.sha256(claim_key.encode()).hexdigest(),
            "display_name": display_name,
            "mode": mode,
            "platform": platform_name(),
        },
        timeout=30,
        verify=verify,
    )
    if response.status_code == 404:
        raise SetupError("This Lemma server does not take connected machines.")
    response.raise_for_status()
    return response.json()


async def claim(
    api_url: str, *, code: str, claim_key: str, installation_id: str, timeout: float
) -> Paired:
    """Wait on the link until the person approves the code."""
    import websockets

    async with websockets.connect(link_url(api_url), compression=None) as link:
        await link.send(
            json.dumps(
                {
                    "type": "pair",
                    "id": "pair",
                    "body": {
                        "role": "compute",
                        "pairing_code": code,
                        "claim_key": claim_key,
                        "hello": {
                            "installation_id": installation_id,
                            "host_release": "lemma-cli",
                            "protocol_version": PROTOCOL_VERSION,
                        },
                    },
                }
            )
        )
        try:
            raw = await asyncio.wait_for(link.recv(), timeout)
        except websockets.ConnectionClosed as closed:
            raise SetupError(
                f"Lemma closed the pairing ({closed.rcvd.reason if closed.rcvd else 'no reason'})."
            ) from None
    frame = json.loads(raw)
    if frame.get("type") != "paired":
        message = (frame.get("body") or {}).get("message") or "the pairing was refused"
        raise SetupError(f"Not paired: {message}.")
    body = frame["body"]
    return Paired(
        connection_id=body["connection_id"],
        user_id=body["user_id"],
        name=body["name"],
        machine_secret=body["machine_secret"],
    )


def pair(
    api_url: str,
    *,
    display_name: str,
    mode: str,
    installation_id: str,
    pod: str | None,
    announce,
    open_browser: bool = True,
    timeout: float = 600,
) -> Paired:
    """Ask for a code, show it, and wait for the approval."""
    claim_key = secrets.token_urlsafe(32)
    issued = request_code(
        api_url, claim_key=claim_key, display_name=display_name, mode=mode
    )
    url = issued.get("approve_url")
    if url and pod:
        url = f"{url}&pod={pod}"
    announce(issued["code"], url)
    if url and open_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass
    return asyncio.run(
        claim(
            api_url,
            code=issued["code"],
            claim_key=claim_key,
            installation_id=installation_id,
            timeout=timeout,
        )
    )


def write_private(path: Path, text: str) -> None:
    """A file only its owner can read, never wider even for an instant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


# ---------------------------------------------------------------- the runtime


def runtime_description(api_url: str, secret: str) -> dict[str, object]:
    response = httpx.get(
        f"{api_url.rstrip('/')}/agent-host/compute/runtime",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=60,
    )
    if response.status_code == 503:
        raise SetupError("This Lemma server has no runtime to give a machine yet.")
    response.raise_for_status()
    return response.json()


def _download(api_url: str, secret: str, path: str) -> bytes:
    response = httpx.get(
        f"{api_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=300,
    )
    response.raise_for_status()
    return response.content


def _run(command: list[str], *, what: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        raise SetupError(f"{what} failed:\n{detail}")


def host_environment(root: Path) -> Path:
    return state_directory(root) / "env"


def environment_python(environment: Path) -> Path:
    if sys.platform.startswith("win"):
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def provision_host(api_url: str, secret: str, root: Path, *, progress) -> Path:
    """Build the uv environment and install the server's runtime into it.

    The third-party closure is the image's own lockfile, hashes and all, so the
    machine imports what a sandbox imports; the first-party code is the
    server's overlay, installed by the server's own installer.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise SetupError(
            "Host mode needs uv: https://docs.astral.sh/uv/getting-started/installation/"
        )
    description = runtime_description(api_url, secret)
    requirements = description.get("requirements")
    if not isinstance(requirements, str) or not requirements.strip():
        raise SetupError(
            "This Lemma server is too old to set up host mode; use --image."
        )
    state = state_directory(root)
    environment = host_environment(root)
    python = environment_python(environment)
    if not python.exists():
        progress(f"Creating a Python {PYTHON_VERSION} environment in {environment}")
        _run(
            [uv, "venv", "--python", PYTHON_VERSION, "--seed", str(environment)],
            what="Creating the Python environment",
        )
    lock = state / "requirements.txt"
    lock.write_text(requirements, encoding="utf-8")
    progress("Installing the runtime's locked dependencies")
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "-r",
            str(lock),
        ],
        what="Installing the runtime's dependencies",
    )
    progress(f"Installing Lemma's runtime {str(description['version'])[:19]}…")
    archive = state / "runtime-bootstrap.zip"
    archive.write_bytes(
        _download(api_url, secret, "/agent-host/compute/runtime/archive")
    )
    installer = state / "runtime-install.py"
    installer.write_bytes(
        _download(api_url, secret, "/agent-host/compute/runtime/installer")
    )
    _run(
        [
            str(python),
            str(installer),
            "install",
            "--root",
            str(state / "runtime"),
            "--archive",
            str(archive),
            "--version",
            str(description["version"]),
            "--archive-sha256",
            str(description["archive_sha256"]),
            "--requires",
            ",".join(str(name) for name in description.get("requires") or []),
        ],
        what="Installing Lemma's runtime",
    )
    archive.unlink(missing_ok=True)
    return python


def machine_config(
    *,
    api_url: str,
    paired: Paired,
    mode: str,
    installation_id: str,
    root: Path,
    python: str,
    secret_file: str,
    home: str,
    overlay_root: str,
    runtime_port: int,
    relay_port: int | None = None,
    python_path: list[str] | None = None,
) -> dict[str, object]:
    """The agent's ``machine.json`` (``sandbox_runtime.machine_link.config``)."""
    return {
        "api_url": api_url,
        "connection_id": paired.connection_id,
        "name": paired.name,
        "mode": mode,
        "home": home,
        "overlay_root": overlay_root,
        "python": python,
        "installation_id": installation_id,
        "secret_file": secret_file,
        "runtime_port": runtime_port,
        "relay_port": relay_port,
        "python_path": python_path or [],
    }


def installation_id(root: Path) -> str:
    """Stable per ``--root``: the same machine joining again is one installation."""
    path = state_directory(root) / "installation-id"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = f"machine-{uuid.uuid4()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def agent_command(python: str, config: Path) -> list[str]:
    return [python, "-m", AGENT_MODULE, "--config", str(config)]


# ----------------------------------------------------------------- image mode


def container_name(name: str) -> str:
    return f"lemma-machine-{name}"


def reachable_from_docker(api_url: str) -> str:
    """The API as a container on this machine reaches it: loopback is the
    container's own, so it becomes the host's gateway name."""
    parts = urlsplit(api_url)
    if parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        netloc = parts.netloc.replace(parts.hostname, "host.docker.internal", 1)
        return urlunsplit(parts._replace(netloc=netloc))
    return api_url


def image_run_command(*, image: str, name: str, mount_docker: bool) -> list[str]:
    command = [
        "docker",
        "create",
        "--name",
        container_name(name),
        "--restart",
        "unless-stopped",
        "--add-host",
        "host.docker.internal:host-gateway",
        "--label",
        "managed-by=lemma-machine",
        "-v",
        f"{container_name(name)}-home:{IMAGE_HOME}",
    ]
    if mount_docker:
        command += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
    return [
        *command,
        image,
        IMAGE_PYTHON,
        "-m",
        AGENT_MODULE,
        "--config",
        f"{IMAGE_STATE}/{CONFIG_NAME}",
    ]


def state_archive(files: dict[str, bytes]) -> bytes:
    """A tar of the agent's files, owned by the image's user and 0600, for
    ``docker cp -a``: a bind mount would keep this machine's uid, which the
    image's user cannot read."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            info.mode = 0o600
            info.uid = IMAGE_UID
            info.gid = IMAGE_UID
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def start_image(
    *,
    image: str,
    name: str,
    config: dict[str, object],
    secret: str,
    mount_docker: bool,
    progress,
) -> None:
    if shutil.which("docker") is None:
        raise SetupError("Image mode needs Docker.")
    container = container_name(name)
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
    present = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    )
    if present.returncode != 0:
        progress(f"Pulling {image}")
        _run(["docker", "pull", image], what=f"Pulling {image}")
    _run(
        image_run_command(image=image, name=name, mount_docker=mount_docker),
        what="Creating the machine's container",
    )
    archive = state_archive(
        {
            CONFIG_NAME: json.dumps(config, indent=2).encode(),
            SECRET_NAME: (secret + "\n").encode(),
        }
    )
    copied = subprocess.run(
        ["docker", "cp", "-a", "-", f"{container}:{IMAGE_STATE}"],
        input=archive,
        capture_output=True,
        check=False,
    )
    if copied.returncode != 0:
        raise SetupError(f"Copying the machine's credential failed: {copied.stderr!r}")
    _run(["docker", "start", container], what="Starting the machine's container")


# ------------------------------------------------------------------ services


def service_name(name: str) -> str:
    return f"lemma-machine-{name}"


def systemd_unit(name: str, command: list[str], root: Path) -> str:
    joined = " ".join(_quote(part) for part in command)
    return (
        "[Unit]\n"
        f"Description=Lemma connected machine {name}\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={joined}\n"
        f"WorkingDirectory={root}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist(name: str, command: list[str], root: Path) -> str:
    arguments = "".join(f"    <string>{_xml(part)}</string>\n" for part in command)
    log = state_directory(root) / "agent.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"  <key>Label</key><string>work.lemma.machine.{_xml(name)}</string>\n"
        f"  <key>ProgramArguments</key>\n  <array>\n{arguments}  </array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        # Restart after a crash, not after the agent stops on purpose (a
        # revoked machine exits 0).
        "  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n"
        f"  <key>WorkingDirectory</key><string>{_xml(str(root))}</string>\n"
        f"  <key>StandardOutPath</key><string>{_xml(str(log))}</string>\n"
        f"  <key>StandardErrorPath</key><string>{_xml(str(log))}</string>\n"
        "</dict>\n</plist>\n"
    )


def _quote(part: str) -> str:
    return f'"{part}"' if any(c.isspace() for c in part) else part


def _xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def service_path(name: str) -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"work.lemma.machine.{name}.plist"
        )
    return (
        Path.home() / ".config" / "systemd" / "user" / f"{service_name(name)}.service"
    )


def install_service(name: str, command: list[str], root: Path) -> Path:
    path = service_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path.write_text(launchd_plist(name, command, root), encoding="utf-8")
        subprocess.run(
            ["launchctl", "unload", str(path)], capture_output=True, check=False
        )
        _run(["launchctl", "load", "-w", str(path)], what="Loading the launchd agent")
    elif sys.platform.startswith("linux"):
        path.write_text(systemd_unit(name, command, root), encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"], what="Reloading systemd")
        _run(
            ["systemctl", "--user", "enable", "--now", f"{service_name(name)}.service"],
            what="Starting the service",
        )
    else:
        raise SetupError("--install-service supports systemd and launchd only.")
    return path


def remove_service(name: str) -> None:
    path = service_path(name)
    if not path.exists():
        return
    if sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "unload", str(path)], capture_output=True, check=False
        )
    else:
        subprocess.run(
            [
                "systemctl",
                "--user",
                "disable",
                "--now",
                f"{service_name(name)}.service",
            ],
            capture_output=True,
            check=False,
        )
    path.unlink(missing_ok=True)


# ------------------------------------------------------------------ disconnect


async def revoke(api_url: str, secret: str, *, installation: str, home: str) -> None:
    """Remove this machine from Lemma over its own link."""
    import websockets

    async with websockets.connect(
        link_url(api_url),
        additional_headers={"Authorization": f"Bearer {secret}"},
        compression=None,
    ) as link:
        await link.send(
            json.dumps(
                {
                    "type": "hello",
                    "id": "h",
                    "body": {
                        "role": "compute",
                        "hello": {
                            "installation_id": installation,
                            "host_release": "lemma-cli",
                            "protocol_version": PROTOCOL_VERSION,
                        },
                        "machine": {
                            "mode": "host",
                            "platform": platform_name(),
                            "home": home,
                            "runtime_port": 1,
                        },
                    },
                }
            )
        )
        await link.recv()
        await link.send(json.dumps({"type": "revoke", "id": "r", "body": {}}))
        while True:
            message = await link.recv()
            if (
                isinstance(message, str)
                and json.loads(message).get("type") == "revoked"
            ):
                return


def display_name() -> str:
    return platform.node() or "machine"


def os_user() -> str:
    try:
        return getpass.getuser()
    except KeyError, OSError:
        return "this user"


# ------------------------------------------------------------- the two verbs


def remove_container(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", container_name(name)], capture_output=True, check=False
    )


@dataclass(frozen=True)
class Steps:
    """Everything ``connect`` and ``disconnect`` do to the world, as one
    collaborator: the command passes the real ones, a test passes its own."""

    pair: Callable[..., Paired] = pair
    runtime_description: Callable[[str, str], dict[str, object]] = runtime_description
    provision_host: Callable[..., Path] = provision_host
    start_image: Callable[..., None] = start_image
    install_service: Callable[[str, list[str], Path], Path] = install_service
    revoke: Callable[..., Awaitable[None]] = revoke
    remove_service: Callable[[str], None] = remove_service
    remove_container: Callable[[str], None] = remove_container
    free_port: Callable[[], int] = free_port


@dataclass(frozen=True)
class ConnectOptions:
    api_url: str
    root: Path
    image: bool = False
    image_ref: str | None = None
    mount_docker: bool = False
    pod: str | None = None
    install_service: bool = False
    open_browser: bool = True


@dataclass(frozen=True)
class Connected:
    name: str
    mode: str
    #: The agent's command line, to run in the foreground; None when a service
    #: or a container runs it instead.
    command: list[str] | None = None
    service: Path | None = None
    container: str | None = None


def connect(
    options: ConnectOptions, *, announce, progress, steps: Steps = Steps()
) -> Connected:
    """Pair, then set the machine up to serve: everything after consent."""
    root = options.root
    installation = installation_id(root)
    paired = steps.pair(
        options.api_url,
        display_name=display_name(),
        mode="image" if options.image else "host",
        installation_id=installation,
        pod=options.pod,
        announce=announce,
        open_browser=options.open_browser,
    )
    progress(f"Approved as {paired.name}.")
    state = state_directory(root)
    secret_file = state / SECRET_NAME
    write_private(secret_file, paired.machine_secret + "\n")
    config_file = state / CONFIG_NAME
    if options.image:
        description = steps.runtime_description(options.api_url, paired.machine_secret)
        reference = options.image_ref or description.get("workspace_image")
        if not isinstance(reference, str) or not reference:
            raise SetupError("This server names no workspace image; pass --image-ref.")
        config = machine_config(
            api_url=reachable_from_docker(options.api_url),
            paired=paired,
            mode="image",
            installation_id=installation,
            root=root,
            python=IMAGE_PYTHON,
            secret_file=f"{IMAGE_STATE}/{SECRET_NAME}",
            home=IMAGE_HOME,
            overlay_root=IMAGE_OVERLAY,
            runtime_port=IMAGE_RUNTIME_PORT,
            relay_port=IMAGE_RELAY_PORT,
            python_path=["/app"],
        )
        write_private(config_file, json.dumps(config, indent=2) + "\n")
        steps.start_image(
            image=reference,
            name=paired.name,
            config=config,
            secret=paired.machine_secret,
            mount_docker=options.mount_docker,
            progress=progress,
        )
        return Connected(
            name=paired.name, mode="image", container=container_name(paired.name)
        )
    python = steps.provision_host(
        options.api_url, paired.machine_secret, root, progress=progress
    )
    config = machine_config(
        api_url=options.api_url,
        paired=paired,
        mode="host",
        installation_id=installation,
        root=root,
        python=str(python),
        secret_file=str(secret_file),
        home=str(root),
        overlay_root=str(state / "runtime"),
        runtime_port=steps.free_port(),
    )
    write_private(config_file, json.dumps(config, indent=2) + "\n")
    command = agent_command(str(python), config_file)
    if options.install_service:
        unit = steps.install_service(paired.name, command, root)
        return Connected(name=paired.name, mode="host", service=unit)
    return Connected(name=paired.name, mode="host", command=command)


def disconnect(api_url: str, root: Path, *, warn, steps: Steps = Steps()) -> str:
    """Revoke this machine, stop serving it, forget its secret; its name."""
    state = state_directory(root)
    config_file = state / CONFIG_NAME
    if not config_file.exists():
        raise SetupError(f"No machine is connected from {root}.")
    config = json.loads(config_file.read_text(encoding="utf-8"))
    name = str(config["name"])
    secret = (state / SECRET_NAME).read_text(encoding="utf-8").strip()
    try:
        asyncio.run(
            steps.revoke(
                api_url,
                secret,
                installation=str(config["installation_id"]),
                home=str(config["home"]),
            )
        )
    except (OSError, ValueError) as error:
        warn(f"Could not tell Lemma ({error}); remove {name} there too.")
    steps.remove_service(name)
    if config.get("mode") == "image":
        steps.remove_container(name)
    (state / SECRET_NAME).unlink(missing_ok=True)
    config_file.unlink(missing_ok=True)
    return name
