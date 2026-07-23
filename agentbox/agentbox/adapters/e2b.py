from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import posixpath
import shlex
from typing import Any, Protocol
from uuid import UUID

import httpx
from e2b import (
    ALL_TRAFFIC,
    AuthenticationException,
    FileNotFoundException,
    InvalidArgumentException,
    RateLimitException,
    SandboxNotFoundException,
    TemplateException,
    TimeoutException,
)
from e2b.sandbox.commands.command_handle import CommandExitException, PtySize
from e2b.sandbox.filesystem.filesystem import EntryInfo, FileType
from e2b.sandbox.sandbox_api import SandboxQuery
from e2b_code_interpreter import AsyncSandbox, Context

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    PortProtocol,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessRef,
    ProcessState,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    SandboxProfileRef,
    StorageKind,
    TerminalSize,
    WorkloadKind,
)
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRejected,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderInventoryAllocation,
    ProviderLifecycleError,
    ProviderNotReady,
    ProviderMetadataEntry,
    ProviderPortTarget,
    ProviderProcessStartAmbiguous,
    ProviderProcessStartRejected,
    ProviderProcessStartRequest,
    ProviderProcessStartResult,
    ProviderPythonExecutionAmbiguous,
    ProviderPythonExecutionRejected,
    ProviderPythonSessionCreateAmbiguous,
    ProviderPythonSessionCreateRejected,
    ProviderPythonSessionCreateResult,
    ProviderRateLimited,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.profiles import ProfileRegistry


_PROCESS_ROOT = "/tmp/.agentbox/processes"
_OPERATION_ENV = "AGENTBOX_OPERATION_ID"


class E2BSandboxType(Protocol):
    sandbox_id: str
    traffic_access_token: str | None
    commands: Any
    pty: Any
    files: Any

    async def get_info(self, **kwargs: Any) -> Any: ...

    async def is_running(self, request_timeout: float | None = None) -> bool: ...

    async def set_timeout(self, timeout: int, **kwargs: Any) -> None: ...

    async def pause(self, keep_memory: bool = True, **kwargs: Any) -> bool: ...

    async def kill(self, **kwargs: Any) -> bool: ...

    async def create_code_context(self, **kwargs: Any) -> Any: ...

    async def list_code_contexts(self) -> list[Any]: ...

    async def restart_code_context(self, context: Any) -> None: ...

    async def remove_code_context(self, context: Any) -> None: ...

    async def run_code(self, code: str, **kwargs: Any) -> Any: ...

    def get_host(self, port: int) -> str: ...


@dataclass(frozen=True, slots=True)
class E2BAdapterConfig:
    api_key: str
    scope: str
    request_timeout_seconds: float = 20
    workspace_timeout_seconds: int = 300
    function_timeout_seconds: int = 300
    function_idle_grace_seconds: int = 300
    rate_limit_retry_after_ms: int = 5_000
    workspace_allow_internet_access: bool = True
    function_allow_out: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("E2B API key is required")
        if not self.scope:
            raise ValueError("E2B provider scope is required")
        if not 1 <= self.request_timeout_seconds <= 120:
            raise ValueError("E2B request timeout must be in 1..120 seconds")
        if self.workspace_timeout_seconds < 60 or self.function_timeout_seconds < 60:
            raise ValueError("E2B sandbox timeouts must be at least 60 seconds")
        if self.rate_limit_retry_after_ms < 1:
            raise ValueError("E2B rate limit retry delay must be positive")
        for destination in self.function_allow_out:
            if not destination or "://" in destination:
                raise ValueError(
                    "E2B function egress entries must be hostnames, IPs, or CIDRs"
                )
            if "/" in destination:
                try:
                    ipaddress.ip_network(destination, strict=False)
                except ValueError as exc:
                    raise ValueError(
                        "E2B function egress CIDR is invalid"
                    ) from exc


