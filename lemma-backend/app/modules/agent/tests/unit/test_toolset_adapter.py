"""A ToolSpec reaches pydantic-ai through the toolset adapter, unchanged."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.infrastructure.tools.workspace_cli import (
    tools as workspace_tools,
)
from app.modules.agent.infrastructure.tools.workspace_cli.models import (
    ExecCommandResult,
)
from app.modules.agent.services.tool_runtime import ToolRuntime


@pytest.mark.asyncio
async def test_a_spec_runs_through_the_runtime_with_its_own_description(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_exec_command(ctx, request):
        return ExecCommandResult(
            success=True,
            stdout=f"{ctx.conversation_id}:{request.cmd}",
            completed=True,
        )

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.workspace_cli.workspace_cli.exec_command_internal",
        fake_exec_command,
    )
    run_ctx = RunContext(
        deps=ToolContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    toolset = function_toolset(
        workspace_tools.SPECS, ToolRuntime(workspace_tools.SPECS)
    )
    prepared = await toolset.for_run(run_ctx)
    async with prepared:
        tools = await prepared.get_tools(run_ctx)
        tool = tools["exec_command"]
        validated = tool.args_validator.validate_python(
            {"cmd": "pwd"},
            context=run_ctx.validation_context,
        )
        result = await prepared.call_tool(
            "exec_command",
            validated,
            run_ctx,
            tool,
        )

    assert isinstance(result, ExecCommandResult)
    assert result.success is True
    assert result.stdout == f"{run_ctx.deps.conversation_id}:pwd"
    assert tool.tool_def.description.startswith(
        "Run a shell command in the private conversation workspace."
    )
    assert tool.tool_def.parameters_json_schema["properties"]["cmd"]["type"] == "string"


def test_a_tools_schema_is_derived_once_per_process() -> None:
    """Deriving a tool's validator is the costliest part of assembling a run's
    tools, and depends only on its request model: every run after the first
    reuses it."""
    from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
        function_toolset,
    )
    from app.modules.agent.infrastructure.tools.deferred.tools import (
        deferred_entry_specs,
    )
    from app.modules.agent.services.tool_runtime import ToolRuntime

    specs = deferred_entry_specs([])
    runtime = ToolRuntime(specs)
    first = function_toolset(specs, runtime).tools["call_tool"]
    second = function_toolset(specs, runtime).tools["call_tool"]

    assert first is not second
    assert first.function_schema.validator is second.function_schema.validator
    assert first.function_schema.function is not second.function_schema.function
