"""The workspace runtime a connected machine serves, and the overlay it runs.

The agent starts the same ``sandbox_runtime.workspace.server`` a sandbox
image starts, on the machine's loopback only, and restarts it when Lemma
pushes a new overlay. It is the machine's child: it goes when the agent goes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

import httpx

from sandbox_runtime.machine_link.config import MachineConfig
from sandbox_runtime.paths import MACHINE_HOME_ENV, RUNTIME_OVERLAY_ROOT_ENV

#: How long a starting runtime has to answer its health check.
START_TIMEOUT_SECONDS = 60.0
#: The overlay a bundle installs into names this interpreter in its scripts;
#: host mode has none at that path, so they are pointed at its own.
_IMAGE_PYTHON_SHEBANG = b"#!/opt/lemma-python/bin/python"
#: The most of a runtime overlay a push may carry.
MAX_BUNDLE_BYTES = 256 * 1024 * 1024


class InstallFailed(RuntimeError):
    pass


class RuntimeProcess:
    def __init__(self, config: MachineConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._config.runtime_port}"

    def installed_version(self) -> str | None:
        stamp = Path(self._config.overlay_root) / "current" / ".stamp"
        try:
            return stamp.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def healthy(self) -> bool:
        if self._token is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{self.url}/health",
                    headers={"X-Lemma-Runtime-Token": self._token},
                )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure(self, token: str) -> None:
        """Running, healthy, and holding ``token``."""
        async with self._lock:
            if self.running() and token == self._token and await self.healthy():
                return
            await self._stop()
            await self._start(token)

    async def restart(self) -> None:
        async with self._lock:
            token = self._token
            await self._stop()
            if token is not None:
                await self._start(token)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    def _environment(self, token_file: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["LEMMA_RUNTIME_TOKEN_FILE"] = str(token_file)
        env[RUNTIME_OVERLAY_ROOT_ENV] = self._config.overlay_root
        if self._config.mode == "host":
            env[MACHINE_HOME_ENV] = self._config.home
        if self._config.python_path:
            env["PYTHONPATH"] = os.pathsep.join(
                [*self._config.python_path, env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
        # The agent's own credential never reaches anything the runtime runs.
        env.pop("LEMMA_MACHINE_SECRET", None)
        return env

    async def _start(self, token: str) -> None:
        if self._config.mode == "host":
            # The overlay the CLI installed names the image's interpreter too.
            self._point_scripts_at_this_python()
        state = self._config.state_directory
        state.mkdir(parents=True, exist_ok=True)
        Path(self._config.home).mkdir(parents=True, exist_ok=True)
        (Path(self._config.home) / "lemma").mkdir(exist_ok=True)
        # The runtime reads this once and unlinks it, as it does in an image.
        descriptor, raw = tempfile.mkstemp(prefix="runtime-token-", dir=state)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
        # Handed to the child, which keeps writing to it after this returns.
        log = await asyncio.to_thread(open, state / "runtime.log", "ab")
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._config.python,
                "-m",
                "uvicorn",
                "sandbox_runtime.workspace.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._config.runtime_port),
                "--no-access-log",
                env=self._environment(Path(raw)),
                cwd=self._config.home,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        finally:
            log.close()
        self._token = token
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT_SECONDS
        while loop.time() < deadline:
            if self._process.returncode is not None:
                raise RuntimeError(
                    f"the runtime exited with {self._process.returncode}; "
                    f"see {state / 'runtime.log'}"
                )
            if await self.healthy():
                return
            await asyncio.sleep(0.2)
        raise RuntimeError("the runtime did not become healthy in time")

    async def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    # ------------------------------------------------------------- installing

    async def install(
        self,
        *,
        archive: Path,
        version: str,
        archive_sha256: str,
        requires: list[str],
        installer: bytes,
    ) -> dict[str, object]:
        """Install one pushed overlay and restart the runtime on it."""
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if f"sha256:{digest}" != archive_sha256:
            raise InstallFailed("the pushed runtime does not match its digest")
        script = self._config.state_directory / "runtime-install.py"
        script.write_bytes(installer)
        process = await asyncio.create_subprocess_exec(
            self._config.python,
            str(script),
            "install",
            "--root",
            self._config.overlay_root,
            "--archive",
            str(archive),
            "--version",
            version,
            "--archive-sha256",
            archive_sha256,
            "--requires",
            ",".join(requires),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise InstallFailed(
                stderr.decode(errors="replace").strip()[-500:] or "install failed"
            )
        outcome = json.loads(stdout.decode().strip().splitlines()[-1] or "{}")
        if self._config.mode == "host":
            self._point_scripts_at_this_python()
        await self.restart()
        return outcome

    def _point_scripts_at_this_python(self) -> None:
        """The overlay's ``bin`` names the image's interpreter; here it is ours."""
        scripts = Path(self._config.overlay_root) / "current" / "bin"
        shebang = f"#!{self._config.python}".encode()
        for script in scripts.glob("*") if scripts.is_dir() else ():
            try:
                body = script.read_bytes()
            except OSError:
                continue
            if body.startswith(_IMAGE_PYTHON_SHEBANG):
                script.write_bytes(shebang + body[len(_IMAGE_PYTHON_SHEBANG) :])


def agent_command(config_path: Path) -> list[str]:
    """How the agent itself is started, for re-exec and for service units."""
    return [
        sys.executable,
        "-m",
        "sandbox_runtime.machine_link.agent",
        "--config",
        str(config_path),
    ]
