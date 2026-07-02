from __future__ import annotations

import json
from pathlib import Path

from lemma_pod_bundle.diff import (
    _order_table_dirs_by_dependency,
    _table_fk_dependencies,
    diff_table_columns,
)


def _table(columns, primary_key="id"):
    return {"primary_key_column": primary_key, "columns": columns}


def test_diff_adds_new_columns():
    existing = _table([{"name": "id", "type": "uuid"}])
    desired = _table(
        [
            {"name": "id", "type": "uuid"},
            {"name": "title", "type": "text", "required": True},
        ]
    )
    diff = diff_table_columns(existing, desired)
    assert diff.to_add == [{"name": "title", "type": "text", "required": True}]
    assert diff.to_remove == []
    assert diff.incompatible == []


def test_diff_removes_missing_columns_but_never_primary_key():
    existing = _table(
        [
            {"name": "id", "type": "uuid"},
            {"name": "old_a", "type": "text"},
            {"name": "old_b", "type": "text"},
        ]
    )
    desired = _table([])
    diff = diff_table_columns(existing, desired)
    assert diff.to_remove == ["old_a", "old_b"]
    assert diff.to_add == []


def test_diff_flags_incompatible_column_changes():
    existing = _table([{"name": "count", "type": "integer"}])
    desired = _table([{"name": "count", "type": "text"}])
    diff = diff_table_columns(existing, desired)
    assert diff.incompatible == ["count"]

    # required flag flip is also incompatible
    existing = _table([{"name": "count", "type": "integer", "required": False}])
    desired = _table([{"name": "count", "type": "integer", "required": True}])
    assert diff_table_columns(existing, desired).incompatible == ["count"]


def test_diff_normalizes_type_params_type():
    existing = _table([{"name": "count", "type_params": {"type": "integer"}}])
    desired = _table([{"name": "count", "type": "integer"}])
    diff = diff_table_columns(existing, desired)
    assert diff.incompatible == []


def test_diff_ignores_system_columns_both_sides():
    existing = _table(
        [
            {"name": "id", "type": "uuid"},
            {"name": "created_at", "type": "timestamp"},
            {"name": "user_id", "type": "uuid"},
        ]
    )
    desired = _table(
        [
            {"name": "id", "type": "uuid"},
            {"name": "updated_at", "type": "timestamp", "system": True},
            {"name": "extra", "type": "text", "system": True},
        ]
    )
    diff = diff_table_columns(existing, desired)
    assert diff.to_add == []
    assert diff.to_remove == []
    assert diff.incompatible == []


def test_table_fk_dependencies_parses_references():
    payload = {
        "columns": [
            {"name": "owner", "foreign_key": {"references": "users.id"}},
            {"name": "tag", "foreign_key": {"references": "tags"}},
            {"name": "plain", "type": "text"},
            "not-a-dict",
        ]
    }
    assert _table_fk_dependencies(payload) == {"users", "tags"}


def _write_table_dir(root: Path, name: str, references: list[str]) -> Path:
    table_dir = root / name
    table_dir.mkdir(parents=True)
    columns = [
        {"name": f"ref_{ref}", "foreign_key": {"references": f"{ref}.id"}}
        for ref in references
    ]
    (table_dir / f"{name}.json").write_text(
        json.dumps({"name": name, "columns": columns}), encoding="utf-8"
    )
    return table_dir


def test_order_table_dirs_by_dependency(tmp_path: Path):
    orders = _write_table_dir(tmp_path, "orders", ["users", "products"])
    users = _write_table_dir(tmp_path, "users", [])
    products = _write_table_dir(tmp_path, "products", ["users"])

    ordered = _order_table_dirs_by_dependency([orders, users, products])
    names = [path.name for path in ordered]
    assert names.index("users") < names.index("products")
    assert names.index("products") < names.index("orders")


def test_order_table_dirs_ignores_external_references(tmp_path: Path):
    a = _write_table_dir(tmp_path, "a", ["not_in_bundle"])
    b = _write_table_dir(tmp_path, "b", [])
    ordered = _order_table_dirs_by_dependency([a, b])
    assert [path.name for path in ordered] == ["a", "b"]


def test_order_table_dirs_cycle_falls_back_to_stable_order(tmp_path: Path):
    a = _write_table_dir(tmp_path, "a", ["b"])
    b = _write_table_dir(tmp_path, "b", ["a"])
    c = _write_table_dir(tmp_path, "c", [])
    ordered = _order_table_dirs_by_dependency([b, a, c])
    names = [path.name for path in ordered]
    # c has no deps and goes first; the a<->b cycle is emitted in stable
    # (alphabetical) order rather than raising.
    assert names == ["c", "a", "b"]


def test_order_table_dirs_unreadable_manifest_treated_as_no_deps(tmp_path: Path):
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    (broken_dir / "broken.json").write_text("{not json", encoding="utf-8")
    ok = _write_table_dir(tmp_path, "ok", ["broken"])
    ordered = _order_table_dirs_by_dependency([ok, broken_dir])
    assert [path.name for path in ordered] == ["broken", "ok"]
