"""The stress diagnostic must fail honestly and reap its owned work."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix", "POSIX stress runner")
class StressRunnerTests(unittest.TestCase):
    def run_stress(self, cargo: str, budget: int = 10) -> subprocess.CompletedProcess[str]:
        temporary = tempfile.TemporaryDirectory(prefix="lemma-stress-test-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        executable = root / "cargo"
        executable.write_text("#!/bin/sh\n" + cargo + "\n")
        executable.chmod(0o700)
        env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "TMPDIR": str(root)}
        result = subprocess.run(
            ["bash", str(Path(__file__).with_name("stress_test_under_load.sh")), "fixture", "1", "1", str(budget)],
            env=env, text=True, capture_output=True, timeout=15,
        )
        for pid in re.findall(r"Load pids: (\d+)", result.stdout):
            with self.assertRaises(ProcessLookupError, msg="load process survived its owner"):
                os.kill(int(pid), 0)
        return result

    def test_failed_test_returns_failure_and_preserves_diagnostics(self) -> None:
        result = self.run_stress("echo ACP_WIRE_DIAGNOSTIC; exit 1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("failures=1/1", result.stdout)
        path = result.stdout.split("Failure log: ", 1)[1].splitlines()[0]
        self.assertEqual(Path(path).read_text(), "ACP_WIRE_DIAGNOSTIC\n")

    def test_success_returns_zero(self) -> None:
        result = self.run_stress('test "$2" = "--workspace" || exit 7; echo "test result: ok. 1 passed; 0 failed;"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("failures=0/1", result.stdout)

    def test_zero_matches_is_a_failure(self) -> None:
        result = self.run_stress('echo "test result: ok. 0 passed; 0 failed; 40 filtered out;"')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("No tests executed for filter: fixture", result.stdout)

    def test_deadline_terminates_hung_test_and_load(self) -> None:
        result = self.run_stress("echo HUNG_TEST_STARTED; sleep 60", budget=2)
        self.assertEqual(result.returncode, 143, result.stderr)
        path = result.stdout.split("Interrupted test log: ", 1)[1].splitlines()[0]
        self.assertIn("HUNG_TEST_STARTED", Path(path).read_text())


if __name__ == "__main__":
    unittest.main()
