from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import pydantic_adapter
from app.modules.agent.tools.workspace_cli.models import ExecCommandResult


@pytest.mark.asyncio
async def test_workspace_cli_pydantic_wrapper_uses_docstring_description(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_exec_command(ctx, request):
        return ExecCommandResult(
            success=True,
            stdout=f"{ctx.conversation_id}:{request.cmd}",
            completed=True,
        )

    monkeypatch.setattr(
        "app.modules.agent.tools.workspace_cli.workspace_cli.exec_command_internal",
        fake_exec_command,
    )
    run_ctx = RunContext(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    prepared = await pydantic_adapter.workspace_cli_toolset.for_run(run_ctx)
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
