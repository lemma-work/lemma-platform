from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.widget_html_validation import validate_widget_html

REPO_ROOT = Path(__file__).resolve().parents[6]
ASSET_ROOT = REPO_ROOT / "lemma-skills" / "lemma-widget" / "assets"

# The shared preamble every example pastes in. It is not a template — it carries
# no placeholders — so it is checked separately from the fragments.
PREAMBLE = "widget-tokens-v1.css"

SUBSTITUTIONS: dict[str, dict[str, str]] = {
    "widget-finding-v1.html": {
        "__WIDGET_SCOPE__": "Tickets · all queues",
        "__FOCUS_CLAIM__": "tickets are blocked — the most since June.",
        "__FOCUS_DELTA__": "+9",
        "__FOCUS_NOTE__": "Every one of them is waiting on the billing export fix.",
        "__FOCUS_VALUE__": "blocked",
        "__EMPTY_LABEL__": "tickets",
        "__GROUP_FIELD__": "status",
        "__TABLE_NAME__": "tickets",
    },
    "widget-table-v1.html": {
        "__WIDGET_TITLE__": "Blocked tickets",
        "__WIDGET_NOUN__": "Ticket",
        "__EMPTY_LABEL__": "tickets",
        "__TITLE_FIELD__": "title",
        "__SUBTITLE_FIELD__": "owner",
        "__STATUS_FIELD__": "status",
        "__DATE_FIELD__": "opened_at",
        "__DEFAULT_FILTER__": "blocked",
        "__TABLE_NAME__": "tickets",
        "__COMPOSE_LABEL__": "Why is the export blocking four of these?",
        "__COMPOSE_TEXT__": "Why is the billing export blocking four of these tickets?",
    },
    "widget-record-v1.html": {
        "__TITLE_FIELD__": "title",
        "__STATUS_FIELD__": "status",
        "__FIELD_CONFIG__": json.dumps(
            [{"label": "Owner", "field": "owner"}, {"label": "Queue", "field": "queue"}]
        ),
        "__TIMELINE_CONFIG__": json.dumps(
            [{"label": "Escalated from support", "field": "opened_at"}]
        ),
        "__TABLE_NAME__": "tickets",
        "__RECORD_ID__": "ticket-123",
        "__COMPOSE_LABEL__": "What would unblock this?",
        "__COMPOSE_TEXT__": "What would unblock the billing export ticket?",
    },
    "widget-trend-v1.html": {
        "__WIDGET_TITLE__": "Tickets opened per week",
        "__TABLE_NAME__": "tickets",
        "__DATE_FIELD__": "opened_at",
        "__BUCKET__": "week",
        "__BUCKET_NOUN__": "weeks",
        "__EMPTY_LABEL__": "tickets",
        "__RISE_READS__": "bad",
        "__FALL_READS__": "good",
    },
    "widget-ranked-v1.html": {
        "__WIDGET_TITLE__": "Tickets by queue",
        "__TABLE_NAME__": "tickets",
        "__GROUP_FIELD__": "queue",
        "__EMPTY_LABEL__": "tickets",
    },
    "widget-note-v1.html": {
        "__NOTE_KICKER__": "Billing export · three ways out",
        "__NOTE_CLAIM__": "Stream the export instead of buffering it.",
        "__NOTE_BECAUSE__": "All three clear the timeout; only one holds at ten times the rows.",
        "__NOTE_CAVEAT__": "The estimate assumes the bucket exists in production. It does not.",
        "__PICK_LABEL__": "Recommended",
        "__OPTION_1_NAME__": "Stream to object storage",
        "__OPTION_1_FIGURE__": "2",
        "__OPTION_1_UNIT__": "days",
        "__OPTION_1_WHY__": "Constant memory and the row count stops mattering.",
        "__OPTION_2_NAME__": "Raise the timeout",
        "__OPTION_2_FIGURE__": "2",
        "__OPTION_2_UNIT__": "hours",
        "__OPTION_2_WHY__": "Unblocks today and fails again at roughly 90k rows.",
        "__OPTION_3_NAME__": "Paginate the query",
        "__OPTION_3_FIGURE__": "5",
        "__OPTION_3_UNIT__": "days",
        "__OPTION_3_WHY__": "Every consumer has to learn to stitch pages together.",
    },
}


