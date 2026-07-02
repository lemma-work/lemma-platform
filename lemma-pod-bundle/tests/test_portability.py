from __future__ import annotations

import json
from pathlib import Path

from lemma_pod_bundle.portability import (
    _extract_portable_variables,
    _placeholder,
    _strip_unresolved_placeholders,
    _tokenize_ref_fields,
)


def _make_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "workflows" / "approval").mkdir(parents=True)
    (root / "schedules" / "daily").mkdir(parents=True)
    (root / "surfaces" / "slack").mkdir(parents=True)
    (root / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (root / "workflows" / "approval" / "approval.json").write_text(
        json.dumps(
            {
                "name": "approval",
                "nodes": [{"config": {"assignee_pod_member_id": "member-123"}}],
            }
        ),
        encoding="utf-8",
    )
    (root / "schedules" / "daily" / "daily.json").write_text(
        json.dumps({"name": "daily", "account_id": "acct-456"}), encoding="utf-8"
    )
    (root / "surfaces" / "slack" / "slack.json").write_text(
        json.dumps({"platform": "SLACK", "account_id": "acct-789"}), encoding="utf-8"
    )
    return root


def test_extract_portable_variables_rewrites_and_records(tmp_path: Path):
    root = _make_bundle(tmp_path)
    variables = _extract_portable_variables(root)

    assert set(variables) == {"approval_assignee", "daily_account", "slack_account"}
    assert variables["approval_assignee"]["type"] == "pod_member"
    assert variables["approval_assignee"]["source_value"] == "member-123"
    assert variables["daily_account"]["type"] == "account"
    assert variables["slack_account"]["platform"] == "slack"

    # Resource files now hold ${name} placeholders in place of the raw ids.
    workflow = json.loads((root / "workflows" / "approval" / "approval.json").read_text())
    assert workflow["nodes"][0]["config"]["assignee_pod_member_id"] == "${approval_assignee}"
    schedule = json.loads((root / "schedules" / "daily" / "daily.json").read_text())
    assert schedule["account_id"] == "${daily_account}"

    # Variables are recorded in the pod manifest.
    pod_data = json.loads((root / "pod.json").read_text())
    assert set(pod_data["variables"]) == set(variables)


def test_extract_portable_variables_is_idempotent(tmp_path: Path):
    root = _make_bundle(tmp_path)
    first = _extract_portable_variables(root)
    second = _extract_portable_variables(root)
    assert second == first
    workflow = json.loads((root / "workflows" / "approval" / "approval.json").read_text())
    assert workflow["nodes"][0]["config"]["assignee_pod_member_id"] == "${approval_assignee}"


def test_extract_portable_variables_no_manifest(tmp_path: Path):
    assert _extract_portable_variables(tmp_path) == {}


def test_placeholder_round_trip_via_substitution(tmp_path: Path):
    """Extraction followed by textual substitution restores the raw ids."""
    root = _make_bundle(tmp_path)
    variables = _extract_portable_variables(root)
    replacements = {
        _placeholder(name): spec["source_value"] for name, spec in variables.items()
    }
    schedule_text = (root / "schedules" / "daily" / "daily.json").read_text()
    for token, value in replacements.items():
        schedule_text = schedule_text.replace(token, value)
    assert json.loads(schedule_text)["account_id"] == "acct-456"


def test_tokenize_ref_fields_skips_templated_and_empty_values():
    node = {
        "account_id": "${already_var}",
        "nested": [{"account_id": "raw-id"}, {"account_id": ""}],
        "other": "untouched",
    }
    changed = _tokenize_ref_fields(
        node, frozenset({"account_id"}), lambda raw: f"${{var_{raw}}}"
    )
    assert changed is True
    assert node["account_id"] == "${already_var}"
    assert node["nested"][0]["account_id"] == "${var_raw-id}"
    assert node["nested"][1]["account_id"] == ""
    assert node["other"] == "untouched"


def test_strip_unresolved_placeholders():
    payload = {
        "account_id": "${unset_account}",
        "name": "daily",
        "nested": {"account_id": "${gone}", "keep": "${not a placeholder}"},
        "items": [{"account_id": "${x}"}, "plain"],
    }
    stripped = _strip_unresolved_placeholders(payload)
    assert stripped == {
        "name": "daily",
        # "${not a placeholder}" contains spaces, so it is not a valid token.
        "nested": {"keep": "${not a placeholder}"},
        "items": [{}, "plain"],
    }
