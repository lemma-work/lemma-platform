"""Email-safe rendering for Lemma's first-party transactional messages.

The renderer intentionally accepts structured, plain-text values. It owns every
HTML fragment so callers cannot accidentally interpolate unescaped user data or
let individual messages drift away from the shared Lemma visual language.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Sequence


@dataclass(frozen=True, slots=True)
class EmailAction:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class EmailDetail:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    html: str
    text: str


def _safe(value: object) -> str:
    return escape(str(value), quote=True)


def _detail_rows(details: Sequence[EmailDetail]) -> str:
    if not details:
        return ""
    rows = "".join(
        (
            '<tr><td class="detail-label" style="padding:12px 18px 2px;'
            "color:#777b85;font-size:11px;line-height:1.4;font-weight:700;"
            'letter-spacing:.08em;text-transform:uppercase;">'
            f"{_safe(detail.label)}</td></tr>"
            '<tr><td class="detail-value" style="padding:0 18px 12px;'
            'color:#25272c;font-size:14px;line-height:1.55;word-break:break-word;">'
            f"{_safe(detail.value)}</td></tr>"
        )
        for detail in details
    )
    return (
        '<tr><td style="padding:0 36px 24px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border:1px solid #e3e4e8;border-radius:12px;background:#f8f8f7;">'
        f"{rows}</table></td></tr>"
    )


def _highlight_rows(highlights: Sequence[str]) -> str:
    if not highlights:
        return ""
    rows = "".join(
        (
            '<tr><td width="28" style="padding:9px 0 9px 18px;vertical-align:top;'
            'color:#6366f1;font-size:17px;line-height:1.35;">&#8226;</td>'
            '<td style="padding:9px 18px 9px 5px;color:#3f424a;font-size:14px;'
            f'line-height:1.55;">{_safe(highlight)}</td></tr>'
        )
        for highlight in highlights
    )
    return (
        '<tr><td style="padding:0 36px 24px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border:1px solid #e3e4e8;border-radius:12px;background:#f8f8f7;">'
        f"{rows}</table></td></tr>"
    )


def render_transactional_email(
    *,
    preheader: str,
    eyebrow: str,
    heading: str,
    body: Sequence[str],
    action: EmailAction | None = None,
    details: Sequence[EmailDetail] = (),
    highlights: Sequence[str] = (),
    footer: Sequence[str] = (),
) -> RenderedEmail:
    """Render a branded HTML email and an equivalent plain-text alternative."""

    body_html = "".join(
        '<p style="margin:0 0 14px;color:#5f6368;font-size:16px;line-height:1.65;">'
        f"{_safe(paragraph)}</p>"
        for paragraph in body
    )
    action_html = ""
    if action is not None:
        safe_url = _safe(action.url)
        action_html = (
            '<tr><td style="padding:2px 36px 24px;">'
            '<table role="presentation" cellspacing="0" cellpadding="0"><tr><td '
            'style="border-radius:10px;background:#050505;">'
            f'<a href="{safe_url}" style="display:inline-block;padding:13px 20px;'
            "color:#ffffff;text-decoration:none;font-size:14px;line-height:1.3;"
            f'font-weight:700;border-radius:10px;">{_safe(action.label)} &rarr;</a>'
            "</td></tr></table></td></tr>"
        )

    fallback_html = ""
    if action is not None:
        safe_url = _safe(action.url)
        fallback_html = (
            '<p style="margin:12px 0 0;color:#777b85;font-size:12px;line-height:1.6;">'
            "Button not working? Copy and paste this link into your browser:<br>"
            f'<a href="{safe_url}" style="color:#5558d9;text-decoration:underline;'
            f'word-break:break-all;">{safe_url}</a></p>'
        )

    footer_html = "".join(
        f'<p style="margin:0 0 8px;">{_safe(paragraph)}</p>' for paragraph in footer
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="supported-color-schemes" content="light">
  <title>{_safe(heading)}</title>
  <style>
    @media only screen and (max-width: 620px) {{
      .email-shell {{ padding: 20px 10px !important; }}
      .email-card {{ border-radius: 14px !important; }}
      .email-content {{ padding-left: 22px !important; padding-right: 22px !important; }}
      .email-heading {{ font-size: 29px !important; }}
      .detail-label, .detail-value {{ padding-left: 14px !important; padding-right: 14px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f5f5f3;color:#151619;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{_safe(preheader)}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;background:#f5f5f3;">
    <tr><td class="email-shell" align="center" style="padding:40px 16px;">
      <table class="email-card" role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e3e4e8;border-radius:18px;overflow:hidden;">
        <tr><td class="email-content" style="padding:30px 36px 22px;">
          <table role="presentation" cellspacing="0" cellpadding="0"><tr>
            <td style="vertical-align:bottom;padding-right:9px;white-space:nowrap;">
              <span style="display:inline-block;width:4px;height:9px;background:#6366f1;border-radius:2px;margin-right:3px;"></span>
              <span style="display:inline-block;width:4px;height:15px;background:#6366f1;border-radius:2px;margin-right:3px;"></span>
              <span style="display:inline-block;width:4px;height:22px;background:#6366f1;border-radius:2px;"></span>
            </td>
            <td style="color:#151619;font-size:18px;line-height:1;font-weight:650;letter-spacing:-.03em;">Lemma</td>
          </tr></table>
        </td></tr>
        <tr><td class="email-content" style="padding:0 36px 10px;color:#6366f1;font-size:11px;line-height:1.4;font-weight:750;letter-spacing:.12em;text-transform:uppercase;">{_safe(eyebrow)}</td></tr>
        <tr><td class="email-content" style="padding:0 36px 14px;">
          <h1 class="email-heading" style="margin:0;color:#151619;font-size:36px;line-height:1.12;font-weight:700;letter-spacing:-.035em;">{_safe(heading)}</h1>
        </td></tr>
        <tr><td class="email-content" style="padding:0 36px 10px;">{body_html}</td></tr>
        {action_html}
        {_detail_rows(details)}
        {_highlight_rows(highlights)}
        <tr><td class="email-content" style="padding:20px 36px 28px;border-top:1px solid #ececef;color:#777b85;font-size:12px;line-height:1.6;">
          {footer_html}{fallback_html}
          <p style="margin:16px 0 0;color:#9a9da5;">Lemma &middot; Build systems that do the work</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text_parts = [eyebrow, heading, *body]
    if details:
        text_parts.extend(f"{detail.label}: {detail.value}" for detail in details)
    if highlights:
        text_parts.extend(f"- {highlight}" for highlight in highlights)
    if action is not None:
        text_parts.append(f"{action.label}: {action.url}")
    text_parts.extend(footer)
    return RenderedEmail(html=html, text="\n\n".join(text_parts))
