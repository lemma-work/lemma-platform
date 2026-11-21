"""`workflow_graph_issues`: the create-time graph rules, asked ahead of time."""

from app.modules.workflow.contracts.graph_checks import workflow_graph_issues


def _function(node_id: str) -> dict[str, object]:
    return {"id": node_id, "type": "FUNCTION", "config": {"function_name": "f"}}


def _loop(node_id: str, body: str) -> dict[str, object]:
    return {
        "id": node_id,
        "type": "LOOP",
        "config": {"items_path": "start.items", "child_node_id": body},
    }


def _edge(source: str, target: str) -> dict[str, object]:
    return {"id": f"{source}-{target}", "source": source, "target": target}


def test_a_loop_whose_body_is_its_config_and_one_edge_after_is_accepted():
    nodes = [_loop("each", body="work"), _function("work"), _function("after")]

    assert workflow_graph_issues(nodes, [_edge("each", "after")]) == []


def test_a_loop_with_an_edge_to_its_body_as_well_as_after_is_refused():
    nodes = [_loop("each", body="work"), _function("work"), _function("after")]
    edges = [_edge("each", "work"), _edge("each", "after")]

    issues = workflow_graph_issues(nodes, edges)

    assert len(issues) == 1
    assert issues[0].startswith("loop 'each' has 2 outgoing edges; at most 1")
    assert "child_node_id ('work')" in issues[0]


def test_a_node_that_does_not_parse_is_named_by_position():
    nodes = [_function("ok"), {"id": "bad", "type": "FUNCTION", "config": {}}]

    issues = workflow_graph_issues(nodes, [])

    assert issues and all(issue.startswith("nodes[1]") for issue in issues)


def test_a_workflow_with_no_nodes_is_a_shell_and_accepted():
    assert workflow_graph_issues([], None) == []
    assert workflow_graph_issues(None, None) == []
