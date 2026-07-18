from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from email.utils import parseaddr
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan

logger = get_logger(__name__)

try:
    import markdown as markdown_lib
except Exception:  # pragma: no cover - optional dependency fallback
    markdown_lib = None


EmailReplyContentType = Literal["text", "markdown", "html"]


@dataclass(slots=True)
class ParsedEmailIdentity:
    email: str | None = None
    display_name: str | None = None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        normalized = " ".join(part.strip() for part in self._parts if part.strip())
        return re.sub(r"\s+", " ", unescape(normalized)).strip()


def normalize_email_address(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def parse_email_identity(
    value: Any,
    *,
    fallback_email: Any = None,
    fallback_name: Any = None,
) -> ParsedEmailIdentity:
    display_name = str(fallback_name or "").strip() or None
    email = normalize_email_address(_read_email_address(value))
    if email:
        parsed_name = _read_email_name(value)
        return ParsedEmailIdentity(
            email=email,
            display_name=str(parsed_name or display_name or "").strip() or None,
        )

    fallback_identity = ParsedEmailIdentity(
        email=normalize_email_address(_read_email_address(fallback_email)),
        display_name=display_name,
    )
    return fallback_identity


def reply_subject(subject: str | None) -> str:
    clean = str(subject or "").strip()
    if not clean:
        return "Reply from Lemma"
    if clean.lower().startswith("re:"):
        return clean
    return f"Re: {clean}"


def plain_text_from_html(value: str | None) -> str:
    html_value = str(value or "").strip()
    if not html_value:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(html_value)
    return parser.text()


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


def guess_content_type(file_name: str) -> str:
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def decode_base64_bytes(
    data: str,
    *,
    urlsafe: bool,
) -> bytes:
    normalized = str(data or "").strip()
    if not normalized:
        return b""
    padding = "=" * (-len(normalized) % 4)
    payload = normalized + padding
    if urlsafe:
        return base64.urlsafe_b64decode(payload.encode("ascii"))
    return base64.b64decode(payload.encode("ascii"))


def file_name_from_path(path: str) -> str:
    return Path(path).name or "attachment"


async def resolve_outbound_email_attachments(
    deps: Any,
    paths: list[str],
    *,
    inline_cap_bytes: int,
) -> tuple[list[tuple[str, bytes, str]], list[tuple[str, str]]]:
    """Resolve attachment paths into (inline files, link files) for an email.

    Datastore (``/me/...``) files are inlined when at/below ``inline_cap_bytes``,
    else returned as a (name, signed_url) link. Workspace paths are always
    inlined. Returns ``(inline, links)`` where ``inline`` is a list of
    ``(file_name, bytes, mime)`` and ``links`` is ``(file_name, url)``.
    """
    # Imported lazily to avoid a module-load cycle (email_common is imported by
    # the platform services).
    from app.composition.surface_agent import is_datastore_path, pod_services

    inline: list[tuple[str, bytes, str]] = []
    links: list[tuple[str, str]] = []
    for path in paths:
        if is_datastore_path(path):
            async with pod_services(deps) as services:
                entity = await services.file.get_file_by_path(
                    deps.pod_id, path, services.ctx
                )
                size = entity.size_bytes
                # A known, positive size at/below the cap inlines. Treat 0 or an
                # unrecorded size as "not known to fit" and deliver a link, so an
                # unbounded file whose size wasn't stamped can't be inlined at full
                # size and blow the provider's hard limit.
                if isinstance(size, int) and 0 < size <= inline_cap_bytes:
                    (
                        _entity,
                        content,
                    ) = await services.file.download_file_content_by_path(
                        deps.pod_id, path, services.ctx
                    )
                    inline.append(
                        (
                            entity.name,
                            content,
                            entity.mime_type or guess_content_type(entity.name),
                        )
                    )
                else:
                    (
                        _entity,
                        signed_url,
                        _expires,
                        _hits,
                    ) = await services.file.create_signed_url(
                        deps.pod_id, path, services.ctx
                    )
                    links.append((entity.name, signed_url))
        else:
            raw = await deps.file_manager.read_file(path)
            content = raw.encode("utf-8") if isinstance(raw, str) else raw
            name = file_name_from_path(path)
            # Workspace files can't be signed into a link, so bound them by the
            # actual byte length — an oversize workspace file inlined unconditionally
            # would fail the whole send. Skip (with a warning) rather than hard-fail.
            if len(content) > inline_cap_bytes:
                logger.debug(
                    'agent_surfaces.email_common.skipping_oversize_workspace_email_attachment.diagnostic',
                    count=len(content),
                    inline_cap_bytes=inline_cap_bytes,
                )
                continue
            inline.append((name, content, guess_content_type(name)))
    return inline, links


async def resolve_outbound_email_attachment_urls(
    deps: Any,
    paths: list[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Resolve attachment paths to signed URLs for a Composio email send.

    Composio's Gmail/Outlook actions accept a public/signed URL in their
    ``attachment`` field and download it server-side, so datastore files become
    ``(name, signed_url)``. Workspace files can't be signed into a URL and are
    returned as unresolved names (the caller notes them). Returns
    ``(url_attachments, unresolved_names)``.
    """
    from app.composition.surface_agent import is_datastore_path, pod_services

    resolved: list[tuple[str, str]] = []
    unresolved: list[str] = []
    for path in paths:
        if is_datastore_path(path):
            async with pod_services(deps) as services:
                entity = await services.file.get_file_by_path(
                    deps.pod_id, path, services.ctx
                )
                (
                    _entity,
                    signed_url,
                    _expires,
                    _hits,
                ) = await services.file.create_signed_url(
                    deps.pod_id, path, services.ctx
                )
                resolved.append((entity.name, signed_url))
        else:
            unresolved.append(file_name_from_path(path))
    return resolved, unresolved


def append_attachment_links(content: str, links: list[tuple[str, str]]) -> str:
    """Append large-file download links to an email body (plain text block)."""
    if not links:
        return content
    block = "\n\n".join(f"{name}: {url}" for name, url in links)
    return f"{content}\n\n{block}" if content else block


def _read_email_address(value: Any) -> str | None:
    if isinstance(value, str):
        _, email = parseaddr(value)
        return email or value
    if isinstance(value, dict):
        nested = value.get("emailAddress")
        if isinstance(nested, dict):
            nested_address = nested.get("address")
            if nested_address:
                return str(nested_address)
        return value.get("email") or value.get("address") or value.get("email_address")
    return None


def _read_email_name(value: Any) -> str | None:
    if isinstance(value, str):
        name, _ = parseaddr(value)
        return str(name or "").strip() or None
    if isinstance(value, dict):
        nested = value.get("emailAddress")
        if isinstance(nested, dict):
            nested_name = nested.get("name")
            if nested_name:
                return str(nested_name).strip() or None
        return str(value.get("name") or value.get("display_name") or "").strip() or None
    return None
