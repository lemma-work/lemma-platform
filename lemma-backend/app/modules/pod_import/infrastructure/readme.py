"""README renderer for a published pod repo — the pod's storefront on GitHub.

The README is the only thing most people ever see of a published pod (the
badge link is the funnel into import), so it is built from the bundle itself:
pod.json's requirements/capabilities plus every resource manifest, rendered
into factual sections rather than a bare counts list. Deterministic on
purpose — the optional AI pass (ai_readme.py) only polishes prose on top of
this draft, so publish never depends on a model being configured.

Parsing is deliberately forgiving: this runs on a freshly exported archive
(top-level wrapper folder present, no ``.chunkNNNNofMMMM`` suffixes yet), but
pod.json is still located at the shallowest depth rather than a fixed path,
and one malformed manifest skips that entry instead of costing the README.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from typing import Any

_RESOURCE_KINDS = ("tables", "functions", "agents", "workflows", "schedules", "surfaces", "apps")

# Source SVG for the badge logo — the Lemma mark (three rounded #D99A32 bars on
# a transparent background), redrawn minimal for a data URI:
#   <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
#     <rect x="14" y="30" width="10" height="24" rx="3" fill="#D99A32"/>
#     <rect x="27" y="18" width="10" height="36" rx="3" fill="#D99A32"/>
#     <rect x="40" y="6" width="10" height="48" rx="3" fill="#D99A32"/>
#   </svg>
# shields.io requires the logo query param URL-encoded, and raw base64 carries
# +/= which a query parser would eat — so the whole data URI is precomputed as
# quote(f"data:image/svg+xml;base64,{b64}", safe="") and hardcoded here.
IMPORT_BADGE_URL = (
    "https://img.shields.io/badge/Import%20to-Lemma-1a1a1a?style=for-the-badge"
    "&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53"
    "My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI%2BPHJlY3QgeD0iMTQiIHk9IjMw"
    "IiB3aWR0aD0iMTAiIGhlaWdodD0iMjQiIHJ4PSIzIiBmaWxsPSIjRDk5QTMyIi8%2BPHJlY3Qg"
    "eD0iMjciIHk9IjE4IiB3aWR0aD0iMTAiIGhlaWdodD0iMzYiIHJ4PSIzIiBmaWxsPSIjRDk5QT"
    "MyIi8%2BPHJlY3QgeD0iNDAiIHk9IjYiIHdpZHRoPSIxMCIgaGVpZ2h0PSI0OCIgcng9IjMiIG"
    "ZpbGw9IiNEOTlBMzIiLz48L3N2Zz4%3D"
)

_GENERIC_TAGLINE = "A ready-to-import Lemma pod — agents, data, and automations in one bundle."

# Reader-facing order: agents lead (they're the headline), plumbing follows.
_INSIDE_ORDER = ("agents", "workflows", "functions", "tables", "schedules", "surfaces", "apps")
_KIND_TITLES = {
    "agents": "Agents",
    "workflows": "Workflows",
    "functions": "Functions",
    "tables": "Tables",
    "schedules": "Schedules",
    "surfaces": "Surfaces",
    "apps": "Apps",
}
_KIND_LABELS = {
    "tables": "table",
    "functions": "function",
    "agents": "agent",
    "workflows": "workflow",
    "schedules": "schedule",
    "surfaces": "surface",
    "apps": "app",
}
_LAYOUT_COMMENTS = {
    "tables": "table schemas + seed data",
    "functions": "Python functions (code + config)",
    "agents": "agent instructions, tools, permissions",
    "workflows": "multi-step automations",
    "schedules": "cron and event triggers",
    "surfaces": "chat-platform wiring",
    "apps": "bundled web apps",
}

_DESCRIPTION_CELL_MAX = 120


def import_badge_markdown(import_url: str) -> str:
    """The README's own badge line — also handed back to the publisher
    (import_badge_markdown in the publish result) for pasting elsewhere."""
    return f"[![Import to Lemma]({IMPORT_BADGE_URL})]({import_url})"


def render_readme(
    pod_name: str,
    description: str | None,
    archive: bytes,
    import_url: str,
    frontend_url: str,
) -> str:
    pod, manifests, seed_rows = _bundle_contents(archive)
    title = pod_name or str(pod.get("name") or "Lemma pod")
    tagline = _one_line(description or pod.get("description") or "") or _GENERIC_TAGLINE

    lines = [
        f"# {title}",
        "",
        f"> {tagline}",
        "",
        import_badge_markdown(import_url),
        "",
        *_what_it_does(pod, manifests),
        *_whats_inside(manifests, seed_rows),
        *_what_you_will_need(pod),
        *_get_started(),
        *_repo_layout(manifests),
        "---",
        "",
        f"Built with [Lemma]({frontend_url}) — portable, remixable AI workspaces. "
        "This repo is a complete pod bundle: everything above is created in your "
        "workspace when you import it.",
        "",
    ]
    return "\n".join(lines)


def resource_counts(archive: bytes) -> dict[str, int]:
    """Non-zero resource counts per kind, e.g. ``{"tables": 5, "agents": 1}`` —
    the publish preview's at-a-glance summary. Counted from the same
    wrapper-tolerant manifest walk the renderer uses, so the two never
    disagree about what's in the bundle."""
    _, manifests, _ = _bundle_contents(archive)
    return {kind: len(items) for kind, items in manifests.items() if items}


