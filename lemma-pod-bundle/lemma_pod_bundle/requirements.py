"""Derive a bundle's ``requirements`` and ``capabilities`` from what it carries.

The keystone of the guided import experience, now shared so the CLI (export +
`pods requirements`) and the backend (`pod_import` plan builder) derive the same
facts from the same code. Pure: reads a bundle directory on disk, no live pod.

* **requirements** — what must be wired up before this pod can run elsewhere:
  connector accounts, people (workflow assignees), free variables, and seed data.
  Each entry records *why* (``purpose``) and *where* (``used_by``) so the importer
  is never asked for a value blind and nothing it needs is silently dropped.
* **capabilities** — what the pod *does*, tier-ordered most-sensitive first — the
  app-store-style consent manifest.

This module is intentionally self-contained (its own tolerant JSON reader and
directory scanner) so it carries no dependency on the CLI's large bundle module:
grants and account references all live inline in the manifest JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lemma_pod_bundle.jsonc import loads_jsonc

_RESOURCE_KINDS = (
    "tables",
    "functions",
    "agents",
    "workflows",
    "schedules",
    "surfaces",
    "apps",
)
_PERMISSIONED_KINDS = ("agents", "functions")
_TABLE_DATA_CANDIDATES = ("data.csv", "data.jsonl", "data.json")

# Consent tiers, ordered most-sensitive first — also the emit order.
CAPABILITY_TIER_ORDER = ("code", "external", "ai", "data")


# -- self-contained bundle readers ------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    payload = loads_jsonc(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _manifest_path(resource_dir: Path) -> Path | None:
    """The resource's manifest JSON — ``<dir-name>.json``, else a lone ``*.json``."""
    primary = resource_dir / f"{resource_dir.name}.json"
    if primary.is_file():
        return primary
    jsons = sorted(resource_dir.glob("*.json"))
    return jsons[0] if len(jsons) == 1 else None


def _resource_dirs(bundle_root: Path, kind: str) -> list[Path]:
    base = bundle_root / kind
    if not base.is_dir():
        return []
    return [
        path
        for path in sorted(base.iterdir())
        if path.is_dir() and _manifest_path(path) is not None
    ]


def _grants_for(resource_dir: Path) -> list[dict[str, Any]]:
    manifest = _manifest_path(resource_dir)
    if manifest is None:
        return []
    try:
        payload = _read_json(manifest)
    except (OSError, ValueError):
        return []
    permissions = payload.get("permissions")
    grants = (permissions or {}).get("grants") if isinstance(permissions, dict) else None
    return [grant for grant in (grants or []) if isinstance(grant, dict)]


# -- small helpers -----------------------------------------------------------


def _plural(n: int) -> str:
    return "" if n == 1 else "s"


def _placeholder(name: str) -> str:
    return "${" + name + "}"


def _count_dirs(parent: Path) -> int:
    if not parent.is_dir():
        return 0
    return sum(1 for child in parent.iterdir() if child.is_dir())


def _count_rows(path: Path) -> int:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data) if isinstance(data, list) else 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = sum(1 for _ in handle)
        if suffix == ".csv":
            return max(0, lines - 1)
        return lines
    except (OSError, ValueError):
        return 0


def _merge_used_by(entry: dict[str, Any], used_by: list[str]) -> None:
    seen = set(entry.get("used_by") or [])
    for ref in used_by:
        if ref not in seen:
            entry.setdefault("used_by", []).append(ref)
            seen.add(ref)
    entry["used_by"] = sorted(entry.get("used_by") or [])


def _used_by_for_token(bundle_root: Path, token: str) -> list[str]:
    refs: list[str] = []
    for kind in _RESOURCE_KINDS:
        kind_dir = bundle_root / kind
        if not kind_dir.is_dir():
            continue
        for sub in sorted(kind_dir.iterdir()):
            if not sub.is_dir():
                continue
            blob = ""
            for manifest in sub.glob("*.json"):
                try:
                    blob += manifest.read_text(encoding="utf-8")
                except OSError:
                    continue
            if token in blob:
                refs.append(f"{kind}/{sub.name}")
    return refs


