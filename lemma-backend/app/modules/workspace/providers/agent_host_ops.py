"""Operations inside a host sandbox: the ``SandboxOpsProvider`` half.

Each method is one ``op`` of docs/architecture/desktop-host-execution.md §4 --
or, for file bodies, a run of them. No frame carries more than
``OP_MAX_DATA_BYTES`` of data, so a read is a sequence of ranged ``file.read``s
and a write a sequence of ``file.write`` chunks under one ``upload_id``, the
last one ``final`` and carrying the digest. The host writes the chunks to a
temporary sibling and renames on ``final``, so nobody sees half a file.

Paths are the host's own absolute paths; nothing here rewrites them, and
containment is the exec-server's check (and Seatbelt's underneath it).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from sandbox_runtime.errors import SandboxCapabilityUnsupported, SandboxRejected
from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.providers.base import (
    ProcessDescriptor,
    ProviderCapability,
    ProviderInstance,
    SandboxEndpoint,
)

#: No single op carries more than this much data before base64. Mirrors
#: ``OP_MAX_DATA_BYTES`` on the link.
OP_MAX_DATA_BYTES = 1024 * 1024

#: The longest one ``process.read`` may wait on the host.
_MAX_WAIT_MS = 30_000

#: What an agent is told when it reaches for a persistent Python session.
PYTHON_ON_HOST_SENTENCE = (
    "Persistent Python sessions are not available when commands run on this "
    "Mac: there is no Python runtime Lemma can rely on here. Run `python3` "
    "through `exec_command` instead, writing the script to a file if it is "
    "longer than a line."
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: object) -> bytes:
    if not isinstance(value, str):
        return b""
    return base64.b64decode(value)


def _time(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def file_stat(raw: object) -> FileStat:
    """A ``FileStat`` from the host's JSON; refuses a shape it cannot read."""
    if not isinstance(raw, dict):
        raise SandboxRejected("the Mac answered a file operation without a stat")
    try:
        kind = FileKind(str(raw.get("kind")))
    except ValueError:
        raise SandboxRejected(f"unknown file kind {raw.get('kind')!r}") from None
    sha = raw.get("sha256")
    return FileStat(
        path=str(raw.get("path") or ""),
        kind=kind,
        size_bytes=_int(raw.get("size_bytes")),
        modified_at=_time(raw.get("modified_at")),
        mode=_int(raw.get("mode")),
        sha256=sha if isinstance(sha, str) and sha else None,
    )


def process_state(state: object, exit_code: int | None) -> ProcessState:
    """The host's three states in the runtime's vocabulary."""
    if state == "running":
        return ProcessState.RUNNING
    if state == "killed":
        return ProcessState.CANCELLED
    if state == "exited":
        return ProcessState.SUCCEEDED if exit_code == 0 else ProcessState.FAILED
    return ProcessState.UNKNOWN


def _exit_code(raw: dict[str, object]) -> int | None:
    value = raw.get("exit_code")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def rechunk(data: AsyncIterable[bytes], size: int) -> AsyncIterator[bytes]:
    """Re-slice a stream into pieces of at most ``size`` bytes."""
    buffer = bytearray()
    async for piece in data:
        buffer.extend(piece)
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


