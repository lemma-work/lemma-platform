import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Literal

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

    def write_helper(self, fault: bool = False, ignore_stop: bool = False,
                     existing_data: bool = False) -> None:
        self.script(self.helper, f"""
import os, pathlib, signal, sys, time
state = pathlib.Path(sys.argv[sys.argv.index('--runtime') + 1])
share = pathlib.Path(sys.argv[sys.argv.index('--control-share') + 1])
count = state / 'boot-count'
boot = int(count.read_text()) + 1 if count.exists() else 1
assert (share / 'data-disk-fresh').exists() == (boot == 1 and not {existing_data!r})
with (state / 'data.raw').open('r+b') as data:
    assert data.read(1) == (b'\\0' if boot == 1 and not {existing_data!r} else bytes([87 + boot]))
    data.seek(0)
    data.write(bytes([88 + boot]))
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

    def write_cli(self, drift: bool = False, lose_shutdown_reply: bool = False,
                  ignore_shutdown: bool = False, interrupt_shutdown: bool = False) -> None:
        self.script(self.cli, f"""
import json, os, pathlib, signal, sys, time
state = pathlib.Path(os.environ['LEMMA_GUEST_CONTROL_SOCKET']).parent
while not (state / 'ready').exists():
    time.sleep(0.01)
request = json.load(sys.stdin)
if request['operation'] == 'system.shutdown':
    if {interrupt_shutdown!r}:
        os.kill(os.getppid(), signal.SIGINT)
        sys.exit(1)
    if {ignore_shutdown!r}:
        sys.exit(0)
    os.kill(int((state / 'pid').read_text()), signal.SIGTERM)
    sys.exit({1 if lose_shutdown_reply else 0})
print(json.dumps({{'ok': True, 'result': {{'status': 'ready', 'clock_epoch': int(time.time()) - {60 if drift else 0}}}}}))
""")

    def run_check(self, boots: int = 1, shutdown_seconds: float = 3,
                  shutdown_method: Literal["guest", "power-button"] = "guest",
                  initial_data_disk: Path | None = None) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            check(self.helper, self.cli, self.release, self.evidence,
                  boots=boots, samples=2, seconds=5, shutdown_seconds=shutdown_seconds,
                  shutdown_method=shutdown_method, initial_data_disk=initial_data_disk)

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
            self.assertFalse(result["fallback_shutdown"])
            self.assertEqual(result["shutdown_method"], "guest")
        self.assertEqual((self.release / "disk.raw").read_bytes(), b"immutable artifact")
        self.assert_reaped()

    def test_existing_data_is_cloned_without_a_fresh_disk_marker(self) -> None:
        original = self.root / "old-data.raw"
        original.write_bytes(b"X" + bytes(511))
        self.write_helper(existing_data=True)
        self.run_check(boots=2, initial_data_disk=original)
        self.assertEqual(original.read_bytes(), b"X" + bytes(511))
        self.assertEqual((self.evidence / "state/data.raw").read_bytes(), b"Z" + bytes(511))
        self.assertIn("initial_data_disk", json.loads((self.evidence / "inputs.json").read_text()))
        self.assert_reaped()

    def test_an_invalid_initial_disk_is_rejected_before_startup(self) -> None:
        original = self.root / "invalid.raw"
        original.write_bytes(b"not a raw disk")
        with self.assertRaisesRegex(ValueError, "512-byte"):
            self.run_check(initial_data_disk=original)
        self.assertFalse(self.evidence.exists())

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
            self.run_check(shutdown_seconds=0.1, shutdown_method="power-button")
        result = json.loads((self.evidence / "result-1.json").read_text())
        self.assertTrue(result["forced_shutdown"])
        self.assert_reaped()

    def test_guest_shutdown_can_finish_before_its_reply_arrives(self) -> None:
        self.write_cli(lose_shutdown_reply=True)
        self.run_check()
        result = json.loads((self.evidence / "result-1.json").read_text())
        self.assertEqual(result["shutdown_request_returncode"], 1)
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["fallback_shutdown"])
        self.assert_reaped()

    def test_guest_shutdown_fallback_fails_even_when_the_power_button_works(self) -> None:
        self.write_cli(ignore_shutdown=True)
        with self.assertRaisesRegex(RuntimeError, "shut down cleanly"):
            self.run_check(shutdown_seconds=0.2)
        result = json.loads((self.evidence / "result-1.json").read_text())
        self.assertTrue(result["fallback_shutdown"])
        self.assertFalse(result["forced_shutdown"])
        self.assertEqual(result["returncode"], 0)
        self.assert_reaped()

    def test_cancellation_during_shutdown_still_reaps_the_helper(self) -> None:
        self.write_cli(interrupt_shutdown=True)
        with self.assertRaises(KeyboardInterrupt):
            self.run_check()
        self.assert_reaped()

    def test_power_button_shutdown_is_an_independent_check(self) -> None:
        self.run_check(shutdown_method="power-button")
        result = json.loads((self.evidence / "result-1.json").read_text())
        self.assertEqual(result["shutdown_method"], "power-button")
        self.assertIsNone(result["shutdown_request_returncode"])
        self.assert_reaped()

    def test_zero_boots_cannot_report_success(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            self.run_check(boots=0)
        self.assertFalse(self.evidence.exists())


if __name__ == "__main__":
    unittest.main()
