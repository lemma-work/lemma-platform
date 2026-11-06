"""What a connected machine's agent is configured with, written by the CLI.

``lemma machine connect`` writes ``<root>/.lemma/machine.json`` and the
machine's secret beside it in a file only its owner can read, then starts the
agent with ``--config`` naming the first. Image mode mounts the same two files
into the container.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: The protocol this agent speaks; ``compute`` arrived in 4.
PROTOCOL_VERSION = 4

#: Where host mode keeps its own state under ``--root``.
STATE_DIRECTORY = ".lemma"
CONFIG_NAME = "machine.json"
SECRET_NAME = "machine-secret"


@dataclass(frozen=True, slots=True)
class MachineConfig:
    #: The API, e.g. ``https://api.lemma.work``.
    api_url: str
    connection_id: str
    name: str
    #: ``host`` or ``image``.
    mode: str
    #: The runtime's home: ``--root`` in host mode, ``/home/user`` in image mode.
    home: str
    #: Where the runtime overlay is installed.
    overlay_root: str
    #: The interpreter that runs the runtime (the uv environment, or the image's).
    python: str
    #: A stable id for this installation, sent on every ``hello``.
    installation_id: str
    secret_file: str
    runtime_port: int = 18080
    relay_port: int | None = None
    #: Extra ``PYTHONPATH`` for the runtime: the image's ``/app`` floor.
    python_path: list[str] = field(default_factory=list)

    @property
    def link_url(self) -> str:
        scheme = "wss" if self.api_url.startswith("https") else "ws"
        return (
            f"{scheme}://{self.api_url.split('://', 1)[-1].rstrip('/')}/agent-host/link"
        )

    @property
    def state_directory(self) -> Path:
        return Path(self.secret_file).parent

    def secret(self) -> str:
        return Path(self.secret_file).read_text(encoding="utf-8").strip()

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> MachineConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**raw)


def write_secret(path: Path, secret: str) -> None:
    """The secret, readable by its owner only, written so it never exists with
    wider permissions even for an instant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(secret + "\n")


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"
