"""Unit tests for the import plan builder — bundle on disk + a fake notion of
what the target pod already contains."""

from __future__ import annotations

import json
from pathlib import Path

from app.modules.pod_import.domain.value_objects import ImportAction
from app.modules.pod_import.services.plan_builder import build_plan


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "acme"
    _write(root / "pod.json", {"name": "acme"})
    _write(root / "tables" / "contacts" / "contacts.json", {
        "name": "contacts", "primary_key_column": "id",
        "columns": [{"name": "id", "type": "UUID"}, {"name": "email", "type": "TEXT"}],
    })
    _write(root / "agents" / "triage" / "triage.json", {"name": "triage"})
    return root


class FakeExisting:
    def __init__(self, present: set[tuple[str, str]], schemas: dict[str, dict]):
        self._present = present
        self._schemas = schemas

    def has(self, resource_type: str, name: str) -> bool:
        return (resource_type, name) in self._present

    def table_schema(self, name: str):
        return self._schemas.get(name)


def test_plan_orders_by_dependency_and_marks_creates(tmp_path):
    root = _bundle(tmp_path)
    steps, requirements, capabilities = build_plan(root, FakeExisting(set(), {}))

    # tables before agents (dependency order), all creates on an empty pod.
    assert [(s.resource_type, s.resource_name) for s in steps] == [
        ("tables", "contacts"),
        ("agents", "triage"),
    ]
    assert all(s.action is ImportAction.CREATE for s in steps)
    assert all(not s.destructive for s in steps)
    # capabilities derived from the bundle (has a table + an agent).
    tiers = {c["tier"] for c in capabilities}
    assert {"data", "ai"} <= tiers


def test_existing_resource_becomes_update(tmp_path):
    root = _bundle(tmp_path)
    existing = FakeExisting(
        present={("agents", "triage")},
        schemas={},
    )
    steps = {s.resource_name: s for s in build_plan(root, existing)[0]}
    assert steps["triage"].action is ImportAction.UPDATE
    assert steps["contacts"].action is ImportAction.CREATE


def test_table_update_dropping_a_column_is_destructive(tmp_path):
    root = _bundle(tmp_path)
    # Pod already has contacts with an extra `phone` column the bundle drops.
    existing = FakeExisting(
        present={("tables", "contacts")},
        schemas={
            "contacts": {
                "primary_key_column": "id",
                "columns": [
                    {"name": "id", "type": "UUID"},
                    {"name": "email", "type": "TEXT"},
                    {"name": "phone", "type": "TEXT"},
                ],
            }
        },
    )
    contacts = {s.resource_name: s for s in build_plan(root, existing)[0]}["contacts"]
    assert contacts.action is ImportAction.UPDATE
    assert contacts.destructive is True


def test_tables_ordered_after_their_foreign_key_targets(tmp_path):
    # `aaa` references `zzz`, but sorts first alphabetically — the plan must put
    # `zzz` before `aaa` so the FK target exists at create time.
    root = tmp_path / "fk"
    _write(root / "pod.json", {"name": "fk"})
    _write(root / "tables" / "aaa" / "aaa.json", {
        "columns": [{"name": "zzz_id", "type": "UUID", "foreign_key": {"references": "zzz.id"}}],
    })
    _write(root / "tables" / "zzz" / "zzz.json", {"columns": [{"name": "id", "type": "UUID"}]})

    steps, _, _ = build_plan(root, FakeExisting(set(), {}))
    table_order = [s.resource_name for s in steps if s.resource_type == "tables"]
    assert table_order.index("zzz") < table_order.index("aaa")


def test_grants_become_a_deferred_step_after_all_resources(tmp_path):
    # An agent that grants access to a function gets its grants applied in a
    # final pass — emitted as an `agent_grants` step ordered after every
    # resource step, so the grant target exists when it runs.
    root = tmp_path / "p"
    _write(root / "pod.json", {"name": "p"})
    _write(root / "functions" / "foo" / "foo.json", {"name": "foo"})
    _write(root / "agents" / "triage" / "triage.json", {
        "name": "triage",
        "permissions": {"grants": [
            {"resource_type": "function", "resource_name": "foo", "permission_ids": ["function.use"]},
        ]},
    })

    steps, _, _ = build_plan(root, FakeExisting(set(), {}))
    keys = [(s.resource_type, s.resource_name) for s in steps]

    assert ("agent_grants", "triage") in keys
    assert keys.index(("agent_grants", "triage")) > keys.index(("agents", "triage"))
    assert keys.index(("agent_grants", "triage")) > keys.index(("functions", "foo"))
    # foo has no grants of its own — no grant step for it.
    assert ("function_grants", "foo") not in keys


def test_no_grant_steps_when_no_grants(tmp_path):
    root = _bundle(tmp_path)  # contacts table + triage agent, neither has grants
    steps, _, _ = build_plan(root, FakeExisting(set(), {}))
    assert not any(s.resource_type.endswith("_grants") for s in steps)


def test_additive_table_update_is_not_destructive(tmp_path):
    root = _bundle(tmp_path)
    # Pod's contacts has only id; the bundle adds email — additive, safe.
    existing = FakeExisting(
        present={("tables", "contacts")},
        schemas={"contacts": {"primary_key_column": "id", "columns": [{"name": "id", "type": "UUID"}]}},
    )
    contacts = {s.resource_name: s for s in build_plan(root, existing)[0]}["contacts"]
    assert contacts.destructive is False
