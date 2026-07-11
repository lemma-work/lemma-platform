#!/usr/bin/env python3
"""Generate a deterministic OpenAPI route inventory grouped by backend module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT.parent / "lemma-python" / "lemma_sdk" / "openapi_spec.json"
DEFAULT_OUTPUT = ROOT / "docs" / "modules" / "route-inventory.md"
METHODS = {"get", "post", "put", "patch", "delete"}
TAG_MODULES = {
    "Agent Surfaces": "agent_surfaces",
    "Agent Surfaces (Ingress)": "agent_surfaces",
    "Agent Surfaces (Me)": "agent_surfaces",
    "Apps": "apps",
    "Auth": "identity",
    "Connectors": "connectors",
    "Functions": "function",
    "Organizations": "identity",
    "Pod Bundle": "pod_bundle",
    "Pod Join Requests": "pod",
    "Pod Members": "pod",
    "Pod Permissions": "pod",
    "Pod Resource Access": "pod",
    "Pod Roles": "pod",
    "Pods": "pod",
    "Schedules": "schedule",
    "Usage": "usage",
    "Users": "identity",
    "Widgets": "agent",
    "Workspace": "workspace",
    "Workspace Apps": "workspace",
    "agent-tools": "agent",
    "agent_conversations": "agent",
    "agent_runtime": "agent",
    "agents": "agent",
    "files": "datastore",
    "icons": "icon",
    "query": "datastore",
    "records": "datastore",
    "tables": "datastore",
    "workflows": "workflow",
}


def _module(operation: dict[str, Any]) -> str:
    tags = operation.get("tags") or []
    if not tags:
        return "core"
    unknown = sorted(set(tags) - TAG_MODULES.keys())
    if unknown:
        raise ValueError(f"Unmapped OpenAPI tags: {', '.join(unknown)}")
    modules = {TAG_MODULES[tag] for tag in tags}
    if len(modules) != 1:
        raise ValueError(f"Operation spans multiple module tags: {sorted(modules)}")
    return modules.pop()


def render(specification: dict[str, Any]) -> str:
    rows: dict[str, list[tuple[str, str, str, str]]] = {}
    for path, path_item in specification.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in METHODS or not isinstance(operation, dict):
                continue
            module = _module(operation)
            rows.setdefault(module, []).append(
                (
                    method.upper(),
                    path,
                    str(operation.get("operationId") or "—"),
                    str(operation.get("summary") or "—"),
                )
            )

    lines = [
        "# Lemma backend route inventory",
        "",
        "Generated from the committed OpenAPI specification. Do not edit by hand;",
        "run `uv run python scripts/generate_route_inventory.py`.",
        "",
    ]
    for module in sorted(rows):
        documentation = ROOT / "docs" / "modules" / f"{module}.md"
        if module != "core" and not documentation.exists():
            raise FileNotFoundError(f"Missing canonical module doc: {documentation}")
        lines.extend(
            [
                f"## {module}",
                "",
                "| Method | Path | Operation ID | Summary |",
                "| --- | --- | --- | --- |",
            ]
        )
        for method, path, operation_id, summary in sorted(rows[module]):
            lines.append(f"| {method} | `{path}` | `{operation_id}` | {summary} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = render(json.loads(args.spec.read_text(encoding="utf-8")))
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            print(f"Route inventory is stale: {args.output}")
            return 1
        print(f"Route inventory is current: {args.output}")
        return 0
    args.output.write_text(content, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
