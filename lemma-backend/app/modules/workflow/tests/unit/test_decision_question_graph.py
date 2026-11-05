"""Saving a decision node that asks a question: what is refused, and why."""

from __future__ import annotations

import pytest

from app.modules.workflow.domain.errors import GraphValidationError
from app.modules.workflow.domain.graph import WorkflowEdge, WorkflowGraphValidator
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    DecisionNodeConfig,
    DecisionQuestion,
    DecisionRule,
    EndNode,
    FunctionNode,
    FunctionNodeConfig,
)

pytestmark = pytest.mark.unit

YES_OR_NO = {"type": "boolean", "description": "Is this a refund request?"}
CATEGORY = {
    "type": "string",
    "enum": ["billing", "bug", "other"],
    "description": "What is this email about?",
}


def _question(
    answer: dict[str, object] = YES_OR_NO, **fields: object
) -> DecisionQuestion:
    return DecisionQuestion.model_validate(
        {
            "instruction": "Triage incoming support email.",
            "evidence": {"type": "expression", "value": "start.payload.email"},
            "answer": answer,
            **fields,
        }
    )


def _graph(
    question: DecisionQuestion, *, default_edge: bool = False, **config: object
) -> tuple[list, list[WorkflowEdge]]:
    nodes = [
        DecisionNode(
            id="triage", config=DecisionNodeConfig(question=question, **config)
        ),
        FunctionNode(id="refund", config=FunctionNodeConfig(function_name="refund")),
        FunctionNode(id="reply", config=FunctionNodeConfig(function_name="reply")),
        EndNode(id="done"),
    ]
    edges = [
        WorkflowEdge(id="e1", source="refund", target="done"),
        WorkflowEdge(id="e2", source="reply", target="done"),
    ]
    if default_edge:
        edges.append(WorkflowEdge(id="e3", source="triage", target="reply"))
    return nodes, edges


def _issues(question: DecisionQuestion, **kwargs: object) -> list[str]:
    nodes, edges = _graph(question, **kwargs)
    with pytest.raises(GraphValidationError) as raised:
        WorkflowGraphValidator.validate(nodes, edges)
    return raised.value.issues


def test_a_fully_routed_question_saves_and_its_routes_count_as_incoming():
    question = _question(
        routes={"true": "refund", "false": "reply"}, unsure_next_node_id="reply"
    )
    nodes, edges = _graph(question)

    assert WorkflowGraphValidator.validate(nodes, edges) == "triage"


def test_a_default_edge_stands_in_for_unrouted_answers_and_unsure():
    question = _question(answer=CATEGORY, routes={"billing": "refund"})
    nodes, edges = _graph(question, default_edge=True)

    assert WorkflowGraphValidator.validate(nodes, edges) == "triage"


def test_an_answer_with_nowhere_to_go_is_refused_naming_it():
    issues = _issues(_question(answer=CATEGORY, routes={"billing": "refund"}))

    assert any("no route for bug, other" in issue for issue in issues), issues
    assert any("no route for an unsure answer" in issue for issue in issues), issues


def test_a_route_for_something_that_is_not_an_answer_is_refused():
    issues = _issues(
        _question(routes={"yes": "refund", "false": "reply"}), default_edge=True
    )

    assert any("routes 'yes', which is not an answer" in i for i in issues), issues


def test_a_route_to_a_missing_node_is_refused():
    issues = _issues(
        _question(routes={"true": "nowhere"}, unsure_next_node_id="gone"),
        default_edge=True,
    )

    assert any("routes 'true' to missing node 'nowhere'" in i for i in issues)
    assert any("unsure answer to missing node 'gone'" in i for i in issues)


def test_a_scale_routes_on_its_levels_as_strings():
    scale = {"type": "integer", "minimum": 1, "maximum": 3, "description": "How bad?"}
    question = _question(
        answer=scale,
        routes={"1": "reply", "2": "reply", "3": "refund"},
        unsure_next_node_id="reply",
    )
    nodes, edges = _graph(question)

    assert WorkflowGraphValidator.validate(nodes, edges) == "triage"


def test_a_multi_choice_cannot_be_routed():
    multi = {
        "type": "array",
        "items": {"type": "string", "enum": ["a", "b"]},
        "uniqueItems": True,
        "description": "Which apply?",
    }
    issues = _issues(_question(answer=multi), default_edge=True)

    assert any("multi-choice" in issue for issue in issues), issues


def test_an_open_question_is_refused_with_the_decisions_contracts_reasons():
    issues = _issues(
        _question(answer={"type": "string", "description": "Summarise it"}),
        default_edge=True,
    )

    assert any(
        issue.startswith("decision 'triage' question.answer") for issue in issues
    ), issues


def test_an_example_must_answer_with_one_of_the_values():
    question = _question(
        examples=[{"evidence": "refund me", "answer": "yes"}],
        unsure_next_node_id="reply",
    )
    issues = _issues(question, default_edge=True)

    assert any("example 0 answers 'yes'" in issue for issue in issues), issues


def test_rules_and_a_question_on_one_node_are_refused():
    issues = _issues(
        _question(),
        default_edge=True,
        rules=[DecisionRule(condition="start.payload.vip", next_node_id="refund")],
    )

    assert any("both rules and a question" in issue for issue in issues), issues


def test_the_evidence_expression_must_compile():
    question = DecisionQuestion.model_validate(
        {
            "instruction": "Triage.",
            "evidence": {"type": "expression", "value": "start.payload[["},
            "answer": YES_OR_NO,
        }
    )
    issues = _issues(question, default_edge=True)

    assert any("question evidence" in issue for issue in issues), issues


def test_a_decision_with_neither_rules_nor_a_question_still_falls_through():
    nodes = [
        DecisionNode(id="pass", config=DecisionNodeConfig()),
        EndNode(id="done"),
    ]
    edges = [WorkflowEdge(id="e1", source="pass", target="done")]

    assert WorkflowGraphValidator.validate(nodes, edges) == "pass"