def _materialize(name: str) -> str:
    content = (ASSET_ROOT / name).read_text(encoding="utf-8")
    for token, value in SUBSTITUTIONS[name].items():
        content = content.replace(token, value)
    return content


def test_versioned_widget_example_set_is_complete():
    assert {path.name for path in ASSET_ROOT.glob("*.html")} == set(SUBSTITUTIONS)
    assert (ASSET_ROOT / PREAMBLE).is_file()


def test_versioned_widget_examples_require_token_replacement():
    for name in SUBSTITUTIONS:
        source = (ASSET_ROOT / name).read_text(encoding="utf-8")
        errors = validate_widget_html(source)
        assert any("unresolved widget example placeholders" in error for error in errors), (
            name
        )


def test_examples_do_not_assume_a_schema():
    """Column names arrive through placeholders, never guessed at in the file."""
    for name in SUBSTITUTIONS:
        source = (ASSET_ROOT / name).read_text(encoding="utf-8")
        assert "created_at" not in source, name
        assert "sort:" not in source, name


def test_every_example_carries_the_shared_preamble():
    """One block, one place to fix a colour. An example that hand-rolls its own
    palette is how the set drifted into four different greys the first time."""
    preamble = (ASSET_ROOT / PREAMBLE).read_text(encoding="utf-8")
    marker = preamble[preamble.index(":root {") : preamble.index("--w-ink:")]
    for name in SUBSTITUTIONS:
        assert marker in (ASSET_ROOT / name).read_text(encoding="utf-8"), name


def test_preamble_gives_every_token_a_fallback():
    """The host delivers the palette by postMessage, so it lands after first
    paint and never lands at all standalone. A bare reference renders nothing."""
    preamble = (ASSET_ROOT / PREAMBLE).read_text(encoding="utf-8")
    # Comments stripped first: the block explains this rule by quoting a bare
    # reference, and a check that cannot tell prose from a declaration would
    # fail on its own documentation.
    declarations = re.sub(r"/\*.*?\*/", "", preamble, flags=re.DOTALL)
    bare = re.findall(r"var\(\s*--lemma-widget-[a-z0-9-]+\s*\)", declarations)
    assert bare == [], bare


def test_no_example_writes_white_onto_a_fill():
    """`--accent` is a light colour in dark mode; white on it is an empty pill.
    Every fill takes its paired ink."""
    for name in SUBSTITUTIONS:
        content = _materialize(name)
        for fill, ink in (("--w-accent)", "--w-on-accent"), ("--w-bad)", "--w-on-bad")):
            for line in content.splitlines():
                if f"background: var({fill}" in line:
                    assert "#fff" not in line.lower() and "white" not in line.lower(), (
                        f"{name}: {line.strip()} — use var({ink})"
                    )


def test_materialized_widget_examples_satisfy_runtime_contract():
    for name in SUBSTITUTIONS:
        content = _materialize(name)
        assert 'data-lemma-widget-version="1"' in content, name
        assert "prefers-color-scheme: dark" in content, name
        assert "--lemma-widget-text" in content, name
        assert "--lemma-widget-accent" in content, name
        assert "--lemma-widget-color-scheme" in content, name
        assert "--lemma-widget-font" in content, name
        assert "[hidden]" in content and "display: none !important" in content, name
        assert validate_widget_html(content) == [], name


def test_the_note_example_needs_no_sdk():
    """A widget that shows what you already know should not open a connection to
    prove it. Nothing else in the set demonstrates that this is allowed."""
    content = _materialize("widget-note-v1.html")
    assert "LemmaClient" not in content
    assert "lemma-client.js" not in content
    assert validate_widget_html(content) == []


def test_chart_examples_carry_a_table_view_and_a_hover_layer():
    for name in ("widget-trend-v1.html", "widget-ranked-v1.html"):
        content = _materialize(name)
        assert "w-sr" in content, name
        assert "<table>" in content, name
        assert "mousemove" in content, name
