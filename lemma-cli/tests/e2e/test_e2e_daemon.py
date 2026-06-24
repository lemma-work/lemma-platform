"""E2E tests for the daemon lifecycle: foreground/background start, status, stop.

These tests spawn the real CLI daemon as a subprocess (not CliRunner —
``daemon start`` blocks forever) and connect it to the real backend websocket
endpoint. They verify the three bugs fixed in this commit:

1. ``--background`` actually daemonizes and stays alive (was dying instantly
   because ``cli_app/main.py`` had no ``__main__`` guard).
2. ``daemon status`` reports ``running: true`` for a live daemon (foreground
   start never wrote a pid file).
3. ``daemon status`` surfaces the connected ``base_url`` so a silent
   environment mismatch is visible.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from lemma_cli.cli_core.app import app
from lemma_cli.daemon import config as daemon_config
from typer.testing import CliRunner

_runner = CliRunner()

pytestmark = pytest.mark.e2e


@contextmanager
def _patched_daemon_dir(daemon_dir: Path):
    """Patch the module-level DAEMON_DIR/paths for in-process CliRunner calls.

    ``DAEMON_DIR`` is computed at import time from ``LEMMA_DAEMON_DIR``, so
    setting the env var after import has no effect on CliRunner. We patch the
    module-level constants directly.
    """
    old = {
        attr: getattr(daemon_config, attr)
        for attr in ("DAEMON_DIR", "DAEMON_CONFIG_PATH", "DAEMON_PID_PATH", "DAEMON_LOG_PATH")
    }
    daemon_config.DAEMON_DIR = daemon_dir
    daemon_config.DAEMON_CONFIG_PATH = daemon_dir / "config.json"
    daemon_config.DAEMON_PID_PATH = daemon_dir / "daemon.pid"
    daemon_config.DAEMON_LOG_PATH = daemon_dir / "logs" / "daemon.log"
    try:
        yield
    finally:
        for attr, value in old.items():
            setattr(daemon_config, attr, value)


def _daemon_config(tmp_path: Path, base_url: str, token: str) -> Path:
    """Write a throwaway CLI config pointing at the test backend."""
    config_path = tmp_path / "daemon-config.json"
    config_path.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {
                    "default": {
                        "base_url": base_url,
                        "token": token,
                        "defaults": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _start_daemon_subprocess(
    *,
    config_path: Path,
    daemon_dir: Path,
    background: bool,
) -> subprocess.Popen[str]:
    """Spawn ``lemma daemon start`` as a real subprocess.

    The daemon module is imported via PYTHONPATH so we use the in-tree CLI
    without installing it.
    """
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    python_paths = [str(repo_root / "lemma-cli"), str(repo_root / "lemma-python")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["LEMMA_DAEMON_DIR"] = str(daemon_dir)

    command = [
        sys.executable,
        "-m",
        "lemma_cli.cli_app.main",
        "--config-file",
        str(config_path),
        "daemon",
        "start",
    ]
    if background:
        command.append("--background")

    return subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )


def _wait_for_daemon_online(
    *,
    base_url: str,
    token: str,
    timeout: float = 30,
) -> dict:
    """Poll the backend's /agent-runtime/harnesses until a daemon is ONLINE."""
    deadline = time.monotonic() + timeout
    last_payload: dict | None = None
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ) as client:
        while time.monotonic() < deadline:
            resp = client.get("/agent-runtime/harnesses")
            if resp.status_code == 200:
                last_payload = resp.json()
                items = last_payload.get("items", [])
                if items and any(
                    item.get("daemon_status") == "ONLINE" for item in items
                ):
                    return last_payload
            time.sleep(0.5)
    raise AssertionError(
        f"Daemon did not come online within {timeout}s. Last payload: {last_payload}"
    )


def _wait_for_daemon_offline(
    *,
    base_url: str,
    token: str,
    timeout: float = 15,
) -> None:
    """Poll until no daemon is ONLINE."""
    deadline = time.monotonic() + timeout
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ) as client:
        while time.monotonic() < deadline:
            resp = client.get("/agent-runtime/harnesses")
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if not items or not any(
                    item.get("daemon_status") == "ONLINE" for item in items
                ):
                    return
            time.sleep(0.5)
    raise AssertionError(f"Daemon stayed online after stop within {timeout}s.")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=10)


