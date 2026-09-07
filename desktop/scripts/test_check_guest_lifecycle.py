import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from check_guest_lifecycle import check


@unittest.skipUnless(os.name == "posix", "The VM helper uses Unix process signals")
class GuestLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.release = self.root / "release"
        self.release.mkdir()
        for name in ("vmlinuz", "initrd", "disk.raw"):
            (self.release / name).write_bytes(b"immutable artifact")
        self.evidence = self.root / "evidence"
        self.helper = self.root / "helper"
        self.cli = self.root / "cli"
        self.write_helper()
        self.write_cli()

    def script(self, path: Path, code: str) -> None:
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o700)

    def write_helper(self, fault: bool = False, ignore_stop: bool = False) -> None:
        self.script(self.helper, f"""
import os, pathlib, signal, sys, time
state = pathlib.Path(sys.argv[sys.argv.index('--runtime') + 1])
share = pathlib.Path(sys.argv[sys.argv.index('--control-share') + 1])
count = state / 'boot-count'
boot = int(count.read_text()) + 1 if count.exists() else 1
assert (share / 'data-disk-fresh').exists() == (boot == 1)
with (state / 'data.raw').open('r+b') as data:
    assert data.read(1) == (b'\\0' if boot == 1 else b'X')
    data.seek(0)
    data.write(b'X')
count.write_text(str(boot))
def stop(signum, frame):
    (state / 'ready').unlink()
    sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN if {ignore_stop!r} else stop)
(state / 'console.log').write_text({'Kernel panic - not syncing: fixture' if fault else 'booted'!r})
(state / 'pid').write_text(str(os.getpid()))
(state / 'ready').touch()
while True:
    time.sleep(1)
""")

    def write_cli(self, drift: bool = False) -> None:
        self.script(self.cli, f"""
import json, os, pathlib, time
state = pathlib.Path(os.environ['LEMMA_GUEST_CONTROL_SOCKET']).parent
while not (state / 'ready').exists():
    time.sleep(0.01)
print(json.dumps({{'ok': True, 'result': {{'status': 'ready', 'clock_epoch': int(time.time()) - {60 if drift else 0}}}}}))
""")

    def run_check(self, boots: int = 1, shutdown_seconds: float = 3) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            check(self.helper, self.cli, self.release, self.evidence,
                  boots=boots, samples=2, seconds=5, shutdown_seconds=shutdown_seconds)

    def assert_reaped(self) -> None:
        pid = int((self.evidence / "state/pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_reboots_preserve_data_and_clear_the_fresh_disk_marker(self) -> None:
        self.run_check(boots=3)
        self.assertEqual((self.evidence / "state/boot-count").read_text(), "3")
        self.assertFalse((self.evidence / "share/data-disk-fresh").exists())
        for boot in range(1, 4):
            result = json.loads((self.evidence / f"result-{boot}.json").read_text())
            self.assertEqual(result["health_checks"], 2)
            self.assertEqual(result["returncode"], 0)
            self.assertFalse(result["forced_shutdown"])
        self.assertEqual((self.release / "disk.raw").read_bytes(), b"immutable artifact")
        self.assert_reaped()

    def test_a_kernel_fault_rejects_an_otherwise_healthy_guest(self) -> None:
        self.write_helper(fault=True)
        with self.assertRaisesRegex(RuntimeError, "kernel fault"):
            self.run_check()
        self.assert_reaped()

    def test_clock_drift_rejects_an_otherwise_healthy_guest(self) -> None:
        self.write_cli(drift=True)
        with self.assertRaisesRegex(RuntimeError, "clock"):
            self.run_check()
        self.assert_reaped()

    def test_forced_shutdown_fails_and_reaps_the_helper(self) -> None:
        self.write_helper(ignore_stop=True)
        with self.assertRaisesRegex(RuntimeError, "shut down cleanly"):
            self.run_check(shutdown_seconds=0.1)
        result = json.loads((self.evidence / "result-1.json").read_text())
        self.assertTrue(result["forced_shutdown"])
        self.assert_reaped()

    def test_zero_boots_cannot_report_success(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            self.run_check(boots=0)
        self.assertFalse(self.evidence.exists())


if __name__ == "__main__":
    unittest.main()
