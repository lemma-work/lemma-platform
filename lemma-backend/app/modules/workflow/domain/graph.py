"""Workflow graph edges and save-time validation.

Validation runs whenever a graph is stored — a flow that saves successfully
can always be executed without graph-shape surprises at run time.
"""

from collections.abc import Collection
from itertools import chain
from typing import List

from pydantic import BaseModel, ValidationError

from app.modules.workflow.domain.decision_questions import question_issues
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
        if not nodes:
            raise GraphValidationError(["graph has no nodes"])
        node_by_id = {node.id: node for node in nodes}
        outgoing: dict[str, list[WorkflowEdge]] = {}
        for edge in edges:
            outgoing.setdefault(edge.source, []).append(edge)

        issues = [*cls._id_issues(nodes), *cls._edge_issues(edges, node_by_id)]
        for node in nodes:
            issues.extend(cls._node_issues(node, node_by_id, outgoing.get(node.id, [])))

        entry_candidates = cls._entry_candidates(nodes, edges)
        if len(entry_candidates) == 0:
            issues.append("graph has no entry node (every node has incoming edges)")
        elif len(entry_candidates) > 1:
            issues.append(
                "graph has multiple entry nodes: " + ", ".join(sorted(entry_candidates))
            )

        # Reachability from the entry node
        if len(entry_candidates) == 1 and not issues:
            reachable = cls._reachable(entry_candidates[0], node_by_id, outgoing)
            unreachable = sorted(set(node_by_id) - reachable)
            if unreachable:
                issues.append("unreachable nodes: " + ", ".join(unreachable))

        if issues:
            raise GraphValidationError(issues)
        return entry_candidates[0]

    @staticmethod
    def _id_issues(nodes: List[WorkflowNode]) -> list[str]:
        """Unique, non-reserved node ids."""
        issues: list[str] = []
        seen: set[str] = set()
        for node in nodes:
            if node.id in seen:
                issues.append(f"duplicate node id '{node.id}'")
            seen.add(node.id)
            if node.id in RESERVED_NODE_IDS:
                issues.append(
                    f"node id '{node.id}' is reserved for the context namespace"
                )
        return issues

    @staticmethod
    def _edge_issues(
        edges: List[WorkflowEdge], node_by_id: dict[str, WorkflowNode]
    ) -> list[str]:
        issues: list[str] = []
        for edge in edges:
            if edge.source not in node_by_id:
                issues.append(f"edge '{edge.id}' source '{edge.source}' does not exist")
            if edge.target not in node_by_id:
                issues.append(f"edge '{edge.id}' target '{edge.target}' does not exist")
        return issues

    @classmethod
    def _node_issues(
        cls,
        node: WorkflowNode,
        node_by_id: dict[str, WorkflowNode],
        node_edges: list[WorkflowEdge],
    ) -> list[str]:
        """Per-node-type rules, then the input bindings any node may carry."""
        if isinstance(node, DecisionNode):
            issues = cls._decision_issues(node, node_by_id, node_edges)
        elif isinstance(node, LoopNode):
            issues = cls._loop_issues(node, node_by_id, node_edges)
        elif node.type == NodeType.END:
            issues = (
                [f"end node '{node.id}' must not have outgoing edges"]
                if node_edges
                else []
            )
        elif len(node_edges) > 1:
            issues = [
                (
                    f"node '{node.id}' has {len(node_edges)} outgoing edges; only "
                    "decision nodes may branch"
                )
            ]
        else:
            issues = []
        if isinstance(node, FormNode):
            issues.extend(cls._form_issues(node))
        input_mapping = getattr(node.config, "input_mapping", None) or {}
        for key, binding in input_mapping.items():
            if isinstance(binding, ExpressionInputBinding):
                issues.extend(
                    cls._expression_issues(node.id, f"input '{key}'", binding.value)
                )
        return issues

    @classmethod
    def _decision_issues(
        cls,
        node: DecisionNode,
        node_by_id: Collection[str],
        node_edges: list[WorkflowEdge],
    ) -> list[str]:
        issues: list[str] = []
        question = node.config.question
        if question is not None and node.config.rules:
            issues.append(
                f"decision '{node.id}' has both rules and a question; it routes "
                "on one or the other"
            )
        for rule in node.config.rules:
            if rule.next_node_id not in node_by_id:
                issues.append(
                    f"decision '{node.id}' rule targets missing node "
                    f"'{rule.next_node_id}'"
                )
            issues.extend(cls._expression_issues(node.id, "condition", rule.condition))
        if question is not None:
            issues.extend(
                question_issues(
                    node.id,
                    question,
                    node_ids=node_by_id,
                    has_default_edge=bool(node_edges),
                )
            )
            if isinstance(question.evidence, ExpressionInputBinding):
                issues.extend(
                    cls._expression_issues(
                        node.id, "question evidence", question.evidence.value
                    )
                )
        return issues

    @classmethod
    def _loop_issues(
        cls,
        node: LoopNode,
        node_by_id: Collection[str],
        node_edges: list[WorkflowEdge],
    ) -> list[str]:
        issues: list[str] = []
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
        return issues

    @classmethod
    def _form_issues(cls, node: FormNode) -> list[str]:
        issues: list[str] = []
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
            return [
                *issues,
                (
                    f"node '{node.id}' has an invalid input binding in its "
                    "input_schema/ui_schema"
                ),
            ]
        for expr in schema_exprs:
            issues.extend(
                cls._expression_issues(node.id, "input_schema expression", expr)
            )
        return issues

    @staticmethod
    def _entry_candidates(
        nodes: List[WorkflowNode], edges: List[WorkflowEdge]
    ) -> list[str]:
        """Nodes nothing points at: no incoming edge, no decision target, not a
        loop body. Exactly one is the entry."""
        incoming = {edge.target for edge in edges}
        loop_body_ids: set[str] = set()
        for node in nodes:
            if isinstance(node, DecisionNode):
                incoming.update(node.config.targets())
            elif isinstance(node, LoopNode):
                loop_body_ids.add(node.config.child_node_id)
        return [
            node.id
            for node in nodes
            if node.id not in incoming and node.id not in loop_body_ids
        ]

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
                stack.extend(node.config.targets())
            if isinstance(node, LoopNode):
                stack.append(node.config.child_node_id)
        return reachable
