import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import check_file_size


class RatchetTests(unittest.TestCase):
    """DES-09's ratchet, and the room a reduction leaves behind if nobody records it."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.baseline = Path(self.directory.name) / "file-size-baseline.json"
        self._real_baseline = check_file_size.BASELINE
        self._real_measure = check_file_size.measure
        check_file_size.BASELINE = self.baseline
        self.addCleanup(setattr, check_file_size, "BASELINE", self._real_baseline)
        self.addCleanup(setattr, check_file_size, "measure", self._real_measure)

    def check(self, baseline: dict[str, int], current: dict[str, int]) -> int:
        self.baseline.write_text(json.dumps(baseline), encoding="utf-8")
        check_file_size.measure = lambda: current
        with contextlib.redirect_stdout(io.StringIO()):
            return check_file_size.check()

    def test_no_change_passes(self) -> None:
        self.assertEqual(self.check({"a.rs": 1000}, {"a.rs": 1000}), 0)

    def test_growth_past_the_baseline_fails(self) -> None:
        self.assertEqual(self.check({"a.rs": 1000}, {"a.rs": 1001}), 1)

    def test_a_new_file_over_the_limit_fails(self) -> None:
        self.assertEqual(self.check({}, {"a.rs": 601}), 1)

    def test_an_unrecorded_reduction_fails(self) -> None:
        """A file that shrank and was not re-recorded keeps its old ceiling.

        Without this, 1,000 -> 700 leaves the baseline at 1,000, and the next
        change can put 299 lines back and pass -- the growth this gate exists
        to stop, arriving in instalments.
        """
        self.assertEqual(self.check({"a.rs": 1000}, {"a.rs": 700}), 1)

    def test_a_recorded_reduction_passes(self) -> None:
        self.assertEqual(self.check({"a.rs": 700}, {"a.rs": 700}), 0)

    def test_falling_below_the_limit_must_also_be_recorded(self) -> None:
        self.assertEqual(self.check({"a.rs": 1000}, {}), 1)
        self.assertEqual(self.check({}, {}), 0)


if __name__ == "__main__":
    unittest.main()
