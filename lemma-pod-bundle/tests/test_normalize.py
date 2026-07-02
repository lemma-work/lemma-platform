from __future__ import annotations

from pathlib import Path

import pytest

from lemma_pod_bundle.layout import FORMAT_VERSION
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _declared_reserved_columns,
    _normalize_agent_payload,
    _normalize_app_payload,
    _normalize_function_payload,
    _normalize_pod_payload,
    _normalize_schedule_payload,
    _normalize_surface_payload,
    _normalize_table_payload,
    _normalize_workflow_payload,
    _sanitize_table_payload_for_import,
    _split_resource_permissions_payload,
    _surface_platform_from_payload,
    _validate_function_payload,
)


def test_normalize_pod_payload():
    pod = {"id": "p1", "name": "demo", "description": "d", "icon_url": None, "extra": 1}
    assert _normalize_pod_payload(pod) == {
        "format_version": FORMAT_VERSION,
        "name": "demo",
        "description": "d",
        "icon_url": None,
    }


def test_normalize_table_payload_defaults_and_drops_none():
    table = {"name": "items", "columns": None, "config": None, "visibility": None}
    payload = _normalize_table_payload(table)
    assert payload == {
        "name": "items",
        "columns": [],
        "enable_rls": True,
        "primary_key_column": "id",
    }


def test_sanitize_table_payload_strips_system_columns():
    payload = {
        "name": "items",
        "columns": [
            {"name": "id", "type": "uuid"},
            {"name": "created_at", "type": "timestamp"},
            {"name": "flagged", "type": "text", "system": True},
        ],
    }
    sanitized = _sanitize_table_payload_for_import(payload)
    assert [c["name"] for c in sanitized["columns"]] == ["id"]
    # original payload untouched
    assert len(payload["columns"]) == 3


def test_declared_reserved_columns_respects_rls_and_system_flag():
    payload = {
        "enable_rls": False,
        "columns": [
            {"name": "created_at"},
            {"name": "user_id"},
            {"name": "updated_at", "system": True},
        ],
    }
    # user_id only reserved under RLS; system-emitted columns excluded.
    assert _declared_reserved_columns(payload) == ["created_at"]
    payload["enable_rls"] = True
    assert _declared_reserved_columns(payload) == ["created_at", "user_id"]


def test_normalize_function_payload_strips_server_fields():
    function = {
        "id": "f1",
        "pod_id": "p1",
        "name": "hello",
        "code": "print('hi')",
        "status": "READY",
        "input_schema": {},
        "allowed_actions": ["run"],
    }
    assert _normalize_function_payload(function) == {"name": "hello", "code": "print('hi')"}


def test_normalize_agent_payload_makes_schemas_explicit():
    agent = {"id": "a1", "name": "helper", "instruction": "hi", "output_schema": {"x": 1}}
    payload = _normalize_agent_payload(agent)
    assert payload["output_schema"] == {"x": 1}
    assert payload["input_schema"] is None
    assert "id" not in payload


def test_normalize_workflow_payload_defaults_graph():
    workflow = {"id": "w1", "name": "flow", "is_active": True}
    payload = _normalize_workflow_payload(workflow)
    assert payload == {"name": "flow", "nodes": [], "edges": []}


def test_normalize_schedule_payload_strips_runtime_fields():
    schedule = {
        "id": "s1",
        "name": "daily",
        "schedule_type": "CRON",
        "config": {"cron": "0 9 * * *"},
        "last_run_at": "2026-01-01",
        "next_run_at": "2026-01-02",
        "agent_id": "a1",
        "agent_name": "helper",
    }
    payload = _normalize_schedule_payload(schedule)
    assert payload == {
        "name": "daily",
        "schedule_type": "CRON",
        "config": {"cron": "0 9 * * *"},
        "agent_name": "helper",
    }


def test_normalize_surface_payload_channels_identity_and_status():
    surface = {
        "platform": "slack",
        "status": "INACTIVE",
        "agent_name": "helper",
        "account_id": "acct-1",
        "config": {
            "channels": [
                {"channel_id": "C1", "channel_name": "general", "enabled": True},
                {"channel_id": "C2", "enabled": False},
                "junk",
            ],
            "identity": {"allowed_domains": ["example.com"], "other": 1},
        },
    }
    payload = _normalize_surface_payload(surface)
    assert payload["name"] == "slack"
    assert payload["platform"] == "SLACK"
    assert payload["default_agent_name"] == "helper"
    assert payload["is_enabled"] is False
    assert payload["config"] == {
        "channels": [{"channel_id": "C1", "channel_name": "general"}],
        "identity": {"allowed_domains": ["example.com"]},
    }


def test_surface_platform_from_payload_falls_back_to_resource_name():
    assert _surface_platform_from_payload({"platform": "slack"}, "x") == "SLACK"
    assert _surface_platform_from_payload({}, "teams") == "TEAMS"


def test_normalize_app_payload_strips_server_fields():
    app = {
        "id": "a1",
        "name": "dash",
        "public_slug": "dash",
        "status": "LIVE",
        "current_release_id": "r1",
        "source_archive_path": "/x",
    }
    assert _normalize_app_payload(app) == {"name": "dash", "public_slug": "dash"}


def test_split_and_attach_permissions_payload():
    payload = {
        "name": "helper",
        "permissions": {"grants": [{"resource_type": "function", "resource_name": "f1"}]},
    }
    resource, permissions = _split_resource_permissions_payload(payload)
    assert "permissions" not in resource
    assert permissions == {"grants": [{"resource_type": "function", "resource_name": "f1"}]}

    reattached = _attach_permissions_payload(resource, permissions)
    assert reattached["permissions"] == permissions

    with pytest.raises(ValueError):
        _split_resource_permissions_payload({"permissions": "nope"})
    with pytest.raises(ValueError):
        _split_resource_permissions_payload({"permissions": {"grants": [{"resource_type": "x"}]}})


def test_validate_function_payload_happy_path(tmp_path: Path):
    code = (
        "#input_type_name: In\n"
        "#output_type_name: Out\n"
        "#function_name: hello\n"
        "def run():\n    return 1\n"
    )
    issues = _validate_function_payload(tmp_path, "hello", {"code": code})
    assert issues == []


def test_validate_function_payload_reports_problems(tmp_path: Path):
    # missing code
    issues = _validate_function_payload(tmp_path, "hello", {"code": "  "})
    assert [i.message for i in issues] == ["Function code is required."]

    # syntax error short-circuits
    issues = _validate_function_payload(tmp_path, "hello", {"code": "def broken(:"})
    assert len(issues) == 1
    assert "Python syntax error" in issues[0].message

    # missing headers + name mismatch
    code = "#function_name: other\ndef run():\n    return 1\n"
    issues = _validate_function_payload(tmp_path, "hello", {"code": code})
    messages = [i.message for i in issues]
    assert "Missing required header #input_type_name." in messages
    assert "Missing required header #output_type_name." in messages
    assert "Invalid #function_name header: expected 'hello'." in messages

    # config_schema requires config_type_name
    code = (
        "#input_type_name: In\n#output_type_name: Out\n#function_name: hello\n"
        "def run():\n    return 1\n"
    )
    issues = _validate_function_payload(
        tmp_path, "hello", {"code": code, "config_schema": {"type": "object"}}
    )
    assert [i.message for i in issues] == ["Missing required header #config_type_name."]
