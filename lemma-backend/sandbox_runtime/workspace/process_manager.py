from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import os
from pathlib import Path
import pty
import signal
import struct
import termios
from uuid import UUID

from sandbox_runtime.protocol import ProcessState, StartProcessRequest
from sandbox_runtime.tasks import create_inherited_task

from .models import OutputChannel, RuntimeProcessResponse


_OUTPUT_DRAIN_GRACE_SECONDS = 0.25
_PROCESS_EXIT_POLL_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class OutputChunk:
    sequence: int
    channel: OutputChannel
    data: bytes


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    chunks: tuple[OutputChunk, ...]
    next_sequence: int
    truncated_before_sequence: int | None


class OutputBuffer:
    def __init__(self, limit_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._chunks: deque[OutputChunk] = deque()
        self._total_bytes = 0
        self._next_sequence = 1
        self._truncated_before_sequence: int | None = None
        self._condition = asyncio.Condition()

    async def append(self, channel: OutputChannel, data: bytes) -> None:
        if not data:
            return
        async with self._condition:
            chunk = OutputChunk(self._next_sequence, channel, data)
            self._next_sequence += 1
            self._chunks.append(chunk)
            self._total_bytes += len(data)
            while self._total_bytes > self._limit_bytes and self._chunks:
                removed = self._chunks.popleft()
                self._total_bytes -= len(removed.data)
                self._truncated_before_sequence = removed.sequence + 1
            self._condition.notify_all()

    async def snapshot(
        self, after_sequence: int, *, wait_seconds: float = 0
    ) -> OutputSnapshot:
        async with self._condition:
            if wait_seconds > 0 and self._next_sequence <= after_sequence + 1:
                try:
                    await asyncio.wait_for(
                        self._condition.wait(), timeout=min(wait_seconds, 30)
                    )
                except TimeoutError:
                    pass
            return OutputSnapshot(
                chunks=tuple(
                    chunk for chunk in self._chunks if chunk.sequence > after_sequence
                ),
                next_sequence=self._next_sequence,
                truncated_before_sequence=self._truncated_before_sequence,
            )

    async def notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def truncated_before_sequence(self) -> int | None:
        return self._truncated_before_sequence


class ManagedProcess:
    def __init__(
        self,
        *,
        operation_id: UUID,
        process: asyncio.subprocess.Process,
        output: OutputBuffer,
        master_fd: int | None,
        pty_queue: asyncio.Queue[bytes | None] | None,
        started_at: datetime,
    ) -> None:
        self.operation_id = operation_id
        self.process = process
        self.output = output
        self.master_fd = master_fd
        self.pty_queue = pty_queue
        self.started_at = started_at
        self.completed_at: datetime | None = None
        self.exit_code: int | None = None
        self.state = ProcessState.RUNNING
        self._termination_requested = False
        self._residual_process_group = False
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._done = asyncio.Event()

    def bind_tasks(self, tasks: tuple[asyncio.Task[None], ...]) -> None:
        self._tasks = tasks

    async def wait(self) -> None:
        await self._done.wait()

    async def send_input(self, data: bytes) -> None:
        if self.state != ProcessState.RUNNING:
            raise RuntimeError("process is not running")
        if self.master_fd is not None:
            view = memoryview(data)
            while view:
                written = os.write(self.master_fd, view)
                view = view[written:]
            return
        if self.process.stdin is None:
            raise RuntimeError("process stdin is unavailable")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    def resize(self, cols: int, rows: int) -> None:
        if self.master_fd is None:
            raise RuntimeError("process does not have a PTY")
        fcntl.ioctl(
            self.master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, cols, 0, 0),
        )

    async def terminate(self, grace_seconds: float) -> None:
        direct_process_running = self.process.returncode is None
        if not direct_process_running and not self._residual_process_group:
            return
        if direct_process_running:
            self._termination_requested = True
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._residual_process_group = False
            return
        try:
            if direct_process_running:
                await asyncio.wait_for(
                    self._wait_for_direct_exit(), timeout=grace_seconds
                )
            else:
                await self._wait_for_process_group_exit(grace_seconds)
        except TimeoutError:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if direct_process_running:
                await self._wait_for_direct_exit()
        finally:
            self._residual_process_group = False

    def response(self) -> RuntimeProcessResponse:
        return RuntimeProcessResponse(
            operation_id=self.operation_id,
            state=self.state,
            started_at=self.started_at,
            completed_at=self.completed_at,
            exit_code=self.exit_code,
            next_output_seq=self.output.next_sequence,
            truncated_before_seq=self.output.truncated_before_sequence,
        )

    async def watch(self) -> None:
        exit_code = await self._wait_for_direct_exit()
        _done, pending = await asyncio.wait(
            self._tasks,
            timeout=_OUTPUT_DRAIN_GRACE_SECONDS,
        )
        self._residual_process_group = bool(pending)
        for task in pending:
            task.cancel()
        if pending:
            self._close_output_transports()
            await asyncio.gather(*pending, return_exceptions=True)
        self.exit_code = exit_code
        self.completed_at = datetime.now(timezone.utc)
        self.state = (
            ProcessState.CANCELLED
            if self._termination_requested
            else ProcessState.SUCCEEDED
            if exit_code == 0
            else ProcessState.FAILED
        )
        self._done.set()
        await self.output.notify_waiters()

    async def _wait_for_direct_exit(self) -> int:
        # asyncio.Process.wait() does not complete until inherited stdout/stderr
        # pipes close. A daemonized descendant may intentionally keep those pipes
        # open after the direct child exits, so terminal state must be fenced on
        # returncode instead.
        while self.process.returncode is None:
            await asyncio.sleep(_PROCESS_EXIT_POLL_SECONDS)
        return self.process.returncode

    async def _wait_for_process_group_exit(self, grace_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + grace_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                os.killpg(self.process.pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(_PROCESS_EXIT_POLL_SECONDS)
        raise TimeoutError

    def _close_output_transports(self) -> None:
        # StreamReader intentionally exposes no public close method. Closing its
        # asyncio transport after the direct process exits prevents a detached
        # descendant from leaking a read descriptor in the long-lived runtime.
        for stream in (self.process.stdout, self.process.stderr):
            transport = getattr(stream, "_transport", None)
            if transport is not None:
                transport.close()

    @property
    def needs_quiesce(self) -> bool:
        return self.state == ProcessState.RUNNING or self._residual_process_group


class ProcessManager:
    def __init__(self, allowed_roots: tuple[str, ...] = ("/workspace", "/tmp")) -> None:
        self._allowed_roots = tuple(Path(root).resolve() for root in allowed_roots)
        self._processes: dict[UUID, ManagedProcess] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: StartProcessRequest) -> tuple[ManagedProcess, bool]:
        self._validate_request(request)
        async with self._lock:
            existing = self._processes.get(request.operation_id)
            if existing is not None:
                return existing, False
            managed = await self._spawn(request)
            self._processes[request.operation_id] = managed
            return managed, True

    async def get(self, operation_id: UUID) -> ManagedProcess | None:
        async with self._lock:
            return self._processes.get(operation_id)

    async def list(self) -> tuple[ManagedProcess, ...]:
        async with self._lock:
            return tuple(self._processes.values())

    async def quiesce(self) -> int:
        processes = await self.list()
        running = tuple(item for item in processes if item.needs_quiesce)
        await asyncio.gather(
            *(item.terminate(2) for item in running), return_exceptions=True
        )
        return len(running)

    def _validate_request(self, request: StartProcessRequest) -> None:
        if request.deadline_at <= datetime.now(timezone.utc):
            raise ValueError("process deadline has elapsed")
        cwd = Path(request.cwd).resolve(strict=True)
        if not any(
            cwd == root or cwd.is_relative_to(root) for root in self._allowed_roots
        ):
            raise ValueError("process cwd is outside allowed roots")

    async def _spawn(self, request: StartProcessRequest) -> ManagedProcess:
        environment = os.environ.copy()
        environment.pop("AGENTBOX_RUNTIME_TOKEN", None)
        environment.pop("AGENTBOX_RUNTIME_TOKEN_FILE", None)
        environment.update({item.name: item.value for item in request.environment})
        output = OutputBuffer(request.output_limit_bytes)
        if request.tty is not None:
            master_fd, slave_fd = pty.openpty()
            fcntl.ioctl(
                master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", request.tty.rows, request.tty.cols, 0, 0),
            )
            os.set_blocking(master_fd, False)
            try:
                process = await self._create_subprocess(
                    request,
                    environment,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                )
            finally:
                os.close(slave_fd)
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def read_pty() -> None:
                try:
                    data = os.read(master_fd, 65536)
                    queue.put_nowait(data or None)
                except BlockingIOError:
                    return
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        queue.put_nowait(None)
                    else:
                        raise

            loop.add_reader(master_fd, read_pty)
            pty_task = create_inherited_task(
                self._pump_pty(master_fd, queue, output),
                name=f"workspace-pty-{request.operation_id}",
            )
            managed = ManagedProcess(
                operation_id=request.operation_id,
                process=process,
                output=output,
                master_fd=master_fd,
                pty_queue=queue,
                started_at=datetime.now(timezone.utc),
            )
            managed.bind_tasks((pty_task,))
        else:
            process = await self._create_subprocess(
                request,
                environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_task = create_inherited_task(
                self._pump_stream(process.stdout, OutputChannel.STDOUT, output),
                name=f"workspace-stdout-{request.operation_id}",
            )
            stderr_task = create_inherited_task(
                self._pump_stream(process.stderr, OutputChannel.STDERR, output),
                name=f"workspace-stderr-{request.operation_id}",
            )
            managed = ManagedProcess(
                operation_id=request.operation_id,
                process=process,
                output=output,
                master_fd=None,
                pty_queue=None,
                started_at=datetime.now(timezone.utc),
            )
            managed.bind_tasks((stdout_task, stderr_task))
        if request.initial_input is not None:
            await managed.send_input(request.initial_input)
        create_inherited_task(
            managed.watch(), name=f"workspace-process-{request.operation_id}"
        )
        return managed

    @staticmethod
    async def _create_subprocess(
        request: StartProcessRequest,
        environment: dict[str, str],
        *,
        stdin,
        stdout,
        stderr,
    ) -> asyncio.subprocess.Process:
        common = {
            "cwd": request.cwd,
            "env": environment,
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "start_new_session": True,
        }
        if request.shell_command is not None:
            return await asyncio.create_subprocess_exec(
                "/bin/bash", "-lc", request.shell_command, **common
            )
        return await asyncio.create_subprocess_exec(*(request.argv or ()), **common)

    @staticmethod
    async def _pump_stream(
        stream: asyncio.StreamReader | None,
        channel: OutputChannel,
        output: OutputBuffer,
    ) -> None:
        if stream is None:
            return
        while chunk := await stream.read(65536):
            await output.append(channel, chunk)

    @staticmethod
    async def _pump_pty(
        master_fd: int,
        queue: asyncio.Queue[bytes | None],
        output: OutputBuffer,
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                await output.append(OutputChannel.PTY, chunk)
        finally:
            loop.remove_reader(master_fd)
            os.close(master_fd)
