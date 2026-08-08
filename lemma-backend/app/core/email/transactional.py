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
            '<tr><td class="detail-label" style="padding:12px 16px 2px;'
            "color:#9a948a;font-size:10px;line-height:1.4;font-weight:500;"
            'letter-spacing:.09em;text-transform:uppercase;">'
            f"{_safe(detail.label)}</td></tr>"
            '<tr><td class="detail-value" style="padding:0 16px 12px;'
            'color:#24211d;font-size:14px;line-height:1.55;word-break:break-word;">'
            f"{_safe(detail.value)}</td></tr>"
        )
        for detail in details
    )
    return (
        '<tr><td style="padding:0 32px 26px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border:1px solid #e8e4dc;border-radius:12px;background:#fbfaf6;">'
        f"{rows}</table></td></tr>"
    )


def _highlight_rows(highlights: Sequence[str]) -> str:
    if not highlights:
        return ""
    rows = "".join(
        (
            '<tr><td width="28" style="padding:9px 0 9px 18px;vertical-align:top;'
            'color:#ff8200;font-size:17px;line-height:1.35;">&#8226;</td>'
            '<td style="padding:9px 16px 9px 5px;color:#6f6a62;font-size:14px;'
            f'line-height:1.55;">{_safe(highlight)}</td></tr>'
        )
        for highlight in highlights
    )
    return (
        '<tr><td style="padding:0 32px 26px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border:1px solid #e8e4dc;border-radius:12px;background:#fbfaf6;">'
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
        '<p style="margin:0 0 12px;color:#6f6a62;font-size:15px;line-height:1.65;">'
        f"{_safe(paragraph)}</p>"
        for paragraph in body
    )
    action_html = ""
    if action is not None:
        safe_url = _safe(action.url)
        action_html = (
            '<tr><td style="padding:10px 32px 22px;">'
            '<table role="presentation" cellspacing="0" cellpadding="0"><tr><td '
            'style="border-radius:10px;background:#24211d;">'
            f'<a href="{safe_url}" style="display:inline-block;padding:12px 20px;'
            "color:#fffdf8;text-decoration:none;font-size:14px;line-height:1.3;"
            f'font-weight:500;border-radius:10px;">{_safe(action.label)}</a>'
            "</td></tr></table></td></tr>"
        )

    fallback_html = ""
    if action is not None:
        safe_url = _safe(action.url)
        fallback_html = (
            '<p style="margin:10px 0 0;color:#9a948a;font-size:11.5px;line-height:1.6;">'
            "Button not working? Copy and paste this link into your browser:<br>"
            f'<a href="{safe_url}" style="color:#8a847b;text-decoration:underline;'
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
      .email-heading {{ font-size: 21px !important; }}
      .detail-label, .detail-value {{ padding-left: 14px !important; padding-right: 14px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f7f6f1;color:#24211d;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{_safe(preheader)}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;background:#f7f6f1;">
    <tr><td class="email-shell" align="center" style="padding:40px 16px;">
      <table class="email-card" role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;max-width:560px;background:#fffdf8;border:1px solid #e4e0d7;border-radius:16px;overflow:hidden;">
        <tr><td class="email-content" style="padding:28px 32px 20px;">
          <table role="presentation" cellspacing="0" cellpadding="0"><tr>
            <td style="vertical-align:bottom;padding-right:9px;white-space:nowrap;">
              <span style="display:inline-block;width:4px;height:9px;background:#24211d;border-radius:2px;margin-right:3px;"></span>
              <span style="display:inline-block;width:4px;height:15px;background:#24211d;border-radius:2px;margin-right:3px;"></span>
              <span style="display:inline-block;width:4px;height:22px;background:#24211d;border-radius:2px;"></span>
            </td>
            <td style="color:#24211d;font-size:16px;line-height:1;font-weight:500;letter-spacing:-.01em;">lemma</td>
          </tr></table>
        </td></tr>
        <tr><td class="email-content" style="padding:0 32px 10px;color:#9a948a;font-size:10px;line-height:1.4;font-weight:500;letter-spacing:.09em;text-transform:uppercase;">{_safe(eyebrow)}</td></tr>
        <tr><td class="email-content" style="padding:0 32px 12px;">
          <h1 class="email-heading" style="margin:0;color:#24211d;font-size:24px;line-height:1.25;font-weight:500;letter-spacing:-.012em;">{_safe(heading)}</h1>
        </td></tr>
        <tr><td class="email-content" style="padding:0 32px 8px;">{body_html}</td></tr>
        {action_html}
        {_detail_rows(details)}
        {_highlight_rows(highlights)}
        <tr><td class="email-content" style="padding:18px 32px 26px;border-top:1px solid #ece8e0;color:#8a847b;font-size:12px;line-height:1.6;">
          {footer_html}{fallback_html}
          <p style="margin:16px 0 0;color:#9a948a;">Lemma &middot; Build systems that do the work</p>
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
