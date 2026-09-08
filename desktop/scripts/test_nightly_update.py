from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nightly_update import TARGETS, assemble, nightly_version, stage, version_order


class NightlyUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / "updates"
        self.manifest = self.root / "runtime.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "version": "0.7.2",
                    "host_packs": {
                        host: {"size": 100 + i}
                        for i, (host, _, _) in enumerate(TARGETS.values())
                    },
                    "guest_runtimes": {
                        guest: {"size": 200 + i}
                        for i, (_, guest, _) in enumerate(TARGETS.values())
                    },
                    "infra": {"postgres": "postgres-pg18@sha256:fixture"},
                }
            )
        )
        self.payload = self.root / "payload"
        self.payload.write_bytes(b"test update bytes")
        Path(str(self.payload) + ".sig").write_text(
            "test signature from the build step"
        )
        self.version = nightly_version("0.7.2", 10, 1)

    def prepare(self, target: str, version: str | None = None) -> None:
        stage(
            target,
            version or self.version,
            self.manifest,
            self.payload,
            self.output,
            "lemma-work/lemma-platform",
        )

    def feed(self, current: Path | None = None) -> dict[str, object]:
        return assemble(self.output, "lemma-work/lemma-platform", current)

    def complete(self) -> None:
        for target in TARGETS:
            self.prepare(target)

    def test_runs_and_attempts_are_unique_and_ordered_numerically(self) -> None:
        versions = [
            "0.7.2-nightly.9",
            self.version,
            nightly_version("0.7.2", 10, 2),
            nightly_version("0.7.2", 11, 1),
        ]
        self.assertEqual(sorted(versions, key=version_order), versions)
        self.assertEqual(len(set(versions)), len(versions))
        for base, run, attempt in [
            ("0.7.2", 0, 1),
            ("0.7.2", 1, 0),
            ("0.7.2-beta", 1, 1),
            ("../bad", 1, 1),
        ]:
            with (
                self.subTest(base=base, run=run, attempt=attempt),
                self.assertRaises(ValueError),
            ):
                nightly_version(base, run, attempt)

    def test_complete_feed_contains_both_matching_platforms_and_their_runtime_sizes(
        self,
    ) -> None:
        self.complete()
        feed = self.feed()
        self.assertEqual(feed["version"], self.version)
        self.assertEqual(set(feed["platforms"]), set(TARGETS))
        self.assertEqual(
            feed["lemma"]["platforms"]["windows-x86_64"]["runtime_download_bytes"], 302
        )
        self.assertEqual(feed["lemma"]["runtime_download_bytes"], 300)
        for entry in feed["platforms"].values():
            self.assertIn(f"/desktop-nightly/Lemma_{self.version}_", entry["url"])
            self.assertEqual(
                entry["signature"], Path(str(self.payload) + ".sig").read_text()
            )

    def test_one_platform_cannot_replace_the_complete_feed(self) -> None:
        self.prepare("darwin-aarch64")
        with self.assertRaises(FileNotFoundError):
            self.feed()

    def test_different_platform_versions_are_rejected(self) -> None:
        self.prepare("darwin-aarch64")
        self.prepare("windows-x86_64", nightly_version("0.7.2", 11, 1))
        with self.assertRaisesRegex(ValueError, "same nightly version"):
            self.feed()

    def test_older_build_cannot_replace_a_newer_feed_and_identical_retry_is_safe(
        self,
    ) -> None:
        self.complete()
        current = self.root / "current.json"
        previous = self.feed()
        current.write_text(json.dumps(previous))
        self.assertEqual(self.feed(current), previous)
        previous["version"] = nightly_version("0.7.2", 11, 1)
        current.write_text(json.dumps(previous))
        with self.assertRaisesRegex(ValueError, "newer nightly"):
            self.feed(current)

    def test_changed_bytes_cannot_be_published_under_the_same_version(self) -> None:
        self.complete()
        current = self.root / "current.json"
        current.write_text(json.dumps(self.feed()))
        Path(str(self.payload) + ".sig").write_text("different build signature")
        self.prepare("darwin-aarch64")
        with self.assertRaisesRegex(ValueError, "different artifacts"):
            self.feed(current)

    def test_artifact_changes_after_staging_are_rejected(self) -> None:
        self.complete()
        path = next(self.output.glob("*.exe"))
        path.write_bytes(b"changed payload")
        with self.assertRaisesRegex(ValueError, "Payload changed"):
            self.feed()

    def test_malformed_platform_metadata_or_redirected_payload_is_rejected(self) -> None:
        self.complete()
        path = self.output / "windows-x86_64.json"
        original = path.read_text()
        for field, value in [
            ("postgres_major", None),
            ("postgres_major", True),
            ("runtime_download_bytes", -1),
            ("runtime_download_bytes", "302"),
        ]:
            with self.subTest(field=field, value=value):
                fragment = json.loads(original)
                fragment["lemma"][field] = value
                path.write_text(json.dumps(fragment))
                with self.assertRaisesRegex(ValueError, "Invalid platform metadata"):
                    self.feed()
        fragment = json.loads(original)
        fragment["entry"]["url"] = "https://example.com/unrelated-installer.exe"
        path.write_text(json.dumps(fragment))
        with self.assertRaisesRegex(ValueError, "immutable payload"):
            self.feed()

    def test_mismatched_runtime_and_missing_signature_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime versions"):
            self.prepare("darwin-aarch64", nightly_version("0.7.3", 10, 1))
        Path(str(self.payload) + ".sig").write_text("")
        with self.assertRaisesRegex(ValueError, "signed update"):
            self.prepare("darwin-aarch64")
        self.assertFalse(self.output.exists())

    def test_version_command_writes_the_same_overlay_for_both_platform_builds(
        self,
    ) -> None:
        cargo = self.root / "Cargo.toml"
        cargo.write_text('[workspace.package]\nversion = "0.7.2"\n')
        output = self.root / "outputs"
        overlay = self.root / "overlay.json"
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("nightly_update.py")),
                "version",
                "--manifest",
                str(self.manifest),
                "--cargo",
                str(cargo),
                "--run",
                "10",
                "--attempt",
                "1",
                "--output",
                str(output),
                "--overlay",
                str(overlay),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(json.loads(overlay.read_text()), {"version": self.version})
        self.assertEqual(
            output.read_text(), f"version={self.version}\nruntime_version=0.7.2\n"
        )


if __name__ == "__main__":
    unittest.main()
