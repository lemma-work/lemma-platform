from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.application.function_definition_compiler import (
    FunctionDefinitionCompiler,
)
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionEntity,
    FunctionSchemaSet,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.domain.errors import FunctionValidationError


pytestmark = pytest.mark.asyncio


def _function(**overrides) -> FunctionEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "inspect-me",
        "type": FunctionType.API,
        "status": FunctionStatus.DRAFT,
    }
    payload.update(overrides)
    return FunctionEntity(**payload)


async def test_schema_extraction_uses_stateless_function_executor() -> None:
    schema_executor = AsyncMock()
    schemas = FunctionSchemaSet(
        input={"type": "object"},
        output={"type": "object"},
        config=None,
    )
    schema_executor.extract_schemas.return_value = schemas
    compiler = FunctionDefinitionCompiler(
        schema_executor=schema_executor,
        storage_factory=lambda _function_id: AsyncMock(),
    )
    function = _function()
    artifact = FunctionArtifact(revision_hash=f"sha256:{'a' * 64}")

    result = await compiler.extract_schemas(function, artifact)

    assert result == schemas
    schema_executor.extract_schemas.assert_awaited_once_with(
        function_id=function.id,
        pod_id=function.pod_id,
        artifact=artifact,
    )


async def test_schema_extraction_rejects_unpersisted_function() -> None:
    schema_executor = AsyncMock()
    compiler = FunctionDefinitionCompiler(
        schema_executor=schema_executor,
        storage_factory=lambda _function_id: AsyncMock(),
    )
    function = _function(id=None)

    with pytest.raises(FunctionValidationError, match="persisted"):
        await compiler.extract_schemas(
            function,
            FunctionArtifact(revision_hash=f"sha256:{'b' * 64}"),
        )

    schema_executor.extract_schemas.assert_not_awaited()
