"""Regenerate exact server-process event catalogs from literal runtime calls."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METHODS = {"debug", "info", "warning", "error"}
TARGETS = {
    ROOT / "lemma-backend" / "app" / "core" / "log" / "event_catalog.py": (
        ROOT / "lemma-backend" / "app",
        ROOT / "lemma-backend" / "scripts",
    ),
    ROOT / "agentbox" / "agentbox" / "event_catalog.py": (
        ROOT / "agentbox" / "agentbox",
    ),
}


def logger_method(node: ast.Call) -> str | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in METHODS:
        return None
    receiver = node.func.value
    if isinstance(receiver, ast.Name) and receiver.id in {"logger", "fs_logger"}:
        return node.func.attr
    if isinstance(receiver, ast.Attribute) and receiver.attr in {"logger", "_logger"}:
        return node.func.attr
    if (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "get_logger"
    ):
        return node.func.attr
    return None


def collect(packages: tuple[Path, ...]) -> dict[str, tuple[str, frozenset[str]]]:
    levels: defaultdict[str, set[str]] = defaultdict(set)
    fields: defaultdict[str, set[str]] = defaultdict(set)
    problems: list[str] = []
    for package in packages:
        for path in sorted(package.rglob("*.py")):
            if "tests" in path.parts or "test_support" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                method = logger_method(node)
                if method is None:
                    continue
                location = f"{path.relative_to(ROOT)}:{node.lineno}"
                if (
                    len(node.args) != 1
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    problems.append(f"{location}: event must be one literal argument")
                    continue
                if any(keyword.arg is None for keyword in node.keywords):
                    problems.append(f"{location}: unrestricted kwargs")
                    continue
                event = node.args[0].value
                levels[event].add(method)
                fields[event].update(
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg not in {None, "exc_info", "stack_info"}
                )
    for event, event_levels in levels.items():
        if len(event_levels) != 1:
            problems.append(f"{event}: conflicting levels {sorted(event_levels)}")
    if problems:
        raise SystemExit("\n".join(problems))
    return {
        event: (next(iter(levels[event])), frozenset(fields[event]))
        for event in levels
    }


def render(catalog: dict[str, tuple[str, frozenset[str]]]) -> str:
    lines = [
        '"""Generated exact event contract for this independently deployed service."""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Literal",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class EventSpec:",
        '    level: Literal["debug", "info", "warning", "error"]',
        "    fields: frozenset[str] = frozenset()",
        "",
        "",
        "EVENT_CATALOG: dict[str, EventSpec] = {",
        '    "logging.contract.violation": EventSpec("error"),',
    ]
    for event, (level, fields) in sorted(catalog.items()):
        expression = (
            "frozenset({" + ", ".join(repr(field) for field in sorted(fields)) + "})"
            if fields
            else "frozenset()"
        )
        lines.append(f"    {event!r}: EventSpec({level!r}, {expression}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    for destination, packages in TARGETS.items():
        catalog = collect(packages)
        destination.write_text(render(catalog))
        roots = ", ".join(str(package.relative_to(ROOT)) for package in packages)
        print(f"{roots}: {len(catalog)} events")


if __name__ == "__main__":
    main()
