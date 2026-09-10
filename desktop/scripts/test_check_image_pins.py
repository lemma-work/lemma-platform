import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_image_pins


class ImagePinTests(unittest.TestCase):
    def check(self, dockerfile: str) -> list[str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "Dockerfile"
        path.write_text(dockerfile)
        with mock.patch.object(check_image_pins, "DOCKERFILES", [path]):
            return check_image_pins.failures()

    def test_the_shipped_dockerfile_passes(self) -> None:
        self.assertEqual(check_image_pins.failures(), [])

    def test_a_moving_tag_is_refused(self) -> None:
        """The failure this exists for: the same commit, a different guest."""
        problems = self.check("FROM ubuntu:24.04 AS runtime\n")

        self.assertEqual(len(problems), 1)
        self.assertIn("moving tag", problems[0])

    def test_a_bare_image_name_is_refused_too(self) -> None:
        self.assertEqual(len(self.check("FROM ubuntu\n")), 1)

    def test_a_digest_pin_passes_with_or_without_a_stage(self) -> None:
        digest = "@sha256:" + "a" * 64
        for line in (f"FROM ubuntu:24.04{digest} AS runtime\n", f"FROM ubuntu{digest}\n"):
            self.assertEqual(self.check(line), [], line)

    def test_scratch_and_earlier_stages_are_not_registry_images(self) -> None:
        """`scratch` is the absence of an image, and a stage name is local."""
        self.assertEqual(
            self.check(
                f"FROM ubuntu:24.04@sha256:{'b' * 64} AS runtime\n"
                "FROM scratch AS export\n"
                "FROM runtime AS more\n"
            ),
            [],
        )

    def test_a_truncated_or_malformed_digest_does_not_count(self) -> None:
        for bad in ["@sha256:abc", "@sha512:" + "a" * 128, "@" + "a" * 64]:
            self.assertEqual(len(self.check(f"FROM ubuntu:24.04{bad}\n")), 1, bad)

    def test_the_line_number_points_at_the_offending_from(self) -> None:
        problems = self.check("# a comment\n\nFROM ubuntu:24.04\n")

        self.assertIn(":3:", problems[0])


if __name__ == "__main__":
    unittest.main()
