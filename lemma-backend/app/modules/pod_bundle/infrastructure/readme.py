"""Render a bundle's README (deterministic; optional AI polish lives elsewhere).

The README documents what the published pod contains and carries an install
badge that deep-links to the importing UI (``/import/github/{owner}/{repo}``), so
anyone viewing the repo can one-click install it into their own workspace.
"""

from __future__ import annotations

from app.core.config import settings

_RESOURCE_LABELS = [
    ("tables", "Tables"),
    ("agents", "Agents"),
    ("functions", "Functions"),
    ("workflows", "Workflows"),
    ("schedules", "Schedules"),
    ("apps", "Apps"),
    ("surfaces", "Surfaces"),
]


def _app_base_url() -> str:
    base = getattr(settings, "frontend_base_url", None) or getattr(
        settings, "app_base_url", None
    )
    return str(base).rstrip("/") if base else "https://lemma.work"


def install_badge(owner: str, repo: str) -> str:
    app = _app_base_url()
    target = f"{app}/import/github/{owner}/{repo}"
    badge = "https://img.shields.io/badge/Install%20to-Lemma-6D3BEB"
    return f"[![Install to Lemma]({badge})]({target})"


def render_readme(
    *,
    pod_name: str,
    description: str | None,
    resource_counts: dict[str, int],
    owner: str,
    repo: str,
) -> str:
    lines: list[str] = [f"# {pod_name}", ""]
    if description:
        lines += [description.strip(), ""]
    lines += [install_badge(owner, repo), ""]

    present = [
        (label, resource_counts.get(key, 0))
        for key, label in _RESOURCE_LABELS
        if resource_counts.get(key, 0) > 0
    ]
    if present:
        lines += ["## What's inside", ""]
        lines += [f"- **{label}:** {count}" for label, count in present]
        lines += [""]

    lines += [
        "## Install",
        "",
        "Click the badge above, or import this repo from Lemma: "
        "**Settings → Share & Export → Import from GitHub**.",
        "",
        "---",
        "",
        "_Exported from [Lemma](https://lemma.work) — the open workspace for "
        "humans and AI agents._",
        "",
    ]
    return "\n".join(lines)
