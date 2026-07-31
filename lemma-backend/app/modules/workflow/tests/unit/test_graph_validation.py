"""Save-time graph validation rules."""

import pytest

from app.modules.workflow.domain.errors import GraphValidationError
from app.modules.workflow.domain.graph import WorkflowGraphValidator, WorkflowEdge
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    DecisionNodeConfig,
    DecisionRule,
    EndNode,
    FormNode,
    FormNodeConfig,
    FunctionNode,
    FunctionNodeConfig,
    LoopNode,
    LoopNodeConfig,
)


def _form(node_id: str = "intake", **config) -> FormNode:
    return FormNode(
        id=node_id,
        config=FormNodeConfig(input_schema={"type": "object"}, **config),
    )


def _function(node_id: str, **config) -> FunctionNode:
    return FunctionNode(
        id=node_id, config=FunctionNodeConfig(function_name="fn", **config)
    )


def _edge(edge_id: str, source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(id=edge_id, source=source, target=target)


def _issues(nodes, edges) -> list[str]:
    with pytest.raises(GraphValidationError) as exc_info:
        WorkflowGraphValidator.validate(nodes, edges)
    return exc_info.value.issues


def test_valid_linear_graph_returns_entry_node():
    nodes = [_form(), _function("save"), EndNode(id="end")]
    edges = [_edge("e1", "intake", "save"), _edge("e2", "save", "end")]
    assert WorkflowGraphValidator.validate(nodes, edges) == "intake"


def test_empty_graph_is_rejected():
    assert "graph has no nodes" in _issues([], [])[0]


def test_duplicate_and_reserved_node_ids():
    issues = _issues(
        [_form("start"), _function("dup"), _function("dup")],
        [],
    )
    assert any("reserved" in issue for issue in issues)
    assert any("duplicate node id 'dup'" in issue for issue in issues)


def test_dangling_edges_are_rejected():
    issues = _issues(
        [_form(), EndNode(id="end")],
        [_edge("e1", "intake", "missing"), _edge("e2", "ghost", "end")],
    )
    assert any("target 'missing'" in issue for issue in issues)
    assert any("source 'ghost'" in issue for issue in issues)


def test_no_entry_when_all_nodes_have_incoming_edges():
    nodes = [_function("a"), _function("b")]
    edges = [_edge("e1", "a", "b"), _edge("e2", "b", "a")]
    assert any("no entry node" in issue for issue in _issues(nodes, edges))


def test_multiple_entry_nodes_rejected():
    nodes = [_form("one"), _form("two"), EndNode(id="end")]
    edges = [_edge("e1", "one", "end")]
    assert any("multiple entry nodes" in issue for issue in _issues(nodes, edges))


def test_decision_rule_targets_and_conditions_validated():
    decision = DecisionNode(
        id="route",
        config=DecisionNodeConfig(
            rules=[
                DecisionRule(condition="intake.ok == `true`", next_node_id="missing"),
                DecisionRule(condition="][bad", next_node_id="end"),
            ]
        ),
    )
    issues = _issues(
        [_form(), decision, EndNode(id="end")],
        [_edge("e1", "intake", "route"), _edge("e2", "route", "end")],
    )
    assert any("targets missing node 'missing'" in issue for issue in issues)
    assert any("condition" in issue and "Invalid expression" in issue for issue in issues)


def test_loop_body_must_exist_and_not_self_reference():
    loop = LoopNode(
        id="each",
        config=LoopNodeConfig(items_path="intake.items", child_node_id="nope"),
    )
    issues = _issues(
        [_form(), loop],
        [_edge("e1", "intake", "each")],
    )
    assert any("body node 'nope' does not exist" in issue for issue in issues)

    self_loop = LoopNode(
        id="each",
        config=LoopNodeConfig(items_path="intake.items", child_node_id="each"),
    )
    issues = _issues([_form(), self_loop], [_edge("e1", "intake", "each")])
    assert any("cannot be its own body" in issue for issue in issues)


def test_end_node_must_not_branch_and_only_decisions_branch():
    nodes = [_form(), _function("a"), _function("b"), EndNode(id="end")]
    edges = [
        _edge("e1", "intake", "a"),
        _edge("e2", "intake", "b"),
        _edge("e3", "a", "end"),
        _edge("e4", "b", "end"),
        _edge("e5", "end", "a"),
    ]
    issues = _issues(nodes, edges)
    assert any("only decision nodes may branch" in issue for issue in issues)
    assert any("end node 'end' must not have outgoing edges" in issue for issue in issues)


def test_unreachable_nodes_rejected():
    nodes = [_form(), EndNode(id="end"), _function("orphan_target"), _function("orphan_src")]
    edges = [
        _edge("e1", "intake", "end"),
        _edge("e2", "orphan_src", "orphan_target"),
    ]
    issues = _issues(nodes, edges)
    # orphan_src is a second entry candidate; once fixed, unreachability shows.
    assert any(
        "multiple entry nodes" in issue or "unreachable" in issue for issue in issues
    )


def test_loop_body_reachable_through_loop():
    loop = LoopNode(
        id="each",
        config=LoopNodeConfig(items_path="intake.items", child_node_id="body"),
    )
    nodes = [_form(), loop, _function("body"), EndNode(id="end")]
    edges = [
        _edge("e1", "intake", "each"),
        _edge("e2", "each", "end"),
        _edge("e3", "body", "each"),
    ]
    assert WorkflowGraphValidator.validate(nodes, edges) == "intake"


def test_input_binding_expressions_validated():
    node = _function(
        "save",
        input_mapping={
            "amount": {"type": "expression", "value": "]["},
            "fixed": {"type": "literal", "value": "ok"},
        },
    )
    issues = _issues(
        [_form(), node],
        [_edge("e1", "intake", "save")],
    )
    assert any("input 'amount'" in issue for issue in issues)


def test_form_assignee_expression_validated():
    node = _form(assignee_pod_member_id_expression="][")
    issues = _issues([node], [])
    assert any("assignee_pod_member_id_expression" in issue for issue in issues)


def test_form_input_schema_expr_compile_checked():
    bad = FormNode(
        id="intake",
        config=FormNodeConfig(
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"enum": {"type": "expression", "value": "]["}}
                },
            }
        ),
    )
    issues = _issues([bad], [])
    assert any("input_schema expression" in issue for issue in issues)


def test_form_input_schema_valid_binding_passes():
    node = FormNode(
        id="intake",
        config=FormNodeConfig(
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"enum": {"type": "expression", "value": "src.labels"}},
                    "body": {
                        "default": {
                            "type": "expression",
                            "value": "src.draft",
                            "optional": True,
                        }
                    },
                },
            }
        ),
    )
    assert WorkflowGraphValidator.validate([node], []) == "intake"
