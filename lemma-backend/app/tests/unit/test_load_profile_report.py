"""The load profile's reading of a run, tested without needing a run.

`profile_run.py` is mostly orchestration — start samplers, run k6, stop samplers
— and orchestration is not worth mocking. What is worth testing is the part that
makes a judgement: separating a memory *floor* that climbed from a *peak* that
came back down, and finding the frame that blocked the loop. Those are the two
answers the whole exercise exists to produce, and both are pure functions over
files the run leaves behind.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "load_tests" / "profile_run.py"


def _module():
    spec = importlib.util.spec_from_file_location("profile_run", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stats_csv(path: Path, rows: list[tuple[str, str]]) -> None:
    lines = ["timestamp,name,cpu_percent,mem_usage,mem_percent"]
    for index, (name, mem) in enumerate(rows):
        lines.append(f"2026-08-15T12:00:{index:02d}Z,{name},10%,{mem},5%")
    path.write_text("\n".join(lines))


def test_a_spike_that_returns_is_not_growth(tmp_path: Path) -> None:
    """A document conversion pushes memory up and gives it back. Not a leak."""
    module = _module()
    _stats_csv(
        tmp_path / "docker_stats.csv",
        [
            ("lemma-load-api", m)
            for m in [
                "400MiB / 2GiB",
                "3.5GiB / 2GiB",
                "410MiB / 2GiB",
                "3.4GiB / 2GiB",
                "405MiB / 2GiB",
                "402MiB / 2GiB",
                "3.9GiB / 2GiB",
                "400MiB / 2GiB",
            ]
        ],
    )

    trend = module._memory_trend(tmp_path)["lemma-load-api"]

    assert trend["start_floor_mib"] == pytest.approx(400, abs=1)
    assert trend["end_floor_mib"] == pytest.approx(400, abs=1)
    assert trend["peak_mib"] > 3000, "the peak is still reported, just not as growth"


def test_a_climbing_floor_is_visible(tmp_path: Path) -> None:
    """The production shape: settles at 400, then 900, then 1400, and stays."""
    module = _module()
    _stats_csv(
        tmp_path / "docker_stats.csv",
        [
            ("lemma-load-api", m)
            for m in [
                "400MiB / 2GiB",
                "420MiB / 2GiB",
                "900MiB / 2GiB",
                "950MiB / 2GiB",
                "1.4GiB / 2GiB",
                "1.5GiB / 2GiB",
                "1.4GiB / 2GiB",
                "1.45GiB / 2GiB",
            ]
        ],
    )

    trend = module._memory_trend(tmp_path)["lemma-load-api"]

    assert trend["end_floor_mib"] > trend["start_floor_mib"] * 3


def test_other_containers_are_ignored(tmp_path: Path) -> None:
    """`docker stats` captures everything running; only api and worker matter."""
    module = _module()
    _stats_csv(
        tmp_path / "docker_stats.csv",
        [("some-postgres", "800MiB / 2GiB"), ("lemma-load-worker", "300MiB / 2GiB")],
    )

    trend = module._memory_trend(tmp_path)

    assert set(trend) == {"lemma-load-worker"}


def test_a_missing_stats_file_is_not_a_crash(tmp_path: Path) -> None:
    assert _module()._memory_trend(tmp_path) == {}


def test_the_blocking_frame_is_the_innermost_one(tmp_path: Path) -> None:
    """A stall stack is scaffolding at the top and the culprit at the bottom."""
    module = _module()
    events = [
        {
            "event": "runtime.loop_stall.degraded",
            "stack_frames": (
                '  File "/app/lemma-platform/lemma-backend/app/modules/x.py", line 1, in f\n'
                "    build()\n"
                '  File "/app/lemma-platform/lemma-backend/app/core/object_storage.py", '
                "line 21, in _gcs_store\n"
                "    return GCSStore(bucket=bucket)"
            ),
        }
    ]

    frames = module._stall_frames(events)

    assert frames.most_common(1)[0][0].startswith("core/object_storage.py")


def test_events_that_are_not_stalls_are_not_counted(tmp_path: Path) -> None:
    module = _module()

    assert module._stall_frames([{"event": "runtime.memory.degraded"}]) == {}


def test_the_summary_says_so_when_nothing_went_wrong(tmp_path: Path) -> None:
    """A clean run must read as clean, not as an empty report."""
    module = _module()
    (tmp_path / "k6-summary.json").write_text(json.dumps({"metrics": {}}))

    text = module._write_summary(tmp_path, [], 0)

    assert "no loop stalls" in text
    assert (tmp_path / "summary.md").exists()


def test_the_summary_names_the_culprit_when_there_was_one(tmp_path: Path) -> None:
    module = _module()
    events = [
        {
            "event": "runtime.loop_stall.degraded",
            "stack_frames": '  File "/x/app/core/object_storage.py", line 21, in _gcs_store',
        },
        {
            "event": "http.request.slow",
            "route": "/pods/{id}/files",
            "duration_ms": 5200,
        },
    ]

    text = module._write_summary(tmp_path, events, 0)

    assert "What blocked the loop" in text
    assert "object_storage.py" in text
    assert "/pods/{id}/files" in text
