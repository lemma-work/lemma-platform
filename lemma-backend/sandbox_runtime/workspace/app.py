from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import logging
import os
from pathlib import Path
import struct
from uuid import UUID

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from sandbox_runtime.protocol import ByteRange, ProcessState
from sandbox_runtime.tasks import create_inherited_task

from .models import (
    OutputChannel,
    RuntimeCreatePythonSessionRequest,
    RuntimeExecutePythonRequest,
    RuntimeHealthResponse,
    RuntimeFileListResponse,
    RuntimeFileStatResponse,
    RuntimeMoveFileRequest,
    RuntimePythonResultResponse,
    RuntimePythonSessionResponse,
    RuntimeProcessListResponse,
    RuntimeProcessResponse,
    RuntimeQuiesceResponse,
    RuntimeResizeRequest,
    RuntimeStartProcessRequest,
    RuntimeTerminateRequest,
)
from .filesystem_manager import (
    FileConflictError,
    FilesystemManager,
    FileTooLargeError,
)
from .browser_guard import shed_browser_if_starved
from .process_manager import ManagedProcess, OutputChunk, ProcessManager
from .python_session_manager import PythonSessionManager
from .quiescer import WorkspaceQuiescer


_CHANNEL_IDS = {
    OutputChannel.STDOUT: 1,
    OutputChannel.STDERR: 2,
    OutputChannel.PTY: 3,
}
_TERMINAL_PROCESS_STATES = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}

# How often to check for processes past their deadline. Coarse on purpose: the
# deadline is an hour-scale leak guard, not a precise command budget.
_REAP_INTERVAL_SECONDS = 60.0


