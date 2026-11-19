"""How many of a bundle's new schedules start each of its new workflows.

The platform keeps a GLOBAL workflow to one schedule per person in a pod, and
refuses the second when it is created. An import creates schedules last, so a
bundle with two schedules on one such workflow failed after its tables,
functions, agents, workflows and the first schedule had already landed. Both
importers ask this first, so the bundle is refused before anything is written.

Only what the import will create is counted:

- a workflow's mode is what the importer would create it with: a manifest
  with no ``mode`` is created USER by the server's import and GLOBAL through
  the API (lemma-cli), so the caller says which (``default_mode``);
- a workflow the pod already has keeps the pod's own mode, which the platform
  checks when the schedule is created;
- a schedule the pod already has, a one-time schedule already past (both
  importers leave those out), or one the caller knows the import will leave
  out (``left_out``: the server's plan skips a schedule naming an account the
  importer cannot use) creates nothing. A schedule whose account is an import
  variable not yet given is counted: the variable may be given on apply.

A schedule or workflow is the one its directory names, as both importers
create and look it up, whatever its manifest's ``name`` says; names are
compared as the schedule service compares them: stripped and lowercased.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

from .apply_fields import is_past_one_time_schedule
from .layout import load_resource_payload


def _key(name: object) -> str:
    return str(name or "").strip().lower()


def _manifests(bundle_root: Path, kind: str) -> list[tuple[str, dict[str, Any]]]:
    root = bundle_root / kind
    if not root.is_dir():
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    for resource_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            payload = load_resource_payload(
                resource_dir, resource_dir.name, resource_type=kind
            )
        except OSError, ValueError:
            continue  # the importer reports a manifest it cannot read
        found.append((resource_dir.name, payload))
    return found


def global_workflows_scheduled_twice(
    bundle_root: Path,
    *,
    default_mode: str,
    existing_schedules: Collection[str] = (),
    existing_workflows: Collection[str] = (),
    left_out: Collection[str] = (),
) -> dict[str, list[str]]:
    """New GLOBAL workflows that more than one new schedule starts, each with
    the names of those schedules."""
    pod_workflows = {_key(name) for name in existing_workflows}
    global_workflows = {
        _key(name)
        for name, payload in _manifests(bundle_root, "workflows")
        if _key(name) not in pod_workflows
        and str(payload.get("mode") or default_mode).upper() == "GLOBAL"
    }
    pod_schedules = {_key(name) for name in (*existing_schedules, *left_out)}
    starts: dict[str, list[str]] = {}
    for name, payload in _manifests(bundle_root, "schedules"):
        workflow = _key(payload.get("workflow_name"))
        if (
            workflow in global_workflows
            and _key(name) not in pod_schedules
            and not is_past_one_time_schedule(payload)
        ):
            starts.setdefault(workflow, []).append(name)
    return {
        workflow: schedules
        for workflow, schedules in starts.items()
        if len(schedules) > 1
    }


def schedules_sharing_a_global_workflow(
    bundle_root: Path,
    *,
    default_mode: str,
    existing_schedules: Collection[str] = (),
    existing_workflows: Collection[str] = (),
    left_out: Collection[str] = (),
) -> str | None:
    """Why the import would fail at its second schedule on one workflow, or
    ``None`` when it would not."""
    twice = global_workflows_scheduled_twice(
        bundle_root,
        default_mode=default_mode,
        existing_schedules=existing_schedules,
        existing_workflows=existing_workflows,
        left_out=left_out,
    )
    if not twice:
        return None
    named = "; ".join(
        f"workflow '{workflow}' by {', '.join(repr(s) for s in schedules)}"
        for workflow, schedules in sorted(twice.items())
    )
    return (
        "A workflow can have one schedule per person, and this bundle gives "
        f"some more than one: {named}. Give each schedule its own workflow "
        "(a small one can start the shared work)."
    )


__all__ = [
    "global_workflows_scheduled_twice",
    "schedules_sharing_a_global_workflow",
]
