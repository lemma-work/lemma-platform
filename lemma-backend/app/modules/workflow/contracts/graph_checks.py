"""Whether a workflow graph would be accepted, asked before it is created.

A bundle import plans every resource and then writes them one at a time. A
graph the workflow service refuses -- a LOOP with two outgoing edges, say --
used to plan fine and then fail when its WORKFLOW step ran, after the tables,
functions and agents before it had already been written. The importer asks
here first, so the same rules (`WorkflowGraphValidator`, the node models) judge
the graph at plan time as at create time, and there is one copy of them.

A submodule rather than `contracts/__init__`, like every other contract here.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from app.modules.workflow.domain.errors import GraphValidationError
from app.modules.workflow.domain.graph import WorkflowEdge, WorkflowGraphValidator
from app.modules.workflow.domain.nodes import WORKFLOW_NODE_ADAPTER, WorkflowNode


def _shape_issues(where: str, exc: ValidationError) -> list[str]:
    return [
        f"{where}{''.join(f'[{p}]' for p in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]


def workflow_graph_issues(
    nodes: Sequence[object] | None, edges: Sequence[object] | None
) -> list[str]:
    """Every reason `create_workflow` would refuse this graph; empty when it
    would accept it. A workflow with no nodes is a shell, which is accepted.

    ``nodes`` and ``edges`` are as a manifest or request carries them: plain
    mappings, checked against the node and edge models before the graph is.
    """
    if not nodes:
        return []
    issues: list[str] = []
    parsed_nodes: list[WorkflowNode] = []
    for index, raw in enumerate(nodes):
        try:
            parsed_nodes.append(WORKFLOW_NODE_ADAPTER.validate_python(raw))
        except ValidationError as exc:
            issues.extend(_shape_issues(f"nodes[{index}]", exc))
    parsed_edges: list[WorkflowEdge] = []
    for index, raw in enumerate(edges or []):
        try:
            parsed_edges.append(WorkflowEdge.model_validate(raw))
        except ValidationError as exc:
            issues.extend(_shape_issues(f"edges[{index}]", exc))
    if issues:
        # The graph rules read node types and ids; a node that did not parse
        # would only add noise about edges pointing at it.
        return issues
    try:
        WorkflowGraphValidator.validate(parsed_nodes, parsed_edges)
    except GraphValidationError as exc:
        return list(exc.issues)
    return []


__all__ = ["workflow_graph_issues"]
