#!/usr/bin/env python3
"""Export agent tool metadata to a single JSON file.

Imports the live pydantic-ai toolsets the agent harness actually wires up and
emits, per tool:

- ``tool_name``     — the name the model calls
- ``toolset``       — source group the tool belongs to
- ``description``   — the tool's prompt-facing description
- ``input_schema``  — JSON Schema the model sees for the tool's arguments
- ``output_schema`` — JSON Schema of the tool's return value (``null`` when the
                      return type has no structured schema, e.g. ``str``/dict)

The schemas come straight from each tool's pydantic-ai ``ToolDefinition`` (the
exact thing handed to the model), so they stay correct as the request/response
models evolve — no brittle source parsing.

Run it inside the project environment so ``pydantic_ai`` and ``app`` import::

    uv run python scripts/export_agent_tool_schemas.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID

# Make ``app...`` importable regardless of the current working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic_ai.tools import RunContext  # noqa: E402
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from app.modules.agent.capabilities.todo import build_todo_toolset  # noqa: E402
from app.modules.agent.domain.entities import Agent  # noqa: E402
from app.modules.agent.domain.value_objects import AgentToolset  # noqa: E402
from app.modules.agent.tools.final_answer.final_answer_tool import (  # noqa: E402
    get_final_answer_tool,
)
from app.modules.agent.tools.registry import resolve_agent_toolsets  # noqa: E402


def _build_toolsets() -> list[tuple[str, AbstractToolset[Any]]]:
    """Return every statically-defined agent toolset, labelled by source group.

    The registry toolsets are pulled through ``resolve_agent_toolsets`` one enum
    at a time, so any newly registered ``AgentToolset`` is picked up here for
    free and labelled with its enum name. The remaining agent-callable surfaces
    are not in that registry and are added explicitly:

    - ``TODO`` is capability-only (realized per-conversation), so it is built
      directly. ``uow_factory``/``conversation_id`` are never exercised during
      schema extraction — the tool is not invoked — so placeholders are fine.
    - ``final_answer`` is a per-agent factory; with no output schema it degrades
      to a plain string output, which is all we need to describe its shape.
    """
    labelled: list[tuple[str, AbstractToolset[Any]]] = []

    for member in AgentToolset:
        # Capability-only toolsets (e.g. TODO) resolve to nothing here.
        for toolset in resolve_agent_toolsets([member]):
            labelled.append(
                (member.value, cast(AbstractToolset[Any], toolset))
            )

    labelled.append(
        (
            AgentToolset.TODO.value,
            build_todo_toolset(uow_factory=None, conversation_id=UUID(int=0)),  # type: ignore[arg-type]
        )
    )
    labelled.append(
        (
            "FINAL_ANSWER",
            FunctionToolset(
                tools=[get_final_answer_tool(Agent.model_construct(output_schema=None))]
            ),
        )
    )
    return labelled


def _make_run_context() -> RunContext[Any]:
    """A minimal run context — enough for static toolsets to build tool defs.

    Schema extraction never runs a tool, so the deps payload is irrelevant; this
    mirrors how ``AgentToolDispatcher`` constructs its standalone context.
    """
    return RunContext(
        deps=None,
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
        retries={},
        run_id=None,
        metadata={},
        model_settings=None,
    )


async def _extract_toolset(
    label: str, toolset: AbstractToolset[Any]
) -> list[dict[str, Any]]:
    run_ctx = _make_run_context()
    prepared = await toolset.for_run(run_ctx)
    rows: list[dict[str, Any]] = []
    async with prepared:
        tools = await prepared.get_tools(run_ctx)
        for name, tool in tools.items():
            td = tool.tool_def
            rows.append(
                {
                    "tool_name": td.name or name,
                    "toolset": label,
                    "description": (td.description or "").strip(),
                    "input_schema": td.parameters_json_schema or {},
                    "output_schema": td.return_schema,
                }
            )
    return rows


async def collect_tools() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, toolset in _build_toolsets():
        for row in await _extract_toolset(label, toolset):
            name = row["tool_name"]
            if name in seen:
                # Same tool reachable from two toolsets — keep the first.
                continue
            seen.add(name)
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
