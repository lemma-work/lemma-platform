from __future__ import annotations

import json
from pathlib import Path

from lemma_cli.cli_app.pod_requirements import (
    extract_requirements,
    read_requirements,
    unresolved_requirements,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _bundle(tmp_path: Path, variables: dict | None = None) -> Path:
    root = tmp_path / "acme-crm"
    pod: dict = {"format_version": 2, "name": "acme-crm"}
    if variables is not None:
        pod["variables"] = variables
    _write(root / "pod.json", pod)
    return root


def test_connector_from_account_variable_and_grant(tmp_path):
    root = _bundle(
        tmp_path,
        variables={
            "slack_account": {
                "type": "account",
                "source_value": "acc_123",
                "platform": "slack",
                "description": "Connector account for the slack surface",
            }
        },
    )
    # Surface references the account placeholder.
    _write(
        root / "surfaces" / "slack" / "slack.json",
        {"name": "slack", "platform": "SLACK", "account_id": "${slack_account}"},
    )
    # A function calls out to a different connector via a grant.
    _write(
        root / "functions" / "sync-invoices" / "sync-invoices.json",
        {
            "name": "sync-invoices",
            "permissions": {
                "grants": [
                    {"resource_type": "connector", "resource_name": "gmail",
                     "permission_ids": ["connector.use"]}
                ]
            },
        },
    )

    result = extract_requirements(root)
    connectors = {c["key"]: c for c in result["requirements"]["connectors"]}

    assert set(connectors) == {"slack", "gmail"}
    assert connectors["slack"]["binds_variable"] == "slack_account"
    assert connectors["slack"]["used_by"] == ["surfaces/slack"]
    assert connectors["slack"]["resolution"]["var"] == "slack_account"
    assert connectors["gmail"]["used_by"] == ["functions/sync-invoices"]
    assert connectors["gmail"]["resolution"]["match_on"] == "connector_id"


def test_member_requirement_defaults_to_importing_user(tmp_path):
    root = _bundle(
        tmp_path,
        variables={
            "expense_approval_assignee": {
                "type": "pod_member",
                "source_value": "pm_999",
                "description": "Pod member assigned in workflow 'expense-approval'",
            }
        },
    )
    _write(
        root / "workflows" / "expense-approval" / "expense-approval.json",
        {"name": "expense-approval", "nodes": [{"assignee_pod_member_id": "${expense_approval_assignee}"}]},
    )

    members = extract_requirements(root)["requirements"]["members"]
    assert len(members) == 1
    assert members[0]["used_by"] == ["workflows/expense-approval"]
    assert members[0]["resolution"]["strategy"] == "default_importing_user"
    # Member defaults to the importing user, so it never blocks import.
    assert unresolved_requirements(root) == []


def test_capabilities_are_tier_ordered_and_data_counts_rows(tmp_path):
    root = _bundle(tmp_path)
    _write(root / "functions" / "f1" / "f1.json", {"name": "f1"})
    _write(root / "agents" / "a1" / "a1.json", {"name": "a1"})
    _write(root / "tables" / "products" / "products.json", {"name": "products"})
    (root / "tables" / "products" / "data.csv").write_text(
        "id,name\n1,Widget\n2,Gadget\n3,Gizmo\n", encoding="utf-8"
    )
    # A connector grant so the external tier appears.
    _write(
        root / "functions" / "f1" / "f1.json",
        {"name": "f1", "permissions": {"grants": [
            {"resource_type": "connector", "resource_name": "stripe"}]}},
    )

    caps = extract_requirements(root)["capabilities"]
    tiers = [c["tier"] for c in caps]
    assert tiers == ["code", "external", "ai", "data"]
    data_cap = next(c for c in caps if c["tier"] == "data")
    assert "seeds 3 rows" in data_cap["summary"]

    data_req = extract_requirements(root)["requirements"]["data"]
    assert data_req["tables_with_seed"] == ["products"]
    assert data_req["row_count"] == 3


def test_extract_writes_into_pod_json_and_read_round_trips(tmp_path):
    root = _bundle(
        tmp_path,
        variables={"slack_account": {"type": "account", "platform": "slack"}},
    )
    _write(root / "surfaces" / "slack" / "slack.json",
           {"name": "slack", "platform": "SLACK", "account_id": "${slack_account}"})

    extract_requirements(root)
    pod_json = json.loads((root / "pod.json").read_text(encoding="utf-8"))
    assert "requirements" in pod_json and "capabilities" in pod_json

    # read_requirements prefers the persisted block over recomputing.
    summary = read_requirements(root)
    assert summary["requirements"]["connectors"][0]["key"] == "slack"


def test_unresolved_connector_blocks_until_var_supplied(tmp_path):
    root = _bundle(
        tmp_path,
        variables={"slack_account": {"type": "account", "platform": "slack"}},
    )
    _write(root / "surfaces" / "slack" / "slack.json",
           {"name": "slack", "platform": "SLACK", "account_id": "${slack_account}"})

    blocking = unresolved_requirements(root)
    assert [b["kind"] for b in blocking] == ["connector"]

    assert unresolved_requirements(root, supplied_vars={"slack_account"}) == []


def test_no_pod_json_returns_empty(tmp_path):
    assert extract_requirements(tmp_path / "missing") == {}
    assert read_requirements(tmp_path / "missing") == {"requirements": {}, "capabilities": []}
