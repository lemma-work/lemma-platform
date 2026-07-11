from __future__ import annotations

import json
from pathlib import Path

from app.core.widget_html_validation import validate_widget_html

REPO_ROOT = Path(__file__).resolve().parents[6]
ASSET_ROOT = REPO_ROOT / "lemma-skills" / "lemma-widget" / "assets"

SUBSTITUTIONS: dict[str, dict[str, str]] = {
    "widget-starter-v1.html": {
        "__WIDGET_EYEBROW__": "Operations",
        "__WIDGET_TITLE__": "Ticket summary",
        "__EMPTY_LABEL__": "tickets",
        "__GROUP_FIELD__": "status",
        "__TABLE_NAME__": "tickets",
    },
    "widget-list-v1.html": {
        "__WIDGET_TITLE__": "Recent tickets",
        "__EMPTY_LABEL__": "tickets",
        "__TITLE_FIELD__": "title",
        "__SUBTITLE_FIELD__": "owner",
        "__STATUS_FIELD__": "status",
        "__TABLE_NAME__": "tickets",
    },
    "widget-chart-v1.html": {
        "__WIDGET_TITLE__": "Tickets by status",
        "__EMPTY_LABEL__": "tickets",
        "__TABLE_NAME__": "tickets",
        "__GROUP_FIELD__": "status",
    },
    "widget-detail-v1.html": {
        "__WIDGET_EYEBROW__": "Ticket",
        "__TITLE_FIELD__": "title",
        "__FIELD_CONFIG__": json.dumps(
            [
                {"label": "Owner", "field": "owner"},
                {"label": "Status", "field": "status"},
            ]
        ),
        "__TABLE_NAME__": "tickets",
        "__RECORD_ID__": "ticket-123",
    },
}


def _materialize(name: str) -> str:
    content = (ASSET_ROOT / name).read_text(encoding="utf-8")
    for token, value in SUBSTITUTIONS[name].items():
        content = content.replace(token, value)
    return content


def test_versioned_widget_starter_set_is_complete():
    assert {path.name for path in ASSET_ROOT.glob("*.html")} == set(SUBSTITUTIONS)


def test_versioned_widget_starters_require_token_replacement():
    for name in SUBSTITUTIONS:
        source = (ASSET_ROOT / name).read_text(encoding="utf-8")
        errors = validate_widget_html(source)
        assert any("unresolved widget starter tokens" in error for error in errors), (
            name
        )


def test_list_widget_uses_schema_agnostic_default_ordering():
    source = (ASSET_ROOT / "widget-list-v1.html").read_text(encoding="utf-8")
    assert "created_at" not in source
    assert "sort:" not in source


def test_materialized_widget_starters_satisfy_runtime_contract():
    for name in SUBSTITUTIONS:
        content = _materialize(name)
        assert 'data-lemma-widget-version="1"' in content, name
        assert "prefers-color-scheme:dark" in content.replace(" ", ""), name
        assert "--lemma-widget-text" in content, name
        assert "--lemma-widget-accent" in content, name
        assert "--lemma-widget-color-scheme" in content, name
        assert "--lemma-widget-font" in content, name
        assert "[hidden]" in content and "display:none !important" in content, name
        assert validate_widget_html(content) == [], name
