"""Cross-platform client/bootstrap for the managed Lemma Local daemon.

The Rust binary remains the protocol implementation on every platform.  This
module discovers the signed binary and installed runtime, invokes its versioned
client command, and starts it detached when a lifecycle command is issued while
Desktop is closed.  It deliberately does not duplicate Unix-socket/named-pipe
framing in Python.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LocaldError(RuntimeError):
    """Managed local control is installed but could not complete a request."""


def app_support_dir() -> Path:
    override = os.environ.get("LEMMA_DESKTOP_APP_SUPPORT_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Lemma"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Lemma"
    state = os.environ.get("XDG_STATE_HOME")
    return (Path(state) if state else Path.home() / ".local/state") / "lemma"


def locald_root() -> Path:
    override = os.environ.get("LEMMA_LOCALD_ROOT")
    return Path(override).expanduser() if override else app_support_dir() / "locald"


def _binary_candidates() -> list[Path]:
    suffix = ".exe" if os.name == "nt" else ""
    name = f"lemma-locald{suffix}"
    candidates: list[Path] = []
    override = os.environ.get("LEMMA_LOCALD_BIN") or os.environ.get("LEMMA_DESKTOP_LOCALD_BIN")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path(sys.executable).resolve().parent / name)
    discovered = shutil.which("lemma-locald")
    if discovered:
        candidates.append(Path(discovered))
    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Lemma.app/Contents/MacOS/lemma-locald"),
                Path.home() / "Applications/Lemma.app/Contents/MacOS/lemma-locald",
            ]
        )
    elif os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        candidates.extend(
            [
                local / "Lemma/lemma-locald.exe",
                local / "Programs/Lemma/lemma-locald.exe",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Lemma/lemma-locald.exe",
            ]
        )
    return list(dict.fromkeys(path.resolve() for path in candidates))


def _installed_runtime() -> tuple[Path | None, Path | None]:
    config_path = app_support_dir() / "desktop-config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        release_root = Path(config["installedRuntime"]["root"])
    except OSError, KeyError, TypeError, ValueError, json.JSONDecodeError:
        return None, None
    host = release_root / "local-runtime"
    managed = release_root / "managed-runtime"
    return (
        host if (host / "release.json").is_file() else None,
        managed if _managed_marker(managed).is_file() else None,
    )


def _managed_marker(root: Path) -> Path:
    target = "macos-aarch64" if sys.platform == "darwin" else "windows-x86_64"
    return root / target / "runtime.json"


def _runtime_environment(binary: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["LEMMA_LOCALD_ROOT"] = str(locald_root())
    bin_dir = binary.parent
    if sys.platform == "darwin":
        resources = (bin_dir / "../Resources").resolve()
        bundled_host = resources / "local-runtime"
        bundled_managed = resources / "managed-runtime"
    else:
        bundled_host = bin_dir / "local-runtime"
        bundled_managed = bin_dir / "managed-runtime"
    installed_host, installed_managed = _installed_runtime()
    host = bundled_host if (bundled_host / "release.json").is_file() else installed_host
    managed = bundled_managed if _managed_marker(bundled_managed).is_file() else installed_managed
    if host:
        environment["LEMMA_LOCALD_HOST_PACK_ROOT"] = str(host)
    if managed:
        bridge = bin_dir / ("lemma-runtime.exe" if os.name == "nt" else "lemma-runtime")
        environment["LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT"] = str(managed)
        environment["LEMMA_LOCALD_RUNTIME_BRIDGE_BIN"] = str(bridge)
        if sys.platform == "darwin":
            environment["LEMMA_LOCALD_VZ_BIN"] = str(bin_dir / "lemma-vz")
    environment["LEMMA_DESKTOP"] = "1"
    return environment


@dataclass(frozen=True)
class LocaldClient:
    binary: Path
    root: Path
    environment: dict[str, str]

    @classmethod
    def discover(cls) -> LocaldClient | None:
        binary = next((path for path in _binary_candidates() if path.is_file()), None)
        if binary is None:
            return None
        environment = _runtime_environment(binary)
        root = locald_root()
        installed = "LEMMA_LOCALD_HOST_PACK_ROOT" in environment and (
            "LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT" in environment
        )
        if not installed and not (root / "control.token").is_file():
            return None
        return cls(binary=binary, root=root, environment=environment)

    def _invoke(self, *arguments: str, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [str(self.binary), *arguments],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self.environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LocaldError(f"could not invoke managed local control: {error}") from error
        if len(result.stdout) > 8 * 1024 * 1024 or len(result.stderr) > 1024 * 1024:
            raise LocaldError("managed local control output exceeded its safety limit")
        return result

    def _responding(self) -> bool:
        result = self._invoke("ping", timeout=3)
        return result.returncode == 0 and any(
            _event(line).get("event") == "pong" for line in result.stdout.splitlines()
        )

    def ensure_running(self) -> None:
        if self._responding():
            return
        if "LEMMA_LOCALD_HOST_PACK_ROOT" not in self.environment:
            raise LocaldError(
                "Lemma Desktop has not installed a managed runtime; open Lemma once to finish setup"
            )
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": self.environment,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen([str(self.binary), "serve"], **kwargs)
        except OSError as error:
            raise LocaldError(f"could not start managed local control: {error}") from error
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self._responding():
                return
            time.sleep(0.1)
        raise LocaldError("managed local control did not become ready")

    def request(self, command: str, **payload: Any) -> dict[str, Any]:
        self.ensure_running()
        request_id = f"lemma-stack-{os.getpid()}-{uuid.uuid4().hex}"
        request = {"v": 1, "cmd": command, "id": request_id, **payload}
        timeout = 600 if command in {"start", "restart", "runtime.prepare"} else 120
        result = self._invoke("send", json.dumps(request, separators=(",", ":")), timeout=timeout)
        matching = [
            event
            for event in (_event(line) for line in result.stdout.splitlines())
            if event.get("id") == request_id
        ]
        error = next((event for event in matching if event.get("event") == "error"), None)
        if error:
            raise LocaldError(str(error.get("message") or error.get("code") or "request failed"))
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            raise LocaldError(detail[-1] if detail else "managed local control request failed")
        final_kinds = {
            "status": "status",
            "ping": "pong",
            "control.snapshot": "control.snapshot",
            "config.apply": "config.applied",
            "runtime.prepare": "runtime.prepared",
        }
        expected = final_kinds.get(command, "done")
        final = next(
            (event for event in reversed(matching) if event.get("event") == expected), None
        )
        if final is None:
            raise LocaldError(f"managed local control omitted the {expected} response")
        if expected == "done" and final.get("ok") is not True:
            raise LocaldError(f"managed local {command} failed")
        return final


def _event(line: str) -> dict[str, Any]:
    try:
        parsed = json.loads(line)
    except TypeError, json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
