#!/usr/bin/env python3
"""Qualify a macOS guest artifact with its production helper and disposable data."""

import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from typing import Literal

from check_vm_boot import FAULTS, clone_disk, digest


def check(
    helper: Path, cli: Path, release: Path, evidence: Path,
    boots: int = 3, samples: int = 20, seconds: float = 180,
    shutdown_seconds: float = 30,
    shutdown_method: Literal["guest", "power-button"] = "guest",
    initial_data_disk: Path | None = None,
) -> None:
    if boots < 1 or samples < 1 or seconds <= 0 or shutdown_seconds <= 0:
        raise ValueError("Boots, samples and deadlines must be positive")
    if shutdown_method not in ("guest", "power-button"):
        raise ValueError("Unknown shutdown method")
    if initial_data_disk is not None:
        size = initial_data_disk.stat().st_size
        if not initial_data_disk.is_file() or size == 0 or size % 512:
            raise ValueError("Initial data must be a nonempty raw disk with 512-byte blocks")
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = evidence / "state"
    share = evidence / "share"
    state.mkdir(mode=0o700)
    share.mkdir(mode=0o700)
    inputs = {"helper": helper, "cli": cli}
    if initial_data_disk is not None:
        inputs["initial_data_disk"] = initial_data_disk
    inputs.update({name: release / name for name in ("vmlinuz", "initrd", "disk.raw")})
    (evidence / "inputs.json").write_text(json.dumps({
        name: {"path": str(path.resolve()), "sha256": digest(path)}
        for name, path in inputs.items()
    }, indent=2) + "\n")
    if initial_data_disk is None:
        with (state / "data.raw").open("xb") as disk:
            disk.truncate(24 * 1024**3)
        (state / "data.raw").chmod(0o600)
    else:
        clone_disk(initial_data_disk, state / "data.raw")
    capability = share / "guest.capability"
    capability.write_text(secrets.token_hex(32))
    capability.chmod(0o600)
    fresh = share / "data-disk-fresh"
    if initial_data_disk is None:
        fresh.touch()
    env = os.environ.copy()
    env["LEMMA_GUEST_CONTROL_SOCKET"] = str(state / "control.sock")
    env["LEMMA_GUEST_CAPABILITY_FILE"] = str(capability)
    request = json.dumps({"version": 1, "operation": "health", "parameters": {}}).encode()

    for boot in range(1, boots + 1):
        (share / "host.epoch").write_text(str(int(time.time())))
        started = time.monotonic()
        completed = 0
        error: str | None = None
        forced = False
        fallback = False
        shutdown_returncode: int | None = None
        shutdown_request_timed_out = False
        with (evidence / f"helper-{boot}.log").open("wb") as log:
            process = subprocess.Popen([
                str(helper), "serve", "--release", str(release), "--runtime", str(state),
                "--control-socket", str(state / "control.sock"), "--control-share", str(share),
            ], stdout=log, stderr=subprocess.STDOUT)
            try:
                while completed < samples:
                    remaining = seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise RuntimeError("Guest health deadline exceeded")
                    if process.poll() is not None:
                        raise RuntimeError("VM exited before completing health checks")
                    console = state / "console.log"
                    text = console.read_text(errors="replace") if console.exists() else ""
                    if any(fault in text for fault in FAULTS):
                        raise RuntimeError("Guest kernel fault")
                    try:
                        result = subprocess.run([str(cli), "request"], input=request,
                                                capture_output=True, env=env,
                                                timeout=min(5, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                    if result.returncode == 0:
                        # The bridge returns untyped JSON; validate health before using it.
                        response = json.loads(result.stdout)
                        health = response.get("result") if isinstance(response, dict) else None
                        if not isinstance(health, dict) or health.get("status") != "ready":
                            raise RuntimeError("Guest did not report ready")
                        clock = health.get("clock_epoch")
                        if type(clock) not in (int, float) or abs(clock - time.time()) >= 10:
                            raise RuntimeError("Guest clock is missing or incorrect")
                        completed += 1
                    if completed < samples:
                        time.sleep(min(1, max(0, seconds - (time.monotonic() - started))))
            except BaseException as failure:
                error = str(failure) or type(failure).__name__
                raise
            finally:
                stopped = time.monotonic()
                try:
                    if process.poll() is not None:
                        error = error or "Guest exited before shutdown was requested"
                    else:
                        if shutdown_method == "guest":
                            shutdown_request = json.dumps({
                                "version": 1, "operation": "system.shutdown", "parameters": {},
                            }).encode()
                            try:
                                response = subprocess.run(
                                    [str(cli), "request"], input=shutdown_request,
                                    capture_output=True, env=env, timeout=min(8, shutdown_seconds),
                                )
                                shutdown_returncode = response.returncode
                            except subprocess.TimeoutExpired:
                                # The guest may power off before acknowledging;
                                # record the lost reply and still verify exit.
                                shutdown_request_timed_out = True
                        else:
                            process.terminate()
                        try:
                            process.wait(timeout=max(0, shutdown_seconds - (time.monotonic() - stopped)))
                        except subprocess.TimeoutExpired:
                            if shutdown_method == "guest":
                                fallback = True
                                process.terminate()
                                try:
                                    process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    forced = True
                            else:
                                forced = True
                            if forced:
                                process.kill()
                                process.wait(timeout=5)
                finally:
                    # Cancellation can arrive while the shutdown RPC or wait is
                    # active. Reap the owned helper even on that path.
                    if process.poll() is None:
                        forced = True
                        process.kill()
                        process.wait(timeout=5)
                console = state / "console.log"
                if console.exists():
                    text = console.read_text(errors="replace")
                    if any(fault in text for fault in FAULTS):
                        error = error or "Guest kernel fault"
                    console.rename(evidence / f"console-{boot}.log")
                report = {
                    "boot": boot, "health_checks": completed, "error": error,
                    "forced_shutdown": forced, "returncode": process.returncode,
                    "shutdown_method": shutdown_method, "fallback_shutdown": fallback,
                    "shutdown_request_returncode": shutdown_returncode,
                    "shutdown_request_timed_out": shutdown_request_timed_out,
                    "shutdown_seconds": round(time.monotonic() - stopped, 2),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
                (evidence / f"result-{boot}.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report), flush=True)
                # These endpoints belong to the child just reaped above. A
                # crashed helper cannot unlink them, and the next boot must
                # not mistake them for another live helper's listeners.
                for port in (5432, 6379, 3567):
                    (state / f"service-{port}.sock").unlink(missing_ok=True)
            if error or forced or fallback or process.returncode != 0:
                raise RuntimeError(error or "Guest did not shut down cleanly")
        fresh.unlink(missing_ok=True)


def main() -> None:
    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("helper", "cli", "release", "evidence"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--initial-data-disk", type=Path)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--shutdown-seconds", type=float, default=30)
    parser.add_argument("--shutdown-method", choices=("guest", "power-button"), default="guest")
    check(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
