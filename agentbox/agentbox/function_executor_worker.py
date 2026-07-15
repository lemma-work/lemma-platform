from __future__ import annotations

import asyncio
import contextlib
import ctypes
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any


_DEFAULT_RESULT_LIMIT_BYTES = 2 * 1024 * 1024
_MIN_RESULT_LIMIT_BYTES = 1024
_MAX_RESULT_LIMIT_BYTES = 16 * 1024 * 1024
_ENCODE_CHUNK_CHARACTERS = 16 * 1024


class _ResultSizeExceeded(Exception):
    pass


def _mark_non_dumpable() -> None:
    """Prevent same-UID processes from inspecting invocation credentials."""

    if not sys.platform.startswith("linux"):
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        errno = ctypes.get_errno()
        raise OSError(errno, "prctl(PR_SET_DUMPABLE) failed")


def _load_module(cache_dir: Path, code_hash: str) -> ModuleType:
    source_path = cache_dir / "function.py"
    module_name = f"_lemma_function_executor_{code_hash}_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load function source at {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _patched_environ(values: dict[str, str]):
    original = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _context(payload: dict[str, Any], metadata, verified, token: str):
    from agentbox.function_executor import FunctionExecutionContext

    request = payload["request"]
    return FunctionExecutionContext(
        run_id=request["run_id"],
        function_id=metadata.id,
        function_name=metadata.name,
        pod_id=metadata.pod_id,
        organization_id=verified.organization_id,
        user_id=verified.user_id,
        user_email=verified.email,
        lemma_token=token,
        lemma_base_url=payload["lemma_base_url"],
        config=metadata.config,
        workspace_root=payload["workspace_root"],
    )


async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    from agentbox.function_executor import (
        FunctionExecuteRequest,
        FunctionMetadata,
        VerifiedToken,
    )

    cache_dir = Path(payload["cache_dir"])
    dependency_dir = payload.get("dependency_dir")
    if dependency_dir:
        sys.path.insert(0, dependency_dir)
    manifest = payload["manifest"]
    runtime = manifest["runtime"]
    metadata = FunctionMetadata.model_validate(payload["metadata"])
    verified = VerifiedToken.model_validate(payload["verified"])
    request = FunctionExecuteRequest.model_validate(payload["request"])
    token = str(payload["token"])
    module = _load_module(cache_dir, manifest["code_hash"])
    input_model = getattr(module, runtime["input_model"])
    output_model = getattr(module, runtime["output_model"])
    function = getattr(module, runtime["function_name"])
    config_model = (
        getattr(module, runtime["config_model"])
        if runtime.get("config_model")
        else None
    )
    if config_model is not None and metadata.config is not None:
        metadata.config = config_model(**metadata.config)
    data = input_model(**request.input_data)
    ctx = _context(payload, metadata, verified, token)
    invocation_env = {
        "LEMMA_TOKEN": token,
        "LEMMA_BASE_URL": payload["lemma_base_url"],
        "LEMMA_USER_ID": str(verified.user_id),
        "LEMMA_POD_ID": str(metadata.pod_id),
    }
    if verified.organization_id is not None:
        invocation_env["LEMMA_ORG_ID"] = str(verified.organization_id)
    if verified.email:
        invocation_env["LEMMA_USER_EMAIL"] = verified.email
    with _patched_environ(invocation_env):
        result = function(ctx, data)
        if inspect.isawaitable(result):
            result = await result
    if hasattr(result, "model_dump"):
        output = result.model_dump()
    elif isinstance(result, dict):
        output = result
    else:
        output = output_model.model_validate(result).model_dump()
    output_model(**output)
    return {"output_data": output}


def _schemas(payload: dict[str, Any]) -> dict[str, Any]:
    cache_dir = Path(payload["cache_dir"])
    dependency_dir = payload.get("dependency_dir")
    if dependency_dir:
        sys.path.insert(0, dependency_dir)
    manifest = payload["manifest"]
    runtime = manifest["runtime"]
    module = _load_module(cache_dir, manifest["code_hash"])
    input_model = getattr(module, runtime["input_model"])
    output_model = getattr(module, runtime["output_model"])
    config_model = (
        getattr(module, runtime["config_model"])
        if runtime.get("config_model")
        else None
    )
    return {
        "input_schema": input_model.model_json_schema(),
        "output_schema": output_model.model_json_schema(),
        "config_schema": config_model.model_json_schema() if config_model else None,
    }


def _result_limit() -> int:
    raw = os.environ.pop(
        "LEMMA_FUNCTION_MAX_RESULT_BYTES", str(_DEFAULT_RESULT_LIMIT_BYTES)
    )
    limit = int(raw)
    if not _MIN_RESULT_LIMIT_BYTES <= limit <= _MAX_RESULT_LIMIT_BYTES:
        raise ValueError("Invalid function result-channel byte limit")
    return limit


def _encode_result_limited(result: dict[str, Any], limit: int):
    spool = tempfile.SpooledTemporaryFile(max_size=min(limit, 64 * 1024))
    size = 0
    encoder = json.JSONEncoder(separators=(",", ":"), default=str)
    try:
        for piece in encoder.iterencode(result):
            for offset in range(0, len(piece), _ENCODE_CHUNK_CHARACTERS):
                encoded = piece[offset : offset + _ENCODE_CHUNK_CHARACTERS].encode(
                    "utf-8"
                )
                size += len(encoded)
                if size > limit:
                    raise _ResultSizeExceeded
                spool.write(encoded)
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def _write_result(result_fd: int, result: dict[str, Any], limit: int) -> None:
    try:
        spool = _encode_result_limited(result, limit)
    except _ResultSizeExceeded:
        result = {
            "ok": False,
            "error": {
                "name": "ResultPayloadTooLargeError",
                "message": f"Function result exceeded the {limit}-byte limit",
                "traceback": [],
            },
        }
        spool = _encode_result_limited(result, limit)
    try:
        while chunk := spool.read(65536):
            view = memoryview(chunk)
            while view:
                written = os.write(result_fd, view)
                view = view[written:]
    finally:
        spool.close()


def main() -> None:
    result_fd = int(os.environ.pop("LEMMA_FUNCTION_RESULT_FD"))
    result_limit = _result_limit()
    os.set_inheritable(result_fd, False)
    try:
        # This must happen before stdin is read: stdin carries the delegated token.
        _mark_non_dumpable()
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("mode") == "execute":
            value = asyncio.run(_execute(payload))
        elif payload.get("mode") == "schemas":
            value = _schemas(payload)
        else:
            raise ValueError("Unsupported function worker operation")
        result = {"ok": True, **value}
    except BaseException as exc:
        result = {
            "ok": False,
            "error": {
                "name": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            },
        }
    try:
        _write_result(result_fd, result, result_limit)
    finally:
        os.close(result_fd)


if __name__ == "__main__":
    main()
