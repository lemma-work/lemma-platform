"""Block Kit for the messages the agent sends: questions, approvals, cards.

Pure construction -- a render plan in, Slack's JSON out. Kept apart from
`blocks.py`, which builds the *configuration* surfaces (App Home, setup
modals): those are the app talking about itself, these are the agent talking.
"""

from typing import Any


from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX as _OTHER_SUFFIX,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.slack.models import (
    SLACK_APPROVAL_ACTION_ID_BY_DECISION,
    SLACK_FORM_SUBMIT_ACTION_ID,
)


def _markdown_chunk(text: str) -> dict[str, Any]:
    """Model text as a stream chunk.

    A stream is either chunk-based or plain-text for its whole life. Because
    the step timeline uses chunks, the answer must be a chunk too — appending
    top-level ``markdown_text`` to a chunk stream is rejected with
    ``streaming_mode_mismatch``.
    """
    return {"type": "markdown_text", "text": text}


def _task_chunk(sequence: int, title: str | None, status: str) -> dict[str, Any]:
    """One step of the agent's work, as a Slack ``task_update`` chunk.

    The id is stable per step so appending the same id with ``complete`` closes
    the step already on screen rather than adding a second one.
    """
    return {
        "type": "task_update",
        "id": f"step-{sequence}",
        "title": _truncate_slack_text(str(title or "Working…"), 200) or "Working…",
        "status": status,
    }


def _progress_status_text(metadata: dict[str, Any] | None) -> tuple[str, str]:
    progress_text = (metadata or {}).get("progress_text")
    if isinstance(progress_text, str) and progress_text.strip():
        text = progress_text.strip()
        return text, text
    return "is taking a look...", "Taking a look..."


def _question_select_element(question: SurfaceQuestion) -> dict[str, Any] | None:
    """A single/multi static_select whose option values are the option labels.

    The block_id is the question header, so the flattened submission comes back
    keyed by header → the chosen option label(s), ready for AskUserResponse.
    """
    options = [
        {
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(
                    f"{opt.label} (recommended)" if opt.recommended else opt.label,
                    74,
                )
                or "—",
            },
            "value": opt.label,
        }
        for opt in question.options[:100]
    ]
    if not options:
        return None
    return {
        "type": ("multi_static_select" if question.multi_select else "static_select"),
        "action_id": question.header,
        "options": options,
    }


def _question_blocks(plan: SurfaceQuestionRenderPlan) -> list[dict[str, Any]]:
    """Build Block Kit select blocks (+ optional Other text) + a Submit button."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(plan.title, 150) or "Questions",
            },
        }
    ]
    for question in plan.questions:
        element = _question_select_element(question)
        if element is None:
            continue
        blocks.append(
            {
                "type": "input",
                "block_id": question.header,
                "optional": True,
                "label": {
                    "type": "plain_text",
                    "text": _truncate_slack_text(question.question, 150)
                    or question.header,
                },
                "element": element,
            }
        )
        if plan.allow_other:
            blocks.append(
                {
                    "type": "input",
                    "block_id": f"{question.header}{_OTHER_SUFFIX}",
                    "optional": True,
                    "label": {
                        "type": "plain_text",
                        "text": "Other (type your own)",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "action_id": f"{question.header}{_OTHER_SUFFIX}",
                    },
                }
            )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": SLACK_FORM_SUBMIT_ACTION_ID,
                    "style": "primary",
                    "text": {
                        "type": "plain_text",
                        "text": _truncate_slack_text(plan.submit_label, 74) or "Submit",
                    },
                    "value": plan.callback_id,
                }
            ],
        }
    )
    return blocks


def _approval_blocks(plan: SurfaceApprovalRenderPlan) -> list[dict[str, Any]]:
    """Build a section (title/reason/action) + Approve/Deny action buttons.

    Each button's ``action_id`` encodes the decision; its ``value`` carries the
    callback id so the block_actions parser can route the tap back to the run.
    """
    text_parts = [f"*Approval needed:* {_slack_escape(plan.title)}"]
    if plan.reason:
        text_parts.append(_slack_escape(plan.reason))
    if plan.action_summary:
        text_parts.append(f"> Action: `{_slack_escape(plan.action_summary)}`")
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_slack_text("\n".join(text_parts), 2900),
            },
        }
    ]
    elements: list[dict[str, Any]] = []
    for button in plan.buttons:
        action_id = SLACK_APPROVAL_ACTION_ID_BY_DECISION.get(button.decision)
        if action_id is None:
            continue
        element: dict[str, Any] = {
            "type": "button",
            "action_id": action_id,
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(button.label, 74) or "Approve",
            },
            "value": plan.callback_id,
        }
        if button.style in ("primary", "danger"):
            element["style"] = button.style
        elements.append(element)
    blocks.append({"type": "actions", "elements": elements})
    return blocks


def _display_resource_blocks(
    render_plan: SurfaceDisplayRenderPlan,
) -> list[dict[str, Any]]:
    text_parts = [f"*{_slack_escape(render_plan.title)}*"]
    if render_plan.summary:
        text_parts.append(_slack_escape(render_plan.summary))
    for line in render_plan.detail_lines[:4]:
        text_parts.append(f"> {_slack_escape(line)}")
    if render_plan.preview_block:
        text_parts.append(f"```\n{_slack_escape(render_plan.preview_block)}\n```")

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_slack_text("\n".join(text_parts), 2900),
            },
        }
    ]
    action = render_plan.primary_action
    if action is not None:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _truncate_slack_text(action.label, 75),
                        },
                        "url": action.url,
                    }
                ],
            }
        )
    return blocks


def _slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate_slack_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "..."


def slack_acknowledgement_body(
    original_message: dict[str, Any],
    *,
    text: str | None,
    clear_actions: bool,
) -> dict[str, Any]:
    """The ``response_url`` payload that answers a tapped button.

    Two shapes, because a tap means two different things. When the decision is
    settled (``clear_actions``) the original message is rewritten without its
    action blocks and with the outcome appended, so the card stays readable in
    the thread and cannot be tapped a second time. When it is not settled --
    "reply with your own answer" -- the card is left alone and the note goes
    only to the person who tapped, because nobody else in the channel is
    waiting on it.

    Pure, so the exact body Slack receives can be asserted without a transport.
    """
    note = (text or "").strip()
    if not clear_actions:
        return {
            "response_type": "ephemeral",
            "replace_original": False,
            "text": note or "Got it.",
        }

    kept = [
        block
        for block in (original_message.get("blocks") or [])
        if isinstance(block, dict) and block.get("type") != "actions"
    ]
    if note:
        kept.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
    body: dict[str, Any] = {
        "replace_original": True,
        # Slack needs a text fallback whenever blocks are sent; it is what a
        # notification preview shows.
        "text": note or str(original_message.get("text") or "").strip() or "Done",
    }
    if kept:
        body["blocks"] = kept
    return body
