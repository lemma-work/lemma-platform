"""Compile a function definition without owning run execution.

This collaborator performs storage, schema extraction, and immutable artifact
construction. It deliberately owns no repository or unit of work, so callers
must invoke it between short database phases.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
    parse_runtime_header,
)
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionEntity,
    FunctionSchemaSet,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.ports import (
    FunctionStorageFactoryPort,
    WorkspaceSessionPort,
)
from app.modules.function.domain.types import JsonObject
from app.modules.workspace.contracts import PythonExecutionResult


class FunctionDefinitionCompiler:
    """Build READY revisions through an isolated workspace Python context."""

    SCHEMA_OUTPUT_MARKER = "__LEMMA_FUNCTION_SCHEMAS__"

    def __init__(
        self,
        *,
        workspace_service: WorkspaceSessionPort,
        storage_factory: FunctionStorageFactoryPort,
    ) -> None:
        self._workspace_service = workspace_service
        self._storage_factory = storage_factory
        self._artifact_builder = FunctionArtifactBuilder(storage_factory)

    async def write_code(self, function_id: UUID, path: str, code: str) -> None:
        await self._storage_factory(function_id).write_file(path, code)

    async def build_artifact(
        self,
        function: FunctionEntity,
        code: str,
        *,
        python_packages: tuple[str, ...],
    ) -> FunctionArtifact:
        if function.id is None:
            raise FunctionValidationError(
                "Function must be persisted before its revision can be built"
            )
        return await self._artifact_builder.build(
            function_id=function.id,
            code=code,
            python_packages=python_packages,
        )

    async def extract_schemas(
        self,
        user_id: UUID,
        code: str,
        code_path: str,
        pod_id: UUID,
        function_id: UUID,
    ) -> tuple[JsonObject, JsonObject, JsonObject | None]:
        """Execute only the supplied source in a fresh, auto-deleted context.

        ``code_path`` is retained as the compiler filename for useful tracebacks;
        no file is read from the sandbox. Therefore the only correct cwd is the
        guaranteed workspace root, not a per-function directory.
        """

        header = parse_runtime_header(code)
        session = await self._workspace_service.get_session(
            user_id=user_id,
            pod_id=pod_id,
            session_id=f"schema-{uuid4()}",
            initial_cwd="/workspace",
            close_on_exit=True,
            workload_type="function",
            workload_id=function_id,
        )
        marker = f"{self.SCHEMA_OUTPUT_MARKER}{uuid4().hex}:"
        schema_program = self._schema_program(
            code=code,
            code_path=code_path,
            marker=marker,
            input_model=header.input_model,
            output_model=header.output_model,
            config_model=header.config_model,
        )

        async with session:
            result = await session.execute_code(
                schema_program,
                timeout=60,
            )
        if not result.success:
            raise FunctionValidationError(
                self._execution_error_message(result),
                details=self._execution_error_details(result),
            )

        schemas = self._extract_payload(result.stdout, marker)
        return schemas.input, schemas.output, schemas.config

    @staticmethod
    def _schema_program(
        *,
        code: str,
        code_path: str,
        marker: str,
        input_model: str,
        output_model: str,
        config_model: str | None,
    ) -> str:
        config_expression = (
            f"_lemma_namespace[{config_model!r}].model_json_schema()"
            if config_model
            else "None"
        )
        return (
            "import json as _lemma_json\n"
            "_lemma_namespace = {}\n"
            f"exec(compile({code!r}, {code_path!r}, 'exec'), "
            "_lemma_namespace, _lemma_namespace)\n"
            "_lemma_schemas = {\n"
            f"    'input': _lemma_namespace[{input_model!r}].model_json_schema(),\n"
            f"    'output': _lemma_namespace[{output_model!r}].model_json_schema(),\n"
            f"    'config': {config_expression},\n"
            "}\n"
            f"print({marker!r} + _lemma_json.dumps(_lemma_schemas, "
            "sort_keys=True, separators=(',', ':')))\n"
        )

    @staticmethod
    def _extract_payload(stdout: str | None, marker: str) -> FunctionSchemaSet:
        if stdout:
            for line in reversed(stdout.splitlines()):
                if not line.startswith(marker):
                    continue
                try:
                    return FunctionSchemaSet.model_validate_json(
                        line[len(marker) :]
                    )
                except ValueError as exc:
                    raise FunctionValidationError(
                        "Function code emitted invalid JSON schema output.",
                        details={"stage": "schema_extraction"},
                    ) from exc
        raise FunctionValidationError(
            "Function code ran but did not emit schema output.",
            details={"stage": "schema_extraction"},
        )

    @staticmethod
    def _execution_error_message(result: PythonExecutionResult) -> str:
        error = result.error_in_exec
        if error:
            name = str(error.get("ename") or "").strip()
            value = str(error.get("evalue") or "").strip()
            if name and value:
                return f"Function schema extraction failed: {name}: {value}"
            if value:
                return f"Function schema extraction failed: {value}"
        if result.stderr and result.stderr.strip():
            return (
                "Function schema extraction failed: "
                f"{result.stderr.strip().splitlines()[0]}"
            )
        return "Function schema extraction failed."

    @staticmethod
    def _execution_error_details(
        result: PythonExecutionResult,
    ) -> JsonObject:
        details: JsonObject = {"stage": "schema_extraction"}
        if result.stdout:
            details["stdout"] = result.stdout
        if result.stderr:
            details["stderr"] = result.stderr
        if result.error_in_exec:
            details["execution_error"] = result.error_in_exec
        return details
