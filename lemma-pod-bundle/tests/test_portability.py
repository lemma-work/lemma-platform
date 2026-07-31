from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_pod_bundle.portability import (
    _extract_portable_variables,
    _placeholder,
    _strip_unresolved_placeholders,
    _tokenize_ref_fields,
    require_account_variable_metadata,
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
        json.dumps(
            {
                "name": "daily",
                "account_id": "acct-456",
                "connector_id": "jira",
                "provider": "LEMMA",
            }
        ),
        encoding="utf-8",
    )
    (root / "surfaces" / "slack" / "slack.json").write_text(
        json.dumps(
            {
                "platform": "SLACK",
                "account_id": "acct-789",
                "connector_id": "slack",
                "provider": "COMPOSIO",
            }
        ),
        encoding="utf-8",
    )
    return root


def test_extract_portable_variables_rewrites_and_records(tmp_path: Path):
    root = _make_bundle(tmp_path)
    variables = _extract_portable_variables(root)

    assert set(variables) == {"approval_assignee", "daily_account", "slack_account"}
    assert variables["approval_assignee"]["type"] == "pod_member"
    assert variables["approval_assignee"]["source_value"] == "member-123"
    assert variables["daily_account"]["type"] == "account"
    assert variables["daily_account"]["connector"] == "jira"
    assert variables["daily_account"]["provider"] == "LEMMA"
    assert variables["slack_account"]["connector"] == "slack"
    assert variables["slack_account"]["provider"] == "COMPOSIO"

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
        node, frozenset({"account_id"}), lambda raw, container: f"${{var_{raw}}}"
    )
    assert changed is True
    assert node["account_id"] == "${already_var}"
    assert node["nested"][0]["account_id"] == "${var_raw-id}"
    assert node["nested"][1]["account_id"] == ""
    assert node["other"] == "untouched"


def test_tokenize_ref_fields_passes_container_for_sibling_lookup():
    node = {"account_id": "raw-id", "connector_id": "slack"}
    seen = {}

    def on_value(raw, container):
        seen["connector_id"] = container.get("connector_id")
        return f"${{var_{raw}}}"

    _tokenize_ref_fields(node, frozenset({"account_id"}), on_value)
    assert seen["connector_id"] == "slack"


def test_extract_portable_variables_covers_any_resource_type(tmp_path: Path):
    """The account tokenizer is a generic sweep, not a hardcoded per-resource
    list — a resource type invented after this code was written (e.g. a future
    'triggers' kind) is covered automatically."""
    root = tmp_path / "bundle"
    (root / "triggers" / "on_ticket").mkdir(parents=True)
    (root / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (root / "triggers" / "on_ticket" / "on_ticket.json").write_text(
        json.dumps(
            {
                "account_id": "acct-999",
                "connector_id": "jira",
                "provider": "COMPOSIO",
            }
        ),
        encoding="utf-8",
    )

    variables = _extract_portable_variables(root)

    assert variables["on_ticket_account"]["connector"] == "jira"
    assert variables["on_ticket_account"]["provider"] == "COMPOSIO"
    trigger = json.loads(
        (root / "triggers" / "on_ticket" / "on_ticket.json").read_text()
    )
    assert trigger["account_id"] == "${on_ticket_account}"


def test_extract_portable_variables_requires_connector_metadata(tmp_path: Path):
    """An account_id with no connector_id/provider sibling is an exporter bug —
    fail the export loudly instead of shipping an unresolvable variable."""
    root = tmp_path / "bundle"
    (root / "schedules" / "daily").mkdir(parents=True)
    (root / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (root / "schedules" / "daily" / "daily.json").write_text(
        json.dumps({"name": "daily", "account_id": "acct-456"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="connector_id/provider"):
        _extract_portable_variables(root)


def test_require_account_variable_metadata_accepts_complete_variables():
    require_account_variable_metadata(
        {
            "slack_account": {
                "type": "account",
                "source_value": "acct-1",
                "connector": "slack",
                "provider": "COMPOSIO",
            },
            "app_slug": {"type": "app_slug", "source_value": "my-app"},
        }
    )


def test_require_account_variable_metadata_rejects_missing_provider():
    with pytest.raises(ValueError, match="slack_account"):
        require_account_variable_metadata(
            {
                "slack_account": {
                    "type": "account",
                    "source_value": "acct-1",
                    "connector": "slack",
                }
            }
        )


def test_require_account_variable_metadata_rejects_missing_connector():
    with pytest.raises(ValueError, match="slack_account"):
        require_account_variable_metadata(
            {
                "slack_account": {
                    "type": "account",
                    "source_value": "acct-1",
                    "provider": "COMPOSIO",
                }
            }
        )


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