# -- requirement derivation --------------------------------------------------


def _connectors(bundle_root: Path, variables: dict[str, Any]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}

    for name, spec in variables.items():
        spec = spec or {}
        if str(spec.get("type") or "") != "account":
            continue
        platform = str(spec.get("platform") or "").lower()
        key = platform or name
        entry = by_key.setdefault(key, {"key": key, "used_by": [], "required": True})
        if platform:
            entry["platform"] = platform
        if spec.get("description"):
            entry["purpose"] = spec["description"]
        entry["binds_variable"] = name
        entry["resolution"] = {
            "strategy": "match_or_connect",
            "match_on": "platform" if platform else "account",
            "var": name,
        }
        _merge_used_by(entry, _used_by_for_token(bundle_root, _placeholder(name)))

    for kind in _PERMISSIONED_KINDS:
        for resource_dir in _resource_dirs(bundle_root, kind):
            for grant in _grants_for(resource_dir):
                if str(grant.get("resource_type") or "") != "connector":
                    continue
                connector_id = str(grant.get("resource_name") or "")
                if not connector_id:
                    continue
                key = connector_id.lower()
                entry = by_key.setdefault(
                    key, {"key": key, "used_by": [], "required": True}
                )
                entry.setdefault("platform", key)
                entry.setdefault("auth", "oauth")
                entry.setdefault(
                    "resolution",
                    {"strategy": "match_or_connect", "match_on": "connector_id"},
                )
                _merge_used_by(entry, [f"{kind}/{resource_dir.name}"])

    return [by_key[key] for key in sorted(by_key)]


def _members(bundle_root: Path, variables: dict[str, Any]) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for name, spec in sorted(variables.items()):
        spec = spec or {}
        if str(spec.get("type") or "") != "pod_member":
            continue
        members.append(
            {
                "key": name,
                "role": "workflow_assignee",
                "source": {"member_id": spec.get("source_value")},
                "purpose": spec.get("description"),
                "used_by": sorted(_used_by_for_token(bundle_root, _placeholder(name))),
                "resolution": {"strategy": "default_importing_user", "var": name},
                "required": True,
            }
        )
    return members


