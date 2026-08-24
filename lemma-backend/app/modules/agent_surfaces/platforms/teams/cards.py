"""Adaptive Cards: the JSON Teams renders for a question, an approval, a resource.

Card construction is pure -- a plan in, a dict out -- and none of it needs the
adapter, its credentials or a session, which is why it can sit apart from the
code that sends the result.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.teams.parser import (
    TEAMS_APPROVAL_DECISION_KEY,
    TEAMS_FORM_CALLBACK_KEY,
)


def _teams_display_resource_card(
    render_plan: SurfaceDisplayRenderPlan,
) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": render_plan.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    if render_plan.summary:
        body.append(
            {
                "type": "TextBlock",
                "text": render_plan.summary,
                "wrap": True,
                "spacing": "Small",
            }
        )
    if render_plan.detail_lines:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {"title": "", "value": line}
                    for line in render_plan.detail_lines[:5]
                    if line
                ],
            }
        )

    if render_plan.preview_block:
        body.append(
            {
                "type": "TextBlock",
                "text": render_plan.preview_block,
                "fontType": "Monospace",
                "wrap": False,
                "spacing": "Small",
            }
        )

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    action = render_plan.primary_action
    if action is not None:
        card["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": action.label,
                "url": action.url,
            }
        ]
    return card


def _teams_question_input(question: SurfaceQuestion) -> dict[str, Any]:
    """An Input.ChoiceSet keyed by the question header; values are option labels."""
    choices = [
        {
            "title": (f"{opt.label} (recommended)" if opt.recommended else opt.label),
            "value": opt.label,
        }
        for opt in question.options
    ]
    element: dict[str, Any] = {
        "type": "Input.ChoiceSet",
        "id": question.header,
        "label": question.question,
        "choices": choices,
    }
    if question.multi_select:
        element["isMultiSelect"] = True
    return element


def _teams_question_card(plan: SurfaceQuestionRenderPlan) -> dict[str, Any]:
    """Adaptive Card with Input.ChoiceSet per question (+ optional Other text)
    and an Action.Submit carrying the callback id. Teams merges the input ids
    into the submitted ``value`` keyed by question header."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": plan.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    for question in plan.questions:
        body.append(_teams_question_input(question))
        if plan.allow_other:
            body.append(
                {
                    "type": "Input.Text",
                    "id": f"{question.header}{OTHER_ANSWER_SUFFIX}",
                    "label": "Other (type your own)",
                }
            )
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": plan.submit_label,
                "data": {TEAMS_FORM_CALLBACK_KEY: plan.callback_id},
            }
        ],
    }


def _teams_approval_card(plan: SurfaceApprovalRenderPlan) -> dict[str, Any]:
    """Adaptive Card with the approval title/reason and one Action.Submit per
    decision (Approve / Deny / optionally Approve-for-session), each carrying the
    callback id + its decision in ``data``."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"Approval needed: {plan.title}",
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    if plan.reason:
        body.append({"type": "TextBlock", "text": plan.reason, "wrap": True})
    if plan.action_summary:
        body.append(
            {
                "type": "TextBlock",
                "text": f"Action: {plan.action_summary}",
                "isSubtle": True,
                "wrap": True,
            }
        )
    actions = [
        {
            "type": "Action.Submit",
            "title": button.label,
            "data": {
                TEAMS_FORM_CALLBACK_KEY: plan.callback_id,
                TEAMS_APPROVAL_DECISION_KEY: button.decision,
            },
        }
        for button in plan.buttons
    ]
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }
