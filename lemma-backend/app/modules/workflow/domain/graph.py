"""Workflow graph edges and save-time validation.

Validation runs whenever a graph is stored — a flow that saves successfully
can always be executed without graph-shape surprises at run time.
"""

from itertools import chain
from typing import List

from pydantic import BaseModel, ValidationError

from app.modules.workflow.domain.errors import (
    ExpressionSyntaxError,
    GraphValidationError,
)
from app.modules.workflow.domain.expressions import ExpressionEngine
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    ExpressionInputBinding,
    FormNode,
    LoopNode,
    NodeType,
    RESERVED_NODE_IDS,
    WorkflowNode,
)
from app.modules.workflow.domain.schema_template import iter_schema_expressions


class WorkflowEdge(BaseModel):
    """An edge in the workflow graph."""

    id: str
    source: str
    target: str
    label: str | None = None


class WorkflowGraphValidator:
    """Structural and expression validation for a workflow graph."""

    @classmethod
    def validate(
        cls,
        nodes: List[WorkflowNode],
        edges: List[WorkflowEdge],
    ) -> str:
        """Validate the graph and return the entry node id.

        Raises GraphValidationError listing every issue found.
        """
        issues: list[str] = []
        node_ids = [node.id for node in nodes]
        node_by_id = {node.id: node for node in nodes}

        if not nodes:
            raise GraphValidationError(["graph has no nodes"])

        # Unique, non-reserved node ids
        seen: set[str] = set()
        for node_id in node_ids:
            if node_id in seen:
                issues.append(f"duplicate node id '{node_id}'")
            seen.add(node_id)
            if node_id in RESERVED_NODE_IDS:
                issues.append(
                    f"node id '{node_id}' is reserved for the context namespace"
                )

        # Edges reference existing nodes
        for edge in edges:
            if edge.source not in node_by_id:
                issues.append(f"edge '{edge.id}' source '{edge.source}' does not exist")
            if edge.target not in node_by_id:
                issues.append(f"edge '{edge.id}' target '{edge.target}' does not exist")

        outgoing: dict[str, list[WorkflowEdge]] = {}
        for edge in edges:
            outgoing.setdefault(edge.source, []).append(edge)

        loop_body_ids = {
            node.config.child_node_id
            for node in nodes
            if isinstance(node, LoopNode)
        }

        # Per-node-type rules
        for node in nodes:
            node_edges = outgoing.get(node.id, [])
            if isinstance(node, DecisionNode):
                for rule in node.config.rules:
                    if rule.next_node_id not in node_by_id:
                        issues.append(
                            f"decision '{node.id}' rule targets missing node "
                            f"'{rule.next_node_id}'"
                        )
                    issues.extend(
                        cls._expression_issues(node.id, "condition", rule.condition)
                    )
            elif isinstance(node, LoopNode):
                if node.config.child_node_id not in node_by_id:
                    issues.append(
                        f"loop '{node.id}' body node '{node.config.child_node_id}' "
                        "does not exist"
                    )
                elif node.config.child_node_id == node.id:
                    issues.append(f"loop '{node.id}' cannot be its own body")
                issues.extend(
                    cls._expression_issues(node.id, "items_path", node.config.items_path)
                )
                if len(node_edges) > 1:
                    issues.append(
                        f"loop '{node.id}' has {len(node_edges)} outgoing edges; at most 1"
                    )
            elif node.type == NodeType.END:
                if node_edges:
                    issues.append(f"end node '{node.id}' must not have outgoing edges")
            else:
                if len(node_edges) > 1:
                    issues.append(
                        f"node '{node.id}' has {len(node_edges)} outgoing edges; only "
                        "decision nodes may branch"
                    )
            if isinstance(node, FormNode):
                if node.config.assignee_pod_member_id_expression:
                    issues.extend(
                        cls._expression_issues(
                            node.id,
                            "assignee_pod_member_id_expression",
                            node.config.assignee_pod_member_id_expression,
                        )
                    )
                # input_schema/ui_schema may embed typed input bindings
                # ({"type": "expression", "value": "..."}) resolved against the
                # run context at suspend time. Validate their shape and
                # compile-check each expression, like any other binding.
                try:
                    schema_exprs = list(
                        chain(
                            iter_schema_expressions(node.config.input_schema),
                            iter_schema_expressions(node.config.ui_schema),
                        )
                    )
                except ValidationError:
                    issues.append(
                        f"node '{node.id}' has an invalid input binding in its "
                        "input_schema/ui_schema"
                    )
                    schema_exprs = []
                for expr in schema_exprs:
                    issues.extend(
                        cls._expression_issues(node.id, "input_schema expression", expr)
                    )
            input_mapping = getattr(node.config, "input_mapping", None)
            if input_mapping:
                for key, binding in input_mapping.items():
                    if isinstance(binding, ExpressionInputBinding):
                        issues.extend(
                            cls._expression_issues(
                                node.id, f"input '{key}'", binding.value
                            )
                        )

        # Exactly one entry node: nothing points at it — no incoming edge, no
        # decision rule, not a loop body.
        incoming = {edge.target for edge in edges}
        for node in nodes:
            if isinstance(node, DecisionNode):
                incoming.update(rule.next_node_id for rule in node.config.rules)
        entry_candidates = [
            node.id
            for node in nodes
            if node.id not in incoming and node.id not in loop_body_ids
        ]
        if len(entry_candidates) == 0:
            issues.append("graph has no entry node (every node has incoming edges)")
        elif len(entry_candidates) > 1:
            issues.append(
                "graph has multiple entry nodes: " + ", ".join(sorted(entry_candidates))
            )

        # Reachability from the entry node
        if len(entry_candidates) == 1 and not issues:
            reachable = cls._reachable(entry_candidates[0], node_by_id, outgoing)
            unreachable = sorted(set(node_ids) - reachable)
            if unreachable:
                issues.append("unreachable nodes: " + ", ".join(unreachable))

        if issues:
            raise GraphValidationError(issues)
        return entry_candidates[0]

    @staticmethod
    def _expression_issues(node_id: str, field: str, expression: str) -> list[str]:
        try:
            ExpressionEngine.compile(expression)
        except ExpressionSyntaxError as exc:
            return [f"node '{node_id}' {field}: {exc.message}"]
        return []

    @staticmethod
    def _reachable(
        entry_id: str,
        node_by_id: dict[str, WorkflowNode],
        outgoing: dict[str, list[WorkflowEdge]],
    ) -> set[str]:
        stack = [entry_id]
        reachable: set[str] = set()
        while stack:
            node_id = stack.pop()
            if node_id in reachable or node_id not in node_by_id:
                continue
            reachable.add(node_id)
            node = node_by_id[node_id]
            for edge in outgoing.get(node_id, []):
                stack.append(edge.target)
            if isinstance(node, DecisionNode):
                stack.extend(rule.next_node_id for rule in node.config.rules)
            if isinstance(node, LoopNode):
                stack.append(node.config.child_node_id)
        return reachable