def _bundle_contents(
    archive: bytes,
) -> tuple[dict[str, Any], dict[str, list[tuple[str, dict[str, Any]]]], dict[str, int]]:
    """``(pod.json, {kind: [(name, manifest)]}, seed-row counts per table)``.

    The bundle root is wherever the shallowest pod.json sits (export archives
    carry a ``<pod_name>/`` wrapper; a bare bundle doesn't), and everything is
    resolved relative to it. A corrupt zip or a manifest with bad JSON degrades
    to "not there" — the README must render from whatever is readable.
    """
    empty: dict[str, list[tuple[str, dict[str, Any]]]] = {k: [] for k in _RESOURCE_KINDS}
    try:
        entries: dict[str, bytes] = {}
        with zipfile.ZipFile(BytesIO(archive)) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    entries[info.filename] = zf.read(info)
    except (zipfile.BadZipFile, OSError):
        return {}, empty, {}

    pod_paths = [path for path in entries if path.rsplit("/", 1)[-1] == "pod.json"]
    if not pod_paths:
        return {}, empty, {}
    pod_path = min(pod_paths, key=lambda path: path.count("/"))
    root = pod_path[: -len("pod.json")]
    pod = _parse_json_object(entries[pod_path])

    manifests: dict[str, list[tuple[str, dict[str, Any]]]] = {k: [] for k in _RESOURCE_KINDS}
    seed_rows: dict[str, int] = {}
    for kind in _RESOURCE_KINDS:
        base = f"{root}{kind}/"
        names = sorted(
            {
                path[len(base) :].split("/", 1)[0]
                for path in entries
                if path.startswith(base) and "/" in path[len(base) :]
            }
        )
        for name in names:
            manifest = _parse_json_object(entries.get(f"{base}{name}/{name}.json", b""))
            if not manifest:
                continue  # malformed/missing manifest — skip the entry, keep the README
            manifests[kind].append((name, manifest))
            if kind == "tables":
                rows = _parse_json(entries.get(f"{base}{name}/data.json"))
                if isinstance(rows, list):
                    seed_rows[name] = len(rows)
    return pod, manifests, seed_rows


def _parse_json(data: bytes | None) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _parse_json_object(data: bytes | None) -> dict[str, Any]:
    payload = _parse_json(data)
    return payload if isinstance(payload, dict) else {}


def _what_it_does(
    pod: dict[str, Any], manifests: dict[str, list[tuple[str, dict[str, Any]]]]
) -> list[str]:
    bullets = [
        f"- {_one_line(cap['summary'])}"
        for cap in (pod.get("capabilities") or [])
        if isinstance(cap, dict) and cap.get("summary")
    ]
    if not bullets:
        # Old bundle without a capabilities block — fall back to plain counts.
        for kind in _RESOURCE_KINDS:
            n = len(manifests[kind])
            if n:
                bullets.append(f"- {n} {_KIND_LABELS[kind]}{_plural(n)}")
    if not bullets:
        return []
    return ["## What it does", "", *bullets, ""]


def _whats_inside(
    manifests: dict[str, list[tuple[str, dict[str, Any]]]], seed_rows: dict[str, int]
) -> list[str]:
    lines: list[str] = []
    for kind in _INSIDE_ORDER:
        items = manifests[kind]
        if not items:
            continue
        headers, rows = _kind_table(kind, items, seed_rows)
        lines += [f"### {_KIND_TITLES[kind]}", "", *_markdown_table(headers, rows), ""]
    if not lines:
        return []
    return ["## What's inside", "", *lines]


