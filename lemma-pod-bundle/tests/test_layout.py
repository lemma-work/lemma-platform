from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_pod_bundle.layout import (
    APP_MANIFEST_ALIAS,
    _file_path_key,
    _parse_function_headers,
    _read_export_contents,
    _read_json,
    _record_export_contents,
    _resource_manifest_path,
    load_resource_payload,
    normalize_resource_dir_name,
)


def test_normalize_resource_dir_name_aliases():
    assert normalize_resource_dir_name("Table") == "tables"
    assert normalize_resource_dir_name(" agents ") == "agents"
    assert normalize_resource_dir_name("nonsense") == ""


def test_read_json_rejects_non_object(tmp_path: Path):
    path = tmp_path / "x.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected JSON object"):
        _read_json(path)


def test_load_resource_payload_resolves_file_refs_and_toolsets(tmp_path: Path):
    resource_dir = tmp_path / "agents" / "helper"
    resource_dir.mkdir(parents=True)
    (resource_dir / "instruction.md").write_text("be helpful", encoding="utf-8")
    (resource_dir / "extra.json").write_text(json.dumps({"k": 1}), encoding="utf-8")
    (resource_dir / "helper.json").write_text(
        json.dumps(
            {
                "name": "helper",
                "instruction": {"$file": "instruction.md"},
                "config": {"$json_file": "extra.json"},
                "tool_sets": ["pod"],
            }
        ),
        encoding="utf-8",
    )
    payload = load_resource_payload(resource_dir, "helper")
    assert payload == {
        "name": "helper",
        "instruction": "be helpful",
        "config": {"k": 1},
        "toolsets": ["pod"],
    }


def test_resource_manifest_path_app_alias(tmp_path: Path):
    resource_dir = tmp_path / "apps" / "site"
    resource_dir.mkdir(parents=True)
    assert _resource_manifest_path(resource_dir, "site", resource_type="apps") is None
    (resource_dir / APP_MANIFEST_ALIAS).write_text("{}", encoding="utf-8")
    assert (
        _resource_manifest_path(resource_dir, "site", resource_type="apps")
        == resource_dir / APP_MANIFEST_ALIAS
    )
    # Alias only applies to apps.
    assert _resource_manifest_path(resource_dir, "site", resource_type="tables") is None


def test_record_and_read_export_contents(tmp_path: Path):
    (tmp_path / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    contents = _record_export_contents(
        tmp_path,
        included={"tables"},
        excluded=set(),
        names={"items"},
        with_data=True,
        with_files=False,
    )
    assert contents == {"resources": ["tables"], "names": ["items"], "with_data": True}
    assert _read_export_contents(tmp_path) == contents
    # No manifest -> empty on both sides.
    assert _record_export_contents(
        tmp_path / "nope", included=set(), excluded=set(), names=set(),
        with_data=False, with_files=False,
    ) == {}
    assert _read_export_contents(tmp_path / "nope") == {}


def test_parse_function_headers_stops_at_first_code_line():
    code = "#a: 1\n\n#b: two words\nx = 1\n#c: ignored\n"
    assert _parse_function_headers(code) == {"a": "1", "b": "two words"}


def test_file_path_key():
    assert _file_path_key(["a", "b", "c"]) == "a/b/c"
    assert _file_path_key([]) == ""
