import contextlib
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from check_vm_boot import FAULTS, check


@unittest.skipUnless(os.name == "posix", "The VM runner uses Unix process signals")
class BootCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.disk = self.root / "original.raw"
        self.disk.write_bytes(b"original guest")
        self.runner = self.root / "runner"
        self.evidence = self.root / "evidence"

    def run_check(self, script: str, seconds: float = 3) -> bool:
        self.runner.write_text("#!/bin/sh\nset -eu\n" + script)
        self.runner.chmod(0o700)
        with contextlib.redirect_stdout(io.StringIO()):
            return check(
                self.runner, self.disk, self.disk, self.disk, None,
                self.evidence, "test", seconds, "BOOT_OK",
            )

    def test_requires_completed_boot_and_never_writes_the_source_disk(self) -> None:
        self.assertTrue(self.run_check('echo changed > "$3"; echo BOOT_OK > "$5"\n'))
        self.assertEqual(self.disk.read_bytes(), b"original guest")
        self.assertEqual((self.evidence / "root.raw").read_text(), "changed\n")
        self.assertIn('"sha256"', (self.evidence / "inputs.json").read_text())

    def test_clean_exit_without_guest_marker_is_failure(self) -> None:
        self.assertFalse(self.run_check('echo starting > "$5"\n'))
        self.assertIn("incomplete-boot", (self.evidence / "result.json").read_text())

    def test_kernel_fault_overrides_success_marker_and_zero_exit(self) -> None:
        self.assertFalse(self.run_check('printf "BOOT_OK\\nInternal error: Oops: fault\\n" > "$5"\n'))
        self.assertIn("kernel-fault", (self.evidence / "result.json").read_text())

    def test_memory_corruption_warnings_also_fail_qualification(self) -> None:
        for index, fault in enumerate(FAULTS):
            with self.subTest(fault=fault):
                self.evidence = self.root / f"fault-{index}"
                self.assertFalse(self.run_check(f'printf "BOOT_OK\\n{fault}\\n" > "$5"\n'))

    def test_deadline_stops_and_reaps_the_runner(self) -> None:
        self.assertFalse(self.run_check('echo $$ > "$5.pid"\nexec sleep 60\n', seconds=3))
        child = int((self.evidence / "console.log.pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        self.assertIn("deadline", (self.evidence / "result.json").read_text())

    def test_existing_evidence_cannot_be_replaced(self) -> None:
        self.evidence.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_check('echo BOOT_OK > "$5"\n')

    def test_cancelling_the_check_stops_its_vm_runner(self) -> None:
        self.runner.write_text('#!/bin/sh\necho $$ > "$5.pid"\nexec sleep 60\n')
        self.runner.chmod(0o700)
        command = [sys.executable, str(Path(__file__).with_name("check_vm_boot.py")),
                   "--runner", str(self.runner), "--kernel", str(self.disk),
                   "--initrd", str(self.disk), "--disk", str(self.disk),
                   "--evidence", str(self.evidence)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        child: int | None = None
        try:
            pid_file = self.evidence / "console.log.pid"
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                self.assertIsNone(process.poll())
                time.sleep(0.05)
            self.assertTrue(pid_file.exists(), "VM runner did not start")
            child = int(pid_file.read_text())
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr.decode())
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            if child is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
