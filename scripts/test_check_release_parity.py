import unittest
from unittest import mock

import check_release_parity as parity


class ReleaseParityTests(unittest.TestCase):
    def test_the_two_shipped_pipelines_agree(self) -> None:
        self.assertEqual(parity.failures(), [])

    def called(self, release: set[str], nightly: set[str], allowed=None) -> list[str]:
        mapping = {"the tagged release": release, "the nightly": nightly}
        with (
            mock.patch.object(
                parity, "scripts_called", lambda path, job: mapping.pop(next(iter(mapping)))
            ),
            mock.patch.object(parity, "ALLOWED_ASYMMETRY", allowed or {}),
        ):
            return parity.failures()

    def test_a_check_only_one_pipeline_runs_is_reported_both_ways(self) -> None:
        """The drift this exists for, and it ran in both directions: the
        nightly was not checking the guest helper's entitlement, and the
        release was not checking that macOS would ask for local network
        access."""
        problems = self.called({"check_macos_signing.py"}, set())
        self.assertEqual(len(problems), 1)
        self.assertIn("the tagged release runs", problems[0])
        self.assertIn("the nightly does not", problems[0])

        problems = self.called(set(), {"check_macos_signing.py"})
        self.assertEqual(len(problems), 1)
        self.assertIn("the nightly runs", problems[0])

    def test_a_script_named_only_in_a_comment_is_not_a_call(self) -> None:
        """A note explaining why a pipeline stopped calling something would
        otherwise count as still calling it -- hiding the very asymmetry this
        file exists to report."""
        import tempfile
        from pathlib import Path

        import yaml

        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "w.yml"
            workflow.write_text(
                yaml.safe_dump(
                    {
                        "jobs": {
                            "build": {
                                "steps": [
                                    {
                                        "run": "# desktop/scripts/check_online_payload.py\n"
                                        "  #   desktop/scripts/check_updater_key.py\n"
                                        "python3 desktop/scripts/check_macos_signing.py app\n"
                                    }
                                ]
                            }
                        }
                    }
                )
            )

            self.assertEqual(
                parity.scripts_called(workflow, "build"), {"check_macos_signing.py"}
            )

    def test_an_allowance_every_pipeline_outgrew_is_reported(self) -> None:
        """An exemption with no asymmetry left is not describing anything, and
        it would excuse the next real one."""
        allowed = {("the nightly", "shared.py"): "was nightly-only"}
        problems = self.called({"shared.py"}, {"shared.py"}, allowed)

        self.assertEqual(len(problems), 1)
        self.assertIn("every pipeline runs it now", problems[0])

    def test_an_agreed_set_passes_however_large(self) -> None:
        shared = {"check_macos_signing.py", "check_online_payload.py", "a.py"}
        self.assertEqual(self.called(shared, set(shared)), [])

    def test_a_recorded_asymmetry_is_allowed(self) -> None:
        allowed = {("the nightly", "nightly_update.py"): "the nightly's own feed"}
        self.assertEqual(
            self.called(set(), {"nightly_update.py"}, allowed), []
        )

    def test_an_allowance_for_a_script_nobody_calls_is_reported(self) -> None:
        """A stale exemption is where the next drift hides."""
        allowed = {("the nightly", "gone.py"): "used to matter"}
        problems = self.called(set(), set(), allowed)
        self.assertEqual(len(problems), 1)
        self.assertIn("Remove the entry", problems[0])

    def test_a_renamed_job_is_a_failure_rather_than_a_pass(self) -> None:
        """Otherwise DMG_JOBS points at nothing and this file checks nothing."""
        with self.assertRaises(SystemExit) as raised:
            parity.scripts_called(
                parity.REPO / ".github/workflows/release-desktop.yml", "no-such-job"
            )
        self.assertIn("no job", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