def _load_token(explicit_token: str | None) -> str:
    if explicit_token:
        return explicit_token
    token_file = os.environ.pop("LEMMA_RUNTIME_TOKEN_FILE", None)
    if token_file:
        path = Path(token_file)
        token = path.read_text().strip()
        path.unlink()
        if token:
            return token
    token = os.environ.pop("LEMMA_RUNTIME_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError("workspace runtime token is not configured")


def create_app(
    *,
    token: str | None = None,
    allowed_roots: tuple[str, ...] = ("/workspace", "/tmp"),
    max_file_transfer_bytes: int | None = None,
) -> FastAPI:
    runtime_token = _load_token(token)
    transfer_limit = (
        int(os.getenv("LEMMA_MAX_FILE_TRANSFER_BYTES", str(256 * 1024 * 1024)))
        if max_file_transfer_bytes is None
        else max_file_transfer_bytes
    )
    if transfer_limit < 1:
        raise ValueError("filesystem transfer limit must be positive")
    manager = ProcessManager(allowed_roots=allowed_roots)
    filesystem = FilesystemManager(
        allowed_roots=allowed_roots,
        max_read_bytes=transfer_limit,
        max_write_bytes=transfer_limit,
    )
    python_sessions = PythonSessionManager(allowed_roots=allowed_roots)
    quiescer = WorkspaceQuiescer()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Nothing else enforces a process deadline, so without this sweep a
        # forgotten long-running process keeps the sandbox permanently "busy"
        # and the idle reclaimer never gets to release it.
        async def _reap_forever() -> None:
            while True:
                await asyncio.sleep(_REAP_INTERVAL_SECONDS)
                with suppress(Exception):
                    # One bad sweep must not end the loop; the next tick retries.
                    await manager.reap_expired()
                with suppress(Exception):
                    _shed_browser_under_pressure()

        def _shed_browser_under_pressure() -> None:
            """Take the browser back when the sandbox has nothing left.

            Runs on the same tick as the deadline sweep because it needs no
            timer of its own and, more usefully, because this loop is already
            proven to keep running in a starved sandbox -- it allocates
            nothing. Everything else that bounds the browser needs a healthy
            process to act, which is exactly what is missing here.

            Said out loud, and at warning, on purpose. A sandbox that quietly
            repaired itself would leave whoever reads these logs with the same
            unexplained `exit_code: 124` this was built from.
            """
            outcome = shed_browser_if_starved()
            if outcome is None:
                return
            available_mb, killed = outcome
            logging.getLogger(__name__).warning(
                "workspace runtime shed the browser: %s MB available, "
                "%s processes killed. It will start again on the next capture.",
                available_mb,
                killed,
            )

        reaper = create_inherited_task(_reap_forever(), name="process-deadline-reaper")
        try:
            yield
        finally:
            reaper.cancel()
            # `gather` rather than a bare `await reaper`: the cancellation is
            # expected, so its CancelledError is collected as a result instead
            # of having to be suppressed around an await whose value is unused.
            await asyncio.gather(reaper, return_exceptions=True)

    app = FastAPI(title="Lemma Workspace Runtime", lifespan=lifespan)
    app.state.process_manager = manager
    app.state.filesystem_manager = filesystem
    app.state.python_session_manager = python_sessions

    async def authenticate(
        # FastAPI derives the header name from this parameter, so it is the
        # wire contract: X-Lemma-Runtime-Token.
        x_lemma_runtime_token: str | None = Header(default=None),
    ) -> None:
        provided = (x_lemma_runtime_token or "").strip()
        if not provided or not hmac.compare_digest(provided, runtime_token):
            raise HTTPException(status_code=401, detail="invalid runtime credential")

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(FileNotFoundError)
    async def not_found_handler(
        _request: Request, error: FileNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(FileConflictError)
    async def file_conflict_handler(
        _request: Request, error: FileConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(FileTooLargeError)
    async def file_too_large_handler(
        _request: Request, error: FileTooLargeError
    ) -> JSONResponse:
        return JSONResponse(status_code=413, content={"detail": str(error)})

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: Request, error: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.get("/health", response_model=RuntimeHealthResponse)
    async def health(_auth: None = Depends(authenticate)) -> RuntimeHealthResponse:
        return RuntimeHealthResponse(
            status="ok",
            managed_processes=len(await manager.list()),
            active_python_sessions=await python_sessions.count(),
        )

    @app.post(
        "/processes",
        response_model=RuntimeProcessResponse,
        status_code=201,
        responses={200: {"model": RuntimeProcessResponse}},
    )
    async def start_process(
        request: RuntimeStartProcessRequest,
        _auth: None = Depends(authenticate),
    ) -> Response | RuntimeProcessResponse:
        process, created = await manager.start(request.to_domain())
        response = process.response()
        if created:
            return response
        return JSONResponse(status_code=200, content=jsonable_encoder(response))

    @app.get("/processes", response_model=RuntimeProcessListResponse)
    async def list_processes(
        _auth: None = Depends(authenticate),
    ) -> RuntimeProcessListResponse:
        return RuntimeProcessListResponse(
            processes=tuple(item.response() for item in await manager.list())
        )

    @app.get("/processes/{operation_id}", response_model=RuntimeProcessResponse)
    async def inspect_process(
        operation_id: UUID,
        _auth: None = Depends(authenticate),
    ) -> RuntimeProcessResponse:
        process = await _require_process(manager, operation_id)
        return process.response()

    @app.get("/processes/{operation_id}/output")
    async def read_process_output(
        operation_id: UUID,
        after_seq: int = Query(default=0, ge=0),
        wait_seconds: float = Query(default=0, ge=0, le=30),
        _auth: None = Depends(authenticate),
    ) -> Response:
        process = await _require_process(manager, operation_id)
        snapshot = await process.output.snapshot(after_seq, wait_seconds=wait_seconds)
        return Response(
            content=_encode_chunks(snapshot.chunks),
            media_type="application/vnd.lemma.process-output",
            headers={
                "X-Lemma-Next-Sequence": str(snapshot.next_sequence),
                "X-Lemma-Truncated-Before": (
                    str(snapshot.truncated_before_sequence or "")
                ),
                "X-Lemma-Process-State": process.state.value,
                "X-Lemma-Exit-Code": (
                    str(process.exit_code) if process.exit_code is not None else ""
                ),
            },
        )

    @app.websocket("/processes/{operation_id}/stream")
    async def stream_process_output(websocket: WebSocket, operation_id: UUID) -> None:
        provided = websocket.headers.get("x-lemma-runtime-token", "").strip()
        if not provided or not hmac.compare_digest(provided, runtime_token):
            await websocket.close(code=4401)
            return
        process = await manager.get(operation_id)
        if process is None:
            await websocket.close(code=4404)
            return
        try:
            after_sequence = max(0, int(websocket.query_params.get("after_seq", "0")))
        except ValueError:
            await websocket.close(code=4400)
            return
        await websocket.accept()
        while True:
            snapshot = await process.output.snapshot(after_sequence, wait_seconds=30)
            for chunk in snapshot.chunks:
                await websocket.send_bytes(_encode_chunk(chunk))
                after_sequence = chunk.sequence
            if process.state in _TERMINAL_PROCESS_STATES and not snapshot.chunks:
                await websocket.close(code=1000)
                return

    @app.post("/processes/{operation_id}:input", status_code=204)
    async def send_process_input(
        operation_id: UUID,
        data: bytes = Body(media_type="application/octet-stream"),
        _auth: None = Depends(authenticate),
    ) -> Response:
        process = await _require_process(manager, operation_id)
        await process.send_input(data)
        return Response(status_code=204)

    @app.post("/processes/{operation_id}:resize", status_code=204)
    async def resize_process(
        operation_id: UUID,
        request: RuntimeResizeRequest,
        _auth: None = Depends(authenticate),
    ) -> Response:
        process = await _require_process(manager, operation_id)
        process.resize(request.cols, request.rows)
        return Response(status_code=204)

    @app.delete("/processes/{operation_id}", response_model=RuntimeProcessResponse)
    async def terminate_process(
        operation_id: UUID,
        request: RuntimeTerminateRequest,
        _auth: None = Depends(authenticate),
    ) -> RuntimeProcessResponse:
        process = await _require_process(manager, operation_id)
        await process.terminate(request.grace_seconds)
        await process.wait()
        return process.response()

    @app.post("/quiesce", response_model=RuntimeQuiesceResponse)
    async def quiesce(
        _auth: None = Depends(authenticate),
    ) -> RuntimeQuiesceResponse:
        process_count, session_count = await asyncio.gather(
            manager.quiesce(), python_sessions.quiesce()
        )
        await quiescer.quiesce()
        return RuntimeQuiesceResponse(
            terminated_processes=process_count,
            terminated_python_sessions=session_count,
        )

    @app.put(
        "/python-sessions/{session_id}",
        response_model=RuntimePythonSessionResponse,
        status_code=201,
        responses={200: {"model": RuntimePythonSessionResponse}},
    )
    async def create_python_session(
        session_id: UUID,
        request: RuntimeCreatePythonSessionRequest,
        _auth: None = Depends(authenticate),
    ) -> Response | RuntimePythonSessionResponse:
        session, created = await python_sessions.create(request.to_domain(session_id))
        response = RuntimePythonSessionResponse(
            session_id=session_id,
            cwd=session.request.cwd,
            environment_keys=session.response_environment_keys(),
        )
        if created:
            return response
        return JSONResponse(status_code=200, content=jsonable_encoder(response))

    @app.post(
        "/python-sessions/{session_id}:execute",
        response_model=RuntimePythonResultResponse,
    )
    async def execute_python(
        session_id: UUID,
        request: RuntimeExecutePythonRequest,
        _auth: None = Depends(authenticate),
    ) -> RuntimePythonResultResponse:
        result = await python_sessions.execute(session_id, request.to_domain())
        return RuntimePythonResultResponse.from_domain(result)

    @app.post(
        "/python-sessions/{session_id}:restart",
        response_model=RuntimePythonSessionResponse,
    )
    async def restart_python_session(
        session_id: UUID,
        _auth: None = Depends(authenticate),
    ) -> RuntimePythonSessionResponse:
        session = await python_sessions.restart(session_id)
        return RuntimePythonSessionResponse(
            session_id=session_id,
            cwd=session.request.cwd,
            environment_keys=session.response_environment_keys(),
        )

    @app.delete("/python-sessions/{session_id}", status_code=204)
    async def delete_python_session(
        session_id: UUID,
        _auth: None = Depends(authenticate),
    ) -> Response:
        await python_sessions.delete(session_id)
        return Response(status_code=204)

    @app.get("/files:stat", response_model=RuntimeFileStatResponse)
    async def stat_file(
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        _auth: None = Depends(authenticate),
    ) -> RuntimeFileStatResponse:
        return RuntimeFileStatResponse.from_domain(await filesystem.stat(path))

    @app.put("/directories", status_code=204)
    async def create_directory(
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        _auth: None = Depends(authenticate),
    ) -> Response:
        await filesystem.create_directory(path)
        return Response(status_code=204)

    @app.get("/files", response_model=RuntimeFileListResponse)
    async def list_files(
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        _auth: None = Depends(authenticate),
    ) -> RuntimeFileListResponse:
        return RuntimeFileListResponse(
            entries=tuple(
                RuntimeFileStatResponse.from_domain(item)
                for item in await filesystem.list(path)
            )
        )

    @app.get("/files:content")
    async def read_file(
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        offset: int = Query(default=0, ge=0),
        length: int | None = Query(default=None, ge=0, le=2 * 1024 * 1024 * 1024),
        _auth: None = Depends(authenticate),
    ) -> StreamingResponse:
        stream = await filesystem.open_read(
            path, ByteRange(offset=offset, length=length)
        )
        return StreamingResponse(stream, media_type="application/octet-stream")

    @app.put("/files:content", response_model=RuntimeFileStatResponse)
    async def write_file(
        request: Request,
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        expected_sha256: str | None = Query(
            default=None, pattern=r"^sha256:[0-9a-f]{64}$"
        ),
        _auth: None = Depends(authenticate),
    ) -> RuntimeFileStatResponse:
        stat = await filesystem.write_stream(
            path,
            request.stream(),
            expected_sha256=expected_sha256,
        )
        return RuntimeFileStatResponse.from_domain(stat)

    @app.post("/files:move", status_code=204)
    async def move_file(
        request: RuntimeMoveFileRequest,
        _auth: None = Depends(authenticate),
    ) -> Response:
        await filesystem.move(request.source, request.destination)
        return Response(status_code=204)

    @app.delete("/files", status_code=204)
    async def delete_file(
        path: str = Query(min_length=1, max_length=4096, pattern=r"^/"),
        recursive: bool = Query(default=False),
        _auth: None = Depends(authenticate),
    ) -> Response:
        await filesystem.delete(path, recursive=recursive)
        return Response(status_code=204)

    return app


async def _require_process(
    manager: ProcessManager, operation_id: UUID
) -> ManagedProcess:
    process = await manager.get(operation_id)
    if process is None:
        raise HTTPException(status_code=404, detail="process does not exist")
    return process


def _encode_chunk(chunk: OutputChunk) -> bytes:
    return (
        struct.pack(
            "!QBI", chunk.sequence, _CHANNEL_IDS[chunk.channel], len(chunk.data)
        )
        + chunk.data
    )


def _encode_chunks(chunks: tuple[OutputChunk, ...]) -> bytes:
    return b"".join(_encode_chunk(chunk) for chunk in chunks)
