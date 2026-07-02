"""Unit tests for the published-repo README renderer — pure zip-in, markdown-out."""

from __future__ import annotations

import io
import json
import zipfile

from app.modules.pod_import.infrastructure.readme import (
    IMPORT_BADGE_URL,
    import_badge_markdown,
    render_readme,
    resource_counts,
)

_IMPORT_URL = "https://lemma.work/import/github/someone/trumpet"
_FRONTEND_URL = "https://lemma.work"


def _zip_with(entries: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data if isinstance(data, (bytes, str)) else json.dumps(data))
    return buffer.getvalue()


def _bundle_archive(**overrides: object) -> bytes:
    entries: dict[str, object] = {
        "trumpet/pod.json": {
            "format_version": 2,
            "name": "trumpet",
            "description": "Horn things.",
            "capabilities": [
                {"tier": "ai", "summary": "Run 1 AI agent"},
                {"tier": "data", "summary": "Create 1 table, seed 2 rows"},
            ],
            "requirements": {
                "connectors": [
                    {"key": "github", "platform": "github", "purpose": "publish repos"}
                ],
                "variables": [{"key": "api_base", "type": "string", "purpose": "target API"}],
                "members": [{"key": "assignee", "role": "workflow_assignee"}],
                "data": {"tables_with_seed": ["widgets"], "row_count": 2},
            },
        },
        "trumpet/tables/widgets/widgets.json": {
            "name": "widgets",
            "primary_key_column": "id",
            "columns": [{"name": "id", "type": "integer"}, {"name": "label", "type": "text"}],
        },
        "trumpet/tables/widgets/data.json": [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}],
        "trumpet/agents/greeter/greeter.json": {
            "name": "greeter",
            "instruction": "Say hi to everyone. Then keep the conversation going forever.",
            "description": "Greets | politely",
        },
    }
    entries.update(overrides)
    return _zip_with(entries)


def _render(archive: bytes) -> str:
    return render_readme("Trumpet", "A horn pod.", archive, _IMPORT_URL, _FRONTEND_URL)


def test_title_tagline_and_badge():
    readme = _render(_bundle_archive())
    assert readme.startswith("# Trumpet\n\n> A horn pod.")
    # The badge is a shields.io for-the-badge with the Lemma mark embedded as a
    # URL-encoded base64 data URI, linking to the import URL.
    assert import_badge_markdown(_IMPORT_URL) in readme
    assert "img.shields.io/badge/Import%20to-Lemma" in IMPORT_BADGE_URL
    assert "logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2C" in IMPORT_BADGE_URL
    assert f"]({_IMPORT_URL})" in readme
    assert f"Built with [Lemma]({_FRONTEND_URL})" in readme


def test_what_it_does_comes_from_pod_json_capabilities():
    readme = _render(_bundle_archive())
    assert "## What it does" in readme
    assert "- Run 1 AI agent" in readme
    assert "- Create 1 table, seed 2 rows" in readme


def test_what_it_does_falls_back_to_counts_without_capabilities():
    archive = _bundle_archive(**{"trumpet/pod.json": {"name": "trumpet"}})
    readme = _render(archive)
    assert "- 1 table" in readme
    assert "- 1 agent" in readme


def test_whats_inside_tables_carry_columns_and_seed_rows():
    readme = _render(_bundle_archive())
    assert "### Tables" in readme
    assert "| Name | Columns | Seed rows |" in readme
    assert "| widgets | 2 | 2 |" in readme


def test_seed_rows_column_is_omitted_when_no_table_ships_data():
    # Preview exports with_data=False, so no data.json anywhere in the archive.
    archive = _bundle_archive()
    entries = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist() if n != "trumpet/tables/widgets/data.json"}
    readme = _render(_zip_with(entries))
    assert "| Name | Columns |" in readme
    assert "Seed rows" not in readme


def test_agent_row_escapes_pipes_in_the_description():
    readme = _render(_bundle_archive())
    assert "### Agents" in readme
    assert "| greeter | Greets \\| politely |" in readme


def test_agent_description_falls_back_to_the_instructions_first_sentence():
    archive = _bundle_archive(
        **{
            "trumpet/agents/greeter/greeter.json": {
                "name": "greeter",
                "instruction": "Say hi to everyone. Then keep going.",
                "description": None,
            }
        }
    )
    readme = _render(archive)
    assert "| greeter | Say hi to everyone. |" in readme


def test_what_you_will_need_lists_connectors_variables_and_members():
    readme = _render(_bundle_archive())
    assert "## What you'll need" in readme
    assert "- A **github** connection — publish repos" in readme
    assert "- A value for `api_base` — target API" in readme
    assert "- A pod member for `assignee` — defaults to the importer" in readme
    assert "- Seed data ships in this repo — 2 rows across 1 table, loaded on import" in readme


def test_self_contained_pod_says_it_needs_nothing():
    archive = _zip_with({"trumpet/pod.json": {"name": "trumpet"}})
    readme = _render(archive)
    assert "Nothing — this pod is self-contained." in readme


def test_repo_layout_lists_only_present_kinds():
    readme = _render(_bundle_archive())
    assert "## Repo layout" in readme
    assert "pod.json" in readme
    assert "tables/" in readme
    assert "agents/" in readme
    assert "workflows/" not in readme


def test_a_malformed_manifest_is_skipped_not_fatal():
    archive = _bundle_archive(
        **{"trumpet/tables/broken/broken.json": b"{not json"}
    )
    readme = _render(archive)
    assert "| widgets |" in readme  # the good table still renders
    assert "broken" not in readme


def test_a_bundle_without_a_wrapper_folder_still_parses():
    archive = _bundle_archive()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        entries = {n[len("trumpet/") :]: zf.read(n) for n in zf.namelist()}
    readme = _render(_zip_with(entries))
    assert "| widgets | 2 | 2 |" in readme


def test_a_corrupt_archive_still_yields_a_minimal_readme():
    readme = render_readme("Trumpet", None, b"not a zip", _IMPORT_URL, _FRONTEND_URL)
    assert readme.startswith("# Trumpet")
    assert IMPORT_BADGE_URL in readme
    assert "## Get started" in readme


def test_resource_counts_reports_only_present_kinds():
    # Same manifest walk as the renderer: absent kinds are omitted entirely
    # rather than reported as zero.
    assert resource_counts(_bundle_archive()) == {"tables": 1, "agents": 1}


def test_resource_counts_of_a_corrupt_archive_is_empty():
    assert resource_counts(b"not a zip") == {}
