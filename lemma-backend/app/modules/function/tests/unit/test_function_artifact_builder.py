from __future__ import annotations

from io import BytesIO
import json
from uuid import uuid4
import zipfile

import pytest

from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
    parse_runtime_header,
)
from app.modules.function.domain.entities import FunctionRevisionStatus
from app.modules.function.domain.errors import FunctionValidationError


class MemoryStorage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def write_file(self, path: str, content: bytes | str):
        self.values[path] = content.encode() if isinstance(content, str) else content


def source(name: str = "increment") -> str:
    return f"""#input_type_name: Input
#output_type_name: Output
#function_name: {name}

from pydantic import BaseModel

class Input(BaseModel):
    value: int

class Output(BaseModel):
    value: int

async def {name}(ctx, data: Input) -> Output:
    return Output(value=data.value + 1)
"""


@pytest.mark.asyncio
async def test_builder_writes_deterministic_typed_artifact_before_ready() -> None:
    storage = MemoryStorage()
    builder = FunctionArtifactBuilder(lambda _function_id: storage)
    function_id = uuid4()

    first = await builder.build(
        function_id=function_id,
        revision_number=1,
        code=source(),
        python_packages=(),
    )
    second = await builder.build(
        function_id=function_id,
        revision_number=2,
        code=source(),
        python_packages=(),
    )

    assert first.status == FunctionRevisionStatus.READY
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact_path == second.artifact_path
    artifact = storage.values[first.artifact_path]
    with zipfile.ZipFile(BytesIO(artifact)) as archive:
        assert set(archive.namelist()) == {"function.py", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["entrypoint"] == "increment"
        assert manifest["runtime_abi"] == "lemma-function-python-1"
        assert manifest["dependency_lock"] == []
        assert archive.read("function.py").decode() == source()


def test_runtime_header_requires_typed_entrypoint_contract() -> None:
    with pytest.raises(FunctionValidationError, match="output_type_name"):
        parse_runtime_header("#input_type_name: Input\n#function_name: run\n")
