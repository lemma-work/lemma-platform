import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_online_payload import (
    MAX_ONLINE_BYTES,
    check_size,
    macos_payload,
    windows_payload,
)


class OnlinePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def put(self, relative: str, size: int = 1) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            stream.truncate(size)
        return path

    def windows(self) -> list[Path]:
        source = Path(__file__).resolve().parents[1]
        for name in ["tauri.windows.conf.json", "tauri.online.conf.json"]:
            (self.root / name).write_bytes((source / name).read_bytes())
        self.put("target/release/lemma-desktop.exe")
        config = json.loads((self.root / "tauri.windows.conf.json").read_text())
        for helper in config["bundle"]["externalBin"]:
            self.put(f"{helper}-x86_64-pc-windows-msvc.exe")
        self.put("runtime/lemma-local.json")
        return windows_payload(self.root)

    def test_windows_counts_the_host_and_future_configured_sidecars(self) -> None:
        files = self.windows()
        self.assertIn(
            self.root / "binaries/lemma-agent-host-x86_64-pc-windows-msvc.exe", files
        )
        config_path = self.root / "tauri.windows.conf.json"
        config = json.loads(config_path.read_text())
        config["bundle"]["externalBin"].append("binaries/additional-helper")
        config_path.write_text(json.dumps(config))
        extra = self.put("binaries/additional-helper-x86_64-pc-windows-msvc.exe", 100)
        self.assertIn(extra, windows_payload(self.root))
        self.assertEqual(check_size(windows_payload(self.root)), len(files) + 100)

    def test_missing_agent_host_fails_instead_of_undercounting(self) -> None:
        self.windows()
        (self.root / "binaries/lemma-agent-host-x86_64-pc-windows-msvc.exe").unlink()
        with self.assertRaisesRegex(ValueError, "missing.*lemma-agent-host"):
            windows_payload(self.root)

    def test_size_boundary_and_oversized_payload(self) -> None:
        payload = self.put("payload", MAX_ONLINE_BYTES)
        self.assertEqual(check_size([payload]), MAX_ONLINE_BYTES)
        self.put("payload", MAX_ONLINE_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "limit"):
            check_size([payload])

    def test_macos_counts_all_bundle_resources_and_requires_every_helper(self) -> None:
        for name in ["desktop", "locald", "agent-host", "runtime"]:
            self.put(f"Contents/MacOS/lemma-{name}")
        self.put("Contents/Resources/lemma-vz")
        self.put("Contents/Resources/lemma-local.json")
        self.put("Contents/Resources/extra-runtime.zip", MAX_ONLINE_BYTES)
        with self.assertRaisesRegex(ValueError, "limit"):
            check_size(macos_payload(self.root))
        (self.root / "Contents/MacOS/lemma-agent-host").unlink()
        with self.assertRaisesRegex(ValueError, "missing.*lemma-agent-host"):
            macos_payload(self.root)

    def test_configured_resources_cannot_escape_the_build_root(self) -> None:
        self.windows()
        (self.root / "tauri.online.conf.json").write_text(
            json.dumps(
                {"bundle": {"resources": {str(Path(__file__).resolve()): "outside.py"}}}
            )
        )
        with self.assertRaisesRegex(ValueError, "outside"):
            windows_payload(self.root)

    def test_cli_reports_missing_and_valid_payloads(self) -> None:
        self.windows()
        script = str(Path(__file__).with_name("check_online_payload.py"))
        command = [sys.executable, script, "--windows-root", str(self.root)]
        valid = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("Online payload verified", valid.stdout)
        (self.root / "target/release/lemma-desktop.exe").unlink()
        invalid = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("missing", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