def _kind_table(
    kind: str, items: list[tuple[str, dict[str, Any]]], seed_rows: dict[str, int]
) -> tuple[list[str], list[list[str]]]:
    if kind == "agents":
        return ["Name", "Description"], [
            [_cell(m.get("name") or name), _cell(_agent_description(m))] for name, m in items
        ]
    if kind == "functions":
        return ["Name", "Type", "Description"], [
            [_cell(m.get("name") or name), _cell(m.get("type")), _cell(m.get("description"))]
            for name, m in items
        ]
    if kind == "tables":
        # Preview exports skip row data (with_data=False), so the Seed rows
        # column only appears when at least one table actually shipped rows.
        headers = ["Name", "Columns"] + (["Seed rows"] if seed_rows else [])
        rows = []
        for name, m in items:
            row = [_cell(m.get("name") or name), str(len(m.get("columns") or []))]
            if seed_rows:
                row.append(str(seed_rows[name]) if name in seed_rows else "—")
            rows.append(row)
        return headers, rows
    if kind == "workflows":
        return ["Name", "Steps", "Description"], [
            [
                _cell(m.get("name") or name),
                str(len(m.get("nodes") or [])),
                _cell(m.get("description")),
            ]
            for name, m in items
        ]
    if kind == "schedules":
        return ["Name", "Schedule", "Runs"], [
            [
                _cell(m.get("name") or name),
                _cell(m.get("schedule_type")),
                _cell(m.get("agent_name") or m.get("workflow_name") or "—"),
            ]
            for name, m in items
        ]
    if kind == "surfaces":
        return ["Platform"], [[_cell(m.get("platform") or name)] for name, m in items]
    return ["Name", "Description"], [
        [_cell(m.get("name") or name), _cell(m.get("description"))] for name, m in items
    ]


def _agent_description(manifest: dict[str, Any]) -> str:
    description = _one_line(manifest.get("description") or "")
    if description:
        return description
    # No description on the agent — the first sentence of its instruction is
    # the closest thing to one, capped so a prompt wall doesn't blow the table.
    instruction = _one_line(manifest.get("instruction") or "")
    first_sentence = re.split(r"(?<=[.!?])\s", instruction, maxsplit=1)[0]
    return _truncate(first_sentence, _DESCRIPTION_CELL_MAX)


def _what_you_will_need(pod: dict[str, Any]) -> list[str]:
    requirements = pod.get("requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    bullets: list[str] = []
    for connector in requirements.get("connectors") or []:
        if not isinstance(connector, dict):
            continue
        key = connector.get("platform") or connector.get("key") or "connector"
        purpose = _one_line(connector.get("purpose") or "")
        bullets.append(f"- A **{key}** connection" + (f" — {purpose}" if purpose else ""))
    for variable in requirements.get("variables") or []:
        if not isinstance(variable, dict) or not variable.get("key"):
            continue
        purpose = _one_line(variable.get("purpose") or "")
        bullets.append(
            f"- A value for `{variable['key']}`" + (f" — {purpose}" if purpose else "")
        )
    for member in requirements.get("members") or []:
        if not isinstance(member, dict) or not member.get("key"):
            continue
        bullets.append(f"- A pod member for `{member['key']}` — defaults to the importer")
    data = requirements.get("data")
    if isinstance(data, dict) and data.get("tables_with_seed"):
        n_tables = len(data["tables_with_seed"])
        n_rows = int(data.get("row_count") or 0)
        bullets.append(
            f"- Seed data ships in this repo — {n_rows} row{_plural(n_rows)} across "
            f"{n_tables} table{_plural(n_tables)}, loaded on import"
        )
    lines = ["## What you'll need", ""]
    lines += bullets if bullets else ["Nothing — this pod is self-contained."]
    return [*lines, ""]


def _get_started() -> list[str]:
    return [
        "## Get started",
        "",
        "1. Click **Import to Lemma** above.",
        "2. Review what the pod does and what it needs.",
        "3. Apply — everything is created in your own workspace.",
        "",
        "Or download this repo as a zip and upload it at **Pod settings → Import** "
        "in any Lemma pod.",
        "",
    ]


def _repo_layout(manifests: dict[str, list[tuple[str, dict[str, Any]]]]) -> list[str]:
    rows = [("pod.json", "name, requirements, capabilities")]
    rows += [(f"{kind}/", _LAYOUT_COMMENTS[kind]) for kind in _RESOURCE_KINDS if manifests[kind]]
    width = max(len(path) for path, _ in rows)
    return [
        "## Repo layout",
        "",
        "```",
        *[f"{path.ljust(width)}  # {comment}" for path, comment in rows],
        "```",
        "",
    ]


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(" --- " for _ in headers) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _cell(text: object) -> str:
    # Pipes would split the cell, newlines would break the row.
    return _one_line(str(text or "")).replace("|", "\\|") or "—"


def _one_line(text: object) -> str:
    return " ".join(str(text or "").split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _plural(n: int) -> str:
    return "" if n == 1 else "s"
