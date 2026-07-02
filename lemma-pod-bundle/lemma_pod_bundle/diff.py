"""Table column diffing and foreign-key dependency ordering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import SYSTEM_TABLE_COLUMNS, load_resource_payload


@dataclass
class TableDiff:
    to_add: list[dict[str, Any]]
    to_remove: list[str]
    incompatible: list[str]


def _is_system_table_column(column: dict[str, Any]) -> bool:
    return bool(column.get("system")) or str(column.get("name") or "") in SYSTEM_TABLE_COLUMNS


def _normalize_column_for_diff(column: dict[str, Any], *, primary_key: bool = False) -> dict[str, Any]:
    type_name = (column.get("type_params") or {}).get("type") or column.get("type")
    normalized = {
        "name": column.get("name"),
        "type": type_name,
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
    }
    if primary_key:
        normalized["primary_key"] = True
    return normalized


def diff_table_columns(existing: dict[str, Any], desired: dict[str, Any]) -> TableDiff:
    primary_key = str(existing.get("primary_key_column") or desired.get("primary_key_column") or "id")
    existing_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column,
            primary_key=str(column.get("name")) == primary_key,
        )
        for column in existing.get("columns") or []
        if not _is_system_table_column(column)
    }
    desired_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column,
            primary_key=str(column.get("name")) == primary_key,
        )
        for column in desired.get("columns") or []
        if not _is_system_table_column(column)
    }
    desired_columns_raw = {
        str(column.get("name")): column
        for column in desired.get("columns") or []
        if not _is_system_table_column(column)
    }

    to_add = [
        desired_columns_raw[name]
        for name in desired_columns_raw.keys() - existing_columns.keys()
    ]
    to_remove = sorted(
        name
        for name in existing_columns.keys() - desired_columns.keys()
        if name and name != primary_key
    )

    incompatible: list[str] = []
    for name in sorted(existing_columns.keys() & desired_columns.keys()):
        existing_column = existing_columns[name]
        desired_column = desired_columns[name]
        if existing_column != desired_column:
            incompatible.append(name)

    return TableDiff(
        to_add=sorted(to_add, key=lambda item: str(item.get("name", ""))),
        to_remove=to_remove,
        incompatible=incompatible,
    )


def _table_fk_dependencies(payload: dict[str, Any]) -> set[str]:
    """Table names referenced by this table's foreign-key columns."""
    deps: set[str] = set()
    for column in payload.get("columns") or []:
        if not isinstance(column, dict):
            continue
        fk = column.get("foreign_key")
        if isinstance(fk, dict) and fk.get("references"):
            referenced = str(fk["references"]).split(".", 1)[0].strip()
            if referenced:
                deps.add(referenced)
    return deps


def _order_table_dirs_by_dependency(table_dirs: list[Path]) -> list[Path]:
    """Sort table directories so a table is imported after any table it
    references via a foreign key. Falls back to alphabetical for ties and
    leaves cycles in a stable order rather than failing."""
    dir_by_name: dict[str, Path] = {path.name: path for path in table_dirs}
    deps_by_name: dict[str, set[str]] = {}
    for path in table_dirs:
        try:
            payload = load_resource_payload(path, path.name)
        except Exception:
            payload = {}
        # Only intra-bundle dependencies matter for ordering.
        deps_by_name[path.name] = _table_fk_dependencies(payload) & set(dir_by_name)

    ordered: list[Path] = []
    placed: set[str] = set()
    remaining = [path.name for path in table_dirs]
    while remaining:
        ready = [
            name for name in remaining if deps_by_name[name] <= placed
        ]
        if not ready:
            # Cycle or self-reference: emit the rest in stable order.
            ready = list(remaining)
        for name in sorted(ready):
            ordered.append(dir_by_name[name])
            placed.add(name)
        remaining = [name for name in remaining if name not in placed]
    return ordered
