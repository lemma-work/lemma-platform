from __future__ import annotations

from io import BytesIO
import json
from uuid import uuid4
import zipfile

import pytest

from app.modules.function.application import function_artifact_builder as builder_module
from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
    parse_runtime_header,
)
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
        code=source(),
        python_packages=(),
    )
    second = await builder.build(
        function_id=function_id,
        code=source(),
        python_packages=(),
    )

    assert first.revision_hash == second.revision_hash
    path = f"artifacts/{first.revision_hash.removeprefix('sha256:')}.zip"
    artifact = storage.values[path]
    with zipfile.ZipFile(BytesIO(artifact)) as archive:
        assert set(archive.namelist()) == {"function.py", "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["entrypoint"] == "increment"
        assert manifest["runtime_abi"] == "lemma-function-python-3.14-linux-x86_64-1"
        assert manifest["dependency_lock"] == []
        assert archive.read("function.py").decode() == source()


def test_runtime_header_requires_typed_entrypoint_contract() -> None:
    with pytest.raises(FunctionValidationError, match="output_type_name"):
        parse_runtime_header("#input_type_name: Input\n#function_name: run\n")


@pytest.mark.asyncio
async def test_builder_reports_a_friendly_error_when_uv_is_not_installed(
    monkeypatch,
) -> None:
    """``_run_builder`` only runs when a function declares ``#python_packages``,
    so it needs a package to compile a lockfile for -- the no-packages path
    never calls the subprocess at all."""

    def _missing_executable(*_args, **_kwargs):
        raise FileNotFoundError("uv")

    monkeypatch.setattr(
        builder_module.asyncio, "create_subprocess_exec", _missing_executable
    )
    storage = MemoryStorage()
    builder = FunctionArtifactBuilder(lambda _function_id: storage)

    with pytest.raises(FunctionValidationError, match="not installed"):
        await builder.build(
            function_id=uuid4(),
            code=source(),
            python_packages=("requests",),
        )


@pytest.mark.asyncio
async def test_builder_reports_truncated_stderr_on_a_nonzero_exit(
    monkeypatch,
) -> None:
    class _FailingProcess:
        returncode = 1

        async def communicate(self):
            # Far longer than the 2000-character tail the builder keeps, so
            # only the truncated tail should show up in the raised message.
            return b"", b"o" * 2100 + b"final failure reason"

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FailingProcess()

    monkeypatch.setattr(
        builder_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    storage = MemoryStorage()
    builder = FunctionArtifactBuilder(lambda _function_id: storage)

    with pytest.raises(
        FunctionValidationError, match="could not be resolved"
    ) as excinfo:
        await builder.build(
            function_id=uuid4(),
            code=source(),
            python_packages=("requests",),
        )

    message = str(excinfo.value)
    assert "final failure reason" in message
    assert "o" * 2100 not in message
