"""Two bundled schedules on one GLOBAL workflow are found before an import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_pod_bundle import (
    check_one_schedule_per_global_workflow,
    global_workflows_scheduled_twice,
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
        ("every_morning", "triage"),
        ("weekly", "digest"),
        ("elsewhere", "a_pod_workflow"),
        ("also_elsewhere", "a_pod_workflow"),
    ):
        _write(
            root / "schedules" / name / f"{name}.json",
            {"name": name, "schedule_type": "TIME", "workflow_name": target},
        )
    return root


def test_a_global_workflow_two_schedules_start_is_named(tmp_path: Path):
    root = _bundle(tmp_path)

    assert global_workflows_scheduled_twice(root) == {
        "triage": ["every_morning", "on_ticket"]
    }
    with pytest.raises(ValueError, match="workflow 'triage' by 'every_morning'"):
        check_one_schedule_per_global_workflow(root)


def test_a_user_workflow_and_a_bundle_without_schedules_pass(tmp_path: Path):
    check_one_schedule_per_global_workflow(_bundle(tmp_path / "a", mode="USER"))
    check_one_schedule_per_global_workflow(tmp_path / "empty")