@dataclass(slots=True)
class _ProcessBuffer:
    output_limit_bytes: int
    chunks: deque[ProcessOutputChunk] = field(default_factory=deque)
    total_bytes: int = 0
    next_sequence: int = 1
    truncated_before_sequence: int | None = None
    state: ProcessState = ProcessState.RUNNING
    exit_code: int | None = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    handle: Any | None = None
    watcher: asyncio.Task[None] | None = None
    deadline_task: asyncio.Task[None] | None = None

    async def append(self, channel: ProcessOutputChannel, data: str | bytes) -> None:
        encoded = data.encode(errors="replace") if isinstance(data, str) else data
        if not encoded:
            return
        async with self.condition:
            chunk = ProcessOutputChunk(
                sequence=self.next_sequence,
                channel=channel,
                data=encoded,
            )
            self.next_sequence += 1
            self.chunks.append(chunk)
            self.total_bytes += len(encoded)
            while self.chunks and self.total_bytes > self.output_limit_bytes:
                removed = self.chunks.popleft()
                self.total_bytes -= len(removed.data)
                self.truncated_before_sequence = removed.sequence + 1
            self.condition.notify_all()

    async def complete(self, state: ProcessState, exit_code: int | None) -> None:
        async with self.condition:
            self.state = state
            self.exit_code = exit_code
            self.condition.notify_all()