def _generic_variables(variables: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, spec in sorted(variables.items()):
        spec = spec or {}
        vtype = str(spec.get("type") or "")
        if vtype in ("pod_member", "account"):
            continue
        entry: dict[str, Any] = {"key": name, "type": vtype or "string"}
        if spec.get("description"):
            entry["purpose"] = spec["description"]
        if spec.get("example") is not None:
            entry["example"] = spec["example"]
        out.append(entry)
    return out


def _seed_tables(bundle_root: Path) -> list[tuple[str, Path]]:
    tables_dir = bundle_root / "tables"
    seeded: list[tuple[str, Path]] = []
    if not tables_dir.is_dir():
        return seeded
    for sub in sorted(tables_dir.iterdir()):
        if not sub.is_dir():
            continue
        for candidate in _TABLE_DATA_CANDIDATES:
            data_file = sub / candidate
            if data_file.is_file():
                seeded.append((sub.name, data_file))
                break
    return seeded


def _data_requirement(bundle_root: Path) -> dict[str, Any]:
    seeded = _seed_tables(bundle_root)
    if not seeded:
        return {}
    return {
        "tables_with_seed": [name for name, _ in seeded],
        "row_count": sum(_count_rows(path) for _, path in seeded),
        "size_bytes": sum(path.stat().st_size for _, path in seeded),
    }


def _capabilities(
    bundle_root: Path, connectors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    caps: list[dict[str, Any]] = []

    n_functions = _count_dirs(bundle_root / "functions")
    if n_functions:
        caps.append(
            {"tier": "code", "summary": f"Run {n_functions} Python function{_plural(n_functions)}"}
        )

    if connectors:
        names = ", ".join(str(conn.get("platform") or conn["key"]) for conn in connectors)
        caps.append(
            {"tier": "external", "summary": f"Use your {names} connection{_plural(len(connectors))}"}
        )

    n_agents = _count_dirs(bundle_root / "agents")
    if n_agents:
        caps.append({"tier": "ai", "summary": f"Run {n_agents} AI agent{_plural(n_agents)}"})

    n_tables = _count_dirs(bundle_root / "tables")
    if n_tables:
        seeded = _seed_tables(bundle_root)
        summary = f"Create {n_tables} table{_plural(n_tables)}"
        if seeded:
            rows = sum(_count_rows(path) for _, path in seeded)
            summary += f", seed {rows} row{_plural(rows)}"
        caps.append({"tier": "data", "summary": summary})

    return caps


def extract_requirements(bundle_root: Path, *, write: bool = True) -> dict[str, Any]:
    """Derive ``{requirements, capabilities}`` for the bundle at ``bundle_root``.

    Reads the ``variables`` block plus the resource folders. When ``write`` is
    true the result is persisted into ``pod.json``. Returns ``{}`` if there's no
    ``pod.json``.
    """
    bundle_root = Path(bundle_root)
    pod_path = bundle_root / "pod.json"
    if not pod_path.is_file():
        return {}
    pod_data = _read_json(pod_path)
    variables = dict(pod_data.get("variables") or {})

    connectors = _connectors(bundle_root, variables)
    members = _members(bundle_root, variables)
    generic = _generic_variables(variables)
    data = _data_requirement(bundle_root)

    requirements: dict[str, Any] = {}
    if connectors:
        requirements["connectors"] = connectors
    if members:
        requirements["members"] = members
    if generic:
        requirements["variables"] = generic
    if data:
        requirements["data"] = data
    # `secrets` is a reserved, currently-unpopulated category — credential needs
    # are covered by connector-account requirements today.

    capabilities = _capabilities(bundle_root, connectors)
    result = {"requirements": requirements, "capabilities": capabilities}

    if write:
        pod_data["requirements"] = requirements
        pod_data["capabilities"] = capabilities
        _write_json(pod_path, pod_data)
    return result


def read_requirements(source_dir: Path) -> dict[str, Any]:
    """Return a bundle's ``{requirements, capabilities}`` — from ``pod.json`` if a
    requirements-aware export wrote them, otherwise derived on the fly."""
    source_dir = Path(source_dir)
    pod_path = source_dir / "pod.json"
    if not pod_path.is_file():
        return {"requirements": {}, "capabilities": []}
    pod_data = _read_json(pod_path)
    if "requirements" in pod_data or "capabilities" in pod_data:
        return {
            "requirements": pod_data.get("requirements") or {},
            "capabilities": pod_data.get("capabilities") or [],
        }
    return extract_requirements(source_dir, write=False)


def unresolved_requirements(
    source_dir: Path, *, supplied_vars: set[str] | None = None
) -> list[dict[str, Any]]:
    """List requirements that still need a human before import can safely apply.

    A connector or variable bound to an unsupplied variable blocks; ``pod_member``
    requirements default to the importing user, so they never block. This is what
    turns a silent placeholder-drop into an explicit gate.
    """
    supplied = supplied_vars or set()
    summary = read_requirements(source_dir)
    requirements = summary.get("requirements") or {}
    blocking: list[dict[str, Any]] = []

    for connector in requirements.get("connectors") or []:
        var = (connector.get("resolution") or {}).get("var")
        if var and var not in supplied:
            blocking.append({"kind": "connector", **connector})

    for member in requirements.get("members") or []:
        resolution = member.get("resolution") or {}
        if resolution.get("strategy") == "default_importing_user":
            continue
        var = resolution.get("var")
        if var and var not in supplied:
            blocking.append({"kind": "member", **member})

    for variable in requirements.get("variables") or []:
        if variable.get("key") not in supplied:
            blocking.append({"kind": "variable", **variable})

    return blocking
