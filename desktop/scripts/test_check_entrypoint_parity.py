import unittest
from unittest import mock

import check_entrypoint_parity as parity


class EntrypointParityTests(unittest.TestCase):
    def test_the_shipped_entrypoints_agree(self) -> None:
        self.assertEqual(parity.failures(), [])

    def compare(self, targets: set[str], verbs: set[str], **overrides) -> list[str]:
        patches = {
            "ONLY_ON_THE_MAKEFILE": {},
            "REPO_WIDE": {},
            **overrides,
        }
        with (
            mock.patch.object(parity, "makefile_targets", lambda: targets),
            mock.patch.object(parity, "powershell_verbs", lambda: verbs),
            mock.patch.object(parity, "ONLY_ON_THE_MAKEFILE", patches["ONLY_ON_THE_MAKEFILE"]),
            mock.patch.object(parity, "REPO_WIDE", patches["REPO_WIDE"]),
        ):
            return parity.failures()

    def test_a_target_windows_cannot_run_is_reported(self) -> None:
        """The drift this exists for: the file-size ratchet, the baked-concepts
        check, the browser journeys and `check` itself were all missing from
        Windows, and none of them is platform-specific."""
        problems = self.compare({"file-size", "lint"}, {"lint"})

        self.assertEqual(len(problems), 1)
        self.assertIn("desktop-file-size", problems[0])

    def test_a_recorded_platform_difference_is_allowed(self) -> None:
        self.assertEqual(
            self.compare(
                {"dmg", "lint"},
                {"lint"},
                ONLY_ON_THE_MAKEFILE={"dmg": "a macOS disk image"},
            ),
            [],
        )

    def test_a_stale_record_is_reported(self) -> None:
        """An exemption for a target nobody has is where the next drift hides."""
        problems = self.compare(
            {"lint"}, {"lint"}, ONLY_ON_THE_MAKEFILE={"gone": "used to matter"}
        )

        self.assertEqual(len(problems), 1)
        self.assertIn("Remove the entry", problems[0])

    def test_a_verb_with_no_target_at_all_is_reported(self) -> None:
        problems = self.compare({"lint"}, {"lint", "invented"})

        self.assertEqual(len(problems), 1)
        self.assertIn("desktop.ps1 invented", problems[0])

    def test_help_is_not_reported_as_a_missing_target(self) -> None:
        """`help` prints the list; it is not something the Makefile builds."""
        self.assertIn("help", parity.powershell_verbs())
        self.assertEqual(self.compare({"lint"}, {"lint", "help"}), [])

    def test_a_restructured_parameter_is_a_failure_rather_than_a_pass(self) -> None:
        """Otherwise the verb list reads as empty and everything looks fine."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            restructured = Path(directory) / "desktop.ps1"
            restructured.write_text("param([string]$Verb)\n")
            with mock.patch.object(parity, "POWERSHELL", restructured):
                with self.assertRaises(SystemExit) as raised:
                    parity.powershell_verbs()
        self.assertIn("ValidateSet", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
