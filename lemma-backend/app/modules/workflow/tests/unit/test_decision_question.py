"""A DECISION node's question: what it accepts, where answers go, what it records."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.decisions.contracts.shapes import Answer, DecisionEntity, Rung
from app.modules.workflow.domain.decision_step import (
    DecisionAsk,
    DecisionOutcome,
    decision_subject,
)
from app.modules.workflow.domain.errors import DecisionStepError
from app.modules.workflow.domain.nodes import (
    DecisionNode,
    DecisionNodeQuestion,
    WORKFLOW_NODE_ADAPTER,
)
from app.modules.workflow.infrastructure.decisions_adapter import outcome_of

TRIAGE = {
    "description": "What to do with a message.",
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should happen to it?",
            "options": {
                "act": "Needs a reply.",
                "ask": "Needs a person.",
                "ignore": "Needs nothing.",
            },
            "fallback": "ask",
        }
    },
}
INPUT = {"type": "expression", "value": "start.payload"}


def _question(**overrides: object) -> DecisionNodeQuestion:
    values: dict[str, object] = {"input": INPUT, "definition": TRIAGE}
    return DecisionNodeQuestion.model_validate({**values, **overrides})


# -- what a question accepts ---------------------------------------------------


def test_a_rules_only_node_loads_exactly_as_before():
    node = WORKFLOW_NODE_ADAPTER.validate_python(
        {
            "id": "route",
            "type": "DECISION",
            "config": {
                "rules": [{"condition": "a == `1`", "next_node_id": "b"}],
            },
        }
    )
    assert isinstance(node, DecisionNode)
    assert node.config.question is None
    assert node.config.targets() == ["b"]


def test_a_question_names_a_decider_or_carries_a_definition():
    assert _question().definition is not None
    named = DecisionNodeQuestion.model_validate(
        {"input": INPUT, "decider": "email-triage"}
    )
    assert named.decider == "email-triage"
    assert (
        DecisionNodeQuestion.model_validate(
            {"input": INPUT, "decider": "system:reply_to_question"}
        ).decider
        == "system:reply_to_question"
    )
    with pytest.raises(ValidationError, match="not both and not neither"):
        _question(decider="email-triage")
    with pytest.raises(ValidationError, match="not both and not neither"):
        DecisionNodeQuestion.model_validate({"input": INPUT})
    with pytest.raises(ValidationError):
        DecisionNodeQuestion.model_validate({"input": INPUT, "decider": "Not A Name"})


def test_an_inline_definition_asks_one_choice_with_options():
    two = {
        **TRIAGE,
        "questions": {
            **TRIAGE["questions"],
            "urgent": {"type": "yes_no", "prompt": "Is it urgent?"},
        },
    }
    with pytest.raises(ValidationError, match="exactly one question"):
        _question(definition=two)
    yes_no = {
        **TRIAGE,
        "questions": {"urgent": {"type": "yes_no", "prompt": "Is it urgent?"}},
    }
    with pytest.raises(ValidationError, match="`choice` with at least two"):
        _question(definition=yes_no)


def test_an_inline_branch_must_name_an_option():
    assert _question(branches={"act": "reply"}).branches == {"act": "reply"}
    with pytest.raises(ValidationError, match=r"branches \['acts'\]"):
        _question(branches={"acts": "reply"})
    with pytest.raises(ValidationError, match="is not the definition's question"):
        _question(question_key="other")


def test_input_is_one_binding_or_a_mapping_of_them():
    assert _question().bindings() == {"input": _question().input}
    mapped = _question(input={"sender": INPUT, "kind": {"type": "literal", "value": 1}})
    assert set(mapped.bindings()) == {"sender", "kind"}


# -- where an answer goes ------------------------------------------------------


def test_an_answer_takes_its_branch_and_an_unmapped_one_falls_through():
    question = _question(branches={"act": "reply", "ignore": "archive"})
    assert question.route("act", []) == "reply"
    assert question.route("ask", []) is None
    assert question.route(True, []) is None


def test_an_open_question_goes_to_on_open_before_its_fallback():
    question = _question(branches={"ask": "review"}, on_open="escalate")
    assert question.route("ask", ["action"]) == "escalate"


def test_without_on_open_an_open_question_takes_its_fallbacks_branch():
    question = _question(branches={"ask": "review"})
    assert question.route("ask", ["action"]) == "review"
    assert question.route(None, ["action"]) is None


def test_a_named_question_is_open_only_when_that_question_is():
    question = DecisionNodeQuestion.model_validate(
        {
            "input": INPUT,
            "decider": "email-triage",
            "question_key": "action",
            "branches": {"act": "reply"},
            "on_open": "review",
        }
    )
    assert question.route("act", ["urgency"]) == "reply"
    assert question.route("act", ["action"]) == "review"


# -- what the step files its decision under ------------------------------------


def test_the_subject_is_the_run_the_node_and_the_loop_index():
    run_id = uuid4()
    assert decision_subject(run_id, "triage", (), set()) == (
        f"workflow:{run_id}:triage:0"
    )
    assert decision_subject(run_id, "triage", (3,), set()) == (
        f"workflow:{run_id}:triage:3"
    )
    assert decision_subject(run_id, "triage", (1, 4), set()) == (
        f"workflow:{run_id}:triage:1.4"
    )


def test_a_cycle_back_to_the_same_step_asks_again_under_a_new_subject():
    run_id = uuid4()
    first = decision_subject(run_id, "triage", (), set())
    second = decision_subject(run_id, "triage", (), {first})
    third = decision_subject(run_id, "triage", (), {first, second})
    assert second == f"{first}:2"
    assert third == f"{first}:3"


def test_a_long_node_id_still_fits_the_subject():
    subject = decision_subject(uuid4(), "n" * 5000, (), set())
    assert DecisionAsk(subject=subject, state={}).subject == subject


# -- what the node records -----------------------------------------------------


def _decision(**fields: object) -> DecisionEntity:
    values: dict[str, object] = {
        "decider_scope": "inline",
        "decider_key": "inline:abc",
        "shape": {"action": {"type": "choice", "options": ["act", "ask", "ignore"]}},
    }
    return DecisionEntity.model_validate({**values, **fields})


def test_an_answered_question_records_the_choice_and_who_answered():
    decision = _decision(answers={"action": Answer(value="act", by=Rung.RULES)})
    outcome = outcome_of(decision, DecisionAsk(subject="s", state={}))
    assert outcome.as_output() == {
        "matched_condition": None,
        "choice": "act",
        "decision_id": str(decision.id),
        "open": [],
        "answered_by": "rules",
    }


def test_an_open_question_records_its_fallback_and_nobody_as_answering():
    decision = _decision(
        answers={"action": Answer(value="ask", by=Rung.MODEL, abstained=True)},
        open=["action"],
    )
    outcome = outcome_of(decision, DecisionAsk(subject="s", state={}))
    assert (outcome.choice, outcome.open, outcome.answered_by) == (
        "ask",
        ["action"],
        None,
    )


def test_a_decider_with_several_questions_needs_a_question_key():
    decision = _decision(
        shape={
            "action": {"type": "choice", "options": ["act", "ignore"]},
            "urgent": {"type": "yes_no"},
        },
        answers={
            "action": Answer(value="act", by=Rung.RULES),
            "urgent": Answer(value=True, by=Rung.RULES),
        },
    )
    with pytest.raises(DecisionStepError, match="asks 2 questions"):
        outcome_of(decision, DecisionAsk(subject="s", state={}))
    keyed = outcome_of(
        decision, DecisionAsk(subject="s", state={}, question_key="urgent")
    )
    assert keyed.choice is True
    with pytest.raises(DecisionStepError, match="no question 'missing'"):
        outcome_of(decision, DecisionAsk(subject="s", state={}, question_key="missing"))


def test_a_matched_rule_leaves_the_question_fields_empty():
    assert DecisionOutcome(matched_condition="a == `1`").as_output() == {
        "matched_condition": "a == `1`",
        "choice": None,
        "decision_id": None,
        "open": [],
        "answered_by": None,
    }
