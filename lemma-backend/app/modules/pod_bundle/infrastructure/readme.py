"""Render a bundle's README as a human-facing landing page for the pod.

The README is what someone sees when they land on the published GitHub repo, so
it reads like a product page: a centered header, a big **Run it on Lemma**
button (a shields.io ``for-the-badge`` badge carrying the Lemma mark) that
deep-links to the one-click importer (``/import/github/{owner}/{repo}``), a
"What's inside" summary, and install instructions. Deterministic; the optional AI
polish (:mod:`ai_readme`) only refines the copy of this same structure.
"""

from __future__ import annotations

import base64
import html
import re
from urllib.parse import urlencode


# Lemma ink.
_BRAND = "11110F"

# Display order + emoji for the "What's inside" table (surfaces read as
# "Connectors", the user-facing term).
_RESOURCE_META = [
    ("apps", "Apps", "🧩"),
    ("agents", "Agents", "🤖"),
    ("workflows", "Workflows", "🔀"),
    ("functions", "Functions", "⚙️"),
    ("tables", "Tables", "🗃️"),
    ("schedules", "Schedules", "⏰"),
    ("surfaces", "Connectors", "🔌"),
]

# The Lemma bar-chart mark, in white, used as the install badge's logo. Kept tiny
# and inline so the README is self-contained: shields.io bakes the data-URI logo
# into the badge it serves, so it renders on GitHub (which would otherwise strip a
# raw ``data:`` image).
_LEMMA_MARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect x="2" y="13" width="5" height="9" rx="1" fill="#fff"/>'
    '<rect x="9.5" y="8" width="5" height="14" rx="1" fill="#fff"/>'
    '<rect x="17" y="3" width="5" height="19" rx="1" fill="#fff"/></svg>'
)

_DEFAULT_TAGLINE = "Apps, agents, workflows, and data — ready to run with your team."


# The install link is deliberately not this deployment's frontend. A bundle's
# README is published to a public GitHub repo, so its reader is a stranger who
# has no account anywhere -- the one-click import has to land somewhere they can
# actually use, which is the hosted product.
#
# It used to read `frontend_base_url` then `app_base_url`, neither of which is a
# field on Settings, so both `getattr`s returned None and the constant below was
# always the answer. That reads as "configurable, with a fallback" and behaved as
# "always lemma.work"; the widened settings gate is what surfaced it.
_INSTALL_BASE_URL = "https://lemma.work"


def _lemma_logo_data_uri() -> str:
    encoded = base64.b64encode(_LEMMA_MARK_SVG.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def install_badge_url() -> str:
    """The shields.io badge image URL: a large ``for-the-badge`` pill in the Lemma
    brand color with the Lemma mark as its logo."""
    query = urlencode(
        {
            "style": "for-the-badge",
            "logo": _lemma_logo_data_uri(),
            "logoColor": "white",
        }
    )
    return f"https://img.shields.io/badge/Run%20it%20on%20Lemma-{_BRAND}?{query}"


def install_target(owner: str, repo: str) -> str:
    return f"{_INSTALL_BASE_URL}/import/github/{owner}/{repo}"


def install_badge(owner: str, repo: str) -> str:
    """A big, clickable install button: the branded badge linked to the one-click
    GitHub import route, sized up with an explicit height."""
    return (
        f'<a href="{install_target(owner, repo)}">'
        f'<img src="{install_badge_url()}" height="44" alt="Run it on Lemma" />'
        "</a>"
    )


def render_readme(
    *,
    pod_name: str,
    description: str | None,
    resource_counts: dict[str, int],
    owner: str,
    repo: str,
    icon_url: str | None = None,
) -> str:
    name = (pod_name or "Lemma Pod").strip() or "Lemma Pod"
    name = re.sub(r"[\r\n]+", " ", name)
    escaped_name = html.escape(name, quote=True)
    markdown_name = _escape_markdown(name)
    tagline = _escape_markdown(
        re.sub(
            r"[\r\n]+",
            " ",
            (description or "").strip() or _DEFAULT_TAGLINE,
        )
    )

    present = [
        (label, emoji, resource_counts.get(key, 0))
        for key, label, emoji in _RESOURCE_META
        if resource_counts.get(key, 0) > 0
    ]

    lines: list[str] = ['<div align="center">', ""]
    safe_icon_url = _safe_icon_url(icon_url)
    if safe_icon_url:
        lines += [
            f'<img src="{safe_icon_url}" width="88" height="88" '
            f'alt="{escaped_name}" />',
            "",
        ]
    lines += [
        f'<img src="./social-card.png" width="100%" alt="Run {escaped_name} on Lemma" />',
        "",
        f"# {markdown_name}",
        "",
        f"### {tagline}",
        "",
        install_badge(owner, repo),
        "",
        "</div>",
        "",
    ]

    if present:
        lines += [
            "## ✨ What's inside",
            "",
            "|  | Resource | Count |",
            "| :-: | :-- | --: |",
        ]
        lines += [
            f"| {emoji} | **{label}** | {count} |" for label, emoji, count in present
        ]
        lines += [""]

    lines += [
        "## 🚀 Install",
        "",
        f"**One click** — press **Run it on Lemma** above "
        f"(or [open the installer]({install_target(owner, repo)})).",
        "",
        "**From inside Lemma** — go to **Settings → Share & Export → Import from "
        f"GitHub** and paste `{owner}/{repo}`.",
        "",
        "Everything installs into *your own* workspace — your data, your model keys. "
        "Connectors reconnect to *your* accounts and apps rebuild for your pod on "
        "import, so nothing is shared but the recipe.",
        "",
        "---",
        "",
        '<div align="center">',
        "",
        "<sub>Run your apps and agents. Bring your team. "
        '<a href="https://lemma.work">Lemma</a>.</sub>',
        "",
        "</div>",
        "",
    ]
    return "\n".join(lines)


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", value)


def _safe_icon_url(value: str | None) -> str | None:
    if not value:
        return None
    from urllib.parse import urlparse

    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return html.escape(normalized, quote=True)
