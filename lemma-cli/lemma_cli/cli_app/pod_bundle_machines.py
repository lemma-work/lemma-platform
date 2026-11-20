"""A bundle's machines against what the target pod's plan offers, before import.

`workspace-docker` is a valid template name that only some deployments can
build, and a plan caps sizes; the bundle cannot know either. Asked of
`GET /machines:policy` before anything is written, so an import that would
fail at its machines fails before its first step instead of partway through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lemma_pod_bundle.layout import load_resource_payload
from lemma_pod_bundle.normalize import BundleValidationIssue
from lemma_sdk import Lemma
from lemma_sdk.errors import LemmaAPIError

from ..cli_core.io import to_plain


def offered_sizes(policy: Any) -> dict[str, list[str]]:
    """``template -> sizes`` the policy offers; a template it omits is not offered."""
    plain = to_plain(policy) or {}
    return {
        str(offer.get("template")): [str(size) for size in offer.get("sizes") or []]
        for offer in plain.get("templates") or []
    }


def machine_policy_issues(
    offered: dict[str, list[str]],
    machine_dirs: list[Path],
    existing: dict[str, dict[str, Any]],
) -> list[BundleValidationIssue]:
    """Why the target would refuse each bundled machine, as the import asks:
    an existing machine keeps its template, so only a size it changes to is
    checked; a new one without a size gets the plan's default."""
    issues: list[BundleValidationIssue] = []
    for machine_dir in machine_dirs:
        name = machine_dir.name
        payload = load_resource_payload(machine_dir, name, resource_type="machines")
        current = existing.get(name)
        template = str(
            (current or {}).get("template") or payload.get("template") or "workspace"
        )
        size = str(payload.get("size") or "").strip().lower() or None
        if current is not None and (size is None or size == current.get("size")):
            continue
        sizes = offered.get(template)
        if sizes is None:
            message = (
                f"Machine '{name}': this pod's plan does not offer {template} "
                f"machines here (it offers {', '.join(sorted(offered)) or 'none'})."
            )
        elif size is not None and size not in sizes:
            message = (
                f"Machine '{name}': {template} machines here come in "
                f"{', '.join(sizes)}, not {size}."
            )
        else:
            continue
        issues.append(BundleValidationIssue(path=str(machine_dir), message=message))
    return issues


def check_machines_against_policy(
    client: Lemma,
    pod_id: str,
    machine_dirs: list[Path],
    existing: dict[str, dict[str, Any]],
) -> tuple[list[BundleValidationIssue], list[str]]:
    """``(issues, advisories)``: refusals found, or a note that it could not ask."""
    if not machine_dirs:
        return [], []
    try:
        policy = client.machines.policy(pod_id=pod_id)
    except LemmaAPIError as exc:
        advisory = (
            f"Could not read this pod's machine policy ({exc}); the import "
            "checks each machine as it creates it."
        )
        return [], [advisory]
    return machine_policy_issues(offered_sizes(policy), machine_dirs, existing), []


__all__ = [
    "check_machines_against_policy",
    "machine_policy_issues",
    "offered_sizes",
]
