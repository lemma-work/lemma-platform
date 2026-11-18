"""Two new schedules on one new GLOBAL workflow are found before an import."""

from __future__ import annotations

import json
from pathlib import Path

from lemma_pod_bundle import (
    global_workflows_scheduled_twice,
    schedules_sharing_a_global_workflow,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(root: Path, *, mode: str | None = None) -> Path:
    workflow: dict[str, object] = {"name": "triage"}
    if mode is not None:
        workflow["mode"] = mode
    _write(root / "workflows" / "triage" / "triage.json", workflow)
    _write(root / "workflows" / "digest" / "digest.json", {"name": "digest"})
    for name, target in (
        ("on_ticket", "triage"),
        ("every_morning", "Triage"),
        ("weekly", "digest"),
        ("elsewhere", "a_pod_workflow"),
        ("also_elsewhere", "a_pod_workflow"),
    ):
        _write(
            root / "schedules" / name / f"{name}.json",
            {
                "name": name,
                "schedule_type": "TIME",
                "config": {"cron": "0 9 * * *"},
                "workflow_name": target,
            },
        )
    return root


def test_a_global_workflow_two_new_schedules_start_is_named(tmp_path: Path):
    root = _bundle(tmp_path, mode="GLOBAL")

    # Names compare as the schedule service compares them.
    assert global_workflows_scheduled_twice(root, default_mode="USER") == {
        "triage": ["every_morning", "on_ticket"]
    }
    clash = schedules_sharing_a_global_workflow(root, default_mode="USER")
    assert clash is not None and "workflow 'triage' by 'every_morning'" in clash


def test_a_workflow_with_no_mode_counts_as_the_importer_creates_it(tmp_path: Path):
    """The server's import creates it USER; the API, which lemma-cli uses,
    creates it GLOBAL."""
    root = _bundle(tmp_path)

    assert schedules_sharing_a_global_workflow(root, default_mode="USER") is None
    assert schedules_sharing_a_global_workflow(root, default_mode="GLOBAL")


def test_what_the_pod_already_has_is_not_counted(tmp_path: Path):
    root = _bundle(tmp_path, mode="GLOBAL")

    assert (
        schedules_sharing_a_global_workflow(
            root, default_mode="GLOBAL", existing_schedules={"on_ticket"}
        )
        is None
    )
    assert (
        schedules_sharing_a_global_workflow(
            root, default_mode="GLOBAL", existing_workflows={"triage"}
        )
        is None
    )


def test_a_one_time_schedule_already_past_is_not_counted(tmp_path: Path):
    root = _bundle(tmp_path, mode="GLOBAL")
    _write(
        root / "schedules" / "every_morning" / "every_morning.json",
        {
            "name": "every_morning",
            "schedule_type": "TIME",
            "config": {"scheduled_at": "2020-01-01T09:00:00Z"},
            "workflow_name": "triage",
        },
    )

    assert schedules_sharing_a_global_workflow(root, default_mode="GLOBAL") is None
