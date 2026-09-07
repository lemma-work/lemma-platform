#!/usr/bin/env python3
"""Boot a disposable disk with the standalone reference runner and retain evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time

FAULTS = (
    "Internal error: Oops",
    "Kernel panic - not syncing:",
    "Fixing recursive fault but reboot is needed!",
    "BUG: Bad rss-counter state",
    "BUG: Bad page state",
    "Kernel stack overflow.",
)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def check(
    runner: Path, kernel: Path, initrd: Path, disk: Path, seed: Path | None,
    evidence: Path, command_line: str, seconds: float, marker: str,
) -> bool:
    evidence.mkdir(parents=True, exist_ok=False)
    console = evidence / "console.log"
    private_disk = evidence / "root.raw"
    inputs = {"runner": runner, "kernel": kernel, "initrd": initrd, "disk": disk}
    if seed is not None:
        inputs["seed"] = seed
    provenance = {name: {"path": str(path.resolve()), "sha256": digest(path)} for name, path in inputs.items()}
    (evidence / "inputs.json").write_text(json.dumps(provenance, indent=2) + "\n")
    # APFS cloning preserves sparse reference images without another full copy.
    if platform.system() == "Darwin":
        subprocess.run(["cp", "-c", str(disk), str(private_disk)], check=True)
    else:
        shutil.copyfile(disk, private_disk)
    private_disk.chmod(0o600)
    started = time.monotonic()
    reason = "deadline"
    returncode: int | None = None
    with (evidence / "runner.log").open("wb") as log:
        process = subprocess.Popen(
            [str(runner), str(kernel), str(initrd), str(private_disk),
             str(seed) if seed is not None else "-", str(console), command_line],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            while time.monotonic() - started < seconds:
                returncode = process.poll()
                text = console.read_text(errors="replace") if console.exists() else ""
                if any(fault in text for fault in FAULTS):
                    reason = "kernel-fault"
                    break
                if returncode is not None:
                    reason = "passed" if returncode == 0 and marker in text else "incomplete-boot"
                    break
                time.sleep(0.2)
        finally:
            stop(process)
    result = {
        "result": reason, "returncode": returncode,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "command_line": command_line, "success_marker": marker,
    }
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return reason == "passed"


def main() -> None:
    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "kernel", "initrd", "disk", "evidence"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--marker", default="LEMMA_REFERENCE_BOOT_OK")
    parser.add_argument("--command-line", default="root=LABEL=cloudimg-rootfs ro console=hvc0 panic=1")
    args = parser.parse_args()
    if args.seconds <= 0 or not args.marker:
        parser.error("seconds and marker must be nonempty")
    raise SystemExit(0 if check(**vars(args)) else 1)


if __name__ == "__main__":
    main()
