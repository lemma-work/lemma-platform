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
from app.modules.agent_surfaces.platforms.email_styles import (
    EMAIL_MARKDOWN_EXTENSIONS,
    EmailStylesExtension,
    email_body_wrapper,
    style_stashed_code_blocks,
)
from app.modules.agent_surfaces.platforms.email_text import plain_text_from_html

try:
    import markdown as markdown_lib
except ImportError:  # pragma: no cover - optional dependency fallback
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
            html_body=email_body_wrapper(f"<pre>{escaped}</pre>"),
            display_resource_plans=display_resource_plans,
        )
    return _append_display_resource_email_content(
        plain_text=normalized_content,
        html_body=email_body_wrapper(_markdown_to_email_html(normalized_content)),
        display_resource_plans=display_resource_plans,
    )


_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\S")


def _blank_line_before_lists(content: str) -> str:
    """Let a list interrupt a paragraph, the way CommonMark already would.

    Python-Markdown requires a blank line before a list. Without one, a label
    line followed straight by bullets is one paragraph, and because HTML does
    not preserve newlines the whole thing arrives as a single flowed sentence
    with hyphens loose in it -- "What you can do - Land results - Run agents".

    That shape is close to the modal form of model output, so it is worth
    meeting rather than correcting: no docstring reliably stops a model writing
    a list directly under its heading, and every agent already deployed writes
    it that way today.

    Only the email path needs this. A chat surface renders newlines as
    newlines, so the same message already reads as a list there.

    ``in_list`` is what keeps a lazy continuation safe. Inside a list an
    unindented line belongs to the item above it, so a break inserted before
    the next item would split one list in two:

        - item one
          continued on the next line
        - item two
    """
    out: list[str] = []
    in_list = False
    in_fence = False
    fence = ""
    for line in content.split("\n"):
        stripped = line.strip()
        if in_fence:
            if stripped.startswith(fence):
                in_fence = False
            out.append(line)
            continue
        # A hyphen inside a fenced block is code, not a bullet.
        if stripped.startswith(("```", "~~~")):
            in_fence, fence = True, stripped[:3]
            out.append(line)
            continue
        if not stripped:
            in_list = False
            out.append(line)
            continue
        if _LIST_ITEM.match(line):
            if not in_list and out and out[-1].strip():
                out.append("")
            in_list = True
        out.append(line)
    return "\n".join(out)


def _markdown_to_email_html(content: str) -> str:
    """Render markdown with the extensions agents actually write against.

    A fresh `Markdown` instance per call rather than a module-level one: the
    parser carries per-document state (footnote refs, link definitions) and
    resetting it is easy to forget. Rendering an email is not a hot path.
    """
    return style_stashed_code_blocks(
        markdown_lib.markdown(
            _blank_line_before_lists(content),
            extensions=[*EMAIL_MARKDOWN_EXTENSIONS, EmailStylesExtension()],
        )
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
    preview_html = (
        '<pre style="background:#f6f8fa;border-radius:6px;color:#111827;'
        'font-size:13px;margin:12px 0 0;overflow-x:auto;padding:12px;">'
        f"{escape(plan.preview_block)}</pre>"
        if plan.preview_block
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
        f"{preview_html}"
        f"{action_html}"
        "</div>"
    )
