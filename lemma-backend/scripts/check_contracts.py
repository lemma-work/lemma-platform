#!/usr/bin/env python3
"""Keep the module contracts honest against the code they describe.

`docs/contracts/` says what each API operation and each event guarantees:
authorization, preconditions, effects, errors, and what it emits. That is worth
writing only if it cannot quietly stop being true, which is what this enforces.

Two directions, both of which matter:

* **Nothing undocumented.** Every operation in the committed OpenAPI
  specification and every event in the two catalogs has a contract entry. A new
  route lands with its contract or the build fails.
* **Nothing orphaned.** Every entry names an operation or event that still
  exists. A route deleted or renamed takes its contract with it, rather than
  leaving prose describing something that is gone — which is worse than no
  prose, because people believe it.

Run with `--write` to create or refresh the per-module skeletons. Hand-written
prose between the markers is preserved; only the generated tables move.

Same posture as `generate_route_inventory.py` and `dump_openapi_spec.py
--check`, deliberately: one more generated-then-gated document, not a new idea.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"
OPENAPI = ROOT.parent / "lemma-python" / "lemma_sdk" / "openapi_spec.json"
LOG_CATALOG = ROOT / "app" / "core" / "log" / "event_catalog.py"
ANALYTICS_CATALOG = ROOT / "app" / "core" / "analytics" / "event_catalog.py"

METHODS = ("get", "post", "put", "patch", "delete")

#: Same mapping the route inventory uses. Kept in step with it deliberately:
#: two documents disagreeing about which module owns a route is its own bug.
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
    "Pod Resource Preview": "pod",
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
    "agent_host": "agent",
    "agent_runtime": "agent",
    "agents": "agent",
    "files": "datastore",
    "icons": "icon",
    "notifications": "agent_surfaces",
    "query": "datastore",
    "records": "datastore",
    "tables": "datastore",
    "workflows": "workflow",
}

GENERATED_START = "<!-- generated:operations -- do not edit below -->"
GENERATED_END = "<!-- /generated:operations -->"

ENTRY_RE = re.compile(r"^\| `([a-z_][a-z0-9_.]*)` \|", re.M)

#: An event with no contract entry is as much a gap as an undocumented route —
#: more so, since events have no OpenAPI to fall back on.



def module_of(operation: dict) -> str:
    tags = operation.get("tags") or []
    if not tags:
        return "core"
    modules = {TAG_MODULES[tag] for tag in tags if tag in TAG_MODULES}
    return modules.pop() if len(modules) == 1 else "core"


def load_operations() -> dict[str, list[tuple[str, str, str, str]]]:
    """Operations grouped by module: (operationId, method, path, summary)."""
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    grouped: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method not in METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                continue
            grouped[module_of(operation)].append(
                (
                    operation_id,
                    method.upper(),
                    path,
                    (operation.get("summary") or "").strip(),
                )
            )
    for rows in grouped.values():
        rows.sort()
    return grouped


def load_events() -> tuple[set[str], set[str]]:
    analytics = set(
        re.findall(
            r'^\s{4}"([a-z_]+\.[a-z_]+)":\s*AnalyticEvent',
            ANALYTICS_CATALOG.read_text(encoding="utf-8"),
            re.M,
        )
    )
    domain: set[str] = set()
    if LOG_CATALOG.is_file():
        domain = set(
            re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', LOG_CATALOG.read_text("utf-8"))
        )
    return analytics, domain


def render(module: str, rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        f"# {module} contract",
        "",
        f"What every `{module}` API operation guarantees: who may call it, what "
        "must be true first, what changes, what it emits, and how it refuses.",
        "",
        (
            "The product promises these serve are in "
            "[the product specification](../../../docs/product/README.md). This "
            "says what each operation does; that says what any of it is for."
        ),
        "",
        (
            "The table below is generated from the committed OpenAPI "
            "specification by `scripts/check_contracts.py --write`. Add the "
            "behaviour in prose under each operation's heading, outside the "
            "generated block — that part is preserved across regeneration."
        ),
        "",
        GENERATED_START,
        "",
        "| Operation | Method | Path | Summary |",
        "| --- | --- | --- | --- |",
    ]
    for operation_id, method, path, summary in rows:
        lines.append(f"| `{operation_id}` | {method} | `{path}` | {summary} |")
    lines += ["", GENERATED_END, ""]
    return "\n".join(lines)


def render_events(analytics: set[str]) -> str:
    """The analytics event contract.

    One file rather than per-module because these events are deliberately not
    owned by a module — they are named for the product's nouns, and several are
    emitted from more than one place. Splitting them by publisher would make the
    document about the code rather than about the contract.
    """
    lines = [
        "# Event contract",
        "",
        "Every product event the platform records, and what each one means.",
        "",
        (
            "These are the events in "
            "[`app/core/analytics/event_catalog.py`]"
            "(../../app/core/analytics/event_catalog.py), which is append-only "
            "and already gated: emitting a name absent from it raises in "
            "development and CI. This document is where the *meaning* lives — "
            "when it fires, exactly once or at least once, and what a consumer "
            "may assume."
        ),
        "",
        (
            "Fill in the behaviour under each event. The table is generated by "
            "`scripts/check_contracts.py --write`."
        ),
        "",
        GENERATED_START,
        "",
        "| Event | Meaning |",
        "| --- | --- |",
    ]
    for name in sorted(analytics):
        lines.append(f"| `{name}` | |")
    lines += ["", GENERATED_END, ""]
    return "\n".join(lines)


def write_skeletons(grouped) -> list[str]:
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    written = []
    for module, rows in sorted(grouped.items()):
        target = CONTRACTS / f"{module}.md"
        generated = render(module, rows)
        if target.is_file():
            existing = target.read_text(encoding="utf-8")
            if GENERATED_START in existing and GENERATED_END in existing:
                head = existing.split(GENERATED_START)[0]
                tail = existing.split(GENERATED_END, 1)[1]
                body = generated.split(GENERATED_START, 1)[1].rsplit(GENERATED_END, 1)[0]
                target.write_text(
                    f"{head}{GENERATED_START}{body}{GENERATED_END}{tail}",
                    encoding="utf-8",
                )
                written.append(target.name)
                continue
        target.write_text(generated, encoding="utf-8")
        written.append(target.name)

    analytics, _ = load_events()
    events_file = CONTRACTS / "events.md"
    generated = render_events(analytics)
    if events_file.is_file() and GENERATED_START in events_file.read_text("utf-8"):
        existing = events_file.read_text("utf-8")
        head = existing.split(GENERATED_START)[0]
        tail = existing.split(GENERATED_END, 1)[1]
        body = generated.split(GENERATED_START, 1)[1].rsplit(GENERATED_END, 1)[0]
        events_file.write_text(
            f"{head}{GENERATED_START}{body}{GENERATED_END}{tail}", encoding="utf-8"
        )
    else:
        events_file.write_text(generated, encoding="utf-8")
    written.append(events_file.name)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="create/refresh skeletons")
    args = parser.parse_args()

    grouped = load_operations()
    analytics, _domain = load_events()
    live = {op for rows in grouped.values() for op, *_ in rows} | analytics

    if args.write:
        written = write_skeletons(grouped)
        print(f"Wrote {len(written)} contract file(s) to {CONTRACTS.relative_to(ROOT)}")
        return 0

    errors: list[str] = []
    if not CONTRACTS.is_dir():
        print(
            "No docs/contracts/ yet. Create it with:\n"
            "  uv run python scripts/check_contracts.py --write",
            file=sys.stderr,
        )
        return 1

    documented: set[str] = set()
    for path in sorted(CONTRACTS.glob("*.md")):
        documented |= set(ENTRY_RE.findall(path.read_text(encoding="utf-8")))

    for rows in grouped.values():
        for operation_id, method, path, _ in rows:
            if operation_id not in documented:
                errors.append(
                    f"{method} {path} (`{operation_id}`) has no contract entry; "
                    f"run scripts/check_contracts.py --write"
                )
    for event in sorted(analytics - documented):
        errors.append(
            f"analytics event `{event}` has no contract entry; "
            f"run scripts/check_contracts.py --write"
        )
    for name in sorted(documented - live):
        errors.append(
            f"contract entry `{name}` names an operation or event that no longer "
            f"exists; delete it or fix the name"
        )

    if errors:
        print("Contract check failed:\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"✓ contracts: {len(documented)} entries cover {len(live)} live names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
