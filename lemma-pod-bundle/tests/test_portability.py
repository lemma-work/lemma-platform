from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        json.dumps({"name": "daily", "pod_connector": "jira"}), encoding="utf-8"
    )
    (root / "surfaces" / "slack" / "slack.json").write_text(
        json.dumps({"platform": "SLACK", "pod_connector": "slack-shared"}),
        encoding="utf-8",
    )
    return root


def test_extract_portable_variables_rewrites_and_records(tmp_path: Path):
    root = _make_bundle(tmp_path)
    variables = _extract_portable_variables(root)

    assert set(variables) == {"approval_assignee"}
    assert variables["approval_assignee"]["type"] == "pod_member"
    assert variables["approval_assignee"]["source_value"] == "member-123"

    workflow = json.loads(
        (root / "workflows" / "approval" / "approval.json").read_text()
    )
    assert (
        workflow["nodes"][0]["config"]["assignee_pod_member_id"]
        == "${approval_assignee}"
    )
    pod_data = json.loads((root / "pod.json").read_text())
    assert set(pod_data["variables"]) == set(variables)


def test_a_pod_connector_slug_is_portable_as_it_is(tmp_path: Path):
    """Surfaces and schedules name a pod connector by its slug, which means
    the same thing in every pod that declares it -- nothing to tokenize, and
    no account variable for an importer to fill in."""
    root = _make_bundle(tmp_path)
    _extract_portable_variables(root)

    schedule = json.loads((root / "schedules" / "daily" / "daily.json").read_text())
    surface = json.loads((root / "surfaces" / "slack" / "slack.json").read_text())
    assert schedule["pod_connector"] == "jira"
    assert surface["pod_connector"] == "slack-shared"


def test_extract_portable_variables_is_idempotent(tmp_path: Path):
    root = _make_bundle(tmp_path)
    first = _extract_portable_variables(root)
    second = _extract_portable_variables(root)
    assert second == first


def test_extract_portable_variables_no_manifest(tmp_path: Path):
    assert _extract_portable_variables(tmp_path) == {}


def test_placeholder_round_trip_via_substitution(tmp_path: Path):
    """Extraction followed by textual substitution restores the raw ids."""
    root = _make_bundle(tmp_path)
    variables = _extract_portable_variables(root)
    text = (root / "workflows" / "approval" / "approval.json").read_text()
    for name, spec in variables.items():
        text = text.replace(_placeholder(name), spec["source_value"])
    assert (
        json.loads(text)["nodes"][0]["config"]["assignee_pod_member_id"] == "member-123"
    )


def test_tokenize_ref_fields_skips_templated_and_empty_values():
    node = {
        "assignee_pod_member_id": "${already_var}",
        "nested": [
            {"assignee_pod_member_id": "raw-id"},
            {"assignee_pod_member_id": ""},
        ],
        "other": "untouched",
    }
    changed = _tokenize_ref_fields(
        node,
        frozenset({"assignee_pod_member_id"}),
        lambda raw, container: f"${{var_{raw}}}",
    )
    assert changed is True
    assert node["assignee_pod_member_id"] == "${already_var}"
    assert node["nested"][0]["assignee_pod_member_id"] == "${var_raw-id}"
    assert node["nested"][1]["assignee_pod_member_id"] == ""
    assert node["other"] == "untouched"


def test_an_account_id_refuses_the_export(tmp_path: Path):
    """An account belongs to one organization and is its owner's to share, so
    a resource still naming one is an exporter bug: refuse rather than ship a
    bundle that leaks a source-org id and cannot work anywhere else."""
    root = tmp_path / "bundle"
    (root / "schedules" / "daily").mkdir(parents=True)
    (root / "pod.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
    (root / "schedules" / "daily" / "daily.json").write_text(
        json.dumps({"name": "daily", "account_id": "acct-456"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="pod_connector"):
        _extract_portable_variables(root)


def test_a_grant_on_an_account_is_left_out_of_the_bundle(tmp_path):
    """A workload's grant on a pod account decides nothing -- a shared account
    is used through a pod connector, and checked as the person -- and the
    account is never portable, so the grant is not carried at all. Grants on
    pod connectors, by slug, are."""
    (tmp_path / "pod.json").write_text(
        json.dumps({"name": "mailpod"}) + "\n", encoding="utf-8"
    )
    agent_dir = tmp_path / "agents" / "mailer"
    agent_dir.mkdir(parents=True)
    connector_grant = {
        "resource_type": "connector",
        "resource_name": "support-inbox",
        "permission_ids": ["connector.use"],
    }
    (agent_dir / "mailer.json").write_text(
        json.dumps(
            {
                "name": "mailer",
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "connector_account",
                            "resource_name": "11111111-2222-4333-8444-555555555555",
                            "permission_ids": ["connector_account.use"],
                        },
                        connector_grant,
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    variables = _extract_portable_variables(tmp_path)

    payload = json.loads((agent_dir / "mailer.json").read_text(encoding="utf-8"))
    assert payload["permissions"]["grants"] == [connector_grant]
    assert variables == {}


def test_strip_unresolved_placeholders():
    payload = {
        "assignee_pod_member_id": "${unset}",
        "name": "daily",
        "nested": {"x": "${gone}", "keep": "${not a placeholder}"},
        "items": [{"x": "${x}"}, "plain"],
    }
    stripped = _strip_unresolved_placeholders(payload)
    assert stripped == {
        "name": "daily",
        # "${not a placeholder}" contains spaces, so it is not a valid token.
        "nested": {"keep": "${not a placeholder}"},
        "items": [{}, "plain"],
    }
