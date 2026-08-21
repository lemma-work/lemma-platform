"""Writing an outbound email: agent content to the parts a mail client shows.

The mirror of :mod:`email_text`. Content arrives as text, markdown or HTML and
has to leave as a plain-text part and an HTML part, because a mail client picks
whichever it can display and a reader may see either one.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Literal

from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.platforms.email_text import plain_text_from_html

try:
    import markdown as markdown_lib
except Exception:  # pragma: no cover - optional dependency fallback
    markdown_lib = None

EmailReplyContentType = Literal["text", "markdown", "html"]


def render_email_content(
    *,
    content: str,
    content_type: EmailReplyContentType,
    display_resource_plans: list[SurfaceDisplayRenderPlan] | None = None,
) -> tuple[str, str | None]:
    normalized_content = str(content or "").strip()
    if content_type == "text":
        plain_text, html_body = normalized_content, None
        return _append_display_resource_email_content(
            plain_text=plain_text,
            html_body=html_body,
            display_resource_plans=display_resource_plans,
        )
    if content_type == "html":
        return _append_display_resource_email_content(
            plain_text=plain_text_from_html(normalized_content),
            html_body=normalized_content,
            display_resource_plans=display_resource_plans,
        )
    if markdown_lib is None:
        escaped = (
            normalized_content.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return _append_display_resource_email_content(
            plain_text=normalized_content,
            html_body=f"<pre>{escaped}</pre>",
            display_resource_plans=display_resource_plans,
        )
    return _append_display_resource_email_content(
        plain_text=normalized_content,
        html_body=markdown_lib.markdown(normalized_content),
        display_resource_plans=display_resource_plans,
    )


def coerce_display_resource_plans(value: Any) -> list[SurfaceDisplayRenderPlan]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    plans: list[SurfaceDisplayRenderPlan] = []
    for item in raw_items:
        try:
            if isinstance(item, SurfaceDisplayRenderPlan):
                plans.append(item)
            elif hasattr(item, "model_dump"):
                plans.append(
                    SurfaceDisplayRenderPlan.model_validate(
                        item.model_dump(mode="json")
                    )
                )
            elif isinstance(item, dict):
                plans.append(SurfaceDisplayRenderPlan.model_validate(item))
        except Exception:
            continue
    return plans


def render_display_resource_email_html(
    display_resource_plans: list[SurfaceDisplayRenderPlan],
    *,
    intro_html: str | None = None,
) -> str:
    parts: list[str] = []
    if intro_html:
        parts.append(intro_html)
    for plan in display_resource_plans:
        parts.append(_display_resource_card_html(plan))
    return "\n".join(parts)


def _append_display_resource_email_content(
    *,
    plain_text: str,
    html_body: str | None,
    display_resource_plans: list[SurfaceDisplayRenderPlan] | None,
) -> tuple[str, str | None]:
    plans = display_resource_plans or []
    if not plans:
        return plain_text, html_body

    resource_plain = "\n\n".join(plan.to_plain_text() for plan in plans)
    combined_plain = "\n\n".join(
        part for part in (plain_text.strip(), resource_plain.strip()) if part
    )
    intro_html = html_body if html_body else _plain_text_to_email_html(plain_text)
    return combined_plain, render_display_resource_email_html(
        plans,
        intro_html=intro_html,
    )


def _plain_text_to_email_html(value: str) -> str:
    paragraphs = [
        f"<p>{escape(part)}</p>"
        for part in re.split(r"\n{2,}", str(value or "").strip())
        if part.strip()
    ]
    return "\n".join(paragraphs)


def _display_resource_card_html(plan: SurfaceDisplayRenderPlan) -> str:
    action = plan.primary_action
    detail_items = "".join(
        f"<li>{escape(line)}</li>" for line in plan.detail_lines if line
    )
    summary_html = (
        f'<p style="color:#4b5563;margin:0 0 12px;">{escape(plan.summary)}</p>'
        if plan.summary
        else ""
    )
    details_html = (
        f'<ul style="color:#374151;margin:0 0 0 18px;padding:0;">{detail_items}</ul>'
        if detail_items
        else ""
    )
    action_html = ""
    if action is not None:
        action_html = (
            '<p style="margin:16px 0 0;">'
            f'<a href="{escape(action.url, quote=True)}" '
            'style="background:#111827;border-radius:6px;color:#ffffff;'
            "display:inline-block;font-weight:600;padding:10px 14px;"
            'text-decoration:none;">'
            f"{escape(action.label)}</a></p>"
        )
    return (
        '<div style="border:1px solid #d8dee4;border-radius:8px;'
        "font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
        'margin:16px 0;padding:16px;">'
        f'<p style="color:#111827;font-size:16px;font-weight:700;'
        f'margin:0 0 8px;">{escape(plan.title)}</p>'
        f"{summary_html}"
        f"{details_html}"
        f"{action_html}"
        "</div>"
    )
