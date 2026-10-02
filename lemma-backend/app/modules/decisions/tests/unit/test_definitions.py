"""Questions, deciders, rules and the input view, as data."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.decisions.domain.deciders import DeciderDefinition, InputView
from app.modules.decisions.domain.decisions import (
    QuestionShape,
    check_against_shape,
    shape_of,
)
from app.modules.decisions.domain.questions import Option, Question, with_options
from app.modules.decisions.infrastructure.rules_engine import (
    answer_by_rules,
    check_expressions,
)
from app.modules.decisions.services.decisions_service import asked_questions, inline_key
from app.modules.decisions.services.deciders_service import warnings_for
from app.modules.decisions.services.rendering import render
from app.modules.decisions.services.system_deciders import (
    SYSTEM_DECIDERS,
    system_decider,
)

QUESTION: TypeAdapter[Question] = TypeAdapter(Question)


def test_options_accept_the_shorthand() -> None:
    question = QUESTION.validate_python(
        {
            "type": "choice",
            "prompt": "Which?",
            "options": {"a": "First", "b": {"description": "Second", "not_for": "x"}},
        }
    )
    assert question.options is not None
    assert question.options["a"] == Option(description="First")
    assert question.options["b"].not_for == "x"


def test_a_fallback_must_be_declared() -> None:
    with pytest.raises(ValidationError):
        QUESTION.validate_python(
            {"type": "choice", "prompt": "Which?", "fallback": "none"}
        )


def test_options_passed_with_a_call_are_added_and_cannot_redefine_declared_ones() -> (
    None
):
    question = QUESTION.validate_python(
        {
            "type": "choice",
            "prompt": "Which conversation?",
            "options": {"none": "None of them"},
            "fallback": "none",
        }
    )
    merged = with_options(question, {"c0": Option(description="Research")})
    assert list(merged.options or {}) == ["none", "c0"]
    with pytest.raises(ValueError, match="already declared"):
        with_options(question, {"none": Option(description="Other")})


def test_a_choice_with_one_option_is_refused_when_asked() -> None:
    definition = DeciderDefinition.model_validate(
        {
            "description": "Pick a pod.",
            "questions": {
                "pod": {
                    "type": "choice",
                    "prompt": "Which pod?",
                    "options": {"new": "Make one"},
                }
            },
        }
    )
    with pytest.raises(Exception, match="between 2 and 255"):
        asked_questions(definition, {})
    assert list(
        asked_questions(definition, {"pod": {"p1": Option(description="Bakery")}})[
            "pod"
        ].options
        or {}
    ) == ["new", "p1"]


def test_rules_must_answer_known_questions_with_allowed_values() -> None:
    base = {
        "description": "d",
        "questions": {"go": {"type": "yes_no", "prompt": "Go?"}},
    }
    with pytest.raises(ValidationError, match="unknown question"):
        DeciderDefinition.model_validate(
            {**base, "rules": [{"when": "a", "answer": {"stop": True}}]}
        )
    with pytest.raises(ValidationError, match="true or false"):
        DeciderDefinition.model_validate(
            {**base, "rules": [{"when": "a", "answer": {"go": "yes"}}]}
        )
    with pytest.raises(ValueError, match="invalid JMESPath"):
        check_expressions(
            DeciderDefinition.model_validate(
                {**base, "rules": [{"when": "1 == 1", "answer": {"go": True}}]}
            ).rules
        )


def test_phrase_rules_fold_case_whitespace_and_trailing_punctuation() -> None:
    definition = DeciderDefinition.model_validate(
        {
            "description": "d",
            "questions": {"ok": {"type": "yes_no", "prompt": "Consent?"}},
            "rules": [
                {
                    "phrases": ["yes", "go ahead"],
                    "field": "reply",
                    "answer": {"ok": True},
                }
            ],
        }
    )
    assert (
        answer_by_rules(
            definition.rules, {"reply": "  Go   Ahead!! "}, definition.questions
        )["ok"].value
        is True
    )
    assert (
        answer_by_rules(
            definition.rules, {"reply": "yes, but only if"}, definition.questions
        )
        == {}
    )


def test_render_keeps_only_the_view_and_bounds_it() -> None:
    rendered = render(
        {"subject": "Hi", "body": "x" * 500, "meta": {"from": "a@b.c"}},
        InputView(fields=["subject", "meta.from"], max_chars=200),
    )
    assert rendered.value == {"subject": "Hi", "meta": {"from": "a@b.c"}}
    assert "body" not in rendered.text
    long = render("y" * 300, InputView(max_chars=200))
    assert (
        long.truncated and long.text.startswith("y" * 200) and "truncated" in long.text
    )


def test_shapes_check_later_answers() -> None:
    question = QUESTION.validate_python(
        {"type": "multi_choice", "prompt": "Which?", "options": {"a": "A", "b": "B"}}
    )
    shape = shape_of(question)
    assert shape == QuestionShape(type="multi_choice", options=["a", "b"])
    assert check_against_shape(shape, ["b", "a", "b"]) == ["a", "b"]
    with pytest.raises(ValueError):
        check_against_shape(shape, ["c"])
    with pytest.raises(ValueError):
        check_against_shape(QuestionShape(type="scale", levels=3), True)


def test_inline_keys_are_stable_digests() -> None:
    definition = DeciderDefinition.model_validate(
        {"description": "d", "questions": {"go": {"type": "yes_no", "prompt": "Go?"}}}
    )
    assert inline_key(definition) == inline_key(
        DeciderDefinition.model_validate(definition.model_dump())
    )
    assert inline_key(definition).startswith("inline:")


def test_warnings_name_the_usual_mistakes() -> None:
    definition = DeciderDefinition.model_validate(
        {
            "description": "d",
            "questions": {
                "pick": {
                    "type": "choice",
                    "prompt": "Which?",
                    "options": {"a": "Same", "b": "same"},
                }
            },
        }
    )
    warnings = " ".join(warnings_for(definition))
    assert "no fields" in warnings
    assert "no fallback" in warnings
    assert "same description" in warnings


def test_system_deciders_load_and_keep_approval_asymmetric() -> None:
    assert set(SYSTEM_DECIDERS) == {"reply_to_question", "approval_reply"}
    approval = system_decider("system:approval_reply")
    assert approval is not None
    assert approval.policy.rules_only == {"decision": ["approve_for_session"]}
    assert approval.policy.require_confidence["decision"]["approve_once"] >= 0.9
    assert not approval.policy.escalate_to_model


NESTED = DeciderDefinition.model_validate(
    {
        "description": "Is this an invoice?",
        "input": {"fields": ["email.subject"]},
        "questions": {"invoice": {"type": "yes_no", "prompt": "Is it an invoice?"}},
        "rules": [
            {
                "phrases": ["invoice"],
                "field": "email.subject",
                "answer": {"invoice": True},
            }
        ],
    }
)
NESTED_BY_EXPRESSION = DeciderDefinition.model_validate(
    {
        **NESTED.model_dump(mode="json", exclude_none=True),
        "rules": [{"when": "email.subject == 'invoice'", "answer": {"invoice": True}}],
    }
)


@pytest.mark.parametrize("definition", [NESTED, NESTED_BY_EXPRESSION])
def test_rules_reach_a_nested_field_the_view_selects(
    definition: DeciderDefinition,
) -> None:
    """The view keeps a field at its path, so a rule naming the path still matches."""
    state = {"email": {"subject": "invoice", "from": "secret@acme.example"}}

    rendered = render(state, definition.input)
    answers = answer_by_rules(definition.rules, rendered.value, definition.questions)

    assert rendered.value == {"email": {"subject": "invoice"}}
    assert answers["invoice"].value is True
    # A sibling the view did not select stays out of what leaves the module.
    assert "secret@acme.example" not in rendered.text


def test_the_view_never_writes_into_the_state() -> None:
    state = {"email": {"subject": "invoice", "from": "a@b.c"}, "body": "kept"}
    before = json.loads(json.dumps(state))

    rendered = render(state, InputView(fields=["email", "email.subject"]))

    assert state == before
    assert rendered.value == {"email": {"subject": "invoice", "from": "a@b.c"}}


def test_a_question_key_holding_a_double_underscore_is_refused() -> None:
    """`<question>__<option>` is how System One is asked a multi-choice option."""
    with pytest.raises(ValidationError, match="must not contain '__'"):
        DeciderDefinition.model_validate(
            {
                "description": "Tags",
                "questions": {"tags__bug": {"type": "yes_no", "prompt": "A bug?"}},
            }
        )
