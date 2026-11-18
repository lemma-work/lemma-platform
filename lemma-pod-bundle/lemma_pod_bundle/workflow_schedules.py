"""How many of a bundle's schedules start each of its workflows.

The platform keeps a GLOBAL workflow -- every workflow, unless its manifest
says ``"mode": "USER"`` -- to one schedule per person in a pod, and refuses
the second when it is created. An import creates schedules last, so a bundle
with two schedules on one such workflow failed after its tables, functions,
agents, workflows and the first schedule had already landed. Both importers
ask this first, so the bundle is refused before anything is written.

Only the bundle's own workflows are judged: a schedule naming one the pod
already has is the platform's to check, with the pod's own mode for it.
"""

from __future__ import annotations

from pathlib import Path

from .layout import load_resource_payload


def _bundled_global_workflows(bundle_root: Path) -> set[str]:
    root = bundle_root / "workflows"
    if not root.is_dir():
        return set()
    names: set[str] = set()
    for resource_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            payload = load_resource_payload(
                resource_dir, resource_dir.name, resource_type="workflows"
            )
        except OSError, ValueError:
            continue  # the importer reports a manifest it cannot read
        if str(payload.get("mode") or "GLOBAL").upper() == "GLOBAL":
            names.add(str(payload.get("name") or resource_dir.name))
    return names


def global_workflows_scheduled_twice(bundle_root: Path) -> dict[str, list[str]]:
    """Bundled GLOBAL workflows more than one bundled schedule starts, each
    with the names of those schedules."""
    root = bundle_root / "schedules"
    if not root.is_dir():
        return {}
    global_workflows = _bundled_global_workflows(bundle_root)
    starts: dict[str, list[str]] = {}
    for resource_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            payload = load_resource_payload(
                resource_dir, resource_dir.name, resource_type="schedules"
            )
        except OSError, ValueError:
            continue
        workflow = payload.get("workflow_name")
        if isinstance(workflow, str) and workflow in global_workflows:
            name = str(payload.get("name") or resource_dir.name)
            starts.setdefault(workflow, []).append(name)
    return {
        workflow: schedules
        for workflow, schedules in starts.items()
        if len(schedules) > 1
    }


def check_one_schedule_per_global_workflow(bundle_root: Path) -> None:
    """Raise ``ValueError`` naming each bundled GLOBAL workflow that more than
    one bundled schedule starts, and how to fix it."""
    twice = global_workflows_scheduled_twice(bundle_root)
    if not twice:
        return
    named = "; ".join(
        f"workflow '{workflow}' by {', '.join(repr(s) for s in schedules)}"
        for workflow, schedules in sorted(twice.items())
    )
    raise ValueError(
        "A workflow can have one schedule per person, and this bundle gives "
        f"some more than one: {named}. Give each schedule its own workflow "
        "(a small one can start the shared work)."
    )


__all__ = [
    "check_one_schedule_per_global_workflow",
    "global_workflows_scheduled_twice",
]
