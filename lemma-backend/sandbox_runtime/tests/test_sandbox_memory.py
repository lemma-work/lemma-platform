"""Reading a cgroup, including the parts that are easy to read wrongly.

The fixtures are shaped like real files: `memory.stat` is about forty lines
in no particular order and gains keys between kernel versions, `memory.max`
reads the word `max` when there is no limit, and `memory.pressure` is simply
absent on a kernel built without PSI -- measured absent on Docker Desktop's,
so nothing may depend on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_runtime.sandbox_memory import available_memory_mb, read_memory

pytestmark = pytest.mark.unit

_MB = 1024 * 1024


def _cgroup(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name.replace("__", ".")).write_text(text)
    return root


class TestTheNumbersThatMatter:
    def test_a_real_looking_stat_file_parses(self, tmp_path: Path) -> None:
        root = _cgroup(
            tmp_path,
            memory__max=str(2048 * _MB),
            memory__current=str(900 * _MB),
            memory__stat=(
                f"anon {400 * _MB}\n"
                f"file {500 * _MB}\n"
                "kernel_stack 1048576\n"
                f"shmem {10 * _MB}\n"
                f"unevictable {1 * _MB}\n"
                f"slab_unreclaimable {2 * _MB}\n"
                "workingset_refault_anon 0\n"
                "a_key_from_a_future_kernel 12345\n"
            ),
            memory__events="low 0\nhigh 0\nmax 614\noom 0\noom_kill 2\n",
        )

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.limit_mb == 2048
        assert memory.anon_mb == 400
        assert memory.shmem_mb == 10
        assert memory.oom_kill == 2

    def test_shmem_is_counted_once_and_file_is_not_counted_at_all(
        self, tmp_path: Path
    ) -> None:
        """In cgroup v2 `shmem` is a subset of `file`, so `anon + file` would
        count tmpfs twice -- and `file` is reclaimable anyway, which is the
        whole reason this is not `memory.current`."""
        root = _cgroup(
            tmp_path,
            memory__max=str(2048 * _MB),
            memory__stat=(
                f"anon {400 * _MB}\n"
                f"file {500 * _MB}\n"
                f"shmem {100 * _MB}\n"
                "unevictable 0\n"
                "slab_unreclaimable 0\n"
                f"inactive_file {400 * _MB}\n"
            ),
        )

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.unreclaimable_mb == 500, "anon 400 + shmem 100, and no file"
        assert memory.headroom_mb == 2048 - 500


class TestTheAbsences:
    def test_no_limit_reads_as_no_limit_not_as_zero(self, tmp_path: Path) -> None:
        """`memory.max` holds the literal word `max` on an unlimited cgroup.
        Parsed as 0 it would look like the tightest possible box."""
        root = _cgroup(tmp_path, memory__max="max\n", memory__stat=f"anon {10 * _MB}\n")

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.limit_mb is None
        assert memory.headroom_mb is None

    def test_a_kernel_without_psi_leaves_only_that_field_unknown(
        self, tmp_path: Path
    ) -> None:
        root = _cgroup(
            tmp_path, memory__max=str(2048 * _MB), memory__stat=f"anon {10 * _MB}\n"
        )

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.pressure_full_avg10 is None
        assert memory.limit_mb == 2048, "one missing file must not lose the others"

    def test_no_cgroup_at_all_is_all_unknown_and_no_crash(self, tmp_path: Path) -> None:
        memory = read_memory(root=tmp_path / "nothing", meminfo=tmp_path / "absent")

        assert memory.limit_mb is None
        assert memory.unreclaimable_mb is None
        assert memory.available_mb is None

    def test_a_malformed_stat_line_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        root = _cgroup(
            tmp_path,
            memory__max=str(2048 * _MB),
            memory__stat=f"anon {400 * _MB}\ngarbage\nshmem not_a_number\n",
        )

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.anon_mb == 400
        assert memory.shmem_mb is None


class TestPressure:
    def test_the_full_line_is_the_one_that_matters(self, tmp_path: Path) -> None:
        """`some` is "at least one task stalled"; `full` is "every task
        was", which is what a sandbox that cannot run `python -c pass`
        looks like."""
        root = _cgroup(
            tmp_path,
            memory__max=str(2048 * _MB),
            memory__pressure=(
                "some avg10=71.11 avg60=20.00 avg300=4.00 total=1\n"
                "full avg10=12.34 avg60=3.00 avg300=1.00 total=2\n"
            ),
        )

        memory = read_memory(root=root, meminfo=tmp_path / "absent")

        assert memory.pressure_full_avg10 == pytest.approx(12.34)


class TestTheHostReading:
    def test_mem_available_not_mem_free(self, tmp_path: Path) -> None:
        """Reclaimable page cache is not pressure; treating it as pressure
        would shed a browser on a sandbox that merely read a large file."""
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(
            "MemTotal:        9214732 kB\n"
            "MemFree:           64280 kB\n"
            "MemAvailable:    1520000 kB\n"
        )

        assert available_memory_mb(meminfo) == 1484

    def test_a_missing_meminfo_is_unknown(self, tmp_path: Path) -> None:
        assert available_memory_mb(tmp_path / "nope") is None
