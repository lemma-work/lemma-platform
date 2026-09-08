"""Opt-in credential checks against an explicitly selected signed locald."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from check_macos_signing import parse_signature, validate_signature


@unittest.skipUnless(
    sys.platform == "darwin"
    and os.environ.get("LEMMA_NATIVE_CREDENTIAL_TEST_BINARY")
    and os.environ.get("LEMMA_SIGNING_TEST_TEAM"),
    "requires an explicitly selected signed macOS candidate and team",
)
class NativeCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binary = Path(os.environ["LEMMA_NATIVE_CREDENTIAL_TEST_BINARY"]).resolve()
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(self.binary)],
            check=True,
            capture_output=True,
            timeout=15,
        )
        signature = subprocess.run(
            ["/usr/bin/codesign", "-dvvv", str(self.binary)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        validate_signature(
            parse_signature(signature.stderr),
            identifier="work.lemma.locald",
            team=os.environ["LEMMA_SIGNING_TEST_TEAM"],
            allow_development=os.environ.get(
                "LEMMA_NATIVE_CREDENTIAL_ALLOW_DEVELOPMENT"
            )
            == "1",
        )

    def credential(self, install: str, operation: dict[str, str]) -> dict[str, object]:
        request = {
            "version": 1,
            "install_id": install,
            "name": "ai.api_key",
            "operation": operation,
        }
        result = subprocess.run(
            [str(self.binary), "credential-vault"],
            input=json.dumps(request).encode(),
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, "credential helper failed")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            self.fail("credential helper returned an invalid response")
        return value

    def test_native_create_read_replace_remove_and_absence(self) -> None:
        install = "desktop-vault-qa-" + uuid.uuid4().hex

        def cleanup() -> None:
            self.assertEqual(
                self.credential(install, {"action": "delete"}), {"status": "success"}
            )

        self.addCleanup(cleanup)
        self.assertEqual(
            self.credential(install, {"action": "get"}),
            {"status": "value", "value": None},
        )
        for value in ["disposable-first-value", "disposable-replacement-value"]:
            self.assertEqual(
                self.credential(install, {"action": "set", "value": value}),
                {"status": "success"},
            )
            self.assertEqual(
                self.credential(install, {"action": "get"}),
                {"status": "value", "value": value},
            )
        cleanup()
        self.assertEqual(
            self.credential(install, {"action": "get"}),
            {"status": "value", "value": None},
        )

    def test_real_broker_preserves_encrypted_state_across_cold_starts_and_quits(
        self,
    ) -> None:
        # Keep the short path for native Unix socket implementations. Do not use
        # TemporaryDirectory cleanup: if credential cleanup fails, retain the
        # installation identity needed to remove the test key on a later retry.
        root = Path(tempfile.mkdtemp(prefix="lemma-vault-qa-", dir="/tmp"))
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("LEMMA_")
        }
        env["LEMMA_LOCALD_ROOT"] = str(root)

        def cleanup() -> None:
            result = subprocess.run(
                [str(self.binary), "reset", "--confirm=erase-local-lemma"],
                env=env,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(
                result.returncode, 0, f"test cleanup needs attention at {root}"
            )
            self.assertFalse(root.exists(), f"test cleanup needs attention at {root}")

        self.addCleanup(cleanup)

        def request(command: str) -> list[dict[str, object]]:
            result = subprocess.run(
                [
                    str(self.binary),
                    "send",
                    json.dumps({"cmd": command, "id": "credential-test"}),
                ],
                env=env,
                capture_output=True,
                timeout=35,
            )
            self.assertEqual(result.returncode, 0, "daemon request failed")
            events = []
            for line in result.stdout.decode().splitlines():
                event = json.loads(line)
                if not isinstance(event, dict):
                    self.fail("daemon returned an invalid event")
                self.assertNotEqual(
                    event.get("event"), "error", "daemon rejected the credential check"
                )
                events.append(event)
            return events

        previous = None
        for _ in range(2):
            with (root / "fixture.log").open("ab") as log:
                child = subprocess.Popen(
                    [str(self.binary), "serve"],
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        self.assertIsNone(
                            child.poll(), "daemon exited before readiness"
                        )
                        ping = subprocess.run(
                            [str(self.binary), "ping"],
                            env=env,
                            capture_output=True,
                            timeout=2,
                        )
                        if ping.returncode == 0:
                            break
                        self.assertLess(
                            time.monotonic(),
                            deadline,
                            "daemon did not open its control endpoint",
                        )
                        time.sleep(0.05)
                    events = request("control.snapshot")
                    self.assertTrue(
                        any(
                            event.get("event") == "control.snapshot" for event in events
                        )
                    )
                    encrypted = (root / "credentials.enc").read_bytes()
                    self.assertTrue(encrypted.startswith(b"LEMMA-VAULT-V1"))
                    if previous is not None:
                        self.assertEqual(encrypted, previous)
                    previous = encrypted
                    request("shutdown-daemon")
                    self.assertEqual(child.wait(timeout=10), 0)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