class E2BSandboxAdapter:
    """E2B adapter built around exact sandbox IDs and native data-plane APIs."""

    name = "e2b"
    workspace_storage_kind = StorageKind.SANDBOX_NATIVE

    def __init__(
        self,
        profiles: ProfileRegistry,
        config: E2BAdapterConfig,
        *,
        sandbox_class: type[Any] = AsyncSandbox,
    ) -> None:
        self._profiles = profiles
        self._config = config
        self._sandbox_class = sandbox_class
        self.scope = config.scope
        self._sandboxes: dict[str, E2BSandboxType] = {}
        self._processes: dict[tuple[str, str], _ProcessBuffer] = {}
        self._process_lock = asyncio.Lock()

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        artifact = self._profiles.e2b_artifact(
            request.profile, workload_kind=request.key.workload_kind
        )
        workspace = request.key.workload_kind == WorkloadKind.WORKSPACE
        metadata = {entry.name: entry.value for entry in request.metadata}
        timeout = (
            self._config.workspace_timeout_seconds
            if workspace
            else self._config.function_timeout_seconds
        )
        lifecycle = (
            {"on_timeout": "pause", "auto_resume": True}
            if workspace
            else {"on_timeout": "kill", "auto_resume": False}
        )
        function_allow_out = list(self._config.function_allow_out)
        network: dict[str, object] = {"allow_public_traffic": False}
        if not workspace and function_allow_out:
            network["allow_out"] = function_allow_out
            # E2B requires an explicit default-deny rule whenever allow_out is
            # present. ALL_TRAFFIC is the SDK's 0.0.0.0/0 selector; the string
            # "ALL_TRAFFIC" itself is not a valid API CIDR.
            network["deny_out"] = [ALL_TRAFFIC]
        try:
            sandbox = await self._sandbox_class.create(
                template=artifact.immutable_reference,
                timeout=timeout,
                metadata=metadata,
                secure=True,
                allow_internet_access=(
                    self._config.workspace_allow_internet_access
                    if workspace
                    else bool(function_allow_out)
                ),
                network=network,
                lifecycle=lifecycle,
                api_key=self._config.api_key,
                request_timeout=self._request_timeout(request.deadline_at),
            )
        except RateLimitException as exc:
            raise ProviderRateLimited(
                str(exc), retry_after_ms=self._config.rate_limit_retry_after_ms
            ) from exc
        except (
            AuthenticationException,
            InvalidArgumentException,
            TemplateException,
        ) as exc:
            raise ProviderCreateRejected(str(exc)) from exc
        except Exception as exc:
            raise ProviderCreateAmbiguous(str(exc)) from exc

        self._sandboxes[sandbox.sandbox_id] = sandbox
        storage = (
            ProviderStorageResult(
                provider_storage_id=sandbox.sandbox_id,
                bound_to_allocation=True,
            )
            if workspace
            else None
        )
        return ProviderCreateResult(
            provider_id=sandbox.sandbox_id,
            provider_instance_id=sandbox.sandbox_id,
            provider_request_id=None,
            workspace_storage=storage,
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        try:
            sandbox = await self._connect(allocation, deadline_at=deadline_at)
            info = await sandbox.get_info(
                request_timeout=self._request_timeout(deadline_at)
            )
            self._validate_info(allocation, profile, info)
            if not await sandbox.is_running(
                request_timeout=self._request_timeout(deadline_at)
            ):
                raise ProviderNotReady("E2B sandbox is not running", retry_after_ms=250)
        except ProviderNotReady:
            raise
        except (TimeoutException, httpx.TimeoutException) as exc:
            raise ProviderNotReady(str(exc), retry_after_ms=500) from exc
        except Exception as exc:
            raise ProviderAllocationFailed(str(exc)) from exc
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_id,
        )

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            sandbox = await self._connect(allocation, deadline_at=deadline_at)
            await self._quiesce(sandbox, allocation.provider_id, deadline_at)
            await sandbox.pause(
                keep_memory=True,
                request_timeout=self._request_timeout(deadline_at),
            )
            self._sandboxes.pop(allocation.provider_id, None)
        except SandboxNotFoundException:
            return
        except Exception as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            await self._sandbox_class.kill(
                allocation.provider_id,
                api_key=self._config.api_key,
                request_timeout=self._request_timeout(deadline_at),
            )
            self._sandboxes.pop(allocation.provider_id, None)
            await self._drop_process_buffers(allocation.provider_id)
        except SandboxNotFoundException:
            return
        except Exception as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        *,
        deadline_at: datetime,
    ) -> None:
        try:
            await self._sandbox_class.kill(
                provider_storage_id,
                api_key=self._config.api_key,
                request_timeout=self._request_timeout(deadline_at),
            )
        except SandboxNotFoundException:
            return
        except Exception as exc:
            raise ProviderLifecycleError(str(exc)) from exc

    async def find_allocations(
        self,
        metadata: tuple[ProviderMetadataEntry, ...],
        *,
        deadline_at: datetime,
    ) -> tuple[ProviderInventoryAllocation, ...]:
        expected = {item.name: item.value for item in metadata}
        try:
            paginator = self._sandbox_class.list(
                query=SandboxQuery(metadata=expected),
                limit=100,
                api_key=self._config.api_key,
                request_timeout=self._request_timeout(deadline_at),
            )
            found: list[Any] = []
            while paginator.has_next:
                found.extend(
                    await paginator.next_items(
                        request_timeout=self._request_timeout(deadline_at)
                    )
                )
        except RateLimitException as exc:
            raise ProviderRateLimited(
                str(exc), retry_after_ms=self._config.rate_limit_retry_after_ms
            ) from exc
        except Exception as exc:
            raise ProviderLifecycleError(str(exc)) from exc
        workspace = expected.get("workload-kind") == WorkloadKind.WORKSPACE.value
        return tuple(
            ProviderInventoryAllocation(
                provider_id=item.sandbox_id,
                provider_instance_id=item.sandbox_id,
                workspace_storage=(
                    ProviderStorageResult(
                        provider_storage_id=item.sandbox_id,
                        bound_to_allocation=True,
                    )
                    if workspace
                    else None
                ),
            )
            for item in found
            if self._inventory_metadata_matches(item, expected)
        )

    @staticmethod
    def _inventory_metadata_matches(item: Any, expected: dict[str, str]) -> bool:
        metadata = getattr(item, "metadata", None)
        return isinstance(metadata, Mapping) and all(
            metadata.get(name) == value for name, value in expected.items()
        )

    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        sandbox = await self._connect(
            request.allocation, deadline_at=request.request.deadline_at
        )
        process_id: str | None = None
        buffer = _ProcessBuffer(request.request.output_limit_bytes)
        environment = {item.name: item.value for item in request.request.environment}
        environment[_OPERATION_ENV] = str(request.process.operation_id)
        command = (
            request.request.shell_command
            if request.request.shell_command is not None
            else shlex.join(request.request.argv or ())
        )
        try:
            await self._extend_for_process(
                sandbox,
                request.allocation.key.workload_kind,
                request.request.deadline_at,
            )
            if request.request.tty is None:
                handle = await sandbox.commands.run(
                    self._wrapped_command(request.process.operation_id, command),
                    background=True,
                    envs=environment,
                    cwd=request.request.cwd,
                    stdin=True,
                    timeout=0,
                    request_timeout=self._request_timeout(request.request.deadline_at),
                    on_stdout=lambda data: buffer.append(
                        ProcessOutputChannel.STDOUT, data
                    ),
                    on_stderr=lambda data: buffer.append(
                        ProcessOutputChannel.STDERR, data
                    ),
                )
            else:
                handle = await sandbox.pty.create(
                    PtySize(
                        rows=request.request.tty.rows,
                        cols=request.request.tty.cols,
                    ),
                    on_data=lambda data: buffer.append(ProcessOutputChannel.PTY, data),
                    cwd=request.request.cwd,
                    envs=environment,
                    timeout=0,
                    request_timeout=self._request_timeout(request.request.deadline_at),
                )
                try:
                    wrapped = self._wrapped_command(
                        request.process.operation_id, command
                    )
                    await sandbox.pty.send_stdin(
                        handle.pid,
                        f"exec bash -lc {shlex.quote(wrapped)}\n".encode(),
                        request_timeout=self._request_timeout(
                            request.request.deadline_at
                        ),
                    )
                except Exception:
                    await sandbox.pty.kill(handle.pid)
                    raise
            process_id = str(handle.pid)
            await self._register_process(
                request.allocation.provider_id,
                process_id,
                buffer,
                handle,
                request.request.deadline_at,
            )
        except Exception as exc:
            if process_id is not None:
                raise ProviderProcessStartAmbiguous(str(exc)) from exc
            try:
                adopted = await self._find_process_by_operation(
                    sandbox, request.process.operation_id, request.request.deadline_at
                )
            except Exception:
                raise ProviderProcessStartAmbiguous(str(exc)) from exc
            if adopted is None:
                if isinstance(
                    exc,
                    (
                        AuthenticationException,
                        InvalidArgumentException,
                        FileNotFoundException,
                    ),
                ):
                    raise ProviderProcessStartRejected(str(exc)) from exc
                raise ProviderProcessStartAmbiguous(str(exc)) from exc
            process_id = str(adopted)
            await self._reconnect_process(
                request.allocation,
                request.process,
                process_id,
                deadline_at=request.request.deadline_at,
            )
        return ProviderProcessStartResult(
            provider_process_id=process_id,
            provider_tag=str(request.process.operation_id),
        )

    async def send_process_input(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        pid = self._pid(process)
        if process.tty:
            await sandbox.pty.send_stdin(
                pid, data, request_timeout=self._request_timeout(deadline_at)
            )
        else:
            await sandbox.commands.send_stdin(
                pid, data, request_timeout=self._request_timeout(deadline_at)
            )

    async def read_process_output(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        process_id = str(self._pid(process))
        buffer = await self._get_or_reconnect_process(
            allocation, process, process_id, deadline_at=deadline_at
        )
        if wait_seconds > 0 and buffer.state == ProcessState.RUNNING:
            async with buffer.condition:
                has_new = any(item.sequence > after_sequence for item in buffer.chunks)
                if not has_new and buffer.state == ProcessState.RUNNING:
                    try:
                        await asyncio.wait_for(
                            buffer.condition.wait(),
                            timeout=min(wait_seconds, self._remaining(deadline_at)),
                        )
                    except TimeoutError:
                        pass
        chunks = tuple(item for item in buffer.chunks if item.sequence > after_sequence)
        return ProcessOutputSnapshot(
            chunks=chunks,
            next_sequence=buffer.next_sequence,
            truncated_before_sequence=buffer.truncated_before_sequence,
            state=buffer.state,
            exit_code=buffer.exit_code,
        )

    async def resize_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        if not process.tty:
            raise InvalidArgumentException("only PTY processes can be resized")
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        await sandbox.pty.resize(
            self._pid(process),
            PtySize(rows=size.rows, cols=size.cols),
            request_timeout=self._request_timeout(deadline_at),
        )

    async def terminate_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        del grace_seconds
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        pid = self._pid(process)
        if process.tty:
            await sandbox.pty.kill(
                pid, request_timeout=self._request_timeout(deadline_at)
            )
        else:
            await sandbox.commands.kill(
                pid, request_timeout=self._request_timeout(deadline_at)
            )
        buffer = await self._get_process(allocation.provider_id, str(pid))
        if buffer is not None:
            await buffer.complete(ProcessState.CANCELLED, None)

    async def stat_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> FileStat:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        safe_path = self._safe_path(allocation.key.workload_kind, path)
        info = await sandbox.files.get_info(
            safe_path, request_timeout=self._request_timeout(deadline_at)
        )
        digest = None
        if info.type == FileType.FILE:
            data = await sandbox.files.read(
                safe_path,
                format="bytes",
                request_timeout=self._request_timeout(deadline_at),
            )
            digest = f"sha256:{hashlib.sha256(bytes(data)).hexdigest()}"
        return self._file_stat(info, sha256=digest)

    async def list_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> tuple[FileStat, ...]:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        entries = await sandbox.files.list(
            self._safe_path(allocation.key.workload_kind, path),
            depth=1,
            request_timeout=self._request_timeout(deadline_at),
        )
        return tuple(self._file_stat(entry, sha256=None) for entry in entries)

    async def read_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> bytes:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        data = bytes(
            await sandbox.files.read(
                self._safe_path(allocation.key.workload_kind, path),
                format="bytes",
                request_timeout=self._request_timeout(deadline_at),
            )
        )
        end = (
            None if byte_range.length is None else byte_range.offset + byte_range.length
        )
        return data[byte_range.offset : end]

    async def write_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        data: bytes,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        safe_path = self._safe_path(allocation.key.workload_kind, path)
        if expected_sha256 is not None:
            existing = await sandbox.files.read(
                safe_path,
                format="bytes",
                request_timeout=self._request_timeout(deadline_at),
            )
            actual = f"sha256:{hashlib.sha256(bytes(existing)).hexdigest()}"
            if actual != expected_sha256:
                raise ValueError("file digest precondition failed")
        temporary = f"{safe_path}.agentbox-{allocation.allocation_token.hex}.tmp"
        await sandbox.files.write(
            temporary,
            data,
            request_timeout=self._request_timeout(deadline_at),
            use_octet_stream=True,
        )
        try:
            info = await sandbox.files.rename(
                temporary,
                safe_path,
                request_timeout=self._request_timeout(deadline_at),
            )
        except Exception:
            try:
                await sandbox.files.remove(temporary)
            except Exception:
                pass
            raise
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return self._file_stat(info, sha256=digest)

    async def move_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        await sandbox.files.rename(
            self._safe_path(allocation.key.workload_kind, source),
            self._safe_path(allocation.key.workload_kind, destination),
            request_timeout=self._request_timeout(deadline_at),
        )

    async def delete_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        safe_path = self._safe_path(allocation.key.workload_kind, path)
        if not await sandbox.files.exists(
            safe_path, request_timeout=self._request_timeout(deadline_at)
        ):
            return False
        info = await sandbox.files.get_info(
            safe_path, request_timeout=self._request_timeout(deadline_at)
        )
        if info.type == FileType.DIR and not recursive:
            entries = await sandbox.files.list(
                safe_path,
                depth=1,
                request_timeout=self._request_timeout(deadline_at),
            )
            if entries:
                raise ValueError("directory is not empty; recursive=true is required")
        await sandbox.files.remove(
            safe_path, request_timeout=self._request_timeout(deadline_at)
        )
        return True

    async def create_python_session(
        self,
        allocation: ProviderAllocationRef,
        request: CreatePythonSessionRequest,
    ) -> ProviderPythonSessionCreateResult:
        sandbox = await self._connect(allocation, deadline_at=request.deadline_at)
        before: set[str] | None = None
        try:
            before = {context.id for context in await sandbox.list_code_contexts()}
            context = await sandbox.create_code_context(
                cwd=request.cwd,
                language="python",
                request_timeout=self._request_timeout(request.deadline_at),
            )
        except (AuthenticationException, InvalidArgumentException) as exc:
            raise ProviderPythonSessionCreateRejected(str(exc)) from exc
        except Exception as exc:
            if before is not None:
                try:
                    after = await sandbox.list_code_contexts()
                    created = [context for context in after if context.id not in before]
                    if len(created) == 1 and created[0].cwd == request.cwd:
                        return ProviderPythonSessionCreateResult(
                            provider_context_id=created[0].id
                        )
                except Exception:
                    pass
            raise ProviderPythonSessionCreateAmbiguous(str(exc)) from exc
        return ProviderPythonSessionCreateResult(provider_context_id=context.id)

    async def execute_python(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        if session.provider_context_id is None:
            raise ProviderPythonExecutionRejected("Python context is not acknowledged")
        sandbox = await self._connect(allocation, deadline_at=request.deadline_at)
        context = Context(
            context_id=session.provider_context_id,
            language="python",
            cwd=session.cwd,
        )
        remaining = self._remaining(request.deadline_at)
        try:
            execution = await sandbox.run_code(
                request.code,
                context=context,
                envs={item.name: item.value for item in request.environment},
                timeout=remaining,
                request_timeout=min(
                    self._config.request_timeout_seconds, max(1, remaining)
                ),
            )
        except TimeoutException as exc:
            if "Execution timed out" in str(exc):
                return PythonResult(
                    operation_id=request.operation_id,
                    state=PythonExecutionState.TIMED_OUT,
                    stdout="",
                    stderr="",
                    result=None,
                    error_name="TimeoutError",
                    error_message="Python execution deadline elapsed",
                    traceback=None,
                    output_truncated=False,
                )
            raise ProviderPythonExecutionAmbiguous(str(exc)) from exc
        except (AuthenticationException, InvalidArgumentException) as exc:
            raise ProviderPythonExecutionRejected(str(exc)) from exc
        except Exception as exc:
            raise ProviderPythonExecutionAmbiguous(str(exc)) from exc
        result_text = next(
            (
                result.text
                for result in reversed(execution.results)
                if result.text is not None and result.is_main_result
            ),
            next(
                (
                    result.text
                    for result in reversed(execution.results)
                    if result.text is not None
                ),
                None,
            ),
        )
        return self._bounded_python_result(
            request,
            state=(
                PythonExecutionState.FAILED
                if execution.error is not None
                else PythonExecutionState.SUCCEEDED
            ),
            stdout="".join(execution.logs.stdout),
            stderr="".join(execution.logs.stderr),
            result=result_text,
            error_name=(execution.error.name if execution.error is not None else None),
            error_message=(
                execution.error.value if execution.error is not None else None
            ),
            traceback=(
                execution.error.traceback if execution.error is not None else None
            ),
        )

    async def restart_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> ProviderPythonSessionCreateResult:
        if session.provider_context_id is None:
            raise ProviderPythonSessionCreateRejected(
                "Python context is not acknowledged"
            )
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        try:
            await sandbox.restart_code_context(session.provider_context_id)
        except Exception as exc:
            raise ProviderPythonSessionCreateAmbiguous(str(exc)) from exc
        return ProviderPythonSessionCreateResult(
            provider_context_id=session.provider_context_id
        )

    async def delete_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> None:
        if session.provider_context_id is None:
            return
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        try:
            await sandbox.remove_code_context(session.provider_context_id)
        except FileNotFoundException:
            return

    async def resolve_port_target(
        self,
        allocation: ProviderAllocationRef,
        *,
        port: int,
        protocol: PortProtocol,
        deadline_at: datetime,
    ) -> ProviderPortTarget:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        headers = (
            (
                ProviderMetadataEntry(
                    name="E2B-Traffic-Access-Token",
                    value=sandbox.traffic_access_token,
                ),
            )
            if sandbox.traffic_access_token
            else ()
        )
        return ProviderPortTarget(
            base_url=f"{protocol.value}://{sandbox.get_host(port)}",
            headers=headers,
        )

    async def close(self) -> None:
        for buffer in tuple(self._processes.values()):
            for task in (buffer.watcher, buffer.deadline_task):
                if task is not None:
                    task.cancel()
        self._processes.clear()
        self._sandboxes.clear()

    async def _connect(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> E2BSandboxType:
        existing = self._sandboxes.get(allocation.provider_id)
        if existing is not None:
            return existing
        timeout = (
            self._config.workspace_timeout_seconds
            if allocation.key.workload_kind == WorkloadKind.WORKSPACE
            else self._config.function_timeout_seconds
        )
        sandbox = await self._sandbox_class.connect(
            allocation.provider_id,
            timeout=timeout,
            api_key=self._config.api_key,
            request_timeout=self._request_timeout(deadline_at),
        )
        self._sandboxes[allocation.provider_id] = sandbox
        return sandbox

    def _validate_info(
        self,
        allocation: ProviderAllocationRef,
        profile: SandboxProfileRef,
        info: Any,
    ) -> None:
        metadata = info.metadata
        expected = {
            "managed-by": "agentbox",
            "provider-scope": self.scope,
            "workload-kind": allocation.key.workload_kind.value,
            "logical-id": str(allocation.key.logical_id),
            "allocation-id": str(allocation.allocation_id),
            "allocation-token": str(allocation.allocation_token),
            "profile-name": profile.name,
            "profile-digest": profile.digest,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ProviderAllocationFailed(
                "E2B allocation ownership metadata does not match durable state"
            )
        artifact = self._profiles.e2b_artifact(
            profile, workload_kind=allocation.key.workload_kind
        )
        if info.template_id not in {artifact.template_id, artifact.build_id}:
            raise ProviderAllocationFailed("E2B template does not match profile")

    async def _quiesce(
        self,
        sandbox: E2BSandboxType,
        provider_id: str,
        deadline_at: datetime,
    ) -> None:
        processes = await sandbox.commands.list(
            request_timeout=self._request_timeout(deadline_at)
        )
        await asyncio.gather(
            *(
                sandbox.commands.kill(
                    process.pid,
                    request_timeout=self._request_timeout(deadline_at),
                )
                for process in processes
            ),
            return_exceptions=True,
        )
        contexts = await sandbox.list_code_contexts()
        await asyncio.gather(
            *(sandbox.remove_code_context(context.id) for context in contexts),
            return_exceptions=True,
        )
        if await sandbox.files.exists(
            _PROCESS_ROOT, request_timeout=self._request_timeout(deadline_at)
        ):
            await sandbox.files.remove(
                _PROCESS_ROOT, request_timeout=self._request_timeout(deadline_at)
            )
        await self._drop_process_buffers(provider_id)

    async def _extend_for_process(
        self,
        sandbox: E2BSandboxType,
        workload_kind: WorkloadKind,
        deadline_at: datetime,
    ) -> None:
        if workload_kind != WorkloadKind.FUNCTION:
            return
        remaining = max(60, int(self._remaining(deadline_at)))
        await sandbox.set_timeout(
            remaining + self._config.function_idle_grace_seconds,
            request_timeout=self._request_timeout(deadline_at),
        )

    async def _register_process(
        self,
        provider_id: str,
        process_id: str,
        buffer: _ProcessBuffer,
        handle: Any,
        deadline_at: datetime,
    ) -> None:
        buffer.handle = handle
        buffer.watcher = asyncio.create_task(self._watch_process(buffer, handle))
        buffer.deadline_task = asyncio.create_task(
            self._enforce_process_deadline(provider_id, process_id, buffer, deadline_at)
        )
        async with self._process_lock:
            self._processes[(provider_id, process_id)] = buffer

    async def _watch_process(self, buffer: _ProcessBuffer, handle: Any) -> None:
        try:
            result = await handle.wait()
        except CommandExitException as exc:
            await buffer.complete(ProcessState.FAILED, exc.exit_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A stream disconnect is not proof that the command stopped. A later
            # read reconnects by exact PID and reports any output gap explicitly.
            return
        else:
            await buffer.complete(
                ProcessState.SUCCEEDED
                if result.exit_code == 0
                else ProcessState.FAILED,
                result.exit_code,
            )

    async def _enforce_process_deadline(
        self,
        provider_id: str,
        process_id: str,
        buffer: _ProcessBuffer,
        deadline_at: datetime,
    ) -> None:
        try:
            await asyncio.sleep(max(0, self._remaining(deadline_at)))
            if buffer.state != ProcessState.RUNNING:
                return
            handle = buffer.handle
            if handle is not None:
                await handle.kill()
            await buffer.complete(ProcessState.TIMED_OUT, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            await buffer.complete(ProcessState.TIMED_OUT, None)

    async def _get_or_reconnect_process(
        self,
        allocation: ProviderAllocationRef,
        process: ProcessRef,
        process_id: str,
        *,
        deadline_at: datetime,
    ) -> _ProcessBuffer:
        existing = await self._get_process(allocation.provider_id, process_id)
        if existing is not None:
            return existing
        return await self._reconnect_process(
            allocation, process, process_id, deadline_at=deadline_at
        )

    async def _reconnect_process(
        self,
        allocation: ProviderAllocationRef,
        process: ProcessRef,
        process_id: str,
        *,
        deadline_at: datetime,
    ) -> _ProcessBuffer:
        sandbox = await self._connect(allocation, deadline_at=deadline_at)
        buffer = _ProcessBuffer(
            output_limit_bytes=process.output_limit_bytes,
            truncated_before_sequence=1,
        )
        pid = int(process_id)
        active = {
            item.pid
            for item in await sandbox.commands.list(
                request_timeout=self._request_timeout(deadline_at)
            )
        }
        if pid not in active:
            exit_code = await self._read_exit_code(
                sandbox, process.operation_id, deadline_at
            )
            await buffer.complete(
                (ProcessState.SUCCEEDED if exit_code == 0 else ProcessState.FAILED),
                exit_code,
            )
            async with self._process_lock:
                self._processes[(allocation.provider_id, process_id)] = buffer
            return buffer
        if process.tty:
            handle = await sandbox.pty.connect(
                pid,
                on_data=lambda data: buffer.append(ProcessOutputChannel.PTY, data),
                timeout=0,
                request_timeout=self._request_timeout(deadline_at),
            )
        else:
            handle = await sandbox.commands.connect(
                pid,
                timeout=0,
                request_timeout=self._request_timeout(deadline_at),
                on_stdout=lambda data: buffer.append(ProcessOutputChannel.STDOUT, data),
                on_stderr=lambda data: buffer.append(ProcessOutputChannel.STDERR, data),
            )
        await self._register_process(
            allocation.provider_id, process_id, buffer, handle, process.deadline_at
        )
        return buffer

    async def _find_process_by_operation(
        self, sandbox: E2BSandboxType, operation_id: UUID, deadline_at: datetime
    ) -> int | None:
        matches = [
            item.pid
            for item in await sandbox.commands.list(
                request_timeout=self._request_timeout(deadline_at)
            )
            if item.envs.get(_OPERATION_ENV) == str(operation_id)
        ]
        if len(matches) > 1:
            raise RuntimeError("multiple E2B processes have the same operation ID")
        return matches[0] if matches else None

    async def _read_exit_code(
        self, sandbox: E2BSandboxType, operation_id: UUID, deadline_at: datetime
    ) -> int | None:
        path = f"{_PROCESS_ROOT}/{operation_id}/exit"
        if not await sandbox.files.exists(
            path, request_timeout=self._request_timeout(deadline_at)
        ):
            return None
        raw = await sandbox.files.read(
            path, request_timeout=self._request_timeout(deadline_at)
        )
        try:
            return int(raw.strip())
        except ValueError:
            return None

    async def _get_process(
        self, provider_id: str, process_id: str
    ) -> _ProcessBuffer | None:
        async with self._process_lock:
            return self._processes.get((provider_id, process_id))

    async def _drop_process_buffers(self, provider_id: str) -> None:
        async with self._process_lock:
            keys = [key for key in self._processes if key[0] == provider_id]
            buffers = [self._processes.pop(key) for key in keys]
        for buffer in buffers:
            for task in (buffer.watcher, buffer.deadline_task):
                if task is not None:
                    task.cancel()

    @staticmethod
    def _wrapped_command(operation_id: UUID, command: str) -> str:
        directory = f"{_PROCESS_ROOT}/{operation_id}"
        return (
            f"mkdir -p {shlex.quote(directory)}; "
            f"printf '%s' \"$$\" > {shlex.quote(directory + '/pid')}; "
            f"bash -lc {shlex.quote(command)}; "
            "agentbox_rc=$?; "
            f"printf '%s' \"$agentbox_rc\" > {shlex.quote(directory + '/exit.tmp')}; "
            f"mv {shlex.quote(directory + '/exit.tmp')} {shlex.quote(directory + '/exit')}; "
            "exit $agentbox_rc"
        )

    @staticmethod
    def _pid(process: ProcessRef) -> int:
        if process.provider_process_id is None:
            raise ValueError("process has no E2B PID")
        return int(process.provider_process_id)

    @staticmethod
    def _file_stat(info: EntryInfo, *, sha256: str | None) -> FileStat:
        kind = (
            FileKind.SYMLINK
            if info.symlink_target is not None
            else FileKind.DIRECTORY
            if info.type == FileType.DIR
            else FileKind.FILE
        )
        return FileStat(
            path=info.path,
            kind=kind,
            size_bytes=info.size,
            modified_at=info.modified_time,
            mode=info.mode,
            sha256=sha256,
        )

    @staticmethod
    def _safe_path(workload_kind: WorkloadKind, path: str) -> str:
        if not path.startswith("/") or "\x00" in path:
            raise ValueError("filesystem path must be absolute")
        normalized = posixpath.normpath(path)
        roots = (
            ("/workspace", "/tmp")
            if workload_kind == WorkloadKind.WORKSPACE
            else ("/tmp",)
        )
        if not any(
            normalized == root or normalized.startswith(root + "/") for root in roots
        ):
            raise ValueError("filesystem path is outside profile roots")
        return normalized

    def _request_timeout(self, deadline_at: datetime) -> float:
        return min(self._config.request_timeout_seconds, self._remaining(deadline_at))

    @staticmethod
    def _remaining(deadline_at: datetime) -> float:
        return max(0.05, (deadline_at - datetime.now(timezone.utc)).total_seconds())

    @staticmethod
    def _bounded_python_result(
        request: ExecutePythonRequest,
        *,
        state: PythonExecutionState,
        stdout: str,
        stderr: str,
        result: str | None,
        error_name: str | None,
        error_message: str | None,
        traceback: str | None,
    ) -> PythonResult:
        values = [stdout, stderr, result, error_message, traceback]
        remaining = request.output_limit_bytes
        clipped: list[str | None] = []
        truncated = False
        for value in values:
            if value is None:
                clipped.append(None)
                continue
            encoded = value.encode(errors="replace")
            selected = encoded[:remaining]
            clipped.append(selected.decode(errors="replace"))
            remaining -= len(selected)
            truncated = truncated or len(encoded) > len(selected)
        return PythonResult(
            operation_id=request.operation_id,
            state=state,
            stdout=clipped[0] or "",
            stderr=clipped[1] or "",
            result=clipped[2],
            error_name=error_name,
            error_message=clipped[3],
            traceback=clipped[4],
            output_truncated=truncated,
        )
