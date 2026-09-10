import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build_release_evidence as evidence


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.artifacts = self.root / "release-artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "Lemma_1.2.3_aarch64-online.dmg").write_bytes(b"dmg bytes")
        (self.artifacts / "latest.json").write_text('{"version":"1.2.3"}')
        self.config = self.root / "tauri.conf.json"
        # The real committed key, so the id in the manifest is a real id.
        shipped = json.loads((Path(__file__).resolve().parent.parent / "tauri.conf.json").read_text())
        self.config.write_text(json.dumps(shipped))
        self.runtime = self.root / "lemma-local.json"
        self.runtime.write_text(json.dumps({
            "version": "1.2.3",
            "host_packs": {"aarch64-apple-darwin": {"size": 11}},
            "guest_runtimes": {"macos-aarch64": {"size": 22}},
        }))

    def build(self, identity: str | None = "Developer ID Application: Lemma (TEAM)") -> dict:
        with mock.patch.object(evidence, "signing_identity", return_value=identity):
            return evidence.build(
                self.artifacts,
                version="1.2.3",
                commit="abc123",
                repository="lemma-work/lemma-platform",
                app=None,
                config=self.config,
                runtime_manifest=self.runtime,
            )

    def test_every_attached_file_is_recorded_with_its_hash(self) -> None:
        manifest = self.build()

        names = {entry["name"] for entry in manifest["artifacts"]}
        self.assertEqual(names, {"Lemma_1.2.3_aarch64-online.dmg", "latest.json"})
        dmg = next(e for e in manifest["artifacts"] if e["name"].endswith(".dmg"))
        # sha256 of b"dmg bytes"
        self.assertEqual(len(dmg["sha256"]), 64)
        self.assertEqual(dmg["size"], len(b"dmg bytes"))

    def test_the_manifest_never_describes_itself(self) -> None:
        """Otherwise writing it changes what it should have said."""
        (self.artifacts / evidence.MANIFEST_NAME).write_text("{}")

        names = {entry["name"] for entry in self.build()["artifacts"]}

        self.assertNotIn(evidence.MANIFEST_NAME, names)

    def test_it_records_the_key_installed_apps_will_verify_with(self) -> None:
        manifest = self.build()

        self.assertRegex(manifest["updater_key_id"], r"^[0-9A-F]{16}$")

    def test_it_records_what_the_first_launch_will_download(self) -> None:
        manifest = self.build()

        self.assertEqual(manifest["runtime"]["version"], "1.2.3")
        self.assertEqual(manifest["runtime"]["sizes"]["macos-aarch64"], 22)

    def test_verify_accepts_the_manifest_it_just_wrote(self) -> None:
        manifest = self.build()
        (self.artifacts / evidence.MANIFEST_NAME).write_text(json.dumps(manifest))

        self.assertEqual(evidence.verify(self.artifacts, manifest), [])

    def test_a_changed_artifact_is_caught(self) -> None:
        """The question this exists to answer: is the file we have the file we published."""
        manifest = self.build()
        (self.artifacts / "Lemma_1.2.3_aarch64-online.dmg").write_bytes(b"different")

        problems = evidence.verify(self.artifacts, manifest)

        self.assertEqual(len(problems), 1)
        self.assertIn("hashes to", problems[0])

    def test_a_missing_or_unexpected_file_is_caught(self) -> None:
        manifest = self.build()
        (self.artifacts / "latest.json").unlink()
        (self.artifacts / "surprise.zip").write_bytes(b"?")

        problems = evidence.verify(self.artifacts, manifest)

        self.assertIn("latest.json is in the manifest and not on disk", problems)
        self.assertIn("surprise.zip is on disk and not in the manifest", problems)

    def test_an_ad_hoc_or_absent_signature_is_refused(self) -> None:
        """A release signed by anything but a Developer ID is not a release."""
        for identity in [None, "Apple Development: Someone (TEAM)", "-"]:
            manifest = self.build(identity)

            problems = evidence.verify(self.artifacts, manifest)

            self.assertTrue(
                any("Developer ID Application" in problem for problem in problems),
                f"{identity!r} was accepted",
            )

    def test_a_release_with_no_updater_key_is_refused(self) -> None:
        self.config.write_text(json.dumps({"plugins": {"updater": {"pubkey": ""}}}))

        manifest = self.build()
        problems = evidence.verify(self.artifacts, manifest)

        self.assertTrue(any("updater key id" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
