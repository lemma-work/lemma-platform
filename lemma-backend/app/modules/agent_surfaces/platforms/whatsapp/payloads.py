"""Building the JSON bodies WhatsApp's Cloud API expects.

Pure construction: what an outbound message, an interactive reply-button block,
a list picker, a CTA-URL card and a media caption look like on the wire, and how
each of them is kept inside Meta's length limits. Split from
:mod:`service` — which owns credentials, HTTP and the API calls — because it is
the half with no I/O in it and the half worth reading on its own.

Every string that reaches these builders is model-authored, so every one of them
goes through :mod:`text_format` on the way in. That is not decoration: the agent
writes Markdown, WhatsApp renders a different and much smaller syntax, and an
untranslated string arrives on the phone as literal asterisks. Truncation runs
*after* translation and is followed by a re-balance, because a cut can land in
the middle of a pair and leave the marker this module exists to prevent.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    balance_whatsapp_delimiters,
    to_plain_text,
    to_whatsapp_text,
)

# Meta's hard ceiling on a text message body.
WHATSAPP_TEXT_LIMIT = 4096


# Separator for encoding ask_user routing into a WhatsApp button/list ``id``
# (``callback_id~header~value``). The callback id itself uses ``|``, so ``~``
# unambiguously splits the three parts. WhatsApp allows ids up to 256 chars.
WHATSAPP_INTERACTION_SEP = "~"

# Sentinel used in place of a question ``header`` to mark an approval button
# reply (``callback_id~__approval__~<decision>``). The parser routes this to an
# approval decision instead of an ask_user answer.
WHATSAPP_APPROVAL_HEADER = "__approval__"


def build_whatsapp_interactive(
    callback_id: str, question: SurfaceQuestion
) -> dict[str, Any] | None:
    """Build a WhatsApp interactive payload for one question, or ``None`` if it
    can't be expressed natively (id over 256 chars, more than 10 options, or a
    header containing the reserved separator)."""
    # The reply id packs ``callback_id~header~value`` and is decoded with a
    # 2-split, so the value may contain ``~`` but the header must not — otherwise
    # the split misassigns and the answer is mis-keyed. Fall back to text when a
    # header contains the separator (rare; header is model-authored).
    if WHATSAPP_INTERACTION_SEP in (question.header or ""):
        return None
    rows: list[tuple[str, str]] = []
    for option in question.options:
        button_id = (
            f"{callback_id}{WHATSAPP_INTERACTION_SEP}{question.header}"
            f"{WHATSAPP_INTERACTION_SEP}{option.label}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        rows.append((button_id, option.label))
    # The question is model-authored, so it arrives as Markdown like every other
    # outbound string and needs the same translation the message body gets.
    body = {"text": to_whatsapp_text(question.question or "")[:1024] or "Please choose"}
    if 1 <= len(rows) <= 3:
        return {
            "type": "button",
            "body": body,
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                    for rid, title in rows
                ]
            },
        }
    if 4 <= len(rows) <= 10:
        return {
            "type": "list",
            "body": body,
            "action": {
                "button": "Choose",
                "sections": [
                    {"rows": [{"id": rid, "title": title[:24]} for rid, title in rows]}
                ],
            },
        }
    return None


def build_whatsapp_approval_interactive(
    plan: SurfaceApprovalRenderPlan,
) -> dict[str, Any] | None:
    """Build a WhatsApp reply-button payload for an approval prompt, or ``None``
    if it can't be expressed natively (more than 3 buttons, or an id over 256
    chars). Each button id packs ``callback_id~__approval__~<decision>``."""
    buttons: list[dict[str, Any]] = []
    for button in plan.buttons:
        button_id = (
            f"{plan.callback_id}{WHATSAPP_INTERACTION_SEP}{WHATSAPP_APPROVAL_HEADER}"
            f"{WHATSAPP_INTERACTION_SEP}{button.decision}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        buttons.append(
            {"type": "reply", "reply": {"id": button_id, "title": button.label[:20]}}
        )
    if not 1 <= len(buttons) <= 3:
        return None
    # The title is wrapped in bold here, so its own markers are stripped first —
    # a ``*`` inside it would close the wrapper early and leave the rest literal.
    body_parts = [f"*{to_plain_text(plan.title)}*"]
    if plan.reason:
        body_parts.append(to_whatsapp_text(plan.reason))
    if plan.action_summary:
        body_parts.append(f"Action: {to_whatsapp_text(plan.action_summary)}")
    body_text = balance_whatsapp_delimiters(
        "\n\n".join(part for part in body_parts if part.strip())[:1024]
    ).strip()
    body_text = body_text or "Approval needed"
    return {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": buttons},
    }


def resolve_whatsapp_send_type(*, delivery_mode: str, mime_type: str) -> str:
    requested = str(delivery_mode or "auto").lower()
    if requested != "auto":
        return requested
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


def whatsapp_message_bodies(message: str) -> list[str]:
    """Translate the agent's answer, then split it into sendable messages.

    A long answer used to be sent as one oversized body that Meta rejects
    outright — the person got nothing rather than a truncated something. Split on
    paragraph, then line, then word boundaries, and each piece is re-balanced
    because the split itself can cut a ``*bold*`` pair in half.
    """
    body = to_whatsapp_text(message)
    if not body:
        return []
    return [
        balanced
        for balanced in (
            balance_whatsapp_delimiters(chunk)
            for chunk in chunk_text(body, limit=WHATSAPP_TEXT_LIMIT)
        )
        if balanced.strip()
    ]


def whatsapp_cta_url_payload(
    *,
    recipient_wa_id: str,
    render_plan: SurfaceDisplayRenderPlan,
) -> dict[str, Any]:
    action = render_plan.primary_action
    body = balance_whatsapp_delimiters(
        truncate_whatsapp_text(
            to_whatsapp_text(
                whatsapp_display_resource_text(render_plan, include_action=False)
            ),
            1024,
        )
    )
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": truncate_whatsapp_button_text(
                        action.label if action else "Open"
                    ),
                    "url": action.url if action else "",
                },
            },
        },
    }


def whatsapp_text_payload(
    *,
    recipient_wa_id: str,
    body: str,
    preview_url: bool,
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_wa_id,
        "type": "text",
        "text": {
            # Balanced *after* truncating: a 4096-character slice can land in the
            # middle of a ``*bold*`` pair, which is the broken marker
            # ``to_whatsapp_text`` exists to prevent.
            "body": balance_whatsapp_delimiters(
                truncate_whatsapp_text(to_whatsapp_text(body), WHATSAPP_TEXT_LIMIT)
            ),
            "preview_url": preview_url,
        },
    }


def whatsapp_display_resource_text(
    render_plan: SurfaceDisplayRenderPlan,
    *,
    include_action: bool = True,
) -> str:
    parts = [f"*{render_plan.title}*"]
    if render_plan.summary:
        parts.append(render_plan.summary)
    parts.extend(render_plan.detail_lines[:5])
    action = render_plan.primary_action
    if include_action and action is not None:
        parts.append(f"{action.label}: {action.url}")
    return "\n\n".join(parts)


def truncate_whatsapp_button_text(value: str) -> str:
    text = " ".join(str(value or "").split()) or "Open"
    return text if len(text) <= 20 else text[:19].rstrip() + "..."


def truncate_whatsapp_text(value: str, max_length: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_length else text[: max_length - 1].rstrip() + "..."


def filename_from_url(url: str) -> str:
    return str(url or "").rstrip("/").split("/")[-1].strip()
