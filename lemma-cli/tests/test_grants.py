"""Grant vocabulary and the live-pod grant commands.

One spec grammar serves both targets: `lemma <kind> grant` edits a bundle file,
`lemma <kind> permissions add/remove` edits a live pod. They share
`parse_grant_spec` / `merge_grants` / `subtract_grants` so the two can't drift.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from lemma_cli.cli_app.scaffold import (
    PERMISSION_PRESETS,
    ScaffoldError,
    merge_grants,
    parse_grant_spec,
    subtract_grants,
)
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import _grants

runner = CliRunner()


# --------------------------------------------------------------------------- #
# spec grammar
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "spec,expected",
    [
        (
            "tickets:read,write",
            {
                "resource_type": "datastore_table",
                "resource_name": "tickets",
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            },
        ),
        (
            "/knowledge:read",
            {
                "resource_type": "folder",
                "resource_name": "/knowledge",
                "permission_ids": ["folder.read"],
            },
        ),
        (
            "connector:gmail:use",
            {
                "resource_type": "connector",
                "resource_name": "gmail",
                "permission_ids": ["connector.use"],
            },
        ),
        (
            "function:write_lesson:execute",
            {
                "resource_type": "function",
                "resource_name": "write_lesson",
                "permission_ids": ["function.execute"],
            },
        ),
        (
            "workflow:intake:execute",
            {
                "resource_type": "workflow",
                "resource_name": "intake",
                "permission_ids": ["workflow.execute"],
            },
        ),
        (
            "app:dashboard:read",
            {
                "resource_type": "app",
                "resource_name": "dashboard",
                "permission_ids": ["app.read"],
            },
        ),
        (
            "doc:/contracts/msa.pdf:read",
            {
                "resource_type": "document",
                "resource_name": "/contracts/msa.pdf",
                "permission_ids": ["folder.read"],
            },
        ),
    ],
)
def test_parse_grant_spec(spec, expected):
    assert parse_grant_spec(spec) == expected


def test_fixed_account_mode_is_expressible():
    """Pinning a shared connector account needs `connector_account.use` on the
    account id, alongside `connector.use`. There was no way to write the first
    half at all, so fixed-account mode could not be authored from the CLI."""
    account_id = "6f1c9e2a-0000-4000-8000-000000000001"
    assert parse_grant_spec(f"account:{account_id}:use") == {
        "resource_type": "connector_account",
        "resource_name": account_id,
        "permission_ids": ["connector_account.use"],
    }


def test_app_use_still_resolves_to_a_connector_grant(capsys):
    """`app:` meant "connector" before connectors were renamed, and that spelling
    is all over the existing skills and bundles. `use` is not an app verb, so the
    old form keeps working — with a note naming the current spelling."""
    assert parse_grant_spec("app:gmail:use") == {
        "resource_type": "connector",
        "resource_name": "gmail",
        "permission_ids": ["connector.use"],
    }
    assert "connector:<name>:use" in capsys.readouterr().out


def test_grant_presets_only_use_permissions_the_backend_accepts():
    """Every preset id must be POD-scoped and applicable to its resource type, or
    the server 400s the whole permissions call. Mirrors the backend's
    RESOURCE_ACTIONS / PERMISSION_DEFINITIONS tables."""
    applicable = {
        "datastore_table": {
            "datastore.table.read",
            "datastore.record.read",
            "datastore.record.write",
            "datastore.table.update",
            "datastore.table.delete",
        },
        "folder": {"folder.read", "folder.write", "folder.delete"},
        "document": {"folder.read", "folder.write", "folder.delete"},
        "connector": {"connector.use"},
        "connector_account": {"connector_account.use", "connector_account.manage"},
        "function": {
            "function.read",
            "function.execute",
            "function.update",
            "function.delete",
        },
        "agent": {"agent.read", "agent.execute", "agent.update", "agent.delete"},
        "workflow": {
            "workflow.read",
            "workflow.execute",
            "workflow.update",
            "workflow.delete",
        },
        "schedule": {"schedule.read", "schedule.update", "schedule.delete"},
        "app": {"app.read", "app.update", "app.publish", "app.delete"},
    }
    assert set(PERMISSION_PRESETS) == set(applicable)
    for resource_type, presets in PERMISSION_PRESETS.items():
        for verb, permission_ids in presets.items():
            unknown = set(permission_ids) - applicable[resource_type]
            assert not unknown, f"{resource_type}:{verb} -> {unknown}"


@pytest.mark.parametrize(
    "spec", ["nope:x:read", "tickets:bogus", "tickets", "connector::use", "tickets:"]
)
def test_parse_grant_spec_rejects_bad_input(spec):
    with pytest.raises(ScaffoldError):
        parse_grant_spec(spec)


# --------------------------------------------------------------------------- #
# merge / subtract
# --------------------------------------------------------------------------- #
def test_subtract_grants_drops_permissions_then_the_whole_grant():
    grants = merge_grants([], [parse_grant_spec("tickets:read,write")])
    trimmed = subtract_grants(grants, [parse_grant_spec("tickets:write")])
    assert trimmed == [
        {
            "resource_type": "datastore_table",
            "resource_name": "tickets",
            "permission_ids": ["datastore.table.read", "datastore.record.read"],
        }
    ]
    assert subtract_grants(grants, [parse_grant_spec("tickets:read,write")]) == []


def test_subtract_grants_leaves_unnamed_grants_alone():
    grants = merge_grants(
        [],
        [parse_grant_spec("tickets:read"), parse_grant_spec("connector:gmail:use")],
    )
    assert subtract_grants(grants, [parse_grant_spec("tickets:read")]) == [
        {
            "resource_type": "connector",
            "resource_name": "gmail",
            "permission_ids": ["connector.use"],
        }
    ]


# --------------------------------------------------------------------------- #
# live-pod commands
# --------------------------------------------------------------------------- #
def _fake_pod(existing_grants, calls):
    resource = SimpleNamespace(
        permissions=lambda name: {"grants": list(existing_grants)},
        replace_permissions=lambda name, request: calls.append(
            (name, request.to_dict())
        )
        or {"grants": request.to_dict()["grants"]},
    )
    return SimpleNamespace(agents=resource, functions=resource)


def test_permissions_add_merges_with_what_the_workload_already_holds(monkeypatch):
    """The API only replaces the whole list, so `add` must read-merge-write. Doing
    that by hand is where grants get dropped."""
    calls: list = []
    pod = _fake_pod(
        [
            {
                "resource_type": "datastore_table",
                "resource_name": "lesson_response",
                "permission_ids": ["datastore.table.read"],
            }
        ],
        calls,
    )
    monkeypatch.setattr("lemma_cli.cli_core.sdk.pod_client", lambda c, s, p: pod)
    monkeypatch.setattr(
        "lemma_cli.cli_core.state.run_with_client",
        lambda ctx, fn: fn(SimpleNamespace(), SimpleNamespace(output="pretty")),
    )
    result = runner.invoke(
        app,
        [
            "functions",
            "permissions",
            "add",
            "maybe_rewrite_lesson",
            "function:write_lesson:execute",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert len(calls) == 1
    _name, payload = calls[0]
    assert payload["grants"] == [
        {
            "resource_type": "datastore_table",
            "resource_name": "lesson_response",
            "permission_ids": ["datastore.table.read"],
        },
        {
            "resource_type": "function",
            "resource_name": "write_lesson",
            "permission_ids": ["function.execute"],
        },
    ]


def test_permissions_add_is_a_no_op_when_nothing_changes(monkeypatch):
    calls: list = []
    pod = _fake_pod(
        [
            {
                "resource_type": "connector",
                "resource_name": "gmail",
                "permission_ids": ["connector.use"],
            }
        ],
        calls,
    )
    monkeypatch.setattr("lemma_cli.cli_core.sdk.pod_client", lambda c, s, p: pod)
    monkeypatch.setattr(
        "lemma_cli.cli_core.state.run_with_client",
        lambda ctx, fn: fn(SimpleNamespace(), SimpleNamespace(output="pretty")),
    )
    result = runner.invoke(
        app, ["agents", "permissions", "add", "triage", "connector:gmail:use"]
    )
    assert result.exit_code == 0, result.stdout
    assert calls == []
    assert "no change" in result.stdout


def test_permissions_remove_writes_the_trimmed_list(monkeypatch):
    calls: list = []
    pod = _fake_pod(
        [
            {
                "resource_type": "datastore_table",
                "resource_name": "tickets",
                "permission_ids": ["datastore.table.read", "datastore.record.write"],
            }
        ],
        calls,
    )
    monkeypatch.setattr("lemma_cli.cli_core.sdk.pod_client", lambda c, s, p: pod)
    monkeypatch.setattr(
        "lemma_cli.cli_core.state.run_with_client",
        lambda ctx, fn: fn(SimpleNamespace(), SimpleNamespace(output="pretty")),
    )
    result = runner.invoke(
        app, ["agents", "permissions", "remove", "triage", "tickets:write"]
    )
    assert result.exit_code == 0, result.stdout
    assert calls[0][1]["grants"] == [
        {
            "resource_type": "datastore_table",
            "resource_name": "tickets",
            "permission_ids": ["datastore.table.read"],
        }
    ]


def test_permissions_replace_from_bundle_lifts_the_declared_grants(tmp_path):
    """The manual "open the JSON, copy permissions.grants, paste into --data"
    round trip every 403-chasing session used to end in."""
    resource_dir = tmp_path / "functions" / "maybe_rewrite_lesson"
    resource_dir.mkdir(parents=True)
    (resource_dir / "maybe_rewrite_lesson.json").write_text(
        json.dumps(
            {
                "name": "maybe_rewrite_lesson",
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "datastore_table",
                            "resource_name": "lesson_response",
                            "permission_ids": ["datastore.table.read"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    grants = _grants.grants_from_options(
        "function", "maybe_rewrite_lesson", None, None, tmp_path
    )
    assert grants == [
        {
            "resource_type": "datastore_table",
            "resource_name": "lesson_response",
            "permission_ids": ["datastore.table.read"],
        }
    ]


def test_permissions_replace_from_bundle_reports_a_missing_permissions_block(tmp_path):
    resource_dir = tmp_path / "agents" / "triage"
    resource_dir.mkdir(parents=True)
    (resource_dir / "triage.json").write_text(
        json.dumps({"name": "triage"}), encoding="utf-8"
    )
    with pytest.raises(typer.BadParameter, match="no grants to push"):
        _grants.grants_from_options("agent", "triage", None, None, tmp_path)


def test_split_inline_permissions_separates_absent_from_empty():
    """Absent means "leave the workload's grants alone"; empty means "it holds
    nothing". Collapsing the two is what made the same pod import differently
    depending on which exporter produced the bundle."""
    body, permissions = _grants.split_inline_permissions({"name": "f"})
    assert body == {"name": "f"} and permissions is None

    body, permissions = _grants.split_inline_permissions(
        {"name": "f", "permissions": {"grants": []}}
    )
    assert body == {"name": "f"} and permissions == {"grants": []}
