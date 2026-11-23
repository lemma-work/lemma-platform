"""Refuse, at plan time, a bundled workflow graph its create would refuse.

The WORKFLOW step runs after the tables, functions and agents, so a graph the
workflow module rejects -- a LOOP with two outgoing edges was the one reported
-- failed the import part-way, with everything before it already written. The
rules are the workflow module's own, reached through its contract; nothing is
re-implemented here.

``${var}`` placeholders are not resolved until apply, so the graph is judged
with each variable's default, and a placeholder with none stands in as a plain
identifier: it may sit in a node id or an expression, and either way it has to
parse once it is filled in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from lemma_pod_bundle import load_resource_payload

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.infrastructure.plan_variables import (
    variables_from_manifest,
)

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def _fill(value: object, defaults: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda m: defaults.get(m.group(1), f"_var_{m.group(1)}"), value
        )
    if isinstance(value, dict):
        return {key: _fill(item, defaults) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, defaults) for item in value]
    return value


def check_workflow_graphs(
    bundle_root: Path, pod_manifest: Mapping[str, object]
) -> None:
    """Raise :class:`BundleInvalidError` naming every bundled workflow whose
    graph would be refused, with each reason."""
    from app.modules.workflow.contracts.graph_checks import workflow_graph_issues

    root = bundle_root / "workflows"
    if not root.is_dir():
        return
    defaults = {
        spec.name: spec.default
        for spec in variables_from_manifest(pod_manifest)
        if spec.default is not None
    }
    refused: list[str] = []
    for resource_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        payload = load_resource_payload(
            resource_dir, resource_dir.name, resource_type="workflows"
        )
        filled = _fill(payload, defaults)
        if not isinstance(filled, dict):
            continue  # a manifest is a mapping; the step reports one that is not
        nodes, edges = filled.get("nodes"), filled.get("edges")
        issues = workflow_graph_issues(
            nodes if isinstance(nodes, list) else None,
            edges if isinstance(edges, list) else None,
        )
        if issues:
            refused.append(f"'{resource_dir.name}': " + "; ".join(issues))
    if refused:
        raise BundleInvalidError(
            "This bundle has workflows that cannot be created, so nothing was "
            "imported. Workflow " + ". Workflow ".join(refused) + "."
        )


__all__ = ["check_workflow_graphs"]
