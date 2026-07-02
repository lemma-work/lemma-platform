from __future__ import annotations

import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DAEMON_DIR = Path(os.environ.get("LEMMA_DAEMON_DIR", "~/.lemma/daemon")).expanduser()
DAEMON_CONFIG_PATH = DAEMON_DIR / "config.json"
DAEMON_PID_PATH = DAEMON_DIR / "daemon.pid"
DAEMON_LOG_PATH = DAEMON_DIR / "logs" / "daemon.log"

# Admission-control cap on concurrent runs -- defined here (not alongside the
# other daemon tunables in runner.py) so ensure_config() can lazy-fill it into
# the persisted config without a circular import (runner.py already imports
# from this module). The persisted value is for discoverability only; the
# actual enforcement in runner.py always reads the env var fresh via this
# function, so an env var change takes effect on the next daemon start without
# needing to edit config.json.
MAX_CONCURRENT_RUNS_ENV = "LEMMA_DAEMON_MAX_CONCURRENT_RUNS"
DEFAULT_MAX_CONCURRENT_RUNS = 4


def max_concurrent_runs() -> int:
    raw = os.getenv(MAX_CONCURRENT_RUNS_ENV, str(DEFAULT_MAX_CONCURRENT_RUNS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_RUNS


def ensure_config() -> dict:
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    if not config.get("device_key"):
        config["device_key"] = str(uuid4())
    if not config.get("display_name"):
        config["display_name"] = socket.gethostname()
    if not config.get("max_concurrent_runs"):
        config["max_concurrent_runs"] = max_concurrent_runs()
    save_config(config)
    return config


def load_config() -> dict:
    if not DAEMON_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(DAEMON_CONFIG_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict) -> None:
    DAEMON_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def device_info() -> dict[str, str]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system().lower(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def daemon_ws_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.startswith("https://"):
        root = "wss://" + root.removeprefix("https://")
    elif root.startswith("http://"):
        root = "ws://" + root.removeprefix("http://")
    else:
        # No recognized scheme (e.g. a bare host) — assume TLS, like a browser.
        root = "wss://" + root.removeprefix("//")
    return f"{root}/me/agent-runtime/daemon/ws"


def read_pid() -> int | None:
    """Return the pid of the running daemon, or None.

    The pid file is a JSON status record (``{"pid": ...}``). For backward
    compatibility, a bare integer is also accepted.
    """
    status = read_daemon_status()
    if status is None:
        return None
    pid = status.get("pid")
    if isinstance(pid, int):
        return pid
    return None


def read_daemon_status() -> dict | None:
    """Read the daemon status record (pid, base_url, server, started_at)."""
    if not DAEMON_PID_PATH.exists():
        return None
    try:
        raw = DAEMON_PID_PATH.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Legacy plain-int pid file.
        try:
            return {"pid": int(raw)}
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def write_daemon_status(pid: int, *, base_url: str, server: str) -> None:
    """Write the daemon status record (JSON) to the pid file."""
    DAEMON_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": pid,
        "base_url": base_url,
        "server": server,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    DAEMON_PID_PATH.write_text(json.dumps(record, indent=2) + "\n")


def clear_daemon_status() -> None:
    """Remove the daemon status/pid file."""
    DAEMON_PID_PATH.unlink(missing_ok=True)


def process_is_running(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently alive.

    POSIX uses the classic ``kill(pid, 0)`` probe, which sends no signal and just
    checks that the process exists. That probe is wrong on Windows — signal ``0``
    maps to ``CTRL_C_EVENT`` there, not a no-op — so Windows queries the process
    handle directly instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by another user.
        return True
    except OSError:
        return False
    return True


def _windows_process_is_running(pid: int) -> bool:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # No handle: the pid is gone, unless we were merely denied access — in
        # which case the process does exist and is treated as running.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        # A live process never becomes signaled, so a zero-timeout wait reports
        # WAIT_TIMEOUT; an exited process reports WAIT_OBJECT_0 (0).
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)
