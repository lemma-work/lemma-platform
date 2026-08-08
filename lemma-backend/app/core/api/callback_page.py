"""The page a provider redirects a browser back to when a connection finishes.

OAuth callbacks and Microsoft's admin-consent callback both land a real person
on a page they did not navigate to, in a tab that is usually not the one Lemma
is running in. They need the same three things — which app, which account, and
a way back — so they share one template rather than each module growing its own.

Lives in ``core`` because ``app.modules.*`` may not import across modules.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from string import Template

from fastapi.responses import HTMLResponse

from app.core.config import settings

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "callback_result.html"

# Provider error codes are a bounded vocabulary (RFC 6749 §4.1.2.1 plus provider
# extensions), all of which fit this shape.
_PROVIDER_ERROR_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_UNKNOWN_APP_GLYPH = (
    '<svg class="glyph" width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true">'
    '<path d="M9.5 14.5l5-5"/>'
    '<path d="M13 7.5l1.2-1.2a3.3 3.3 0 014.7 4.7L17.7 12"/>'
    '<path d="M11 16.5l-1.2 1.2a3.3 3.3 0 01-4.7-4.7L6.3 12"/>'
    "</svg>"
)


def safe_provider_error(error: str) -> str:
    """Reduce a provider error to something safe to show and to log.

    Never reflect the provider's string back verbatim: anything outside the
    bounded code shape is not information worth relaying, and reflecting it is
    exactly what makes these callbacks an injection surface.
    """
    candidate = (error or "").strip()
    return candidate if _PROVIDER_ERROR_RE.match(candidate) else "unrecognized_error"


def _monogram(app_label: str) -> str:
    """First letter of each of the first two words — "Google Sheets" -> "GS".

    Mirrors the frontend's connector monogram so an app without a usable logo
    looks the same here as it does in the connectors list.
    """
    initials = "".join(word[:1] for word in app_label.split()[:2])
    return (initials or "?").upper()


def _icon_html(icon: str | None, app_label: str, logo_asset: str | None) -> str:
    """Render the app's brand mark, or the closest honest stand-in.

    Three sources, in order of how much we trust them:

    - an absolute http(s) icon from the connector catalog;
    - a logo the frontend ships under ``/connector-logos`` — the apps Lemma
      supports natively are synced from ``lemma_apps_config.json`` and can reach
      us with no catalog icon at all, and that asset is the only mark they have;
    - the monogram, which is what the connectors list falls back to as well.

    A relative catalog value is ignored rather than guessed at: it would resolve
    against the API origin, which serves no logos. And a provider error can
    arrive before we know which app it was about — that case gets a neutral
    glyph rather than a monogram for an app we are only guessing at.

    The monogram is always rendered underneath, so an image that 404s uncovers
    it instead of leaving a broken tile. That mirrors the frontend's own
    ``onError`` fallback.
    """
    if not app_label:
        return _UNKNOWN_APP_GLYPH

    monogram = f'<span class="monogram">{escape(_monogram(app_label))}</span>'
    if icon and icon.startswith(("https://", "http://")):
        source = icon
    elif logo_asset:
        source = f"{settings.frontend_url.rstrip('/')}/connector-logos/{logo_asset}"
    else:
        return monogram
    return (
        f'{monogram}<img src="{escape(source, quote=True)}" alt="" '
        'aria-hidden="true" onerror="this.remove()">'
    )


def identity_html(display_name: str | None, email: str | None) -> str:
    """The account the provider handed back, or nothing at all.

    An account whose provider told us neither name nor email gets no block: the
    app name in the heading is already the whole story, and a placeholder line
    would only restate it.
    """
    lines = [escape(value) for value in (email, display_name) if value]
    if not lines:
        return ""
    head, *rest = lines
    tail = "".join(f"<br>{line}" for line in rest)
    return f'<p class="body">Connected as <strong>{head}</strong>{tail}</p>'


def message_html(message: str) -> str:
    return f'<p class="body">{escape(message)}</p>'


def sentence(text: str) -> str:
    """Upstream messages are written without a guaranteed full stop, and we read
    one straight into a following sentence."""
    stripped = text.strip()
    return stripped if stripped.endswith((".", "!", "?")) else f"{stripped}."


def render_callback_page(
    *,
    succeeded: bool,
    app_label: str,
    icon: str | None,
    title: str,
    body_html: str,
    logo_asset: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return HTMLResponse(
        content=template.substitute(
            # `title` lands in <title> and <h1>, both text contexts — escaping
            # quotes there only turns readable copy into entities.
            title=escape(title),
            icon=_icon_html(icon, app_label, logo_asset),
            link_class="link" if succeeded else "link link--broken",
            body=body_html,
            action_href=escape(
                f"{settings.frontend_url.rstrip('/')}/connectors", quote=True
            ),
        ),
        status_code=status_code,
    )
