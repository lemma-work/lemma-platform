from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[4]


def test_watchdog_does_not_abort_during_normal_interpreter_shutdown() -> None:
    environment = os.environ.copy()
    environment["LEMMA_LOCALD_PARENT_WATCHDOG"] = "1"
    with subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from app.core.locald_watchdog import "
                "install_locald_parent_watchdog; "
                "install_locald_parent_watchdog(); "
                "print('ready', flush=True)"
            ),
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        return_code = process.wait(timeout=5)
        assert process.stderr is not None
        stderr = process.stderr.read()

    assert return_code == 0, stderr
    assert "_enter_buffered_busy" not in stderr
