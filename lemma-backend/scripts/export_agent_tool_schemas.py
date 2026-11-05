#!/usr/bin/env python3
"""Export agent tool metadata to a single JSON file.

Reads the ToolSpecs every surface serves and emits, per tool:

- ``tool_name``     — the name the model calls
- ``toolset``       — source group the tool belongs to
- ``description``   — the tool's prompt-facing description
- ``input_schema``  — JSON Schema the model sees for the tool's arguments
- ``output_schema`` — JSON Schema of the tool's return value

The input schema and description are the spec's own, the exact ones handed to
models and MCP clients; the output schema is derived from the tool function's
return annotation the way pydantic-ai derives it.

Run it inside the project environment so ``pydantic_ai`` and ``app`` import::

    uv run python scripts/export_agent_tool_schemas.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, get_type_hints

# Make ``app...`` importable regardless of the current working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import TypeAdapter  # noqa: E402
from pydantic_ai._function_schema import extract_return_schema_type  # noqa: E402
from pydantic_ai.tools import GenerateToolJsonSchema, Tool  # noqa: E402

from app.modules.agent.domain.entities import Agent  # noqa: E402
from app.modules.agent.domain.tools import ToolSpec  # noqa: E402
from app.modules.agent.domain.value_objects import AgentToolset  # noqa: E402
from app.modules.agent.infrastructure.harness.lemma.final_answer_output import (  # noqa: E402
    get_final_answer_tool,
)
from app.modules.agent.infrastructure.tools.messaging import answer  # noqa: E402
from app.modules.agent.infrastructure.tools.todo import todo_specs  # noqa: E402
from app.modules.agent.services.tool_catalog import STATIC_SPECS  # noqa: E402


def _labelled_specs() -> list[tuple[str, ToolSpec]]:
    """Every statically-defined spec, labelled by the toolset that brings it.

    TODO is built per run (it needs a unit of work, never used here) and
    ``answer_question`` rides only while a question is open, so both are added
    explicitly.
    """
    labelled = [
        (member.value, spec)
        for member in AgentToolset
        for spec in STATIC_SPECS.get(member, ())
    ]
    labelled += [
        (AgentToolset.TODO.value, spec) for spec in todo_specs(uow_factory=object)
    ]
    labelled += [(AgentToolset.MESSAGING.value, spec) for spec in answer.SPECS]
    return labelled


def _output_schema(function: Any) -> dict[str, Any]:
    return_type = extract_return_schema_type(
        get_type_hints(function).get("return"), function
    )
    return TypeAdapter(return_type).json_schema(
        schema_generator=GenerateToolJsonSchema, mode="serialization"
    )


def _row(label: str, spec: ToolSpec) -> dict[str, Any]:
    return {
        "tool_name": spec.name,
        "toolset": label,
        "description": spec.description.strip(),
        "input_schema": spec.json_schema,
        "output_schema": _output_schema(spec.run),
    }


def _final_answer_row() -> dict[str, Any]:
    """The in-process harness's output tool, which is not a ToolSpec."""
    tool = Tool(get_final_answer_tool(Agent.model_construct(output_schema=None)))
    return {
        "tool_name": tool.name,
        "toolset": "FINAL_ANSWER",
        "description": (tool.description or "").strip(),
        "input_schema": tool.function_schema.json_schema,
        "output_schema": tool.function_schema.return_schema,
    }


async def collect_tools() -> list[dict[str, Any]]:
    rows = [_row(label, spec) for label, spec in _labelled_specs()]
    rows.append(_final_answer_row())
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        # Same tool reachable from two toolsets: keep the first.
        if row["tool_name"] not in seen:
            seen.add(row["tool_name"])
            results.append(row)
    results.sort(key=lambda row: row["tool_name"])
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default=str(ROOT / "agent_tool_schemas.json"),
        help="Output JSON file path",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    data = asyncio.run(collect_tools())
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    names = ", ".join(row["tool_name"] for row in data)
    print(f"Wrote {len(data)} tools to {output_path}")
    print(f"Tools: {names}")


if __name__ == "__main__":
    main()
