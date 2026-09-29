from __future__ import annotations

from uuid import uuid4

from app.modules.agent.tools.user_interaction.models import (
    AskUserOption,
    AskUserQuestion,
    AskUserRequest,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_ask_user_render_plan,
    merge_other_answers,
    parse_callback_id,
)


def _request() -> AskUserRequest:
    return AskUserRequest(
        questions=[
            AskUserQuestion(
                question="Pick a color",
                header="color",
                options=[
                    AskUserOption(label="Red"),
                    AskUserOption(label="Blue", recommended=True),
                ],
            ),
            AskUserQuestion(
                question="Which sizes?",
                header="size",
                multi_select=True,
                options=[
                    AskUserOption(label="S", description="small"),
                    AskUserOption(label="M"),
                ],
            ),
        ]
    )


def test_build_ask_user_render_plan_keys_by_header():
    conversation_id = uuid4()
    plan = build_ask_user_render_plan(
        request=_request(),
        conversation_id=conversation_id,
        tool_call_id="tool-1",
    )
    assert [q.header for q in plan.questions] == ["color", "size"]
    assert plan.questions[1].multi_select is True
    assert plan.questions[0].options[1].recommended is True
    # callback id round-trips to (conversation_id, tool_call_id)
    assert parse_callback_id(plan.callback_id) == (str(conversation_id), "tool-1")


def test_single_question_title_is_the_question():
    plan = build_ask_user_render_plan(
        request=AskUserRequest(
            questions=[
                AskUserQuestion(
                    question="Ship it?",
                    header="ship",
                    options=[AskUserOption(label="Yes"), AskUserOption(label="No")],
                )
            ]
        ),
        conversation_id=uuid4(),
        tool_call_id="t",
    )
    assert plan.title == "Ship it?"


def test_question_plan_to_plain_text_numbers_options_and_marks_recommended():
    plan = build_ask_user_render_plan(
        request=_request(), conversation_id=uuid4(), tool_call_id="t"
    )
    text = plan.to_plain_text()
    assert "1. Pick a color" in text
    assert "Blue (recommended)" in text
    assert "S — small" in text
    assert "type your own answer" in text


def test_merge_other_answers_overrides_selection_and_drops_empties():
    merged = merge_other_answers(
        {
            "color": "Red",
            "color__other": "Teal",
            "size": ["S"],
            "size__other": "",
            "blank": "",
        }
    )
    assert merged == {"color": "Teal", "size": ["S"]}


def test_merge_other_answers_keeps_selection_when_other_blank():
    assert merge_other_answers({"color": "Red", "color__other": "  "}) == {
        "color": "Red"
    }
