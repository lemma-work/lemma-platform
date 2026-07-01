"""Public, environment-independent readers over a bundle directory.

Small surface used by both the requirements extractor and the backend plan
builder so neither re-implements "what resources does this bundle contain" or
"read this manifest" — keeping one notion of the on-disk layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lemma_pod_bundle.jsonc import loads_jsonc

# A bundle extracts large text/JSON fields into sidecar files, referenced inline
# as {"$file": "code.py"} or {"$json_file": "schema.json"}. Readers resolve them
# back to the field value so callers see a complete manifest.
RAW_FILE_REF_KEY = "$file"
JSON_FILE_REF_KEY = "$json_file"

# Resource folders, in the canonical dependency order an import applies them:
# tables before the functions/agents that grant against them, workflows and
# schedules after the resources they reference, surfaces/apps last.
RESOURCE_KINDS = (
    "tables",
    "functions",
    "agents",
    "workflows",
    "schedules",
    "surfaces",
    "apps",
)


def manifest_path(resource_dir: Path) -> Path | None:
    """The resource's manifest JSON — ``<dir-name>.json``, else a lone ``*.json``."""
    primary = resource_dir / f"{resource_dir.name}.json"
    if primary.is_file():
        return primary
    jsons = sorted(resource_dir.glob("*.json"))
    return jsons[0] if len(jsons) == 1 else None


def list_resource_names(bundle_root: Path, kind: str) -> list[str]:
    """Names of resources of ``kind`` the bundle declares (dirs with a manifest),
    sorted for a stable plan order."""
    base = Path(bundle_root) / kind
    if not base.is_dir():
        return []
    return [
        path.name
        for path in sorted(base.iterdir())
        if path.is_dir() and manifest_path(path) is not None
    ]


_TABLE_DATA_CANDIDATES = ("data.csv", "data.jsonl", "data.json")


def read_table_data(bundle_root: Path, name: str) -> list[dict[str, Any]]:
    """Seed rows for a table from its bundled ``data.{csv,jsonl,json}`` file
    (empty list if none). CSV cells that hold JSON (complex values exported as
    text) are parsed back; plain scalars are left as strings for the backend's
    type coercion."""
    resource_dir = Path(bundle_root) / "tables" / name
    data_file = next(
        (resource_dir / candidate for candidate in _TABLE_DATA_CANDIDATES
         if (resource_dir / candidate).is_file()),
        None,
    )
    if data_file is None:
        return []
    text = data_file.read_text(encoding="utf-8")
    suffix = data_file.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    import csv
    import io

    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        rows.append({key: _maybe_json(value) for key, value in raw.items()})
    return rows


def _maybe_json(value: Any) -> Any:
    """Parse a CSV cell that holds a JSON object/array back to its value."""
    if isinstance(value, str) and value[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def resolve_file_refs(value: Any, *, base_dir: Path) -> Any:
    """Inline any ``$file``/``$json_file`` sidecar references, reading the
    referenced file relative to ``base_dir``."""
    if isinstance(value, dict):
        if set(value) == {RAW_FILE_REF_KEY}:
            return (base_dir / value[RAW_FILE_REF_KEY]).read_text(encoding="utf-8")
        if set(value) == {JSON_FILE_REF_KEY}:
            return json.loads((base_dir / value[JSON_FILE_REF_KEY]).read_text(encoding="utf-8"))
        return {key: resolve_file_refs(item, base_dir=base_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_file_refs(item, base_dir=base_dir) for item in value]
    return value


def table_fk_dependencies(manifest: dict[str, Any]) -> set[str]:
    """Table names this table references via a foreign-key column."""
    deps: set[str] = set()
    for column in manifest.get("columns") or []:
        if not isinstance(column, dict):
            continue
        fk = column.get("foreign_key")
        if isinstance(fk, dict) and fk.get("references"):
            referenced = str(fk["references"]).split(".", 1)[0].strip()
            if referenced:
                deps.add(referenced)
    return deps


def order_tables_by_fk(bundle_root: Path, names: list[str]) -> list[str]:
    """Order table names so a table comes after any in-bundle table it
    references by foreign key. Alphabetical for ties; cycles/self-refs are left
    in stable order rather than failing."""
    names = list(names)
    in_bundle = set(names)
    deps_by_name: dict[str, set[str]] = {}
    for name in names:
        try:
            manifest = read_manifest(bundle_root, "tables", name)
        except (OSError, ValueError, FileNotFoundError):
            manifest = {}
        deps_by_name[name] = table_fk_dependencies(manifest) & in_bundle

    ordered: list[str] = []
    placed: set[str] = set()
    remaining = list(names)
    while remaining:
        ready = [name for name in remaining if deps_by_name[name] <= placed]
        if not ready:  # cycle / self-reference — emit the rest stably
            ready = list(remaining)
        for name in sorted(ready):
            ordered.append(name)
            placed.add(name)
        remaining = [name for name in remaining if name not in placed]
    return ordered


def read_manifest(bundle_root: Path, kind: str, name: str) -> dict[str, Any]:
    """Parse a resource's manifest JSON (tolerant of JSONC), with sidecar
    ``$file``/``$json_file`` references resolved. Raises if missing."""
    resource_dir = Path(bundle_root) / kind / name
    path = manifest_path(resource_dir)
    if path is None:
        raise FileNotFoundError(f"No manifest for {kind}/{name} in {bundle_root}")
    payload = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return resolve_file_refs(payload, base_dir=resource_dir)
