"""Bundle format constants and manifest/file-layout helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .jsonc import loads_jsonc

FORMAT_VERSION = 2
# The bundle's root manifest file (pod metadata, contents scope, variables).
POD_MANIFEST_FILE = "pod.json"
# Legacy placeholder for a pod-member assignee; superseded by ${name} variables
# but still recognized on import so older templated bundles keep working.
POD_MEMBER_TOKEN = "$POD_MEMBER"
# Per-table row dump carried by `--with-data` (CSV, complex cells as JSON text).
TABLE_DATA_FILE = "data.csv"
_TABLE_DATA_CANDIDATES = ("data.csv", "data.jsonl", "data.json")
RAW_FILE_REF_KEY = "$file"
JSON_FILE_REF_KEY = "$json_file"
# Some app scaffolds write the manifest as `lemma.app.json` rather than
# `<name>.json`. Accept it as an alias so those bundles import without a rename.
APP_MANIFEST_ALIAS = "lemma.app.json"
RESOURCE_DIRS = (
    "tables",
    "functions",
    "agents",
    "workflows",
    "schedules",
    "surfaces",
    "apps",
    "files",
)
EXPORTABLE_RESOURCE_DIRS = frozenset(RESOURCE_DIRS)
RESOURCE_DIR_ALIASES = {
    "table": "tables",
    "tables": "tables",
    "function": "functions",
    "functions": "functions",
    "agent": "agents",
    "agents": "agents",
    "workflow": "workflows",
    "workflows": "workflows",
    "schedule": "schedules",
    "schedules": "schedules",
    "surface": "surfaces",
    "surfaces": "surfaces",
    "app": "apps",
    "apps": "apps",
    "file": "files",
    "files": "files",
}
SYSTEM_TABLE_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})

FILES_MANIFEST = ".files.json"


def _json_dump(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(_json_dump(data), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _sanitize_resource_name(name: str) -> str:
    return name.strip()


def normalize_resource_dir_name(value: str) -> str:
    normalized = value.lower().strip().replace("-", "_")
    return RESOURCE_DIR_ALIASES.get(normalized, "")


def _record_export_contents(
    bundle_root: Path,
    *,
    included: set[str],
    excluded: set[str],
    names: set[str],
    with_data: bool,
    with_files: bool,
) -> dict[str, Any]:
    """Record the bundle's selective scope under ``pod.json -> contents``: which
    resource types/names it covers and whether it carries table rows (data.csv)
    and file bytes. On import this auto-enables seeding/upload; a re-export can
    refresh exactly this set."""
    pod_path = bundle_root / "pod.json"
    if not pod_path.is_file():
        return {}
    pod_data = _read_json(pod_path)
    contents: dict[str, Any] = {}
    if included:
        contents["resources"] = sorted(included)
    if excluded:
        contents["exclude"] = sorted(excluded)
    if names:
        contents["names"] = sorted(names)
    if with_data:
        contents["with_data"] = True
    if with_files:
        contents["with_files"] = True
    pod_data["contents"] = contents
    _write_json(pod_path, pod_data)
    return contents


def _read_export_contents(source_dir: Path) -> dict[str, Any]:
    """Read the ``contents`` block written by a manifest-aware export (empty for
    older bundles)."""
    pod_path = source_dir / "pod.json"
    if not pod_path.is_file():
        return {}
    contents = _read_json(pod_path).get("contents")
    return contents if isinstance(contents, dict) else {}


def _resolve_file_refs(value: Any, *, base_dir: Path) -> Any:
    if isinstance(value, list):
        return [_resolve_file_refs(item, base_dir=base_dir) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value.keys()) == {RAW_FILE_REF_KEY}:
        raw_path = base_dir / str(value[RAW_FILE_REF_KEY])
        return raw_path.read_text(encoding="utf-8")
    if set(value.keys()) == {JSON_FILE_REF_KEY}:
        json_path = base_dir / str(value[JSON_FILE_REF_KEY])
        return loads_jsonc(json_path.read_text(encoding="utf-8"))
    return {key: _resolve_file_refs(item, base_dir=base_dir) for key, item in value.items()}


def _resource_manifest_path(
    resource_dir: Path,
    resource_name: str,
    *,
    resource_type: str | None = None,
) -> Path | None:
    """Path to a resource directory's manifest JSON, or None if absent.

    The canonical name is `<resource_name>.json`. Apps additionally accept
    `lemma.app.json` as a fallback, since some app scaffolds write that name.
    """
    primary = resource_dir / f"{resource_name}.json"
    if primary.exists():
        return primary
    if resource_type == "apps":
        alias = resource_dir / APP_MANIFEST_ALIAS
        if alias.exists():
            return alias
    return None


def load_resource_payload(
    resource_dir: Path,
    resource_name: str,
    *,
    resource_type: str | None = None,
) -> dict[str, Any]:
    manifest_path = _resource_manifest_path(
        resource_dir, resource_name, resource_type=resource_type
    )
    if manifest_path is None:
        manifest_path = resource_dir / f"{resource_name}.json"
    payload = _resolve_file_refs(
        _read_json(manifest_path),
        base_dir=resource_dir,
    )
    if "tool_sets" in payload and "toolsets" not in payload:
        payload["toolsets"] = payload.pop("tool_sets")
    return payload


def _looks_like_single_resource_dir(path: Path, resource_type: str) -> bool:
    if resource_type == "files":
        return False
    return _resource_manifest_path(
        path, path.name, resource_type=resource_type
    ) is not None


def _parse_function_headers(code: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        if ":" not in stripped:
            continue
        key, value = stripped[1:].split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def _file_path_key(parts: list[str]) -> str:
    return "/".join(parts)


def _bundle_folder_keys(files_root: Path) -> set[str]:
    keys: set[str] = set()
    if files_root.exists():
        for folder_dir in (path for path in files_root.rglob("*") if path.is_dir()):
            parts = list(folder_dir.relative_to(files_root).parts)
            if parts:
                keys.add(_file_path_key(parts))
    return keys
