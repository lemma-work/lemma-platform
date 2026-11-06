"""Two lines the tool rework drew, held by reading the source.

* A decision about a tool is made on its ``ToolPolicy``, never by matching its
  name: no tool-name string literal is compared against outside the tools
  themselves (``infrastructure/tools/``) and ``domain/tools.py``. A stored
  call is still read back by the name it was stored under, through a member of
  ``PausingKind`` or a constant the tool's own module declares.
* pydantic-ai is the in-process harness's library, so only
  ``infrastructure/harness/lemma/`` and ``infrastructure/providers/`` import it.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MODULE = Path(__file__).resolve().parents[2]
_GOLDEN = _MODULE / "tests" / "unit" / "goldens" / "tool_surfaces.json"


def _sources() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(_MODULE.rglob("*.py")):
        rel = path.relative_to(_MODULE).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel:
            continue
        yield rel, ast.parse(path.read_text(), filename=rel)


def _tool_names() -> set[str]:
    """Every built-in tool name, off the recorded tool surfaces."""
    pool = json.loads(_GOLDEN.read_text())["pool"]
    names = {
        entry["name"].removeprefix("lemma_")
        for entry in pool.values()
        if isinstance(entry, dict) and "name" in entry
    }
    names = {
        name
        for name in names
        if not name.startswith(("function_", "agent_")) and name != "final_result"
    }
    return names | {"final_answer"}


def _strings(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.Set | ast.Tuple | ast.List):
        for element in node.elts:
            yield from _strings(element)
    elif isinstance(node, ast.Call) and node.args:
        if isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set"}:
            yield from _strings(node.args[0])


def _may_name_tools(rel: str) -> bool:
    return rel.startswith("infrastructure/tools/") or rel == "domain/tools.py"


def test_no_decision_is_made_by_matching_a_tool_name() -> None:
    names = _tool_names()
    found: list[str] = []
    for rel, tree in _sources():
        if _may_name_tools(rel):
            continue
        # A module-level name bound to a tool name is the same literal, moved.
        local = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign) and set(_strings(node.value)) & names
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for operand in (node.left, *node.comparators):
                hits = set(_strings(operand)) & names
                if isinstance(operand, ast.Name) and operand.id in local:
                    hits.add(operand.id)
                found.extend(f"{rel}:{node.lineno} {hit}" for hit in sorted(hits))
    assert found == []


_PYDANTIC_AI_HOMES = ("infrastructure/harness/lemma/", "infrastructure/providers/")


def test_pydantic_ai_is_imported_only_by_the_harness_and_providers() -> None:
    found: list[str] = []
    for rel, tree in _sources():
        if rel.startswith(_PYDANTIC_AI_HOMES):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(m == "pydantic_ai" or m.startswith("pydantic_ai.") for m in modules):
                found.append(f"{rel}:{node.lineno}")
    assert found == []
