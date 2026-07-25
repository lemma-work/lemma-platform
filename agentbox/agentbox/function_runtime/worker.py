from __future__ import annotations

import asyncio
import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import inspect
import io
import os
from pathlib import Path
import sys
import traceback
from types import ModuleType
from typing import Any, Iterator

from lemma_sdk import FunctionContext
from lemma_sdk.runtime import FunctionInvocationBinding, function_invocation_scope

from .runtime_models import (
    FunctionArtifactManifest,
    RuntimeFailure,
    WorkerReady,
    WorkerRequest,
    WorkerResponse,
)
from .types import JsonObject


_MAX_LOG_BYTES = 4 * 1024 * 1024
_MISSING_ENV = object()


class _BoundedTextBuffer(io.TextIOBase):
    def __init__(self, limit_bytes: int = _MAX_LOG_BYTES) -> None:
        self._limit_bytes = limit_bytes
        self._data = bytearray()
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        encoded = value.encode(errors="replace")
        remaining = self._limit_bytes - len(self._data)
        if remaining > 0:
            self._data.extend(encoded[:remaining])
        if len(encoded) > remaining:
            self.truncated = True
        return len(value)

    def value(self) -> str:
        return self._data.decode(errors="replace")


class LoadedRevision:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.manifest = FunctionArtifactManifest.model_validate_json(
            (self.root / "manifest.json").read_bytes()
        )
        dependency_path = self.manifest.dependency_path
        if dependency_path is not None:
            dependencies = (self.root / dependency_path).resolve()
            if self.root not in dependencies.parents or not dependencies.is_dir():
                raise ValueError("function dependency path is invalid")
            sys.path.insert(0, str(dependencies))
        self.module = _load_module(
            self.root,
            self.manifest.source_path,
            self.root.name,
        )
        self.input_model = getattr(self.module, self.manifest.input_model)
        self.output_model = getattr(self.module, self.manifest.output_model)
        self.function = getattr(self.module, self.manifest.entrypoint)
        self.config_model = (
            getattr(self.module, self.manifest.config_model)
            if self.manifest.config_model is not None
            else None
        )


@contextmanager
def _invocation_environment(request: WorkerRequest) -> Iterator[None]:
    """Expose the legacy function environment for one isolated worker request.

    Revision workers execute one request at a time. Temporarily setting these
    process variables therefore preserves the original function contract
    without sharing one user's delegated token with another concurrent run.
    The SDK's ContextVar binding remains the primary path for ``Pod.from_env()``
    and ``ctx.pod``; this scope also supports existing functions that read the
    documented variables directly or start a thread without copied context.
    """

    identity = request.identity
    values: dict[str, str | None] = {
        "LEMMA_TOKEN": request.lemma_token,
        "LEMMA_BASE_URL": request.lemma_base_url,
        "LEMMA_USER_ID": str(identity.user_id),
        "LEMMA_POD_ID": str(identity.pod_id),
        "LEMMA_ORG_ID": (
            str(identity.organization_id)
            if identity.organization_id is not None
            else None
        ),
        "LEMMA_USER_EMAIL": identity.user_email,
    }
    previous: dict[str, str | object] = {
        key: os.environ.get(key, _MISSING_ENV) for key in values
    }
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is _MISSING_ENV:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)


def _load_module(root: Path, source_path: str, revision_key: str) -> ModuleType:
    source = (root / source_path).resolve()
    if root not in source.parents or not source.is_file():
        raise ValueError("function artifact source path is invalid")
    name = f"_lemma_function_{revision_key.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError("function source cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def execute_loaded(
    request: WorkerRequest,
    revision: LoadedRevision,
) -> JsonObject:
    root = Path(request.artifact_root).resolve()
    if root != revision.root or request.manifest != revision.manifest:
        raise ValueError("worker request does not match its loaded revision")
    identity = request.identity
    binding = FunctionInvocationBinding(
        base_url=request.lemma_base_url,
        token=request.lemma_token,
        pod_id=identity.pod_id,
        organization_id=identity.organization_id,
        run_id=request.run_id,
    )
    with _invocation_environment(request), function_invocation_scope(binding):
        config: Any = request.config
        if revision.config_model is not None and config is not None:
            config = revision.config_model(**config)
        data = revision.input_model(**request.input_data)
        context = FunctionContext(
            pod_id=identity.pod_id,
            function_id=str(identity.function_id),
            user_id=identity.user_id,
            user_email=identity.user_email,
            config=config,
        )
        result = revision.function(context, data)
        if inspect.isawaitable(result):
            result = await result
        if hasattr(result, "model_dump"):
            output = result.model_dump()
        elif isinstance(result, dict):
            output = result
        else:
            output = revision.output_model.model_validate(result).model_dump()
        return revision.output_model(**output).model_dump()


def _failure(exc: BaseException) -> RuntimeFailure:
    return RuntimeFailure(
        name=type(exc).__name__,
        message=str(exc),
        traceback=tuple(traceback.format_exc().splitlines()),
    )


async def _serve_request(
    request: WorkerRequest,
    revision: LoadedRevision,
) -> WorkerResponse:
    stdout = _BoundedTextBuffer()
    stderr = _BoundedTextBuffer()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            output = await execute_loaded(request, revision)
        return WorkerResponse(
            ok=True,
            output_data=output,
            stdout=stdout.value(),
            stderr=stderr.value(),
            output_truncated=stdout.truncated or stderr.truncated,
        )
    except BaseException as exc:
        return WorkerResponse(
            ok=False,
            error=_failure(exc),
            stdout=stdout.value(),
            stderr=stderr.value(),
            output_truncated=stdout.truncated or stderr.truncated,
        )


def _write_protocol(stream, model) -> None:
    stream.write(model.model_dump_json().encode() + b"\n")
    stream.flush()


def serve(root: Path) -> int:
    protocol_out = sys.stdout.buffer
    # User/module output must never corrupt the stdout framing channel.
    sys.stdout = sys.stderr
    try:
        revision = LoadedRevision(root)
    except BaseException as exc:
        _write_protocol(protocol_out, WorkerReady(ready=False, error=_failure(exc)))
        return 1
    _write_protocol(protocol_out, WorkerReady(ready=True))
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            request = WorkerRequest.model_validate_json(line)
            response = asyncio.run(_serve_request(request, revision))
        except BaseException as exc:
            response = WorkerResponse(ok=False, error=_failure(exc))
        _write_protocol(protocol_out, response)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    if not args.serve:
        parser.error("--serve is required")
    if args.artifact_root is None:
        parser.error("--artifact-root is required with --serve")
    raise SystemExit(serve(args.artifact_root))


if __name__ == "__main__":
    main()