class AgentHostOpsMixin:
    """Mixed into ``AgentHostSandboxProvider``, which supplies ``_instance_op``."""

    # Deliberately no PORT_REACH: a port on the Mac is the Mac's, and the VM
    # reaches it through the loopback relay, not through the sandbox seam.
    capabilities = frozenset({ProviderCapability.SECRET_DELIVERY})

    async def _instance_op(
        self,
        instance: ProviderInstance,
        method: str,
        params: dict[str, object],
        *,
        deadline_at: datetime,
    ) -> dict[str, object]:
        raise NotImplementedError

    # --------------------------------------------------------- processes

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        params: dict[str, object] = {
            "operation_id": str(request.operation_id),
            "cwd": request.cwd,
            "environment": [
                {"name": item.name, "value": item.value} for item in request.environment
            ],
            "tty": (
                {"rows": request.tty.rows, "cols": request.tty.cols}
                if request.tty is not None
                else None
            ),
            "output_limit_bytes": request.output_limit_bytes,
            "initial_input": (
                _b64(request.initial_input)
                if request.initial_input is not None
                else None
            ),
        }
        if request.argv is not None:
            params["argv"] = list(request.argv)
        else:
            params["shell_command"] = request.shell_command
        result = await self._instance_op(
            instance, "process.start", params, deadline_at=deadline_at
        )
        process_id = result.get("process_id")
        return str(process_id) if process_id else str(request.operation_id)

    async def read_process_output(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        result = await self._instance_op(
            instance,
            "process.read",
            {
                "process_id": process_id,
                "after_sequence": after_sequence,
                # The exec-server waits at most 30 s per read.
                "wait_ms": min(_MAX_WAIT_MS, max(0, int(wait_seconds * 1000))),
            },
            deadline_at=deadline_at,
        )
        chunks = []
        for raw in result.get("chunks") or []:
            if not isinstance(raw, dict):
                continue
            try:
                channel = ProcessOutputChannel(str(raw.get("stream")))
            except ValueError:
                channel = ProcessOutputChannel.STDOUT
            chunks.append(
                ProcessOutputChunk(
                    sequence=_int(raw.get("sequence")),
                    channel=channel,
                    data=_unb64(raw.get("data")),
                )
            )
        exit_code = _exit_code(result)
        truncated = result.get("truncated_before_sequence")
        return ProcessOutputSnapshot(
            chunks=tuple(chunks),
            next_sequence=_int(result.get("next_sequence"), after_sequence),
            truncated_before_sequence=(
                truncated if isinstance(truncated, int) and truncated else None
            ),
            state=process_state(result.get("state"), exit_code),
            exit_code=exit_code,
        )

    async def send_process_input(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        for offset in range(0, max(len(data), 1), OP_MAX_DATA_BYTES):
            await self._instance_op(
                instance,
                "process.input",
                {
                    "process_id": process_id,
                    "data": _b64(data[offset : offset + OP_MAX_DATA_BYTES]),
                },
                deadline_at=deadline_at,
            )

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        await self._instance_op(
            instance,
            "process.resize",
            {"process_id": process_id, "rows": size.rows, "cols": size.cols},
            deadline_at=deadline_at,
        )

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        await self._instance_op(
            instance,
            "process.terminate",
            {"process_id": process_id, "grace_ms": int(grace_seconds * 1000)},
            deadline_at=deadline_at,
        )

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        result = await self._instance_op(
            instance, "process.list", {}, deadline_at=deadline_at
        )
        descriptors = []
        for raw in result.get("processes") or []:
            if not isinstance(raw, dict) or not raw.get("process_id"):
                continue
            exit_code = _exit_code(raw)
            started = raw.get("started_at")
            descriptors.append(
                ProcessDescriptor(
                    process_id=str(raw["process_id"]),
                    state=process_state(raw.get("state"), exit_code),
                    exit_code=exit_code,
                    started_at=_time(started) if started else None,
                    command=str(raw.get("command") or ""),
                )
            )
        return tuple(descriptors)

    # ------------------------------------------------------------- files

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        result = await self._instance_op(
            instance, "file.stat", {"path": path}, deadline_at=deadline_at
        )
        return file_stat(result)

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        result = await self._instance_op(
            instance, "file.list", {"path": path}, deadline_at=deadline_at
        )
        return tuple(file_stat(entry) for entry in result.get("entries") or [])

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        await self._instance_op(
            instance, "file.mkdir", {"path": path}, deadline_at=deadline_at
        )

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        offset = byte_range.offset
        remaining = byte_range.length
        while remaining is None or remaining > 0:
            length = (
                OP_MAX_DATA_BYTES
                if remaining is None
                else min(remaining, OP_MAX_DATA_BYTES)
            )
            result = await self._instance_op(
                instance,
                "file.read",
                {"path": path, "offset": offset, "length": length},
                deadline_at=deadline_at,
            )
            data = _unb64(result.get("data"))
            if data:
                yield data
            offset += len(data)
            if remaining is not None:
                remaining -= len(data)
            # A short read without `eof` would loop for ever on a host that
            # misbehaves; an empty one ends the stream either way.
            if result.get("eof") is True or not data:
                return

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        """Chunked, and final only on the last chunk, so ``final`` is known.

        One chunk is held back so that the one sent with ``final`` really is
        the last; an empty file is a single empty ``final`` chunk.
        """
        upload_id = str(uuid4())
        offset = 0
        pending: bytes | None = None
        async for chunk in rechunk(data, OP_MAX_DATA_BYTES):
            if pending is not None:
                await self._write_chunk(
                    instance,
                    path=path,
                    upload_id=upload_id,
                    offset=offset,
                    data=pending,
                    deadline_at=deadline_at,
                )
                offset += len(pending)
            pending = chunk
        result = await self._write_chunk(
            instance,
            path=path,
            upload_id=upload_id,
            offset=offset,
            data=pending or b"",
            deadline_at=deadline_at,
            final=True,
            expected_sha256=expected_sha256,
        )
        return file_stat(result)

    async def _write_chunk(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        upload_id: str,
        offset: int,
        data: bytes,
        deadline_at: datetime,
        final: bool = False,
        expected_sha256: str | None = None,
    ) -> dict[str, object]:
        params: dict[str, object] = {
            "path": path,
            "upload_id": upload_id,
            "offset": offset,
            "data": _b64(data),
            "final": final,
        }
        if final:
            params["expected_sha256"] = expected_sha256
        return await self._instance_op(
            instance, "file.write", params, deadline_at=deadline_at
        )

    async def move_file(
        self,
        instance: ProviderInstance,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        await self._instance_op(
            instance,
            "file.move",
            {"source": source, "destination": destination},
            deadline_at=deadline_at,
        )

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        result = await self._instance_op(
            instance,
            "file.delete",
            {"path": path, "recursive": recursive},
            deadline_at=deadline_at,
        )
        return bool(result.get("existed"))

    # ------------------------------------------------------ not offered

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        del instance, request
        raise _python_unsupported()

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        del instance, session, request
        raise _python_unsupported()

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        """Nothing was ever created, so there is nothing to delete."""
        del instance, session_id, deadline_at

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        del instance, port, deadline_at
        raise SandboxCapabilityUnsupported(
            str(ProviderCapability.PORT_REACH), kind="Host"
        )

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        """Written 0600 under a 0700 parent by the host, never through a shell."""
        await self._instance_op(
            instance,
            "secret.deliver",
            {"path": path, "data": _b64(value)},
            deadline_at=deadline_at,
        )


def _python_unsupported() -> SandboxCapabilityUnsupported:
    error = SandboxCapabilityUnsupported("python_sessions", kind="Host")
    error.args = (PYTHON_ON_HOST_SENTENCE,)
    return error
