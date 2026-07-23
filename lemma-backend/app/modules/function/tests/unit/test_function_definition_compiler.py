from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.application.function_definition_compiler import (
    FunctionDefinitionCompiler,
)
from app.modules.function.domain.errors import FunctionValidationError


pytestmark = pytest.mark.asyncio


class _Session:
    def __init__(self, response) -> None:
        self._response = response
        self.entered = False
        self.exited = False
        self.program: str | None = None
        self.timeout: int | None = None

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def execute_code(self, program: str, timeout: int):
        self.program = program
        self.timeout = timeout
        marker_start = program.index("__LEMMA_FUNCTION_SCHEMAS__")
        marker_end = program.index(":", marker_start) + 1
        marker = program[marker_start:marker_end]
        response = self._response
        if response.stdout == "AUTO":
            response.stdout = marker + json.dumps(
                {
                    "input": {"type": "object"},
                    "output": {"type": "object"},
                    "config": None,
                }
            )
        return response


async def test_schema_extraction_uses_isolated_rooted_session() -> None:
    workspace = AsyncMock()
    session = _Session(
        SimpleNamespace(
            success=True,
            stdout="AUTO",
            stderr="",
            error_in_exec=None,
        )
    )
    workspace.get_session.return_value = session
    compiler = FunctionDefinitionCompiler(
        workspace_service=workspace,
        storage_factory=lambda _function_id: AsyncMock(),
    )
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()
    code = (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        "#function_name: execute\n"
        "class Input: pass\n"
        "class Output: pass\n"
    )

    result = await compiler.extract_schemas(
        user_id,
        code,
        "functions/execute.py",
        pod_id,
        function_id,
    )

    assert result == ({"type": "object"}, {"type": "object"}, None)
    call = workspace.get_session.await_args.kwargs
    assert call["initial_cwd"] == "/workspace"
    assert call["close_on_exit"] is True
    assert call["session_id"].startswith("schema-")
    assert call["session_id"] != str(function_id)
    assert session.entered and session.exited
    assert "compile(" in (session.program or "")
    assert "'functions/execute.py'" in (session.program or "")
    assert session.timeout == 60


async def test_schema_extraction_rejects_expression_headers_before_sandbox() -> None:
    workspace = AsyncMock()
    compiler = FunctionDefinitionCompiler(
        workspace_service=workspace,
        storage_factory=lambda _function_id: AsyncMock(),
    )
    code = (
        "#input_type_name: __import__('os').system('id')\n"
        "#output_type_name: Output\n"
        "#function_name: execute\n"
    )

    with pytest.raises(FunctionValidationError, match="identifiers"):
        await compiler.extract_schemas(
            uuid4(), code, "function.py", uuid4(), uuid4()
        )

    workspace.get_session.assert_not_awaited()


async def test_schema_session_is_cleaned_when_execution_fails() -> None:
    workspace = AsyncMock()
    session = _Session(
        SimpleNamespace(
            success=False,
            stdout="",
            stderr="bad schema",
            error_in_exec={"ename": "SyntaxError", "evalue": "bad schema"},
        )
    )
    workspace.get_session.return_value = session
    compiler = FunctionDefinitionCompiler(
        workspace_service=workspace,
        storage_factory=lambda _function_id: AsyncMock(),
    )
    code = (
        "#input_type_name: Input\n"
        "#output_type_name: Output\n"
        "#function_name: execute\n"
    )

    with pytest.raises(FunctionValidationError, match="SyntaxError"):
        await compiler.extract_schemas(
            uuid4(), code, "function.py", uuid4(), uuid4()
        )

    assert session.exited is True
