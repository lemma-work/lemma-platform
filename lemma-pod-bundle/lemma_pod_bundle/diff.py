"""Table column diffing and foreign-key dependency ordering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .layout import SYSTEM_TABLE_COLUMNS, load_resource_payload


@dataclass
class TableDiff:
    to_add: list[dict[str, Any]]
    to_remove: list[str]
    incompatible: list[str]
    #: ENUM columns the bundle offers more options for: column name -> every
    #: option it should then have, the table's own first. Widening is safe --
    #: no row holds an option that is not there yet.
    options_to_add: dict[str, list[str]] = field(default_factory=dict)
    #: ENUM columns whose table has options the bundle lacks. Those are kept:
    #: a row may hold one, and dropping it would strand the row.
    options_kept: dict[str, list[str]] = field(default_factory=dict)
    #: Columns whose foreign key the bundle no longer declares: dropped.
    foreign_keys_to_drop: list[str] = field(default_factory=list)
    #: Columns the bundle gives a foreign key the table's column lacks, or
    #: another one: an existing column cannot gain one, so the table's stands.
    foreign_keys_kept: list[str] = field(default_factory=list)


def _references(column: dict[str, Any]) -> str | None:
    """What a column's foreign key points at, or ``None`` when it has none."""
    key = column.get("foreign_key")
    if isinstance(key, dict) and key.get("references"):
        return str(key["references"]).strip()
    return None


def _enum_options(column: dict[str, Any]) -> list[str] | None:
    """An ENUM column's options, in order, or ``None`` for any other column."""
    type_name = (column.get("type_params") or {}).get("type") or column.get("type")
    if str(type_name or "").upper() != "ENUM":
        return None
    options = column.get("options") or (column.get("type_params") or {}).get("options")
    return [str(option) for option in options or []]


def _option_changes(
    existing: dict[str, Any], desired: dict[str, Any]
) -> tuple[list[str] | None, list[str]]:
    """``(options to set, options only the table has)`` for one ENUM column;
    the first is ``None`` when the bundle adds nothing."""
    have, want = _enum_options(existing), _enum_options(desired)
    if have is None or want is None:
        return None, []
    added = [option for option in want if option not in have]
    only_here = [option for option in have if option not in want]
    return ([*have, *added] if added else None), only_here


def _is_system_table_column(column: dict[str, Any]) -> bool:
    return (
        bool(column.get("system"))
        or str(column.get("name") or "") in SYSTEM_TABLE_COLUMNS
    )


def _normalize_column_for_diff(
    column: dict[str, Any], *, primary_key: bool = False
) -> dict[str, Any]:
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
    primary_key = str(
        existing.get("primary_key_column") or desired.get("primary_key_column") or "id"
    )
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

    existing_columns_raw = {
        str(column.get("name")): column
        for column in existing.get("columns") or []
        if not _is_system_table_column(column)
    }
    incompatible: list[str] = []
    options_to_add: dict[str, list[str]] = {}
    options_kept: dict[str, list[str]] = {}
    foreign_keys_to_drop: list[str] = []
    foreign_keys_kept: list[str] = []
    for name in sorted(existing_columns.keys() & desired_columns.keys()):
        existing_column = existing_columns[name]
        desired_column = desired_columns[name]
        if existing_column != desired_column:
            incompatible.append(name)
            continue
        have = _references(existing_columns_raw[name])
        want = _references(desired_columns_raw[name])
        if have is not None and want is None:
            foreign_keys_to_drop.append(name)
        elif want is not None and want != have:
            foreign_keys_kept.append(name)
        widened, only_here = _option_changes(
            existing_columns_raw[name], desired_columns_raw[name]
        )
        if widened is not None:
            options_to_add[name] = widened
        if only_here:
            options_kept[name] = only_here

    return TableDiff(
        to_add=sorted(to_add, key=lambda item: str(item.get("name", ""))),
        to_remove=to_remove,
        incompatible=incompatible,
        options_to_add=options_to_add,
        options_kept=options_kept,
        foreign_keys_to_drop=foreign_keys_to_drop,
        foreign_keys_kept=foreign_keys_kept,
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
        ready = [name for name in remaining if deps_by_name[name] <= placed]
        if not ready:
            # Cycle or self-reference: emit the rest in stable order.
            ready = list(remaining)
        for name in sorted(ready):
            ordered.append(dir_by_name[name])
            placed.add(name)
        remaining = [name for name in remaining if name not in placed]
    return ordered