def test_daemon_foreground_connects(backend_server, test_user, tmp_path):
    """A foreground daemon connects to the backend and shows ONLINE."""
    base_url = backend_server["base_url"]
    config_path = _daemon_config(tmp_path, base_url, test_user["token"])
    daemon_dir = tmp_path / "daemon"

    process = _start_daemon_subprocess(
        config_path=config_path,
        daemon_dir=daemon_dir,
        background=False,
    )
    try:
        _wait_for_daemon_online(base_url=base_url, token=test_user["token"])
    finally:
        _stop_process(process)
        _wait_for_daemon_offline(base_url=base_url, token=test_user["token"])


def test_daemon_background_stays_alive(backend_server, test_user, tmp_path):
    """Bug 1 regression: --background daemonizes and the child stays alive.

    Before the fix, the child exited instantly because ``cli_app/main.py``
    had no ``__main__`` guard — ``python -m lemma_cli.cli_app.main`` imported
    the module and exited without ever calling ``main()``.
    """
    base_url = backend_server["base_url"]
    config_path = _daemon_config(tmp_path, base_url, test_user["token"])
    daemon_dir = tmp_path / "daemon"

    process = _start_daemon_subprocess(
        config_path=config_path,
        daemon_dir=daemon_dir,
        background=True,
    )
    # The parent exits immediately after spawning the background child.
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
        pytest.fail("--background parent process did not exit")

    # The parent should have written the pid file before exiting.
    pid_file = daemon_dir / "daemon.pid"
    log_file = daemon_dir / "logs" / "daemon.log"
    if not pid_file.exists():
        log_contents = log_file.read_text() if log_file.exists() else "(no log file)"
        pytest.fail(
            f"daemon.pid was not written after parent exit.\n"
            f"daemon_dir contents: {list(daemon_dir.glob('**/*'))}\n"
            f"log:\n{log_contents}"
        )

    status = json.loads(pid_file.read_text())
    pid = status["pid"]
    assert status["base_url"] == base_url

    # The child daemon should now be alive and connecting to the backend.
    _wait_for_daemon_online(base_url=base_url, token=test_user["token"])

    time.sleep(3)
    # Bug 1: the background child must still be alive.
    assert _pid_is_alive(pid), (
        "Background daemon died within 3s — the __main__ guard fix is broken."
    )

    # Clean up: kill the background daemon.
    try:
        os.kill(pid, 15)  # SIGTERM
    except OSError:
        pass
    _wait_for_daemon_offline(base_url=base_url, token=test_user["token"])


def test_daemon_status_reports_running_and_base_url(backend_server, test_user, tmp_path):
    """Bug 2+3: ``daemon status`` reports running:true and the connected base_url."""
    base_url = backend_server["base_url"]
    config_path = _daemon_config(tmp_path, base_url, test_user["token"])
    daemon_dir = tmp_path / "daemon"

    process = _start_daemon_subprocess(
        config_path=config_path,
        daemon_dir=daemon_dir,
        background=True,
    )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)

    _wait_for_daemon_online(base_url=base_url, token=test_user["token"])

    with _patched_daemon_dir(daemon_dir):
        result = _runner.invoke(
            app,
            ["--config-file", str(config_path), "--json", "daemon", "status"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["running"] is True, (
        f"daemon status reported running={payload['running']} — Bug 2 not fixed."
    )
    assert payload["base_url"] == base_url, (
        f"daemon status base_url={payload['base_url']!r} — Bug 3 not fixed."
    )

    # Clean up.
    pid = json.loads((daemon_dir / "daemon.pid").read_text())["pid"]
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    _wait_for_daemon_offline(base_url=base_url, token=test_user["token"])


def test_daemon_stop_kills_background_daemon(backend_server, test_user, tmp_path):
    """``daemon stop`` terminates a background daemon."""
    base_url = backend_server["base_url"]
    config_path = _daemon_config(tmp_path, base_url, test_user["token"])
    daemon_dir = tmp_path / "daemon"

    process = _start_daemon_subprocess(
        config_path=config_path,
        daemon_dir=daemon_dir,
        background=True,
    )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)

    _wait_for_daemon_online(base_url=base_url, token=test_user["token"])

    with _patched_daemon_dir(daemon_dir):
        result = _runner.invoke(
            app,
            ["--config-file", str(config_path), "daemon", "stop"],
        )

    assert result.exit_code == 0, result.output
    _wait_for_daemon_offline(base_url=base_url, token=test_user["token"])


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
