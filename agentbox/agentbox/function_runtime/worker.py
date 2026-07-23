from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
from pathlib import Path
import sys
import traceback
from types import ModuleType
from typing import Any

from lemma_sdk import FunctionContext

from .runtime_models import RuntimeFailure, WorkerRequest, WorkerResult


def _load_module(root: Path, source_path: str, attempt_id: str) -> ModuleType:
    source = (root / source_path).resolve()
    if root not in source.parents or not source.is_file():
        raise ValueError("function artifact source path is invalid")
    name = f"_lemma_function_{attempt_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError("function source cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def execute(request: WorkerRequest) -> dict[str, Any]:
    root = Path(request.artifact_root).resolve()
    dependency_path = request.manifest.dependency_path
    if dependency_path is not None:
        dependencies = (root / dependency_path).resolve()
        if root not in dependencies.parents or not dependencies.is_dir():
            raise ValueError("function dependency path is invalid")
        sys.path.insert(0, str(dependencies))
    module = _load_module(root, request.manifest.source_path, str(request.attempt_id))
    input_model = getattr(module, request.manifest.input_model)
    output_model = getattr(module, request.manifest.output_model)
    function = getattr(module, request.manifest.entrypoint)
    config: Any = request.config
    if request.manifest.config_model is not None and config is not None:
        config = getattr(module, request.manifest.config_model)(**config)
    data = input_model(**request.input_data)
    identity = request.identity
    context = FunctionContext(
        pod_id=identity.pod_id,
        function_id=str(identity.function_id),
        user_id=identity.user_id,
        user_email=identity.user_email,
        config=config,
    )
    environment = {
        "LEMMA_TOKEN": request.lemma_token,
        "LEMMA_BASE_URL": request.lemma_base_url,
        "LEMMA_USER_ID": str(identity.user_id),
        "LEMMA_POD_ID": str(identity.pod_id),
    }
    if identity.organization_id is not None:
        environment["LEMMA_ORG_ID"] = str(identity.organization_id)
    if identity.user_email:
        environment["LEMMA_USER_EMAIL"] = identity.user_email
    os.environ.update(environment)
    result = function(context, data)
    if inspect.isawaitable(result):
        result = await result
    if hasattr(result, "model_dump"):
        output = result.model_dump()
    elif isinstance(result, dict):
        output = result
    else:
        output = output_model.model_validate(result).model_dump()
    return output_model(**output).model_dump()


def main() -> None:
    try:
        request = WorkerRequest.model_validate_json(sys.stdin.buffer.read())
        output = asyncio.run(execute(request))
        result = WorkerResult(ok=True, output_data=output)
    except BaseException as exc:
        result = WorkerResult(
            ok=False,
            error=RuntimeFailure(
                name=type(exc).__name__,
                message=str(exc),
                traceback=tuple(traceback.format_exc().splitlines()),
            ),
        )
    result_path = Path(os.environ.pop("LEMMA_FUNCTION_RESULT_PATH"))
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(result.model_dump_json(), encoding="utf-8")
    os.replace(temporary, result_path)


if __name__ == "__main__":
    main()
