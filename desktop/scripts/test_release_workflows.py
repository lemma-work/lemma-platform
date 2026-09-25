"""What the release and nightly workflows build, and what they check first.

Read as text: this suite runs under `uv run --no-project`, without a YAML
parser, and every property below is a line that has to be present or absent.
"""

from __future__ import annotations

from pathlib import Path
import unittest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def job(source: str, name: str) -> str:
    """The text of one top-level job, up to the next one."""
    start = source.index(f"\n  {name}:\n")
    rest = source[start + 1 :]
    following = [
        index
        for index in (rest.find(f"\n  {line}") for line in _job_names(rest))
        if index > 0
    ]
    return rest[: min(following)] if following else rest


def _job_names(text: str) -> list[str]:
    names = []
    for line in text.splitlines()[1:]:
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            names.append(line.strip())
    return names


class ReleaseBuildsItsTag(unittest.TestCase):
    """A stable release used to build main's HEAD under the tag's version."""

    def test_the_desktop_release_checks_out_the_tag_in_every_job(self) -> None:
        source = read("release-desktop.yml")
        self.assertEqual(source.count("uses: actions/checkout@v7"), 2)
        self.assertEqual(
            source.count(
                "ref: ${{ github.event.release.tag_name || (startsWith(inputs.version, 'v')"
            ),
            2,
        )
        self.assertNotIn("github.sha", source, "the gate and build must name the tag's commit")

    def test_the_runtime_release_starts_the_desktop_release_on_the_tag(self) -> None:
        source = read("release-local-images.yml")
        self.assertIn('gh workflow run release-desktop.yml --ref "v${VERSION}"', source)

    def test_a_published_dispatch_must_run_on_the_tag(self) -> None:
        gate = job(read("release-local-images.yml"), "protected-e2e-gate")
        self.assertIn("Require the release tag as the ref", gate)
        self.assertIn('expected="refs/tags/v${VERSION#v}"', gate)


class NightliesAreCheckedBeforeAnythingIsPublished(unittest.TestCase):
    def test_the_first_gate_checks_ci_before_the_manifest_publishes_runtimes(self) -> None:
        source = read("release-local-images.yml")
        gate = job(source, "protected-e2e-gate")
        self.assertIn("uses: ./.github/actions/require-ci-passed", gate)
        self.assertIn("inputs.share && !inputs.publish", gate)
        self.assertIn("needs: [merge, infra-digests, guest-runtimes, host-packs]", job(source, "manifest"))

    def test_no_job_reads_only_the_first_page_of_check_runs(self) -> None:
        for name in ("release-local-images.yml", "nightly-desktop.yml"):
            with self.subTest(workflow=name):
                self.assertNotIn("check-runs?per_page=100", read(name))

    def test_the_nightly_waits_for_ci_instead_of_skipping_the_night(self) -> None:
        source = read("nightly-desktop.yml")
        self.assertIn("uses: ./.github/actions/require-ci-passed", source)
        self.assertIn('wait-minutes: "75"', source)
        self.assertNotIn("update to the next nightly automatically", source)

    def test_the_shared_check_asks_for_the_one_check_by_name(self) -> None:
        action = (WORKFLOWS.parent / "actions" / "require-ci-passed" / "action.yml").read_text()
        self.assertIn('-f check_name="CI passed"', action)
        self.assertIn("-f filter=latest", action)


if __name__ == "__main__":
    unittest.main()
