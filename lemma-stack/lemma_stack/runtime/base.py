"""Thin subprocess wrapper over the docker/podman CLI.

One codepath drives both runtimes: podman's CLI is argument-compatible with
docker for everything the stack needs, so the only divergence lives in
socket discovery and (on macOS) machine management.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from lemma_stack.output import AdminError


class Runtime:
    def __init__(self, cli: str) -> None:
        if cli not in {"docker", "podman"}:
            raise AdminError(f"unsupported container runtime: {cli}")
        if not shutil.which(cli):
            raise AdminError(f"{cli} CLI not found on PATH")
        self.cli = cli

    # --- low-level ---------------------------------------------------------

    def run(
        self,
        *args: str,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [self.cli, *args],
            check=False,
            capture_output=capture,
            text=True,
        )
        if check and proc.returncode != 0:
            detail = (proc.stderr or "").strip() if capture else f"exit {proc.returncode}"
            raise AdminError(f"{self.cli} {' '.join(args)} failed: {detail}")
        return proc

    def stream(self, *args: str) -> int:
        """Run attached to the terminal (logs -f, interactive shells)."""
        return subprocess.run([self.cli, *args], check=False).returncode

    # --- queries ------------------------------------------------------------

    def available(self) -> bool:
        return self.run("info", check=False).returncode == 0

    def inspect(self, name: str) -> dict[str, Any] | None:
        proc = self.run("inspect", "--type", "container", name, check=False)
        if proc.returncode != 0:
            return None
        parsed = json.loads(proc.stdout)
        return parsed[0] if isinstance(parsed, list) and parsed else None

    def container_running(self, name: str) -> bool:
        data = self.inspect(name)
        if not data:
            return False
        state = data.get("State") or {}
        return bool(state.get("Running"))

    def container_health(self, name: str) -> str | None:
        data = self.inspect(name)
        if not data:
            return None
        health = (data.get("State") or {}).get("Health") or {}
        return health.get("Status")

    def image_exists(self, ref: str) -> bool:
        return self.run("image", "inspect", ref, check=False).returncode == 0

    # --- mutations ----------------------------------------------------------

    def ensure_network(self, name: str) -> None:
        if self.run("network", "inspect", name, check=False).returncode != 0:
            self.run("network", "create", name)

    def ensure_volume(self, name: str) -> None:
        if self.run("volume", "inspect", name, check=False).returncode != 0:
            self.run("volume", "create", name)

    def pull(self, ref: str) -> None:
        self.run("pull", ref, capture=False)

    def remove_container(self, name: str) -> None:
        self.run("rm", "-f", name, check=False)

    def stop_container(self, name: str) -> None:
        self.run("stop", name, check=False)

    def start_container(self, name: str) -> None:
        self.run("start", name)

    # --- runtime socket (mounted into the agentbox manager) -----------------

    def socket_path(self) -> str:
        if self.cli == "docker":
            return "/var/run/docker.sock"
        proc = self.run("info", "--format", "{{.Host.RemoteSocket.Path}}", check=False)
        path = (proc.stdout or "").strip()
        if proc.returncode != 0 or not path:
            raise AdminError(
                "could not discover the podman API socket; is the podman machine/service running?"
            )
        return path.removeprefix("unix://")
