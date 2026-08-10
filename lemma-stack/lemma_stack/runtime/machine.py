"""Podman machine (macOS/Windows VM) and Linux socket management.

lemma-stack owns a podman machine named ``lemma-runtime``. The goal is that a
user with podman installed but nothing configured ends up with a working
runtime: we create the machine if it's missing, start (and reuse) it if it
already exists, point the podman CLI's default connection at it so ``podman
info`` and socket discovery target the right VM, and wait for it to actually
respond — recreating it once if it's wedged — rather than failing.
"""

from __future__ import annotations

import json
import platform
import subprocess
import time

from lemma_stack.output import AdminError, info

MACHINE_NAME = "lemma-runtime"
MACHINE_MEMORY_MB = 6144
MACHINE_CPUS = 4
MACHINE_DISK_GB = 100
READY_TIMEOUT_S = 120


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise AdminError(f"{' '.join(args)} failed: {(proc.stderr or '').strip()}")
    return proc


def _machines() -> list[dict]:
    proc = _run(["podman", "machine", "list", "--format", "json"], check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []


def ensure_podman_ready(runtime) -> None:
    system = platform.system()
    if system == "Linux":
        _ensure_linux_socket()
        return
    _ensure_machine(runtime)


def _init_machine() -> None:
    info(
        f"Creating podman machine '{MACHINE_NAME}' "
        f"({MACHINE_MEMORY_MB} MiB RAM, {MACHINE_CPUS} CPUs, {MACHINE_DISK_GB} GiB disk). "
        "First run downloads a VM image — this can take a minute…"
    )
    proc = _run(
        [
            "podman",
            "machine",
            "init",
            MACHINE_NAME,
            "--memory",
            str(MACHINE_MEMORY_MB),
            "--cpus",
            str(MACHINE_CPUS),
            "--disk-size",
            str(MACHINE_DISK_GB),
            "--rootful=false",
            "--now",
        ],
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        # A machine with this name already exists (stale list / prior partial run):
        # reuse it rather than fail.
        if "already exists" in err:
            _start_machine()
            return
        raise AdminError(
            f"podman machine init '{MACHINE_NAME}' failed: {(proc.stderr or '').strip()}"
        )


def _start_machine() -> bool:
    """Start our machine. True if it started or was already running."""
    info(f"Starting podman machine '{MACHINE_NAME}'…")
    proc = _run(["podman", "machine", "start", MACHINE_NAME], check=False)
    if proc.returncode == 0:
        return True
    return "already running" in (proc.stderr or "").lower()


def _set_default_connection() -> None:
    """Point the podman CLI's default connection at our machine.

    ``machine init``/``start`` do not re-point an existing default connection,
    so if the user already had another (possibly stopped) machine as default,
    ``podman info`` would talk to the wrong VM. Make ours the default; the
    rootless connection is named after the machine.
    """
    _run(["podman", "system", "connection", "default", MACHINE_NAME], check=False)


def _recreate_machine() -> None:
    info(f"podman machine '{MACHINE_NAME}' is not responding; recreating it…")
    _run(["podman", "machine", "stop", MACHINE_NAME], check=False)
    _run(["podman", "machine", "rm", "--force", MACHINE_NAME], check=False)
    _init_machine()


def _wait_ready(runtime, timeout_s: int = READY_TIMEOUT_S) -> bool:
    """Poll until the runtime answers `info` (the API socket is live)."""
    deadline = time.monotonic() + timeout_s
    while True:
        if runtime.available():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


def _ensure_machine(runtime) -> None:
    # Already usable — the user's own running machine, or ours. Leave it be.
    if runtime.available():
        return

    have_ours = any(m.get("Name") == MACHINE_NAME for m in _machines())
    if have_ours:
        # We manage this machine: reuse it. Start it, recreating only if it
        # won't even start.
        if not _start_machine():
            _recreate_machine()
    else:
        _init_machine()
    _set_default_connection()

    if _wait_ready(runtime):
        return

    # Started/created but the API never came up — one clean recreate, then give up.
    _recreate_machine()
    _set_default_connection()
    if _wait_ready(runtime):
        return

    raise AdminError(
        f"podman machine '{MACHINE_NAME}' isn't responding after a restart and a recreate.\n"
        "Inspect or reset it manually:\n"
        f"  podman machine ls\n"
        f"  podman machine rm -f {MACHINE_NAME} && lemma-stack install\n"
        "or use Docker instead:\n"
        "  lemma-stack install --runtime docker"
    )


def _ensure_linux_socket() -> None:
    # Rootless podman needs the user API socket for provisioning sandboxes.
    proc = _run(
        ["systemctl", "--user", "enable", "--now", "podman.socket"],
        check=False,
    )
    if proc.returncode != 0:
        info("note: could not enable podman.socket via systemd; relying on existing socket")
